import asyncio
import contextlib
import dataclasses
import datetime
import json
import math
import os
import pathlib
import re
import time

from .dashboard import DashboardConnection
from .errors import CommandRejected, DeviceUnavailable, OutcomeUnknown
from .rtde import JointMonitor, JointSample
from .receipts import check_records

JOINT_NAMES = ("Base", "Shoulder", "Elbow", "Wrist1", "Wrist2", "Wrist3")
JOINT_TEST_SPEED_DEG_S = 10.0


def validate_speed(speed_degrees: float) -> None:
    """Limit explicit joint command speed to one through forty degrees per second."""
    if (
        isinstance(speed_degrees, bool)
        or not isinstance(speed_degrees, (int, float))
        or not math.isfinite(speed_degrees)
        or not 1 <= speed_degrees <= 40
    ):
        msg = "Command speed must be between 1 and 40 degrees per second."
        raise CommandRejected(msg)


def validate_joint_test(joint: str, delta_degrees: float, request_id: str) -> int:
    """Validate a bounded relative move and return the selected joint index."""
    names = tuple(name.lower() for name in JOINT_NAMES)
    if not isinstance(joint, str) or joint.lower() not in names:
        msg = "Choose Base, Shoulder, Elbow, Wrist1, Wrist2 or Wrist3."
        raise CommandRejected(msg)
    if (
        isinstance(delta_degrees, bool)
        or not isinstance(delta_degrees, (float, int))
        or not math.isfinite(delta_degrees)
        or not 0.5 <= abs(delta_degrees) <= 10
    ):
        msg = "Relative test angles must be between 0.5 and 10 degrees in either direction."
        raise CommandRejected(msg)
    if not isinstance(request_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) is None:
        msg = "Use a unique request ID of 1-64 letters, digits, underscores or hyphens."
        raise CommandRejected(msg)
    return names.index(joint.lower())


def build_joint_test(
    start: tuple[float, ...],
    joint_index: int,
    delta_degrees: float,
    *,
    speed_degrees: float = JOINT_TEST_SPEED_DEG_S,
) -> tuple[tuple[float, ...], str]:
    """Build a bounded single-joint test at ten degrees per second."""
    return _build_positive_five_step(start, joint_index, delta_degrees=delta_degrees, speed_degrees=speed_degrees)


def _check_test_receipts(directory: pathlib.Path) -> None:
    check_records(directory)


async def execute_joint_test(
    host: str,
    directory: pathlib.Path,
    request_id: str,
    joint_index: int,
    delta_degrees: float,
    *,
    speed_degrees: float = JOINT_TEST_SPEED_DEG_S,
    expires_at: float | None = None,
) -> str:
    """Serialize persisted test requests and block retries after incomplete execution."""
    if joint_index not in range(6):
        msg = "Invalid joint index."
        raise CommandRejected(msg)
    validate_joint_test(JOINT_NAMES[joint_index], delta_degrees, request_id)
    validate_speed(speed_degrees)
    active = directory / ".active"
    reserved = False
    try:
        await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        try:
            active.mkdir()
        except FileExistsError as error:
            msg = "Another test is active or interrupted. Inspect the controller and receipts before proceeding."
            raise CommandRejected(msg) from error
        reserved = True
        await asyncio.wait_for(asyncio.to_thread(_check_test_receipts, directory), timeout=10)
        return await _execute_step(
            host,
            directory / f"{request_id}.json",
            joint_index=joint_index,
            delta_degrees=delta_degrees,
            speed_degrees=speed_degrees,
            expires_at=expires_at,
        )
    except (OSError, ValueError) as error:
        raise DeviceUnavailable(str(error)) from error
    finally:
        if reserved:
            with contextlib.suppress(OSError):
                active.rmdir()


def build_base_step(start: tuple[float, ...]) -> tuple[tuple[float, ...], str]:
    """Build one positive five-degree Base move with an on-controller start-pose guard."""
    return _build_positive_five_step(start, 0)


def build_elbow_step(start: tuple[float, ...]) -> tuple[tuple[float, ...], str]:
    """Build one positive five-degree Elbow move while holding the other joints."""
    return _build_positive_five_step(start, 2)


