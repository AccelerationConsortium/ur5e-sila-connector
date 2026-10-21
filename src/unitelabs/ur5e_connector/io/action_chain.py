"""Compile bounded, C2-continuous six-axis waypoint chains without angle wrapping."""

import itertools
import math

from .choreography import LIMITS, MARGIN, pose
from .errors import CommandRejected
from .joint_step import JOINT_NAMES, validate_speed
from .playback import boundaries

DT = 0.008


class TimingRejected(CommandRejected):
    """Carry measured software derivatives for a read-only timing recommendation."""

    def __init__(self, velocity: float, acceleration: float) -> None:
        self.velocity, self.acceleration = velocity, acceleration
        super().__init__(
            f"Action requires {velocity:.2f} deg/s and {acceleration:.2f} deg/s². "
            "Increase travel times or reduce travel."
        )


def finite(value: object, low: float, high: float) -> bool:
    """Check a bounded finite number, excluding booleans."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and low <= value <= high
    )


def evaluate(coefficients: list, u: float) -> list[float]:
    """Evaluate six quintic polynomials using Horner's rule."""
    return [sum(c * u**i for i, c in enumerate(row)) for row in coefficients]


def build(start: list, waypoints: list, speed: float, *, reserve_pause: bool = False) -> dict:
    """Connect up to 24 absolute joint poses with shared velocity and acceleration at knots."""
    start = pose(start)
    validate_speed(speed)
    if not isinstance(waypoints, list) or not 1 <= len(waypoints) <= 24:
        msg = "An action chain needs 1-24 waypoints."
        raise CommandRejected(msg)
    rows = []
    points = [start]
    for row in waypoints:
        if not isinstance(row, dict) or set(row) - {"name", "degrees", "duration", "hold"}:
            msg = "Each waypoint requires degrees, duration, optional name and hold."
            raise CommandRejected(msg)
        angles = row.get("degrees")
        if not isinstance(angles, list) or len(angles) != 6 or not all(finite(v, -360, 360) for v in angles):
            msg = "Each waypoint needs six finite absolute joint angles in degrees."
            raise CommandRejected(msg)
        duration, hold = row.get("duration"), row.get("hold", 0)
        if not finite(duration, 1, 60) or not finite(hold, 0, 10):
            msg = "Travel duration must be 1-60 seconds and hold must be 0-10 seconds."
            raise CommandRejected(msg)
        name = row.get("name", "Waypoint")
        if not isinstance(name, str) or not 1 <= len(name) <= 60:
            msg = "Waypoint names must contain 1-60 characters."
            raise CommandRejected(msg)
        target = pose([math.radians(v) for v in angles])
        rows.append(
            {
                "name": name,
                "degrees": angles.copy(),
                "duration": round(duration / DT) * DT,
                "hold": round(hold / DT) * DT,
            }
        )
        points.append(target)
    duration = sum(row["duration"] + row["hold"] for row in rows)
    if duration > 120:
        msg = "The whole action chain must fit within 120 controller seconds."
        raise CommandRejected(msg)
    if max(abs(a - b) for point in points[1:] for a, b in zip(start, point, strict=True)) < math.radians(0.5):
        msg = "The action chain must contain at least 0.5 degrees of travel."
        raise CommandRejected(msg)
    velocities = [[0.0] * 6 for _ in points]
    # Same-direction tangents flow through interior waypoints. Holds and reversals stop that axis.
    for i in range(1, len(points) - 1):
        if rows[i - 1]["hold"]:
            continue
        for j in range(6):
            left = (points[i][j] - points[i - 1][j]) / rows[i - 1]["duration"]
            right = (points[i + 1][j] - points[i][j]) / rows[i]["duration"]
            if left * right > 0:
                velocities[i][j] = math.copysign(min(abs(left), abs(right)), left)
    segments, samples = [], [start]
    for i, row in enumerate(rows):
        travel = row["duration"]
        coefficients = []
        for j in range(6):
            a, b = points[i][j], points[i + 1][j]
            v, w = velocities[i][j] * travel, velocities[i + 1][j] * travel
            delta = b - a
            coefficients.append(
                [a, v, 0.0, 10 * delta - 6 * v - 4 * w, -15 * delta + 8 * v + 7 * w, 6 * delta - 3 * v - 3 * w]
            )
        for seconds, coeff in [(travel, coefficients), (row["hold"], [[v, 0, 0, 0, 0, 0] for v in points[i + 1]])]:
            count = round(seconds / DT)
            if not count:
                continue
            segments.append({"start_index": len(samples) - 1, "count": count, "coefficients": coeff})
            samples.extend(pose(evaluate(coeff, k / count)) for k in range(1, count + 1))
    velocity_samples = [
        [(b - a) / DT for a, b in zip(left, right, strict=True)] for left, right in itertools.pairwise(samples)
    ]
    acceleration_samples = [
        [(b - a) / DT for a, b in zip(left, right, strict=True)] for left, right in itertools.pairwise(velocity_samples)
    ]
    peak_v = max(abs(math.degrees(v)) for row in velocity_samples for v in row)
    peak_a = max(abs(math.degrees(v)) for row in acceleration_samples for v in row)
    derivative_cap = speed * (0.8 if reserve_pause else 1)
    if peak_v > derivative_cap or peak_a > 2 * derivative_cap:
        raise TimingRejected(peak_v, peak_a)
    return {
        "kind": "action_chain",
        "start": start,
        "target": samples[-1],
        "duration": (len(samples) - 1) * DT,
        "speed": speed,
        "peak_speed": peak_v,
        "peak_acceleration": peak_a,
        "samples": samples,
        "dt": DT,
        "waypoints": rows,
        "segments": segments,
        "ranges": [
            {
                "joint": name,
                "min": min(math.degrees(q[i]) for q in samples),
                "max": max(math.degrees(q[i]) for q in samples),
                "limit": LIMITS[i] - MARGIN,
            }
            for i, name in enumerate(JOINT_NAMES)
        ],
        "validation": "125 Hz C2 joint-space interpolation; no collision, dynamics or hardware validation",
    }


