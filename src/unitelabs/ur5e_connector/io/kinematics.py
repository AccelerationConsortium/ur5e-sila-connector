"""Nominal UR5e DH kinematics; not calibrated collision geometry."""

import math
from .choreography import pose
from .errors import CommandRejected

MODEL = "UR5e nominal DH; base frame; metres/radians; no calibration, collision or dynamics validation"


def multiply(a: list, b: list) -> list:
    """Multiply matrices."""
    return [[sum(x * b[k][j] for k, x in enumerate(row)) for j in range(len(b[0]))] for row in a]


def vector(values: object, size: int) -> list:
    """Validate finite numeric vectors."""
    if (
        not isinstance(values, list)
        or len(values) != size
        or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values)
    ):
        msg = f"Expected {size} finite numbers."
        raise CommandRejected(msg)
    return values.copy()


def transform(values: list) -> list:
    """Convert XYZ and rotation vector to a transform."""
    x, y, z, *r = vector(values, 6)
    angle = math.sqrt(sum(v * v for v in r))
    rotation = [[float(i == j) for j in range(3)] for i in range(3)]
    if angle > 1e-12:
        n = [v / angle for v in r]
        skew = [[0, -n[2], n[1]], [n[2], 0, -n[0]], [-n[1], n[0], 0]]
        c, s = math.cos(angle), math.sin(angle)
        rotation = [[c * (i == j) + (1 - c) * n[i] * n[j] + s * skew[i][j] for j in range(3)] for i in range(3)]
    return [rotation[i] + [v] for i, v in enumerate((x, y, z))] + [[0, 0, 0, 1]]


def rotation_vector(rotation: list) -> list:
    """Calculate an axis-angle vector, including near pi."""
    angle = math.acos(max(-1, min(1, (sum(rotation[i][i] for i in range(3)) - 1) / 2)))
    skew = [rotation[2][1] - rotation[1][2], rotation[0][2] - rotation[2][0], rotation[1][0] - rotation[0][1]]
    if angle < 1e-7:
        return [v / 2 for v in skew]
    if math.pi - angle < 1e-5:
        index = max(range(3), key=lambda i: rotation[i][i])
        axis = [0.0] * 3
        axis[index] = math.sqrt(max(0, (rotation[index][index] + 1) / 2))
        for j in range(3):
            if j != index:
                axis[j] = (rotation[index][j] + rotation[j][index]) / (4 * axis[index])
        if sum(a * b for a, b in zip(axis, skew, strict=True)) < 0:
            axis = [-v for v in axis]
        return [angle * v for v in axis]
    return [angle * v / (2 * math.sin(angle)) for v in skew]


def coordinates(matrix: list) -> list:
    """Extract XYZ and rotation-vector coordinates."""
    return [matrix[i][3] for i in range(3)] + rotation_vector(matrix)


def forward(joints: list, tcp: list | None = None) -> list:
    """Calculate nominal TCP transform from unwrapped radians."""
    matrix = [[float(i == j) for j in range(4)] for i in range(4)]
    for theta, a, d, alpha in zip(
        pose(joints),
        (0, -0.425, -0.3922, 0, 0, 0),
        (0.1625, 0, 0, 0.1333, 0.0997, 0.0996),
        (math.pi / 2, 0, 0, math.pi / 2, -math.pi / 2, 0),
        strict=True,
    ):
        c, s, ca, sa = math.cos(theta), math.sin(theta), math.cos(alpha), math.sin(alpha)
        matrix = multiply(
            matrix, [[c, -s * ca, s * sa, a * c], [s, c * ca, -c * sa, a * s], [0, sa, ca, d], [0, 0, 0, 1]]
        )
    return multiply(matrix, transform(tcp if tcp is not None else [0.0] * 6))


def error(current: list, target: list) -> list:
    """Compute translation and base-frame orientation residuals."""
    rotation = multiply([row[:3] for row in target[:3]], [[current[j][i] for j in range(3)] for i in range(3)])
    return [target[i][3] - current[i][3] for i in range(3)] + rotation_vector(rotation)


def linear_solve(matrix: list, rhs: list) -> list:
    """Solve a damped system using partial pivoting."""
    rows = [[*row, v] for row, v in zip(matrix, rhs, strict=True)]
    for i in range(len(rows)):
        pivot = max(range(i, len(rows)), key=lambda j: abs(rows[j][i]))
        rows[i], rows[pivot] = rows[pivot], rows[i]
        scale = rows[i][i]
        if abs(scale) < 1e-16:
            msg = "IK system is singular. Choose another pose."
            raise CommandRejected(msg)
        rows[i] = [v / scale for v in rows[i]]
        for j in range(len(rows)):
            if i != j:
                factor = rows[j][i]
                rows[j] = [a - factor * b for a, b in zip(rows[j], rows[i], strict=True)]
    return [row[-1] for row in rows]


