"""Continuous six-axis choreography, bounded in joint space; no collision model."""

import math

from .errors import CommandRejected
from .joint_step import JOINT_NAMES, validate_speed
import itertools

LIMITS = (360, 360, 160, 360, 360, 360)
MARGIN = 5


def pose(values: list | tuple) -> list[float]:
    """Reject malformed, non-finite or unwrapped-out-of-range joint poses."""
    if (
        not isinstance(values, (list, tuple))
        or len(values) != 6
        or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values)
    ):
        msg = "Expected six finite joint angles in radians."
        raise CommandRejected(msg)
    for name, value, limit in zip(JOINT_NAMES, values, LIMITS, strict=True):
        if abs(math.degrees(value)) >= limit - MARGIN:
            msg = f"{name} is inside the {MARGIN} degree limit margin; reposition with the pendant. No angle wrapping."
            raise CommandRejected(msg)
    return list(values)


def offsets(u: float) -> list[float]:
    """Blend sway, lift, bow and wrist rolls with a smooth start/end envelope."""
    e = math.sin(math.pi * u) ** 4
    phase = 2 * math.pi * u
    return [
        18 * e * math.sin(phase),
        e * (-12 + 4 * math.sin(2 * phase)),
        e * (12 + 6 * math.sin(phase + 0.6)),
        22 * e * math.sin(2 * phase + 0.3),
        18 * e * math.sin(phase + 1.2),
        e * (-18 + 12 * math.sin(3 * phase)),
    ]


def build(
    start: list, speed: float, duration: float = 60, target: list | None = None, amplitude_scale: float = 1.0
) -> dict:
    """Prepare a checked dense path; reject rather than clip, wrap or silently change it."""
    if (
        not isinstance(amplitude_scale, (int, float))
        or isinstance(amplitude_scale, bool)
        or not math.isfinite(amplitude_scale)
        or not 0.1 <= amplitude_scale <= 1
    ):
        msg = "Amplitude scale must be between 0.1 and 1."
        raise CommandRejected(msg)
    start = pose(start)
    validate_speed(speed)
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or not 20 <= duration <= 120
    ):
        msg = "Duration must be 20-120 seconds."
        raise CommandRejected(msg)
    if target is not None:
        target = pose(target)
        if max(abs(math.degrees(a - b)) for a, b in zip(start, target, strict=True)) > 90:
            msg = "Reset exceeds 90 degrees on one joint; reposition manually before previewing reset."
            raise CommandRejected(msg)
    # 125 Hz controller setpoints. This is a kinematic model, not a physics simulator.
    count = round(duration / 0.008)
    duration = count * 0.008
    samples = []
    for i in range(count + 1):
        u = i / count
        if target is None:
            q = [s + math.radians(d * amplitude_scale) for s, d in zip(start, offsets(u), strict=True)]
        else:
            blend = 10 * u**3 - 15 * u**4 + 6 * u**5
            q = [a + (b - a) * blend for a, b in zip(start, target, strict=True)]
        samples.append(pose(q))
    velocities = [
        [(b - a) / 0.008 for a, b in zip(left, right, strict=True)] for left, right in itertools.pairwise(samples)
    ]
    accelerations = [
        [(b - a) / 0.008 for a, b in zip(left, right, strict=True)] for left, right in itertools.pairwise(velocities)
    ]
    peak_v = max(abs(math.degrees(v)) for row in velocities for v in row)
    peak_a = max(abs(math.degrees(v)) for row in accelerations for v in row)
    if peak_v > speed or peak_a > 2 * speed:
        msg = f"Path requires {peak_v:.2f} deg/s and {peak_a:.2f} deg/s². Increase duration or reviewed speed."
        raise CommandRejected(msg)
    ranges = [
        {
            "joint": name,
            "min": min(math.degrees(q[i]) for q in samples),
            "max": max(math.degrees(q[i]) for q in samples),
            "limit": LIMITS[i] - MARGIN,
        }
        for i, name in enumerate(JOINT_NAMES)
    ]
    return {
        "amplitude_scale": amplitude_scale,
        "kind": "reset" if target is not None else "continuous_dance",
        "start": start,
        "target": samples[-1],
        "duration": duration,
        "speed": speed,
        "peak_speed": peak_v,
        "peak_acceleration": peak_a,
        "ranges": ranges,
        "samples": samples,
        "dt": 0.008,
        "validation": "joint-space kinematics only; no collision or hardware validation",
    }


def script(plan: dict) -> str:
    """Generate one controller-local servo loop, with no intermediate stop or network pacing."""
    lines = ["def sila_continuous_motion():", "  actual = get_actual_joint_positions()", "  valid = True"]
    for i, value in enumerate(plan["start"]):
        lines += [f"  if norm(actual[{i}]-({value:.12f})) > 0.001745329252:", "    valid = False", "  end"]
    lines += [
        "  if valid:",
        "    i = 0",
        f"    while i <= {len(plan['samples']) - 1}:",
        f"      u = i/{float(len(plan['samples']) - 1):.1f}",
    ]
    if plan["kind"] == "continuous_dance":
        lines += ["      e = pow(sin(3.141592653590*u),4)", "      p = 6.283185307180*u"]
        expressions = [
            "18*e*sin(p)",
            "e*(-12+4*sin(2*p))",
            "e*(12+6*sin(p+0.6))",
            "22*e*sin(2*p+0.3)",
            "18*e*sin(p+1.2)",
            "e*(-18+12*sin(3*p))",
        ]
        q = [
            f"({s:.12f})+0.017453292520*{plan.get('amplitude_scale', 1.0):.6f}*({e})"
            for s, e in zip(plan["start"], expressions, strict=True)
        ]
    else:
        lines += ["      b = 10*pow(u,3)-15*pow(u,4)+6*pow(u,5)"]
        q = [f"({a:.12f})+({b - a:.12f})*b" for a, b in zip(plan["start"], plan["target"], strict=True)]
    lines += [
        f"      servoj([{','.join(q)}],0,0,0.008,0.1,200)",
        "      i = i+1",
        "    end",
        "    stopj(0.5)",
        "  end",
        "end",
        "",
    ]
    return "\n".join(lines)


def public_plan(plan: dict) -> dict:
    """Return a bounded preview with time-labelled samples and explicit evidence limits."""
    result = {k: v for k, v in plan.items() if k not in {"samples", "expires"}}
    result["preview_samples"] = [
        {"t": i * plan["dt"], "degrees": [math.degrees(q) for q in plan["samples"][i]]}
        for i in range(0, len(plan["samples"]), max(1, len(plan["samples"]) // 240))
    ]
    result["preview_samples"].append({"t": plan["duration"], "degrees": [math.degrees(q) for q in plan["target"]]})
    return result
