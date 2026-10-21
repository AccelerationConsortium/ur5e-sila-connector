import asyncio
import contextlib
import datetime
import json
import math
import os
import pathlib
import re
import tempfile
import time
import uuid

from sila.client import Client, ClientConfig

JOINTS = ("Base", "Shoulder", "Elbow", "Wrist1", "Wrist2", "Wrist3")
ROBOT = "io.unitelabs/robot/RobotController/v1"
SIMULATION = "org.silastandard/core/SimulationController/v1"


class PanelError(ValueError):
    """Represent a rejected panel operation without sending hardware commands."""


def now() -> str:
    """Return a UTC audit timestamp."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def identifier(value: str) -> str:
    """Validate IDs before using them as local record filenames."""
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is None:
        msg = "Invalid record identifier."
        raise PanelError(msg)
    return value


def write_record(path: pathlib.Path, value: dict, *, exclusive: bool = False) -> None:
    """Persist an audit record atomically, or reserve it without replacing an earlier run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        with path.open("x") as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = pathlib.Path(stream.name)
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_records(directory: pathlib.Path) -> list[dict]:
    """Read persistent panel records without silently discarding corrupt data."""
    if not directory.exists():
        return []
    records = []
    for path in sorted(directory.glob("*.json")):
        record = json.loads(path.read_text())
        if not isinstance(record, dict):
            msg = "A panel record is invalid; inspect the data directory."
            raise PanelError(msg)
        records.append(record)
    return records


def compile_steps(rows: list, initial: list[float], positions: dict) -> tuple[list, list]:
    """Expand reviewed relative moves or taught joint poses into bounded SiLA operations."""
    if not isinstance(rows, list) or not 1 <= len(rows) <= 30:
        msg = "A sequence must contain 1-30 rows."
        raise PanelError(msg)
    if len(initial) != 6 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in initial):
        msg_0 = "A finite six-joint pose is required."
        raise PanelError(msg_0)
    q = [math.degrees(v) for v in initial]
    steps = []

    def append_move(joint: str, delta: float) -> None:
        if joint not in JOINTS or isinstance(delta, bool) or not isinstance(delta, (int, float)):
            msg = "Choose a valid joint and a numeric angle."
            raise PanelError(msg)
        if not math.isfinite(delta) or not 0.5 <= abs(delta) <= 90:
            msg = "Each relative row must move 0.5-90 degrees."
            raise PanelError(msg)
        index = JOINTS.index(joint)
        count = math.ceil(abs(delta) / 10)
        part = delta / count
        for _ in range(count):
            q[index] += part
            for i, angle in enumerate(q):
                limit = 160 if i == 2 else 360
                if not -limit < angle < limit:
                    msg = f"The predicted {JOINTS[i]} target is outside its joint limits."
                    raise PanelError(msg)
            steps.append({"joint": joint, "delta": part, "target_degrees": q.copy()})

    for row in rows:
        row_start = len(steps)
        if not isinstance(row, dict):
            msg = "Invalid sequence row."
            raise PanelError(msg)
        if "position_id" in row:
            position = positions.get(identifier(row["position_id"]))
            if position is None:
                msg = "Select a recorded position first."
                raise PanelError(msg)
            target = [math.degrees(v) for v in position["joints"]]
            for i, value in enumerate(target):
                delta = value - q[i]
                if abs(delta) <= 0.1:
                    continue
                if abs(delta) < 0.5:
                    msg = "This taught target needs a sub-0.5 degree trim, below the current verified step range."
                    raise PanelError(msg)
                append_move(JOINTS[i], delta)
        else:
            append_move(row.get("joint"), row.get("delta"))
        for step in steps[row_start:]:
            step["phrase"] = str(row.get("phrase", "Custom sequence"))[:60]
        if len(steps) > 80:
            msg = "This sequence exceeds 80 bounded moves; split it into shorter runs."
            raise PanelError(msg)
    return steps, q