def _build_positive_five_step(
    start: tuple[float, ...], joint_index: int, *, delta_degrees: float = 5, speed_degrees: float = 1
) -> tuple[tuple[float, ...], str]:
    if joint_index not in range(6):
        msg = "Invalid joint index."
        raise CommandRejected(msg)
    validate_joint_test(JOINT_NAMES[joint_index], delta_degrees, "validation")
    validate_speed(speed_degrees)
    if len(start) != 6 or not all(math.isfinite(q) for q in start):
        msg = "Expected six finite joint angles."
        raise CommandRejected(msg)
    target = tuple(q + math.radians(delta_degrees) if i == joint_index else q for i, q in enumerate(start))
    for i, (before, after) in enumerate(zip(start, target, strict=True)):
        limit = math.radians(160 if i == 2 else 360)
        if not -limit < before < limit or not -limit < after < limit:
            msg = "A joint is outside the standard UR5e joint range."
            raise CommandRejected(msg)
    name = JOINT_NAMES[joint_index].lower()
    speed = f"{math.radians(speed_degrees):.12f}"
    acceleration = f"{math.radians(2 * speed_degrees):.12f}"
    lines = [f"def codex_{name}_positive_five():", "  actual = get_actual_joint_positions()", "  valid = True"]
    for index, expected in enumerate(start):
        lines.extend(
            [f"  if norm(actual[{index}] - ({expected:.12f})) > 0.001745329252:", "    valid = False", "  end"]
        )
    lines.extend(
        [
            "  if valid:",
            f"    movej({list(target)!r}, a={acceleration}, v={speed}, t=0, r=0)",
            "  end",
            "end",
            "",
        ]
    )
    return target, "\n".join(lines)


def require_stopped(sample: JointSample) -> None:
    """Reject movement unless RTDE reports a ready, normal, stationary controller."""
    if sample.robot_mode != 7 or sample.safety_mode != 1 or sample.runtime_state != 1:
        msg = "Require robot RUNNING, safety NORMAL and program STOPPED before the joint step."
        raise CommandRejected(msg)
    if max(abs(v) for v in sample.velocities) > math.radians(0.1):
        msg = "The robot is not stationary."
        raise CommandRejected(msg)


async def execute_base_step(host: str, receipt_path: pathlib.Path, reference_path: pathlib.Path | None = None) -> str:
    """Execute one journaled Base +5 degree request and verify actual settling without replay."""
    return await _execute_step(host, receipt_path, reference_path, joint_index=0)


async def execute_elbow_step(host: str, receipt_path: pathlib.Path) -> str:
    """Execute one journaled Elbow +5 degree request and verify actual settling without replay."""
    return await _execute_step(host, receipt_path, joint_index=2)