def inverse(target_pose: list, seed: list, tcp: list | None = None) -> dict:
    """Solve nearby nominal IK without angle wrapping or remote control."""
    target = transform(target_pose)
    q = pose(seed)
    tool = vector(tcp if tcp is not None else [0.0] * 6, 6)
    if math.sqrt(sum(v * v for v in tool[:3])) > 0.5:
        msg = "TCP offset must be within 0.5 m of the flange."
        raise CommandRejected(msg)
    for iteration in range(220):
        current = forward(q, tool)
        residual = error(current, target)
        pe = math.sqrt(sum(v * v for v in residual[:3]))
        oe = math.sqrt(sum(v * v for v in residual[3:]))
        if pe < 0.0002 and oe < 0.002:
            return {
                "joints": q,
                "degrees": [math.degrees(v) for v in q],
                "tcp_pose": coordinates(current),
                "position_error_m": pe,
                "orientation_error_rad": oe,
                "iterations": iteration,
                "model": MODEL,
            }
        columns = []
        for j in range(6):
            shifted = q.copy()
            shifted[j] += 1e-6
            columns.append([v / 1e-6 for v in error(current, forward(shifted, tool))])
        weights = [1, 1, 1, 0.25, 0.25, 0.25]
        jacobian = [[columns[j][i] * weights[i] for j in range(6)] for i in range(6)]
        residual = [v * w for v, w in zip(residual, weights, strict=True)]
        normal = [
            [sum(jacobian[k][i] * jacobian[k][j] for k in range(6)) + (0.0001 if i == j else 0) for j in range(6)]
            for i in range(6)
        ]
        delta = linear_solve(normal, [sum(jacobian[k][i] * residual[k] for k in range(6)) for i in range(6)])
        scale = min(1, 0.15 / max(1e-10, *(abs(v) for v in delta)))
        try:
            q = pose([a + scale * b for a, b in zip(q, delta, strict=True)])
        except CommandRejected as exc:
            msg = "Nearby IK branch reaches a joint limit; change target or seed. No angle wrapping."
            raise CommandRejected(msg) from exc
    msg = "No nearby nominal IK solution converged. Target may be unreachable or singular; no path changed."
    raise CommandRejected(msg)


def task_waypoints(settings: dict, seed: list) -> dict:
    """Generate a fixed-location pickup rehearsal without gripper commands."""
    if not isinstance(settings, dict) or settings.get("template", "pickup_rehearsal") != "pickup_rehearsal":
        msg = "Choose pickup_rehearsal."
        raise CommandRejected(msg)
    target = vector(settings.get("target"), 6)
    tcp = vector(settings.get("tcp", [0.0] * 6), 6)
    approach, lift = settings.get("approach_m", 0.08), settings.get("lift_m", 0.10)
    if any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0.01 <= v <= 0.3
        for v in (approach, lift)
    ):
        msg = "Approach and lift must be 0.01-0.30 m in base Z."
        raise CommandRejected(msg)
    origin = pose(seed)
    q = origin.copy()
    rows = []
    for name, height, hold in [
        ("Approach", approach, 0),
        ("Descend", 0, 0),
        ("Simulate grasp — no gripper", 0, 1),
        ("Lift", lift, 0),
        ("Retreat", approach, 0),
    ]:
        target_pose = target.copy()
        target_pose[2] += height
        start_pose = coordinates(forward(q, tcp))
        count = 1 if name == "Approach" else max(1, math.ceil(abs(target_pose[2] - start_pose[2]) / 0.025))
        for i in range(1, count + 1):
            point = target_pose.copy()
            point[:3] = [a + (b - a) * i / count for a, b in zip(start_pose[:3], target_pose[:3], strict=True)]
            solved = inverse(point, q, tcp)
            q = solved["joints"]
            rows.append(
                {
                    "name": name if i == count else f"{name} {i}/{count}",
                    "degrees": solved["degrees"],
                    "hold": hold if i == count else 0,
                }
            )
    if settings.get("return_to_start", True):
        rows.append({"name": "Return to start", "degrees": [math.degrees(v) for v in origin], "hold": 0})
    if len(rows) > 24:
        msg = "Task needs more than 24 nodes. Reduce approach/lift distances."
        raise CommandRejected(msg)
    return {
        "waypoints": rows,
        "settings": settings,
        "model": MODEL,
        "note": "Fixed-location rehearsal only. No gripper. Sampled Cartesian targets use joint interpolation.",
    }