class PanelService:
    """Control the connector exclusively through its public SiLA interface."""

    def __init__(self, data: pathlib.Path, read_port: int = 50053, control_port: int = 50054) -> None:
        self.data = data
        self.read = Client(ClientConfig("127.0.0.1", read_port, tls=False, default_timeout=5000))
        self.control = Client(ClientConfig("127.0.0.1", control_port, tls=False, default_timeout=65000))
        self.read_port, self.control_port = read_port, control_port
        self.robot = None
        self.controller = None
        self.read_mode = None
        self.control_mode = None
        self.simulated = False
        self.cache: dict = {}
        self.cache_time = 0.0
        self.status_lock = asyncio.Lock()
        self.start_lock = asyncio.Lock()
        self.previews: dict = {}
        self.positions: dict = {}
        self.jobs: dict = {}
        self.task = None
        self.cancelled = asyncio.Event()
        self.closing = False
        self.continuous_plan = None
        self.reset_checkpoint = None
        self.motion_check = None
        self.task_library = {}
        self.resume_context = None

    async def open(self) -> None:
        """Connect and recover records without resuming any earlier movement."""
        try:
            if await self.read.open() or await self.control.open():
                msg = "Some SiLA features could not be loaded."
                raise PanelError(msg)
            self.robot = await self.read.load_feature(ROBOT)
            self.controller = await self.control.load_feature(ROBOT)
            read_mode = await self.read.load_feature(SIMULATION)
            control_mode = await self.control.load_feature(SIMULATION)
            self.read_mode, self.control_mode = read_mode, control_mode
            self.simulated = await read_mode.properties["SimulationMode"].read()
            if self.simulated != await control_mode.properties["SimulationMode"].read():
                msg = "Read and control servers must use the same simulation mode."
                raise PanelError(msg)
            if "MoveJointAtSpeed" not in self.controller.commands:
                msg = "Restart the connector to load the adjustable-speed command."
                raise PanelError(msg)
            self.positions = {p["id"]: p for p in await asyncio.to_thread(read_records, self.data / "positions")}
            self.jobs = {j["id"]: j for j in await asyncio.to_thread(read_records, self.data / "runs")}
            self.task_library = {j["id"]: j for j in await asyncio.to_thread(read_records, self.data / "tasks")}
            for job in self.jobs.values():
                if job.get("status") in {"running", "queued", "pausing", "stopping", "paused"}:
                    job["status"] = "interrupted"
            await self.status(force=True)
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        """Stop queuing further work and disconnect; never resume or replay a run."""
        self.closing = True
        self.cancelled.set()
        if self.task is not None and not self.task.done():
            # A verified bounded move may finish before the queue can safely close.
            await self.task
        await self.control.close()
        await self.read.close()

    async def status(self, *, force: bool = False) -> dict:
        """Read live status; retain an explicit offline marker if a refresh fails."""
        async with self.status_lock:
            if not force and time.monotonic() - self.cache_time < 0.7:
                return self.cache
            state = {"connected": False, "simulation": self.simulated, "updated_at": now()}
            try:
                if (
                    await self.read_mode.properties["SimulationMode"].read() != self.simulated
                    or await self.control_mode.properties["SimulationMode"].read() != self.simulated
                ):
                    msg = "The backend mode changed; restart the panel to review the new mode."
                    raise PanelError(msg)
                report = {}
                if "MotionProgress" in self.controller.properties:
                    report = json.loads(await self.controller.properties["MotionProgress"].read())
                    state["motion_progress"] = report
                    job = self.jobs.get(report.get("request_id"))
                    if job is not None and job.get("status") in {"running", "pausing", "stopping"}:
                        job["progress"] = report
                if self.busy():
                    # The motion owns its command lock. Read its independent measured channel instead.
                    for key in ("SoftwareVersion", "ToolPose", "SpeedSliderFraction"):
                        if key in self.cache:
                            state[key] = self.cache[key]
                    state["metadata_stale_during_motion"] = True
                    state["connected"] = time.time() - report.get("observed_at", 0) < 3
                    state["JointPositions"] = report.get("positions", [])
                    state["RobotMode"] = "RUNNING" if report.get("robot_mode") == 7 else "UNKNOWN"
                    state["SafetyStatus"] = "NORMAL" if report.get("safety_mode") == 1 else "UNKNOWN"
                    state["ProgramState"] = {1: "STOPPED", 2: "PLAYING", 4: "PAUSED"}.get(
                        report.get("runtime_state"), "UNKNOWN"
                    )
                    state["Running"] = report.get("runtime_state") == 2
                else:
                    for name in (
                        "JointPositions",
                        "RobotMode",
                        "SafetyStatus",
                        "ProgramState",
                        "Running",
                        "SoftwareVersion",
                        "ToolPose",
                        "SpeedSliderFraction",
                    ):
                        state[name] = await self.robot.properties[name].read()
                    state["connected"] = True
                state["ControlOutcomeUnknown"] = await self.controller.properties["ControlOutcomeUnknown"].read()
                state["MotionControlsEnabled"] = await self.controller.properties["MotionControlsEnabled"].read()
            except Exception as error:  # noqa: BLE001 -- SDK errors are dynamically defined by remote features.
                state["error"] = str(error)
            state["jobs"] = [
                {k: v for k, v in j.items() if k not in {"steps", "receipt", "plan"}}
                for j in sorted(self.jobs.values(), key=lambda j: j["created_at"], reverse=True)[:15]
            ]
            state["positions"] = [p for p in self.positions.values() if p.get("simulation") is self.simulated]
            state["task_library"] = [
                {"id": p["id"], "name": p["name"], "created_at": p["created_at"], "simulation": p["simulation"]}
                for p in self.task_library.values()
            ]
            state["playback_available"] = "ControlActiveMotion" in self.controller.commands
            state["kinematics_available"] = "SolveTcp" in self.controller.commands
            state["motion_check"] = self.motion_check
            state["continuous_plan"] = self.continuous_plan
            state["read_port"], state["control_port"] = self.read_port, self.control_port
            self.cache, self.cache_time = state, time.monotonic()
            return state

    async def ready(self) -> dict:
        """Require fresh stopped, normal state before previewing, teaching or starting."""
        if self.closing:
            msg = "The panel is closing."
            raise PanelError(msg)
        state = await self.status(force=True)
        if not state["connected"]:
            msg = "The robot is unavailable; refresh the connection first."
            raise PanelError(msg)
        if state["SafetyStatus"] != "NORMAL" or state["ProgramState"] != "STOPPED" or state["RobotMode"] != "RUNNING":
            msg = "The robot must report NORMAL and STOPPED."
            raise PanelError(msg)
        if state["ControlOutcomeUnknown"]:
            msg = "An earlier control result is uncertain; inspect and reconcile the controller first."
            raise PanelError(msg)
        return state

    def busy(self) -> bool:
        """Report an active panel-owned run."""
        return self.task is not None and not self.task.done()

    async def teach(self, name: str) -> dict:
        """Record an actual stationary joint pose and its measured TCP location."""
        async with self.start_lock:
            if self.busy():
                msg = "Wait for the active run to finish before teaching a position."
                raise PanelError(msg)
            if not isinstance(name, str) or not name.strip() or len(name) > 60:
                msg = "Use a position name of 1-60 characters."
                raise PanelError(msg)
            state = await self.ready()
            await asyncio.sleep(0.2)
            latest = await self.robot.properties["JointPositions"].read()
            if max(abs(a - b) for a, b in zip(latest, state["JointPositions"], strict=True)) > math.radians(0.05):
                msg = "The robot moved during teaching; record again after it stops."
                raise PanelError(msg)
            record = {
                "id": str(uuid.uuid4()),
                "name": name.strip(),
                "joints": latest,
                "tool_pose": state["ToolPose"],
                "simulation": self.simulated,
                "created_at": now(),
            }
            await asyncio.to_thread(
                write_record, self.data / "positions" / f"{record['id']}.json", record, exclusive=True
            )
            self.positions[record["id"]] = record
            self.cache_time = 0
            return record

    async def preview(self, payload: dict) -> dict:
        """Compile a concrete sequence from live state without executing it."""
        if self.busy():
            msg = "A run is already active."
            raise PanelError(msg)
        speed = payload.get("speed", 10)
        if (
            isinstance(speed, bool)
            or not isinstance(speed, (int, float))
            or not math.isfinite(speed)
            or not 1 <= speed <= 40
        ):
            msg = "Choose a command speed from 1 through 40 degrees per second."
            raise PanelError(msg)
        state = await self.ready()
        steps, target = compile_steps(
            payload.get("rows"),
            state["JointPositions"],
            {k: p for k, p in self.positions.items() if p.get("simulation") is self.simulated},
        )
        if not steps:
            msg = "The robot is already at this target."
            raise PanelError(msg)
        poses = [[math.degrees(v) for v in state["JointPositions"]], *[s["target_degrees"] for s in steps]]
        ranges = [{"min": min(q[i] for q in poses), "max": max(q[i] for q in poses)} for i in range(6)]
        fraction = state["SpeedSliderFraction"]
        travel = sum(abs(s["delta"]) for s in steps)
        record = {
            "ranges_degrees": ranges,
            "travel_seconds_minimum": travel / (speed * fraction) if fraction > 0 else None,
            "id": str(uuid.uuid4()),
            "created_at": now(),
            "expires": time.monotonic() + 300,
            "start": state["JointPositions"],
            "target_degrees": target,
            "steps": steps,
            "speed": speed,
            "simulation": self.simulated,
            "rows": payload["rows"],
        }
        self.previews = {k: p for k, p in self.previews.items() if p["expires"] > time.monotonic()}
        self.previews[record["id"]] = record
        return record

    async def start(self, preview_id: str, run_id: str) -> dict:
        """Reserve one explicit run; duplicate IDs return the original run without sending more commands."""
        identifier(run_id)
        if len(run_id) > 55:
            msg = "Run IDs must be at most 55 characters to leave room for move identifiers."
            raise PanelError(msg)
        async with self.start_lock:
            if run_id in self.jobs:
                return json.loads(json.dumps(self.jobs[run_id]))
            if self.busy() or any(j["status"] == "interrupted" for j in self.jobs.values()):
                msg = "A run is active or an interrupted run needs inspection."
                raise PanelError(msg)
            plan = self.previews.get(preview_id)
            if plan is None or plan["expires"] < time.monotonic():
                msg = "Preview expired; create a fresh preview."
                raise PanelError(msg)
            state = await self.ready()
            if not state["MotionControlsEnabled"]:
                msg = "Motion controls are disabled in the connector configuration."
                raise PanelError(msg)
            if max(abs(a - b) for a, b in zip(state["JointPositions"], plan["start"], strict=True)) > math.radians(0.1):
                msg = "The robot moved since preview; create a fresh preview."
                raise PanelError(msg)
            job = {
                "id": run_id,
                "preview_id": preview_id,
                "status": "queued",
                "created_at": now(),
                "total": len(plan["steps"]),
                "completed": 0,
                "steps": [],
                "speed": plan["speed"],
                "simulation": self.simulated,
                "current": None,
            }
            time.time() + 20
            await asyncio.to_thread(write_record, self.data / "runs" / f"{run_id}.json", job, exclusive=True)
            self.jobs[run_id] = job
            self.previews.pop(preview_id)
            self.cancelled.clear()
            self.task = asyncio.create_task(self._run(plan, job))
            self.cache_time = 0
            return json.loads(json.dumps(job))

    async def _run(self, plan: dict, job: dict) -> None:
        expected = plan["start"]
        try:
            job["status"] = "running"
            for index, step in enumerate(plan["steps"]):
                if self.cancelled.is_set():
                    job["status"] = "cancelled"
                    break
                actual = await self.robot.properties["JointPositions"].read()
                if max(abs(a - b) for a, b in zip(actual, expected, strict=True)) > math.radians(0.1):
                    msg = "Pose changed between moves; remaining steps were not sent."
                    raise PanelError(msg)
                dispatch_expires = time.time() + 20
                job["current"] = {"joint": step["joint"], "delta": step["delta"], "phrase": step["phrase"]}
                await asyncio.to_thread(write_record, self.data / "runs" / f"{job['id']}.json", job)
                if self.cancelled.is_set():
                    job["status"] = "cancelled"
                    break
                result = await self.controller.commands["MoveJointAtSpeed"].execute(
                    {
                        "Joint": step["joint"],
                        "DeltaDegrees": step["delta"],
                        "SpeedDegreesPerSecond": plan["speed"],
                        "RequestId": f"{job['id']}-{index:03d}",
                        "ExpectedSimulationMode": self.simulated,
                        "ExpiresAtUnixSeconds": dispatch_expires,
                    }
                )
                receipt = json.loads(result["Receipt"])
                required = "simulated" if self.simulated else "verified"
                if receipt["status"] != required:
                    msg = "Completion was not verified; remaining moves were not sent."
                    raise PanelError(msg)
                expected = receipt["after_radians"] if self.simulated else receipt["after"]["positions"]
                job["completed"] += 1
                job["steps"].append(
                    {
                        "joint": step["joint"],
                        "delta": step["delta"],
                        "request_id": f"{job['id']}-{index:03d}",
                        "status": receipt["status"],
                    }
                )
            else:
                job["status"] = "completed"
        except Exception as error:  # noqa: BLE001 -- record SDK-defined errors and never retry physical commands.
            job.update(status="attention_required", error=str(error))
        finally:
            job.update(current=None, finished_at=now())
            self.cache_time = 0
            with contextlib.suppress(OSError):
                await asyncio.to_thread(write_record, self.data / "runs" / f"{job['id']}.json", job)

    async def check_motion(self) -> dict:
        """Run explicit read-only SiLA preflight checks."""
        result = await self.controller.commands["MotionPreflight"].execute({})
        self.motion_check = json.loads(result["Report"])
        self.cache_time = 0
        return self.motion_check

    async def preview_continuous(self, payload: dict) -> dict:
        """Prepare a SiLA-owned whole-body dance or explicit reset without execution."""
        async with self.start_lock:
            if self.busy() or self.closing:
                msg = "Wait for the current operation to finish."
                raise PanelError(msg)
            speed, duration = payload.get("speed", 20), payload.get("duration", 60)
            if not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (speed, duration)
            ):
                msg = "Choose finite speed and duration values."
                raise PanelError(msg)
            if payload.get("reset"):
                if self.reset_checkpoint is None:
                    msg = "Preview a dance first to capture its reference pose."
                    raise PanelError(msg)
                command = "PrepareReset"
                args = {"CheckpointId": self.reset_checkpoint}
            elif payload.get("low_speed"):
                command = "PrepareLowSpeedDance"
                args = {}
            else:
                command = "PrepareContinuousDance"
                args = {}
            args.update(Duration=duration, ExpectedSimulationMode=self.simulated)
            if command != "PrepareLowSpeedDance":
                args["Speed"] = speed
            result = await self.controller.commands[command].execute(args)
            self.resume_context = None
            self.continuous_plan = json.loads(result["Plan"])
            self.reset_checkpoint = self.continuous_plan["checkpoint_id"]
            self.cache_time = 0
            return self.continuous_plan

    async def preview_action_chain(self, payload: dict) -> dict:
        """Prepare an arbitrary waypoint chain exclusively through the public SiLA command."""
        async with self.start_lock:
            if self.busy() or self.closing:
                msg = "Wait for the current operation to finish."
                raise PanelError(msg)
            timing = payload.get("timing_mode", "manual")
            if timing not in {"manual", "automatic"}:
                msg = "Choose manual or automatic timing."
                raise PanelError(msg)
            command = "PrepareActionChainAtSpeed" if timing == "automatic" else "PrepareActionChain"
            result = await self.controller.commands[command].execute(
                {
                    "WaypointsJson": json.dumps(payload.get("waypoints"), allow_nan=False),
                    "Speed": payload.get("speed", 10),
                    "ExpectedSimulationMode": self.simulated,
                }
            )
            self.continuous_plan = json.loads(result["Plan"])
            self.resume_context = None
            parent_id = payload.get("resume_job_id")
            if parent_id:
                parent = self.jobs.get(identifier(parent_id))
                if parent is None or parent.get("status") != "paused":
                    msg = "Only a verified paused run can supply a remaining-action plan."
                    raise PanelError(msg)
                offset = parent.get("action_offset", 0) + parent["progress"]["completed_actions"]
                self.resume_context = {
                    "parent_id": parent_id,
                    "action_offset": offset,
                    "prefix": parent["authored_waypoints"][:offset],
                    "original_remainder": parent["authored_waypoints"][offset:],
                }
            self.cache_time = 0
            return self.continuous_plan

    async def suggest_action_chain_timing(self, payload: dict) -> dict:
        """Read kinematic timing recommendations from SiLA without editing or storing an executable plan."""
        async with self.start_lock:
            if self.busy() or self.closing:
                msg = "Wait for the current operation to finish."
                raise PanelError(msg)
            result = await self.controller.commands["SuggestActionChainTiming"].execute(
                {
                    "WaypointsJson": json.dumps(payload.get("waypoints"), allow_nan=False),
                    "Speed": payload.get("speed", 10),
                    "ExpectedSimulationMode": self.simulated,
                }
            )
            return json.loads(result["Report"])

    async def start_continuous(self, payload: dict) -> dict:
        """Reserve one run and execute the whole prepared trajectory once through SiLA."""
        run_id = identifier(payload.get("run_id"))
        async with self.start_lock:
            if run_id in self.jobs:
                return dict(self.jobs[run_id])
            if self.busy() or self.closing or any(j["status"] == "interrupted" for j in self.jobs.values()):
                msg = "Another run is active or interrupted; inspect it first."
                raise PanelError(msg)
            paused = [j for j in self.jobs.values() if j["status"] == "paused"]
            if paused and (
                not self.resume_context or self.resume_context["parent_id"] not in {j["id"] for j in paused}
            ):
                msg = "Continue the paused remainder or Stop all before starting another chain."
                raise PanelError(msg)
            plan = self.continuous_plan
            if plan is None or payload.get("plan_id") != plan["id"]:
                msg = "Create a fresh continuous preview first."
                raise PanelError(msg)
            check = await self.check_motion()
            enabled = (
                check.get("action_chains_enabled", False)
                if plan["kind"] == "action_chain"
                else check["continuous_enabled"]
            )
            if plan.get("playback_controls") and not check.get("feedback_motion_enabled", False):
                msg = "Real feedback playback has not been qualified; enable_feedback_motion remains off."
                raise PanelError(msg)
            if not check["ready"] or not enabled:
                msg = "Preflight failed or real continuous motion is disabled pending hardware qualification."
                raise PanelError(msg)
            job = {
                "id": run_id,
                "status": "queued",
                "created_at": now(),
                "total": 1,
                "completed": 0,
                "steps": [],
                "speed": plan["speed"],
                "simulation": self.simulated,
                "current": None,
                "task_context": payload.get("task_context", {}),
                "kind": plan["kind"],
                "plan_supports_pause": plan.get("playback_controls", False),
                "action_count": len(plan.get("waypoints", []))
                + (self.resume_context["action_offset"] if self.resume_context else 0),
                "plan": plan,
                "authored_waypoints": (self.resume_context["prefix"] if self.resume_context else [])
                + plan.get("waypoints", []),
                "action_offset": self.resume_context["action_offset"] if self.resume_context else 0,
                "parent_id": self.resume_context["parent_id"] if self.resume_context else None,
                "continuation_kind": (
                    "edited_remainder"
                    if self.resume_context
                    and [{k: v for k, v in row.items() if k != "duration"} for row in plan.get("waypoints", [])]
                    != [
                        {k: v for k, v in row.items() if k != "duration"}
                        for row in self.resume_context["original_remainder"]
                    ]
                    else "unchanged_remainder"
                    if self.resume_context
                    else "new_task"
                ),
                "original_remainder": self.resume_context["original_remainder"] if self.resume_context else None,
            }
            if (
                not isinstance(job["task_context"], dict)
                or len(json.dumps(job["task_context"], allow_nan=False)) > 32768
            ):
                msg = "Task context must be a JSON object of at most 32 KiB."
                raise PanelError(msg)
            dispatch_expires = time.time() + 20
            await asyncio.to_thread(write_record, self.data / "runs" / f"{run_id}.json", job, exclusive=True)
            self.jobs[run_id] = job
            if self.resume_context:
                parent = self.jobs[self.resume_context["parent_id"]]
                parent["status"] = "continued"
                parent["continued_by"] = run_id
                await asyncio.to_thread(write_record, self.data / "runs" / f"{parent['id']}.json", parent)
            self.task = asyncio.create_task(
                self._run_continuous(plan, job, payload.get("remote_confirmed") is True, dispatch_expires)
            )
            self.cache_time = 0
            return dict(job)

    async def _run_continuous(self, plan: dict, job: dict, remote_confirmed: bool, dispatch_expires: float) -> None:
        try:
            job["status"] = "running"
            await asyncio.to_thread(write_record, self.data / "runs" / f"{job['id']}.json", job)
            if job.get("stop_requested"):
                job.update(
                    status="stopped",
                    receipt={"status": "not_sent", "completion": "stopped"},
                    progress={"completed_actions": 0, "current_action": None, "controller_state": "stopped"},
                )
                return
            operation = asyncio.create_task(
                self.controller.commands["RunContinuousMotion"].execute(
                    {
                        "PlanId": plan["id"],
                        "RequestId": job["id"],
                        "ExpectedSimulationMode": self.simulated,
                        "ExpiresAtUnixSeconds": dispatch_expires,
                        "RemoteControlConfirmed": remote_confirmed,
                    },
                    timeout=((plan["duration"] + 15) / 0.05 + 40) * 1000,
                )
            )
            while not operation.done():
                action = (
                    "stop"
                    if job.get("stop_requested")
                    else "pause_after_action"
                    if job.get("pause_requested")
                    else None
                )
                if action and job.get("control_delivered") != action:
                    try:
                        await self.controller.commands["ControlActiveMotion"].execute(
                            {"RequestId": job["id"], "Action": action, "ExpectedSimulationMode": self.simulated}
                        )
                        job["control_delivered"] = action
                    except Exception as error:  # noqa: BLE001 -- retain intent until connector ownership exists.
                        job["control_error"] = str(error)
                await asyncio.sleep(0.05)
            result = await operation
            receipt = json.loads(result["Receipt"])
            if receipt["status"] != ("simulated" if self.simulated else "verified") and not (
                receipt["status"] == "not_sent" and receipt.get("completion") == "stopped"
            ):
                msg = "Continuous completion was not verified. Do not retry."
                raise PanelError(msg)
            completion = receipt.get("completion", "completed")
            if completion not in {"completed", "paused", "stopped"}:
                msg = "Unknown completion state; do not replay."
                raise PanelError(msg)
            job.update(
                status=completion,
                completed=int(completion == "completed"),
                receipt=receipt,
                progress=receipt.get(
                    "progress",
                    {"completed_actions": len(plan.get("waypoints", [])) if completion == "completed" else 0},
                ),
            )
            if "ReadMotionTrace" in self.controller.commands:
                trace_result = await self.controller.commands["ReadMotionTrace"].execute({"RequestId": job["id"]})
                trace = json.loads(trace_result["Trace"])
                await asyncio.to_thread(write_record, self.data / "trajectories" / f"{job['id']}.json", trace)
                job["trajectory_saved"] = True
        except Exception as error:  # noqa: BLE001 -- SDK errors are dynamic; never replay.
            if job.get("receipt", {}).get("status") in {"verified", "simulated"}:
                job["recording_error"] = str(error)
            else:
                job.update(status="attention_required", error=str(error))
        finally:
            job["finished_at"] = now()
            self.cache_time = 0
            with contextlib.suppress(OSError):
                await asyncio.to_thread(write_record, self.data / "runs" / f"{job['id']}.json", job)

    async def control_motion(self, payload: dict) -> dict:
        """Request optional playback control without waiting behind a running operation."""
        run_id = identifier(payload.get("run_id"))
        action = payload.get("action")
        job = self.jobs.get(run_id)
        if job is None or action not in {"stop", "pause_after_action"}:
            msg = "Choose this panel's active run and a supported control."
            raise PanelError(msg)
        if job["status"] == "paused" and action == "stop":
            job["status"] = "stopped"
            await asyncio.to_thread(write_record, self.data / "runs" / f"{run_id}.json", job)
            self.cache_time = 0
            return {"status": "stopped", "message": "Remaining actions cancelled; no new motion sent."}
        if job["status"] not in {"queued", "running", "pausing", "stopping"}:
            msg = "Run is not active."
            raise PanelError(msg)
        if job["status"] == "stopping":
            return {"status": "stopping"}
        # Intent is set before disk or network awaits, so startup cannot lose a Stop click.
        job["stop_requested" if action == "stop" else "pause_requested"] = True
        job["status"] = "stopping" if action == "stop" else "pausing"
        self.cache_time = 0
        await asyncio.to_thread(write_record, self.data / "runs" / f"{run_id}.json", job)
        return {
            "run_id": run_id,
            "requested": action,
            "confirmed_stopped": False,
            "message": "Request retained. Awaiting run-owned controller delivery and measured verification.",
        }

    async def kinematics(self, payload: dict) -> dict:
        """Compute a read-only kinematics report through SiLA."""
        result = await self.controller.commands["SolveTcp"].execute(
            {"PayloadJson": json.dumps(payload, allow_nan=False)}
        )
        return json.loads(result["Report"])

    async def save_task(self, payload: dict) -> dict:
        """Save a portable authored task and explicit teaching provenance, never an execution authorization."""
        document = payload.get("document")
        name = payload.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or not isinstance(document, dict):
            msg = "Name and task document are required."
            raise PanelError(msg)
        if document.get("schema") != "ur5e.action-chain/2" or document.get("timing_mode") != "automatic":
            msg = "Save an automatic action-chain v2 document."
            raise PanelError(msg)
        from ..io.action_chain import build_at_speed
        from ..io.kinematics import vector

        reference = vector(document.get("reference"), 6)
        await asyncio.to_thread(
            build_at_speed, [math.radians(v) for v in reference], document.get("waypoints"), document.get("speed")
        )
        record = {
            "schema": "ur5e.task-record/1",
            "id": str(uuid.uuid4()),
            "name": name.strip(),
            "created_at": now(),
            "simulation": self.simulated,
            "document": document,
            "teaching": payload.get("teaching"),
            "description": str(payload.get("description", ""))[:2000],
        }
        await asyncio.to_thread(write_record, self.data / "tasks" / f"{record['id']}.json", record, exclusive=True)
        self.task_library[record["id"]] = record
        self.cache_time = 0
        return record

    async def dataset(self, run_id: str) -> dict:
        """Export one authored run, measured or simulated trace and interruption provenance."""
        job = self.jobs.get(identifier(run_id))
        if job is None:
            msg = "Run not found."
            raise PanelError(msg)
        path = self.data / "trajectories" / f"{run_id}.json"
        trace = await asyncio.to_thread(
            lambda: json.loads(path.read_text()) if path.exists() else {"samples": [], "missing": True}
        )
        return {
            "schema": "ur5e.episode/1",
            "exported_at": now(),
            "simulation": job["simulation"],
            "run": job,
            "trajectory": trace,
            "evidence": "Kinematic simulation"
            if job["simulation"]
            else "Measured robot telemetry; not task success or collision validation",
        }

    async def inspect_failed_run(self) -> dict:
        """Compare the latest panel failure with its SiLA receipt without replay or fault clearing."""
        jobs = sorted(self.jobs.values(), key=lambda j: j["created_at"], reverse=True)
        job = next((j for j in jobs if j["status"] in {"attention_required", "interrupted"}), None)
        if job is None:
            return {"status": "no_failed_run", "message": "No failed or interrupted panel run."}
        request_id = job["id"] if job.get("kind") else f"{job['id']}-{job['completed']:03d}"
        result = await self.controller.commands["InspectMotionRequest"].execute({"RequestId": request_id})
        receipt = json.loads(result["Receipt"])
        return {
            "run_id": job["id"],
            "request_id": request_id,
            "status": receipt["status"],
            "message": (
                "Controller receipt says verified; the panel response was lost. Do not repeat this step."
                if receipt["status"] == "verified"
                else "Inspect this unresolved result before any further action."
            ),
            "receipt_created_at": receipt.get("created_at"),
            "panel_failed_at": job.get("finished_at"),
            "receipt": receipt,
        }

    async def cancel(self) -> dict:
        """Cancel remaining steps; the currently executing bounded move finishes first."""
        self.cancelled.set()
        return {"message": "No further steps will be sent after the current move finishes."}