async def _execute_step(
    host: str,
    receipt_path: pathlib.Path,
    reference_path: pathlib.Path | None = None,
    *,
    joint_index: int,
    delta_degrees: float = 5,
    speed_degrees: float = 1,
    expires_at: float | None = None,
) -> str:
    name = JOINT_NAMES[joint_index]
    monitor = JointMonitor(host)
    dashboard = DashboardConnection(host)
    writer = None
    report: dict = {"operation": f"{name} {delta_degrees:+g} degrees then stop", "host": host, "status": "preflight"}
    receipt = None
    sent = False

    def persist() -> None:
        if receipt is not None:
            receipt.seek(0)
            receipt.write(json.dumps(report, indent=2) + "\n")
            receipt.truncate()
            receipt.flush()
            os.fsync(receipt.fileno())

    try:
        # Exclusive creation reserves this authorization before any network mutation.
        try:
            receipt = receipt_path.open("x+")
        except FileExistsError as error:
            msg = "This one-shot motion authorization already has a receipt; it cannot be replayed."
            raise CommandRejected(msg) from error
        report["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        persist()
        await monitor.open()
        await dashboard.open()
        version = await dashboard.execute("PolyscopeVersion")
        if not version.startswith("URSoftware 5.4.2."):
            msg = f"This first-motion procedure was prepared for the confirmed 5.4.2 controller, received {version}."
            raise CommandRejected(msg)
        report["software"] = version
        # Several fresh timestamps ensure the robot has been stopped for at least 0.5 controller seconds.
        first = await monitor.sample()
        require_stopped(first)
        start = first
        preflight_deadline = time.monotonic() + 5
        while start.timestamp - first.timestamp < 0.5:
            if time.monotonic() >= preflight_deadline:
                msg = "Stationary RTDE verification did not advance in time."
                raise DeviceUnavailable(msg)
            start = await monitor.sample()
            require_stopped(start)
            if max(abs(a - b) for a, b in zip(start.positions, first.positions, strict=True)) > math.radians(0.1):
                msg = "Joint positions changed during stationary verification."
                raise CommandRejected(msg)
        if not (await dashboard.execute("programState")).startswith("STOPPED"):
            msg = "Dashboard no longer reports STOPPED."
            raise CommandRejected(msg)
        if await dashboard.execute("safetystatus") not in {"Safetymode: NORMAL", "Safetystatus: NORMAL"}:
            msg = "Dashboard no longer reports NORMAL."
            raise CommandRejected(msg)
        # Drain the small stream backlog and bind the script to a fresh measured starting pose.
        newest = await monitor.sample()
        require_stopped(newest)
        commanded_start = newest.positions
        if reference_path is not None:
            previous = json.loads(await asyncio.to_thread(reference_path.read_text))
            commanded_start = tuple(previous["before"]["positions"])
            observations = previous.get("samples", [])
            if (
                previous.get("status") != "outcome_unknown"
                or previous.get("stop_response") != "Stopped"
                or not observations
                or any(frame["runtime_state"] != 1 for frame in observations)
                or any(
                    max(abs(a - b) for a, b in zip(frame["positions"], commanded_start, strict=True))
                    > math.radians(0.1)
                    for frame in observations
                )
                or max(abs(a - b) for a, b in zip(newest.positions, commanded_start, strict=True)) > math.radians(0.1)
            ):
                msg = "The previous motion cannot be reconciled as unexecuted at the original starting pose."
                raise CommandRejected(msg)
            report["reconciles_receipt"] = str(reference_path)
        target, script = _build_positive_five_step(
            commanded_start, joint_index, delta_degrees=delta_degrees, speed_degrees=speed_degrees
        )
        report.update(
            before=dataclasses.asdict(newest),
            target_radians=list(target),
            target_degrees=[math.degrees(q) for q in target],
            max_joint_speed_deg_s=speed_degrees,
            acceleration_deg_s2=2 * speed_degrees,
            script=script,
            status="prepared",
        )
        persist()
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, 30002), timeout=3)
        report["status"] = "sending_outcome_unknown"
        persist()
        # Set before write: any exception after this point must be treated as uncertain.
        if expires_at is not None and time.time() >= expires_at:
            msg = "Request expired before dispatch; no motion was sent."
            raise CommandRejected(msg)
        sent = True
        writer.write(script.encode("ascii"))
        await asyncio.wait_for(writer.drain(), timeout=3)
        report["status"] = "sent_monitoring"
        persist()
        deadline = time.monotonic() + 45
        settled_since = None
        observed_motion = False
        samples = []
        report["samples"] = samples
        previous_timestamp = newest.timestamp
        while time.monotonic() < deadline:
            sample = await monitor.sample()
            if sample.timestamp <= previous_timestamp:
                continue
            previous_timestamp = sample.timestamp
            samples.append(dataclasses.asdict(sample))
            joint_delta = sample.positions[joint_index] - newest.positions[joint_index]
            if sample.safety_mode != 1 or sample.robot_mode != 7:
                msg = "Robot left NORMAL/RUNNING during movement; inspect the controller."
                raise OutcomeUnknown(msg)
            if (
                not math.radians(min(0, delta_degrees) - 0.2)
                <= joint_delta
                <= math.radians(max(0, delta_degrees) + 0.2)
            ):
                msg = f"Observed {name} position left the authorized movement interval."
                raise OutcomeUnknown(msg)
            if max(
                abs(a - b)
                for i, (a, b) in enumerate(zip(sample.positions, newest.positions, strict=True))
                if i != joint_index
            ) > math.radians(0.2):
                msg = "An unrequested joint moved beyond the observation tolerance."
                raise OutcomeUnknown(msg)
            observed_motion = observed_motion or abs(joint_delta) > math.radians(0.1)
            at_target = max(abs(a - b) for a, b in zip(sample.positions, target, strict=True)) < math.radians(0.1)
            stationary = max(abs(v) for v in sample.velocities) < math.radians(0.1)
            if observed_motion and at_target and stationary and sample.runtime_state == 1:
                settled_since = sample.timestamp if settled_since is None else settled_since
                if sample.timestamp - settled_since >= 0.5:
                    report.update(
                        status="verified",
                        after=dataclasses.asdict(sample),
                        samples=samples,
                        actual_delta_degrees=[
                            math.degrees(a - b) for a, b in zip(sample.positions, newest.positions, strict=True)
                        ],
                    )
                    persist()
                    return json.dumps(report)
            else:
                settled_since = None
        msg = "Motion completion was not verified within 45 seconds. Do not repeat this request."
        raise OutcomeUnknown(msg)
    except (
        OSError,
        TimeoutError,
        asyncio.IncompleteReadError,
        DeviceUnavailable,
        CommandRejected,
        OutcomeUnknown,
        asyncio.CancelledError,
    ) as error:
        report.update(status="outcome_unknown" if sent else "not_sent", error=str(error))
        if sent:
            # Best-effort controlled stop after uncertain execution; never replay the movement.
            try:
                report["stop_response"] = await dashboard.execute("stop")
            except (OSError, TimeoutError, DeviceUnavailable):
                report["stop_response"] = "unconfirmed; use the on-site stop control"
            with contextlib.suppress(OSError):
                persist()
            if not isinstance(error, asyncio.CancelledError):
                raise OutcomeUnknown(str(error)) from error
        with contextlib.suppress(OSError):
            persist()
        if isinstance(error, (OSError, TimeoutError, asyncio.IncompleteReadError)):
            raise DeviceUnavailable(str(error)) from error
        raise
    finally:
        if writer is not None:
            writer.close()
        await monitor.close()
        await dashboard.close()
        if receipt is not None:
            receipt.close()