def build_at_speed(start: list, waypoints: list, speed: float) -> dict:
    """Calculate travel times from one speed, then check the complete continuous curve."""
    start = pose(start)
    validate_speed(speed)
    if not isinstance(waypoints, list) or not 1 <= len(waypoints) <= 24:
        msg = "An action chain needs 1-24 waypoints."
        raise CommandRejected(msg)
    rows = []
    previous = [math.degrees(v) for v in start]
    for row in waypoints:
        if not isinstance(row, dict) or set(row) - {"name", "degrees", "hold"}:
            msg = "Automatic timing accepts name, six degrees and optional hold; do not supply travel durations."
            raise CommandRejected(msg)
        angles = row.get("degrees")
        if not isinstance(angles, list) or len(angles) != 6 or not all(finite(v, -360, 360) for v in angles):
            msg = "Each waypoint needs six finite absolute joint angles in degrees."
            raise CommandRejected(msg)
        pose([math.radians(v) for v in angles])
        distance = max(abs(a - b) for a, b in zip(previous, angles, strict=True))
        # Rest-to-rest quintic extrema seed travel times. Shared knot tangents are
        # then checked against the same complete sampled compiler as manual plans.
        seconds = max(1.0, 1.03 * 1.875 * distance / speed, 1.03 * math.sqrt(5.774 * distance / (2 * speed)))
        rows.append({**row, "degrees": angles.copy(), "duration": math.ceil(seconds / DT) * DT})
        previous = angles
    for _ in range(5):
        if any(row["duration"] > 60 for row in rows):
            msg = "This speed needs over 60 seconds for a segment. Split the action or review a higher speed."
            raise CommandRejected(msg)
        try:
            plan = build(start, rows, speed, reserve_pause=True)
        except TimingRejected as error:
            scale = max(error.velocity / (speed * 0.8), math.sqrt(error.acceleration / (1.6 * speed))) * 1.03
            rows = [{**row, "duration": math.ceil(row["duration"] * scale / DT) * DT} for row in rows]
            continue
        plan["speed"] = speed
        plan["playback_controls"] = True
        plan["timing_mode"] = "automatic"
        plan["timing_note"] = "Travel times calculated from speed and acceleration caps; pendant scaling still applies."
        return plan
    msg = "Unable to fit a checked trajectory within automatic timing bounds. Split the action."
    raise CommandRejected(msg)


def recommend(start: list, waypoints: list, speed: float) -> dict:
    """Simulate timing alternatives without changing poses, storing a plan or enabling execution."""
    try:
        plan = build(start, waypoints, speed)
    except TimingRejected as error:
        velocity, acceleration = error.velocity, error.acceleration
    else:
        return {
            "status": "valid",
            "peak_speed": plan["peak_speed"],
            "peak_acceleration": plan["peak_acceleration"],
            "changes_applied": False,
        }
    result = {
        "status": "timing_rejected",
        "changes_applied": False,
        "positions_changed": False,
        "requested_speed_cap": speed,
        "peak_speed": velocity,
        "peak_acceleration": acceleration,
        "maximum_speed_cap": 40,
        "speed_suggestion": None,
        "timing_suggestion": None,
        "validation": "Accelerated joint-space kinematic calculation only; no collision or dynamics validation.",
    }
    # A higher cap does not change fixed-duration motion: it only permits the existing derivatives.
    required_cap = max(velocity, acceleration / 2)
    if required_cap < 40:
        cap = min(40, math.ceil(required_cap * 1.03))
        candidate = build(start, waypoints, cap)
        result["speed_suggestion"] = {
            "cap": cap,
            "peak_speed": candidate["peak_speed"],
            "note": "Keep travel times; this only raises the validation cap.",
        }
    scale = max(velocity / speed, math.sqrt(acceleration / (2 * speed))) * 1.03
    revised = [
        {**row, "degrees": row["degrees"].copy(), "duration": math.ceil(round(row["duration"] / DT) * scale) * DT}
        for row in waypoints
    ]
    total = sum(row["duration"] + round(row.get("hold", 0) / DT) * DT for row in revised)
    if all(row["duration"] <= 60 for row in revised) and total <= 120:
        candidate = build(start, revised, speed)
        # Try restoring the smaller moves to their authored times. Bound the number
        # of full compiler passes so suggestions cannot monopolize the local server.
        previous = [math.degrees(v) for v in start]
        excursions = []
        for index, row in enumerate(waypoints):
            excursions.append((max(abs(a - b) for a, b in zip(row["degrees"], previous, strict=True)), index))
            previous = row["degrees"]
        for _, index in sorted(excursions)[:8]:
            trial = [row.copy() for row in revised]
            trial[index]["duration"] = waypoints[index]["duration"]
            try:
                checked = build(start, trial, speed)
            except TimingRejected:
                continue
            revised, candidate = trial, checked
        result["timing_suggestion"] = {
            "speed_cap": speed,
            "travel_seconds": [row["duration"] for row in candidate["waypoints"]],
            "total_seconds": candidate["duration"],
            "peak_speed": candidate["peak_speed"],
            "peak_acceleration": candidate["peak_acceleration"],
            "note": "Keep poses, order and holds unchanged; recheck the full trajectory after adjusting travel times.",
        }
    return result


