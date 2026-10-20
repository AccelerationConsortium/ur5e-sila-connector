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

from .choreography import script
from .dashboard import DashboardConnection
from .errors import CommandRejected, DeviceUnavailable, OutcomeUnknown
from .joint_step import require_stopped
from .rtde import JointMonitor
from .receipts import check_records


async def execute(host: str, directory: pathlib.Path, request_id: str, plan: dict) -> str:
    """Send one locally paced program, then observe it; never retry a lost outcome."""
    active = directory / ".active"
    monitor, dashboard = JointMonitor(host), DashboardConnection(host)
    writer = stream = None
    reserved = sent = False
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
        sent = True
        writer.write(program.encode("ascii"))
        await asyncio.wait_for(writer.drain(), 3)
        record["status"] = "sent_monitoring"
        persist()
        deadline = time.monotonic() + plan["duration"] / max(0.05, plan["slider_fraction"]) + 30
        moving = False
        settled = None
        frames = []
        last_time = latest.timestamp
        while time.monotonic() < deadline:
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
            if moving and at_target and stopped and elapsed >= plan["duration"] * 0.95:
                settled = frame.timestamp if settled is None else settled
                if frame.timestamp - settled >= 0.5:
                    record.update(status="verified", after=dataclasses.asdict(frame), observations=len(frames))
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
        if writer is not None:
            writer.close()
        await monitor.close()
        await dashboard.close()
        if stream is not None:
            stream.close()
        if reserved:
            with contextlib.suppress(OSError):
                active.rmdir()
