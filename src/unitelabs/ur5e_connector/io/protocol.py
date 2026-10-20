import asyncio
import json
import pathlib
import re
import time
import math
import uuid

from . import choreography, motion_program
from .joint_step import require_stopped

from .joint_step import (
    build_base_step,
    build_elbow_step,
    build_joint_test,
    execute_base_step,
    execute_elbow_step,
    execute_joint_test,
    validate_joint_test,
    validate_speed,
)
from .dashboard import DashboardConnection
from .errors import CommandRejected, DeviceUnavailable, OutcomeUnknown
from .rtde import JointMonitor, ToolMonitor
from .simulation import SimulatedDashboard


class Ur5eProtocol:
    """Manage a real or simulated Dashboard through the same serialized operations."""

    def __init__(
        self,
        *,
        simulation_mode: bool = True,
        host: str = "",
        port: int = 29999,
        enable_program_control: bool = False,
        approved_program: str = "",
        timeout: float = 3.0,
        enable_base_step: bool = False,
        base_step_receipt: str = "",
        base_step_reference: str = "",
        enable_elbow_step: bool = False,
        elbow_step_receipt: str = "",
        enable_joint_testing: bool = False,
        enable_continuous_motion: bool = False,
        joint_test_receipt_directory: str = "",
    ) -> None:
        self.device = SimulatedDashboard()
        self.enable_continuous_motion = enable_continuous_motion
        self.motion_plans = {}
        self.motion_requests = {}
        self.motion_checkpoints = {}
        self.enable_base_step = enable_base_step
        self.base_step_receipt = base_step_receipt
        self.base_step_reference = base_step_reference
        self.enable_elbow_step = enable_elbow_step
        self.elbow_step_receipt = elbow_step_receipt
        self.enable_joint_testing = enable_joint_testing
        self.joint_test_receipt_directory = joint_test_receipt_directory
        self.simulation_mode = simulation_mode
        self.host, self.port, self.timeout = host, port, timeout
        self.enable_program_control = enable_program_control
        self.approved_program = approved_program
        self.connected = False
        self.control_outcome_unknown = False
        self._transport: DashboardConnection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """Open the configured backend without triggering physical actions."""
        async with self._lock:
            await self._open()

    async def close(self) -> None:
        """Close the backend without stopping or starting any robot program."""
        async with self._lock:
            await self._close()

    async def set_simulation_mode(self, simulation_mode: bool) -> None:
        """Switch backends only while stopped, preserving the old mode on failure."""
        async with self._lock:
            if simulation_mode == self.simulation_mode:
                if not self.connected:
                    await self._open()
                return
            state = await self._query("programState")
            if state.split(" ", 1)[0] != "STOPPED":
                msg = "Stop the current program before switching backends."
                raise CommandRejected(msg)
            candidate = None
            if not simulation_mode:
                candidate = await self._connect_real()
            await self._close()
            self._transport = candidate
            self.simulation_mode = simulation_mode
            self.connected = True

    async def send_command(self, command: str) -> str:
        """Execute a supported command with the same restrictions as typed operations."""
        self._validate_command(command)
        async with self._lock:
            if not self.connected:
                msg = "The device connection is closed; explicitly reconnect before another request."
                raise DeviceUnavailable(msg)
            query = command in {
                "PolyscopeVersion",
                "robotmode",
                "programState",
                "get loaded program",
                "running",
                "safetystatus",
            }
            if not query:
                await self._check_control(command)
            try:
                response = await self._query(command)
            except (DeviceUnavailable, asyncio.CancelledError) as error:
                if not query and not self.simulation_mode:
                    self.control_outcome_unknown = True
                    if isinstance(error, DeviceUnavailable):
                        msg = f"Response lost for {command!r}; outcome unknown. Do not repeat the request."
                        raise OutcomeUnknown(msg) from error
                raise
            if not query:
                expected = {"play": "Starting program", "pause": "Pausing program", "stop": "Stopped"}
                wanted = f"Loading program: {command[5:]}" if command.startswith("load ") else expected[command]
                if response != wanted:
                    # The controller explicitly rejected the command; do not report success.
                    if response.startswith(
                        ("Failed", "File not found", "Error", "Command is not allowed", "Could not")
                    ):
                        raise CommandRejected(response)
                    self.control_outcome_unknown = True
                    msg = f"Unexpected control response: {response}. Inspect robot state before continuing."
                    raise OutcomeUnknown(msg)
                if command == "stop" and not self.simulation_mode:
                    try:
                        stopped = (await self._query("programState")).split(" ", 1)[0] == "STOPPED"
                    except (DeviceUnavailable, asyncio.CancelledError) as error:
                        self.control_outcome_unknown = True
                        if isinstance(error, DeviceUnavailable):
                            msg = "Stop was acknowledged but the resulting state could not be confirmed."
                            raise OutcomeUnknown(msg) from error
                        raise
                    self.control_outcome_unknown = not stopped
                    if not stopped:
                        msg = "Stop was acknowledged but the robot did not report STOPPED."
                        raise OutcomeUnknown(msg)
            return response

    async def get_joint_positions(self) -> list[float]:
        """Read six actual joint angles in radians from an output-only RTDE connection."""
        async with self._lock:
            if self.simulation_mode:
                return list(self.device.joint_positions)
            monitor = JointMonitor(self.host)
            try:
                await monitor.open()
                return list((await monitor.sample()).positions)
            except (OSError, TimeoutError, asyncio.IncompleteReadError) as error:
                raise DeviceUnavailable(str(error)) from error
            finally:
                await monitor.close()

    async def move_base_positive_five_degrees(self) -> str:
        """Execute the separately enabled one-shot Base +5 degree operation."""
        return await self._move_positive_five_degrees(elbow=False)

    async def move_elbow_positive_five_degrees(self) -> str:
        """Execute the separately enabled one-shot Elbow +5 degree operation."""
        return await self._move_positive_five_degrees(elbow=True)

    async def _move_positive_five_degrees(self, *, elbow: bool) -> str:
        async with self._lock:
            builder = build_elbow_step if elbow else build_base_step
            if self.simulation_mode:
                before = self.device.joint_positions
                target, _ = builder(before)
                self.device.joint_positions = target
                return json.dumps(
                    {"status": "simulated", "simulation_mode": True, "before_radians": before, "after_radians": target}
                )
            enabled = self.enable_elbow_step if elbow else self.enable_base_step
            receipt = self.elbow_step_receipt if elbow else self.base_step_receipt
            if not enabled or not receipt:
                msg = "The requested one-shot joint +5 degree operation is not enabled."
                raise CommandRejected(msg)
            if self.control_outcome_unknown:
                msg = "Reconcile the previous unknown control outcome before any motion."
                raise CommandRejected(msg)
            try:
                if elbow:
                    return await execute_elbow_step(self.host, pathlib.Path(receipt))
                return await execute_base_step(
                    self.host,
                    pathlib.Path(self.base_step_receipt),
                    pathlib.Path(self.base_step_reference) if self.base_step_reference else None,
                )
            except (OutcomeUnknown, asyncio.CancelledError):
                self.control_outcome_unknown = True
                raise

    async def move_joint(self, joint: str, delta_degrees: float, request_id: str) -> str:
        """Run one bounded relative joint test, rejecting duplicate or uncertain requests."""
        return await self.move_joint_at_speed(joint, delta_degrees, 10.0, request_id)

    async def move_joint_at_speed(
        self,
        joint: str,
        delta_degrees: float,
        speed_degrees_per_second: float,
        request_id: str,
        expected_simulation_mode: bool | None = None,
        expires_at_unix_seconds: float | None = None,
    ) -> str:
        """Run one bounded relative joint test at an explicitly requested speed."""
        index = validate_joint_test(joint, delta_degrees, request_id)
        validate_speed(speed_degrees_per_second)
        if expires_at_unix_seconds is not None and (
            isinstance(expires_at_unix_seconds, bool)
            or not isinstance(expires_at_unix_seconds, (int, float))
            or not math.isfinite(expires_at_unix_seconds)
        ):
            msg = "Dispatch expiry must be finite."
            raise CommandRejected(msg)
        async with self._lock:
            expires_at_unix_seconds = min(
                expires_at_unix_seconds if expires_at_unix_seconds is not None else time.time() + 20, time.time() + 20
            )
            if time.time() >= expires_at_unix_seconds:
                msg = "Request expired; no motion was sent."
                raise CommandRejected(msg)
            if expected_simulation_mode is not None and expected_simulation_mode != self.simulation_mode:
                msg = "The backend mode changed; no movement was sent. Reconnect and review the mode."
                raise CommandRejected(msg)
            if self.simulation_mode:
                before = self.device.joint_positions
                target, _ = build_joint_test(before, index, delta_degrees, speed_degrees=speed_degrees_per_second)
                self.device.joint_positions = target
                return json.dumps(
                    {"status": "simulated", "simulation_mode": True, "before_radians": before, "after_radians": target}
                )
            if not self.enable_joint_testing or not self.joint_test_receipt_directory:
                msg = "General joint testing is not enabled."
                raise CommandRejected(msg)
            if self.control_outcome_unknown:
                msg = "Reconcile the previous unknown control outcome before any motion."
                raise CommandRejected(msg)
            try:
                return await execute_joint_test(
                    self.host,
                    pathlib.Path(self.joint_test_receipt_directory),
                    request_id,
                    index,
                    delta_degrees,
                    speed_degrees=speed_degrees_per_second,
                    expires_at=expires_at_unix_seconds,
                )
            except (OutcomeUnknown, asyncio.CancelledError):
                self.control_outcome_unknown = True
                raise

    async def motion_preflight(self) -> str:
        """Read stationary telemetry and explain every gate without starting movement."""
        async with self._lock:
            checks = []
            if self.simulation_mode:
                positions = list(self.device.joint_positions)
                stopped = self.device.execute("programState").startswith("STOPPED")
                normal = self.device.execute("safetystatus").endswith("NORMAL")
                running = self.device.execute("robotmode").endswith("RUNNING")
                fraction = 1.0
            else:
                monitor = JointMonitor(self.host)
                try:
                    await monitor.open()
                    first = await monitor.sample()
                    require_stopped(first)
                    deadline = time.monotonic() + 4
                    last = first
                    while last.timestamp - first.timestamp < 0.5:
                        if time.monotonic() > deadline:
                            msg = "Stationary telemetry did not advance."
                            raise DeviceUnavailable(msg)
                        last = await monitor.sample()
                        require_stopped(last)
                        if max(abs(a - b) for a, b in zip(first.positions, last.positions, strict=True)) > math.radians(
                            0.1
                        ):
                            msg = "Pose moved during preflight."
                            raise CommandRejected(msg)
                    positions = list(last.positions)
                finally:
                    await monitor.close()
                stopped = (await self._query("programState")).startswith("STOPPED")
                normal = (await self._query("safetystatus")).endswith("NORMAL")
                running = (await self._query("robotmode")).endswith("RUNNING")
                _, fraction = await self.read_tool_state()
            checks.extend(
                [
                    {"name": "Program stopped", "passed": stopped},
                    {"name": "Safety NORMAL", "passed": normal},
                    {"name": "Robot RUNNING", "passed": running},
                    {"name": "No unknown control outcome", "passed": not self.control_outcome_unknown},
                    {"name": "Speed slider above zero", "passed": fraction > 0},
                ]
            )
            if not self.simulation_mode and self.joint_test_receipt_directory:
                directory = pathlib.Path(self.joint_test_receipt_directory)
                try:
                    if (directory / ".active").exists():
                        msg_0 = "A motion reservation is active or interrupted."
                        raise CommandRejected(msg_0)
                    receipt_scan = await asyncio.wait_for(asyncio.to_thread(motion_program.check_records, directory), 5)
                    checks.append({"name": "No unfinished motion receipts", "passed": True, "scan": receipt_scan})
                except (OSError, ValueError, TimeoutError, CommandRejected) as error:
                    detail = str(error) or "historical receipt scan timed out after 5 seconds; no movement permitted"
                    checks.append({"name": f"Motion receipt check: {detail}", "passed": False})
            try:
                choreography.pose(positions)
                checks.append({"name": "Joint limits with 5 degree margin; no angle wrapping", "passed": True})
            except CommandRejected as error:
                checks.append({"name": str(error), "passed": False})
            return json.dumps(
                {
                    "ready": all(c["passed"] for c in checks),
                    "checks": checks,
                    "positions": positions,
                    "simulation": self.simulation_mode,
                    "slider_fraction": fraction,
                    "continuous_enabled": self.simulation_mode or self.enable_continuous_motion,
                    "remote_control": "simulated"
                    if self.simulation_mode
                    else "manual confirmation required on PolyScope 5.4",
                    "collision_validation": False,
                }
            )

    async def prepare_motion(
        self, speed: float, duration: float, expected_mode: bool, checkpoint_id: str = "", low_speed_test: bool = False
    ) -> str:
        """Bind a continuous dance or explicit reset preview to measured state and mode."""
        state = json.loads(await self.motion_preflight())
        async with self._lock:
            if (
                expected_mode != self.simulation_mode
                or state["simulation"] != self.simulation_mode
                or not state["ready"]
            ):
                msg = "Preflight failed or backend changed; no motion prepared."
                raise CommandRejected(msg)
            target = None
            if checkpoint_id:
                checkpoint = self.motion_checkpoints.get(checkpoint_id)
                if checkpoint is None or checkpoint["simulation"] != self.simulation_mode:
                    msg = "Checkpoint unavailable or belongs to a different mode. Capture a new reference."
                    raise CommandRejected(msg)
                target = checkpoint["positions"]
            plan = choreography.build(
                state["positions"],
                3.0 if low_speed_test else speed,
                duration,
                target,
                amplitude_scale=0.25 if low_speed_test else 1.0,
            )
            plan.update(
                id=str(uuid.uuid4()),
                simulation=self.simulation_mode,
                expires=time.time() + 180,
                slider_fraction=state["slider_fraction"],
                checkpoint_id=checkpoint_id,
            )
            if not checkpoint_id:
                plan["checkpoint_id"] = plan["id"]
                self.motion_checkpoints[plan["id"]] = {"positions": plan["start"], "simulation": self.simulation_mode}
            self.motion_plans = {k: v for k, v in self.motion_plans.items() if v["expires"] > time.time()}
            self.motion_plans[plan["id"]] = plan
            return json.dumps(choreography.public_plan(plan))

    async def execute_motion(
        self, plan_id: str, request_id: str, expected_mode: bool, expires_at: float, remote_confirmed: bool
    ) -> str:
        """Execute one reviewed whole-body trajectory; simulation and hardware are explicitly separated."""
        validate_joint_test("Base", 1, request_id)
        if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)) or not math.isfinite(expires_at):
            msg = "Dispatch expiry must be finite."
            raise CommandRejected(msg)
        async with self._lock:
            if request_id in self.motion_requests:
                msg = "Request already reserved; inspect its receipt instead of replaying."
                raise CommandRejected(msg)
            plan = self.motion_plans.get(plan_id)
            if plan is None or plan["expires"] < time.time() or expires_at <= time.time():
                msg = "Motion preview or dispatch expired. Prepare again."
                raise CommandRejected(msg)
            if expected_mode != self.simulation_mode or plan["simulation"] != self.simulation_mode:
                msg = "Backend mode changed; no movement sent."
                raise CommandRejected(msg)
            if self.control_outcome_unknown:
                msg = "Reconcile previous unknown outcome first."
                raise CommandRejected(msg)
            if not self.simulation_mode and (
                not self.enable_continuous_motion or not remote_confirmed or not self.joint_test_receipt_directory
            ):
                msg = (
                    "Real continuous motion is disabled pending separate hardware qualification "
                    "and Remote Control confirmation."
                )
                raise CommandRejected(msg)
            self.motion_requests[request_id] = {"status": "reserved"}
            self.motion_plans.pop(plan_id)
            plan["dispatch_expires_at"] = min(expires_at, time.time() + 20)
            if self.simulation_mode:
                if not self.device.execute("programState").startswith("STOPPED") or not self.device.execute(
                    "safetystatus"
                ).endswith("NORMAL"):
                    msg = "Simulator no longer stopped and normal."
                    raise CommandRejected(msg)
                if max(
                    abs(a - b) for a, b in zip(self.device.joint_positions, plan["start"], strict=True)
                ) > math.radians(0.1):
                    msg = "Simulator pose changed after preview."
                    raise CommandRejected(msg)
                # Run every 125 Hz setpoint through the same pose validator; time is accelerated.
                for q in plan["samples"]:
                    self.device.joint_positions = tuple(choreography.pose(q))
                result = {
                    "status": "simulated",
                    "simulation_mode": True,
                    "plan_id": plan_id,
                    "request_id": request_id,
                    "after_radians": list(self.device.joint_positions),
                    "sample_count": len(plan["samples"]),
                    "duration": plan["duration"],
                    "peak_speed": plan["peak_speed"],
                    "peak_acceleration": plan["peak_acceleration"],
                    "controller_program": choreography.script(plan),
                    "validation": plan["validation"],
                    "wall_clock": "accelerated kinematic simulation",
                }
                self.motion_requests[request_id] = result
                return json.dumps(result)
            try:
                result = await motion_program.execute(
                    self.host, pathlib.Path(self.joint_test_receipt_directory), request_id, plan
                )
                self.motion_requests[request_id] = json.loads(result)
                return result
            except (OutcomeUnknown, asyncio.CancelledError):
                self.control_outcome_unknown = True
                self.motion_requests[request_id] = {"status": "outcome_unknown"}
                raise

    async def inspect_motion_request(self, request_id: str) -> str:
        """Read an existing receipt; never retry or clear uncertain control state."""
        validate_joint_test("Base", 1, request_id)
        if request_id in self.motion_requests:
            return json.dumps(self.motion_requests[request_id])
        if not self.joint_test_receipt_directory:
            msg = "No receipt is available for this request."
            raise CommandRejected(msg)
        path = pathlib.Path(self.joint_test_receipt_directory) / f"{request_id}.json"
        try:
            record = json.loads(await asyncio.wait_for(asyncio.to_thread(path.read_text), 5))
        except (OSError, ValueError, TimeoutError) as error:
            raise DeviceUnavailable(str(error)) from error
        return json.dumps(record)

    async def read_tool_state(self) -> tuple[list[float], float]:
        """Read actual TCP pose and pendant speed through an output-only connection."""
        if self.simulation_mode:
            return [0.0] * 6, 1.0
        monitor = ToolMonitor(self.host)
        try:
            await monitor.open()
            return await monitor.read_tool_state()
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as error:
            raise DeviceUnavailable(str(error)) from error
        finally:
            await monitor.close()

    async def get_software_version(self) -> str:
        """Read the controller software version without using post-5.4 commands."""
        response = await self.send_command("PolyscopeVersion")
        return response.removeprefix("URSoftware ")

    async def get_robot_mode(self) -> str:
        """Read the robot mode, distinct from program execution state."""
        return self._parse_prefix(await self.send_command("robotmode"), "Robotmode: ")

    async def get_safety_status(self) -> str:
        """Read safety status, including the Safetymode prefix used by firmware 5.4.2."""
        response = await self.send_command("safetystatus")
        # Observed on URSoftware 5.4.2.76197: safetystatus returns Safetymode: NORMAL.
        for prefix in ("Safetystatus: ", "Safetymode: "):
            if response.startswith(prefix):
                return self._parse_prefix(response, prefix)
        msg = f"Unexpected safety status response: {response}"
        raise DeviceUnavailable(msg)

    async def get_program_state(self) -> str:
        """Read STOPPED, PLAYING, or PAUSED from a fresh controller response."""
        response = (await self.send_command("programState")).split(" ", 1)[0]
        if response not in {"STOPPED", "PLAYING", "PAUSED"}:
            msg = f"Unexpected program state: {response}"
            raise DeviceUnavailable(msg)
        return response

    async def get_loaded_program(self) -> str:
        """Read the loaded program path, or an empty string."""
        response = await self.send_command("get loaded program")
        return "" if response == "No program loaded" else self._parse_prefix(response, "Loaded program: ")

    async def get_running(self) -> bool:
        """Read whether a program is running."""
        response = await self.send_command("running")
        if response not in {"Program running: true", "Program running: false"}:
            msg = f"Unexpected running response: {response}"
            raise DeviceUnavailable(msg)
        return response.endswith("true")

    async def load_program(self, program: str) -> None:
        """Load the approved program while stopped."""
        await self.send_command(f"load {program}")

    async def play(self) -> None:
        """Request start or resume; acceptance does not mean motion completed."""
        await self.send_command("play")

    async def pause(self) -> None:
        """Request program pause."""
        await self.send_command("pause")

    async def stop(self) -> None:
        """Request program stop; this is not an emergency stop."""
        await self.send_command("stop")

    async def _open(self) -> None:
        if self.connected:
            return
        if not self.simulation_mode:
            self._transport = await self._connect_real()
        self.connected = True

    async def _close(self) -> None:
        self.connected = False
        if self._transport is not None:
            await self._transport.close()
            self._transport = None

    async def _connect_real(self) -> DashboardConnection:
        if not self.host:
            msg = "Configure the controller hostname before entering real mode."
            raise DeviceUnavailable(msg)
        candidate = DashboardConnection(self.host, self.port, self.timeout)
        try:
            await candidate.open()
            version = await candidate.execute("PolyscopeVersion")
            if not re.match(r"URSoftware 5\.\d+\.\d+", version):
                msg = f"Expected a PolyScope 5 controller, received: {version}"
                raise DeviceUnavailable(msg)
        except (DeviceUnavailable, asyncio.CancelledError):
            await candidate.close()
            raise
        return candidate

    async def _query(self, command: str) -> str:
        if self.simulation_mode:
            return self.device.execute(command)
        if self._transport is None:
            msg = "No Dashboard connection."
            raise DeviceUnavailable(msg)
        try:
            return await self._transport.execute(command)
        except (DeviceUnavailable, asyncio.CancelledError):
            self.connected = False
            raise

    async def _check_control(self, command: str) -> None:
        if not self.simulation_mode:
            if not self.enable_program_control:
                msg = "Real program control is disabled in this configuration."
                raise CommandRejected(msg)
            if command == "play" or command.startswith("load "):
                approved = self.approved_program
                if not approved.startswith("/programs/") or "<" in approved or not approved.endswith(".urp"):
                    msg = "Configure the exact reviewed /programs/*.urp path before loading or playing."
                    raise CommandRejected(msg)
                self._validate_command(f"load {approved}")
                if self.control_outcome_unknown:
                    msg = "Previous control outcome is unknown. Reconcile with an acknowledged stop before starting."
                    raise CommandRejected(msg)
                if command.startswith("load ") and command[5:] != approved:
                    msg = "The requested program is not the configured approved program."
                    raise CommandRejected(msg)
                if command == "play":
                    response = await self._query("get loaded program")
                    loaded = "" if response == "No program loaded" else self._parse_prefix(response, "Loaded program: ")
                    if loaded != approved:
                        msg = "The loaded program does not match the configured approved program."
                        raise CommandRejected(msg)
                safety = await self._query("safetystatus")
                if safety not in {
                    "Safetystatus: NORMAL",
                    "Safetystatus: REDUCED",
                    "Safetymode: NORMAL",
                    "Safetymode: REDUCED",
                }:
                    msg = f"Robot safety state does not permit program start: {safety}"
                    raise CommandRejected(msg)
                if command == "play" and await self._query("robotmode") != "Robotmode: RUNNING":
                    msg = "Robot must be powered and ready before Play."
                    raise CommandRejected(msg)
        state = (await self._query("programState")).split(" ", 1)[0]
        if (
            (command.startswith("load ") and state != "STOPPED")
            or (command == "play" and state not in {"STOPPED", "PAUSED"})
            or (command == "pause" and state != "PLAYING")
        ):
            msg = f"Command {command!r} is not permitted while the program is {state}."
            raise CommandRejected(msg)

    @staticmethod
    def _validate_command(command: str) -> None:
        if (
            not command
            or len(command) > 1024
            or command != command.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in command)
        ):
            msg = "Send a single non-empty Dashboard command without control characters (maximum 1024 characters)."
            raise CommandRejected(msg)
        if command in {
            "PolyscopeVersion",
            "robotmode",
            "programState",
            "get loaded program",
            "running",
            "safetystatus",
            "play",
            "pause",
            "stop",
        }:
            return
        if command.startswith("load "):
            path = command[5:]
            if path == path.strip() and path.endswith(".urp") and ".." not in pathlib.PurePosixPath(path).parts:
                return
        msg = "Unsupported Dashboard command or program path."
        raise CommandRejected(msg)

    @staticmethod
    def _parse_prefix(response: str, prefix: str) -> str:
        if not response.startswith(prefix) or not response[len(prefix) :]:
            msg = f"Unexpected Dashboard response: {response}"
            raise DeviceUnavailable(msg)
        return response[len(prefix) :]