def script(plan: dict) -> str:
    """Emit compact controller-local polynomials matching the validated sampled path."""
    lines = ["def sila_action_chain():", "  actual = get_actual_joint_positions()", "  valid = True"]
    for i, value in enumerate(plan["start"]):
        lines += [f"  if norm(actual[{i}]-({value:.15f})) > 0.001745329252:", "    valid = False", "  end"]
    controls = plan.get("playback_controls", False)
    if controls:
        token = plan.get("playback_token", 0)
        lines += [
            f"  valid = valid and read_input_integer_register(25) == {token}",
            f"  write_output_integer_register(26,{token})",
            "  write_output_integer_register(24,0)",
            "  write_output_integer_register(25,1)",
            "  write_output_integer_register(27,-1)",
        ]
    lines += [
        "  if valid:",
        "    i = 0.0",
        "    rate = 1.0",
        "    pause_at = -1",
        f"    while i <= {len(plan['samples']) - 1}:",
    ]
    if controls:
        ends = [round(v / plan["dt"]) for v in boundaries(plan)]
        lines += ["      if read_input_integer_register(24) == 1 and pause_at < 0:"]
        for j, endpoint in enumerate(ends):
            lines += [
                f"        {'if' if j == 0 else 'elif'} {endpoint}-i >= rate*rate/(0.4*0.008)+2:",
                f"          pause_at = {endpoint}",
            ]
        lines += [
            "        end",
            "        if pause_at >= 0:",
            "          write_output_integer_register(25,2)",
            "          write_output_integer_register(27,pause_at)",
            "        end",
            "      end",
        ]
    for index, segment in enumerate(plan["segments"]):
        first, count = segment["start_index"], segment["count"]
        lines += [
            f"      {'if' if index == 0 else 'elif'} i <= {first + count}:",
            f"        u = (i-{first})/{float(count):.1f}",
        ]
        expressions = []
        for coefficients in segment["coefficients"]:
            expression = f"({coefficients[-1]:.15f})"
            for value in reversed(coefficients[:-1]):
                expression = f"({value:.15f})+u*({expression})"
            expressions.append(expression)
        lines.append(f"        q = [{','.join(expressions)}]")
    lines += [
        "      end",
        "      servoj(q,0,0,0.008,0.1,200)",
        *(
            [
                "      write_output_integer_register(24,floor(i))",
                "      if pause_at >= 0:",
                "        remaining = (pause_at-i)*0.008",
                "        if remaining <= 0.0000128:",
                "          i = pause_at",
                "          rate = 0",
                "          break",
                "        end",
                "        decel = rate*rate/(2*remaining)",
                "        if decel > 0.2:",
                "          decel = 0.2",
                "        end",
                "        next_rate = rate-decel*0.008",
                "        if next_rate < 0:",
                "          next_rate = 0",
                "        end",
                "        i = i+(rate+next_rate)/2",
                "        if i > pause_at:",
                "          i = pause_at",
                "        end",
                "        rate = next_rate",
                "      else:",
                "        i = i+1",
                "      end",
            ]
            if controls
            else ["      i = i+1"]
        ),
        "    end",
        "    stopj(0.5)",
        *(
            [
                "    if pause_at >= 0:",
                "      write_output_integer_register(24,pause_at)",
                "      write_output_integer_register(25,3)",
                "    else:",
                f"      write_output_integer_register(24,{len(plan['samples']) - 1})",
                "      write_output_integer_register(25,4)",
                "    end",
            ]
            if controls
            else []
        ),
        "  end",
        "end",
        "",
    ]
    return "\n".join(lines)
