"""Optional path-time braking at reachable action boundaries; continuous by default."""

import math

RATE_ACCELERATION = 0.2


def boundaries(plan: dict) -> list[float]:
    """Return action-complete path times, including authored holds."""
    result = []
    elapsed = 0.0
    for row in plan["waypoints"]:
        elapsed += row["duration"] + row["hold"]
        result.append(elapsed)
    return result


def pause_boundary(path_time: float, rate: float, ends: list[float]) -> float | None:
    """Choose a boundary reachable without excessive path-rate deceleration."""
    distance = rate * rate / (2 * RATE_ACCELERATION) + 0.016
    return next((v for v in ends if v - path_time >= distance), None)


def advance(path_time: float, rate: float, target: float | None, dt: float = 0.008) -> tuple[float, float]:
    """Advance nominal path time, braking only after an explicit pause request."""
    if target is None:
        return path_time + rate * dt, rate
    remaining = max(0, target - path_time)
    if remaining <= RATE_ACCELERATION * dt * dt:
        return target, 0.0
    acceleration = min(RATE_ACCELERATION, rate * rate / (2 * remaining))
    next_rate = max(0, rate - acceleration * dt)
    return min(target, path_time + (rate + next_rate) * dt / 2), next_rate


def progress(plan: dict, sample: int, controller_state: str, pause_sample: int = -1) -> dict:
    """Map controller-owned path progress to action cards without wall-clock guesses."""
    ends = boundaries(plan)
    seconds = max(0, sample) * plan["dt"]
    completed = sum(seconds + 1e-6 >= v for v in ends)
    return {
        "sample": sample,
        "path_seconds": seconds,
        "completed_actions": completed,
        "current_action": completed
        if completed < len(ends) and controller_state not in {"paused", "completed", "stopped"}
        else None,
        "pause_after_action": next(
            (i for i, v in enumerate(ends) if math.isclose(v, pause_sample * plan["dt"], abs_tol=0.016)), None
        ),
        "controller_state": controller_state,
        "source": "controller register + measured RTDE joints",
    }
