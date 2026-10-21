"""Single-dispatch continuous motion with durable reservations and telemetry checks."""

import asyncio
import contextlib
import dataclasses
import datetime
import json
import math
import os
import pathlib
import time
import secrets
from collections.abc import Callable

from .choreography import script
from .dashboard import DashboardConnection
from .errors import CommandRejected, DeviceUnavailable, OutcomeUnknown
from .joint_step import require_stopped
from .rtde import JointMonitor, PlaybackMonitor
from . import playback
from .receipts import check_records


async def execute(
    host: str,
    directory: pathlib.Path,
    request_id: str,
    plan: dict,
    control: dict | None = None,
    observe: Callable[[dict], None] | None = None,
) -> str:
    """Send one locally paced program, then observe it; never retry a lost outcome."""
    active = directory / ".active"
    control = control if control is not None else {"stop": False, "pause": False}
    observe = observe if observe is not None else lambda _report: None
    controls = plan.get("playback_controls", False)
    plan = {**plan, "playback_token": secrets.randbelow(2**30) + 1}
    monitor = PlaybackMonitor(host, plan["playback_token"]) if controls else JointMonitor(host)
    dashboard = DashboardConnection(host)
    writer = stream = None
    reserved = sent = False
    trace = None
    last_index = -1
    stop_acknowledged = False
    pause_sent = False
    record = {
        "id": request_id,
        "plan_id": plan["id"],
        "kind": plan["kind"],
        "status": "preflight",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    def persist() -> None:
        stream.seek(0)
        json.dump(record, stream)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())

    try:
        await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        try:
            active.mkdir()
        except FileExistsError as error:
            msg = "Another motion is active or interrupted; inspect its receipt."
            raise CommandRejected(msg) from error
        reserved = True
        await asyncio.wait_for(asyncio.to_thread(check_records, directory), 10)
        try:
            stream = (directory / f"{request_id}.json").open("x+")
        except FileExistsError as error:
            msg = "This request already exists and cannot be replayed."
            raise CommandRejected(msg) from error
        persist()
        await monitor.open()
        await dashboard.open()
        first = await monitor.sample()
        require_stopped(first)
        latest = first
        preflight_deadline = time.monotonic() + 5
        while latest.timestamp - first.timestamp < 0.5:
            if time.monotonic() >= preflight_deadline:
                msg_0 = "Stationary telemetry did not advance in time."
                raise DeviceUnavailable(msg_0)
            latest = await monitor.sample()
            require_stopped(latest)
        if max(abs(a - b) for a, b in zip(latest.positions, plan["start"], strict=True)) > math.radians(0.1):
            msg = "Pose changed after preview. No motion was sent."
            raise CommandRejected(msg)
        if not (await dashboard.execute("programState")).startswith("STOPPED"):
            msg = "Controller is no longer stopped."
            raise CommandRejected(msg)
        if control["stop"]:
            record.update(status="not_sent", completion="stopped", after=dataclasses.asdict(latest))
            persist()
            observe({"controller_state": "stopped", "completed_actions": 0, "current_action": None})
            return json.dumps(record)
        program = script(plan)
        record.update(script=program, before=dataclasses.asdict(latest), status="prepared")
        persist()
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, 30002), 3)
        record["status"] = "dispatch_pending"
        persist()
        # Disk or queue delays cannot turn an expired preview into a late physical action.
        if time.time() >= plan["dispatch_expires_at"]:
            msg = "Dispatch authorization expired; no motion was sent."
            raise CommandRejected(msg)
        if control["stop"]:
            record.update(status="not_sent", completion="stopped", after=dataclasses.asdict(latest))
            persist()
            return json.dumps(record)
        trace_path = directory / f"{request_id}.trajectory.jsonl"
        trace = trace_path.open("x")
        record["trajectory_file"] = str(trace_path)
        sent = True
        writer.write(program.encode("ascii"))
        await asyncio.wait_for(writer.drain(), 3)
        record["status"] = "sent_monitoring"
        persist()
        deadline = time.monotonic() + (plan["duration"] + 15) / max(0.05, plan["slider_fraction"]) + 30
        progress_deadline = time.monotonic() + 5
        moving = False
        settled = None
        frames = []
        last_time = latest.timestamp
        while time.monotonic() < deadline:
            if control["stop"] and not stop_acknowledged:
                response = await dashboard.execute("stop")
                record["stop_response"] = response
                if response != "Stopped":
                    msg = "Stop was not acknowledged. Use the pendant and inspect the result."
                    raise OutcomeUnknown(msg)
                stop_acknowledged = True
                observe({"controller_state": "stopping", "sample": last_index})
            if controls and control["pause"] and not pause_sent and not stop_acknowledged:
                await monitor.command(True)
                pause_sent = True
            frame = await monitor.sample()
            if frame.timestamp <= last_time:
                continue
            last_time = frame.timestamp
            if frame.safety_mode != 1 or frame.robot_mode != 7:
                msg = "Controller left NORMAL/RUNNING during the program."
                raise OutcomeUnknown(msg)
            for i, bounds in enumerate(plan["ranges"]):
                angle = math.degrees(frame.positions[i])
                if not bounds["min"] - 1 <= angle <= bounds["max"] + 1:
                    msg = "Observed pose left the reviewed trajectory envelope."
                    raise OutcomeUnknown(msg)
            if max(abs(math.degrees(v)) for v in frame.velocities) > plan["speed"] + 2:
                msg = "Observed joint speed exceeded the reviewed cap."
                raise OutcomeUnknown(msg)
            progress_state = "running"
            if controls:
                index, code, token, pause_index = monitor.progress
                if token == plan["playback_token"]:
                    if index < last_index or index > len(plan["samples"]) - 1 or code not in {1, 2, 3, 4}:
                        msg = "Invalid or regressing run-bound controller progress."
                        raise OutcomeUnknown(msg)
                    last_index = index
                    progress_state = {1: "running", 2: "pausing", 3: "paused", 4: "completed"}[code]
                    report = playback.progress(
                        plan, index, "stopping" if stop_acknowledged else progress_state, pause_index
                    )
                    report.update(
                        positions=list(frame.positions),
                        velocities=list(frame.velocities),
                        runtime_state=frame.runtime_state,
                        robot_mode=frame.robot_mode,
                        safety_mode=frame.safety_mode,
                        timestamp=frame.timestamp,
                    )
                    observe(report)
                    record["progress"] = report
                else:
                    if last_index >= 0 or time.monotonic() > progress_deadline:
                        msg = "Controller progress token changed or did not bind to this run."
                        raise OutcomeUnknown(msg)
                    progress_state = "starting"
            observation = dataclasses.asdict(frame)
            observation.update(path_sample=last_index, controller_state=progress_state, simulation=False)
            if last_index >= 0:
                expected = plan["samples"][last_index]
                observation["target_radians"] = expected
                observation["max_tracking_error_degrees"] = max(
                    abs(math.degrees(a - b)) for a, b in zip(frame.positions, expected, strict=True)
                )
            trace.write(json.dumps(observation) + "\n")
            moving = moving or max(
                abs(a - b) for a, b in zip(frame.positions, plan["start"], strict=True)
            ) > math.radians(0.2)
            if len(frames) % 10 == 0:
                record["last_observation"] = dataclasses.asdict(frame)
            frames.append(frame.timestamp)
            at_target = max(abs(a - b) for a, b in zip(frame.positions, plan["target"], strict=True)) < math.radians(
                0.15
            )
            stopped = max(abs(v) for v in frame.velocities) < math.radians(0.1) and frame.runtime_state == 1
            elapsed = frame.timestamp - first.timestamp
            paused_target = None
            if controls and progress_state == "paused" and last_index >= 0:
                paused_target = plan["samples"][last_index]
            pause_at_target = paused_target is not None and max(
                abs(a - b) for a, b in zip(frame.positions, paused_target, strict=True)
            ) < math.radians(0.15)
            completion = "stopped" if stop_acknowledged else "paused" if pause_at_target else "completed"
            final_condition = (
                stop_acknowledged
                or pause_at_target
                or (
                    moving
                    and at_target
                    and elapsed >= plan["duration"] * 0.95
                    and (not controls or progress_state == "completed")
                )
            )
            if stopped and final_condition:
                settled = frame.timestamp if settled is None else settled
                if frame.timestamp - settled >= 0.5:
                    if not (await dashboard.execute("programState")).startswith("STOPPED"):
                        msg = "Joint settling did not agree with Dashboard STOPPED."
                        raise OutcomeUnknown(msg)
                    report = record.get("progress", {"sample": last_index})
                    report.update(controller_state=completion, current_action=None)
                    report.update(
                        positions=list(frame.positions),
                        velocities=list(frame.velocities),
                        runtime_state=frame.runtime_state,
                        robot_mode=frame.robot_mode,
                        safety_mode=frame.safety_mode,
                        timestamp=frame.timestamp,
                    )
                    observe(report)
                    record.update(
                        status="verified",
                        completion=completion,
                        after=dataclasses.asdict(frame),
                        observations=len(frames),
                        progress=report,
                    )
                    trace.flush()
                    os.fsync(trace.fileno())
                    persist()
                    return json.dumps(record)
            else:
                settled = None
        msg = "Continuous motion did not reach a verified final stop."
        raise OutcomeUnknown(msg)
    except (
        OSError,
        ValueError,
        TimeoutError,
        DeviceUnavailable,
        CommandRejected,
        OutcomeUnknown,
        asyncio.CancelledError,
        asyncio.IncompleteReadError,
    ) as error:
        record.update(status="outcome_unknown" if sent else "not_sent", error=str(error))
        if sent:
            with contextlib.suppress(OSError, TimeoutError, DeviceUnavailable):
                record["stop_response"] = await dashboard.execute("stop")
        if stream is not None:
            with contextlib.suppress(OSError):
                persist()
        if sent and not isinstance(error, asyncio.CancelledError):
            raise OutcomeUnknown(str(error)) from error
        raise
    finally:
        if trace is not None:
            trace.close()
        if writer is not None:
            writer.close()
        await monitor.close()
        await dashboard.close()
        if stream is not None:
            stream.close()
        if reserved:
            with contextlib.suppress(OSError):
                active.rmdir()
