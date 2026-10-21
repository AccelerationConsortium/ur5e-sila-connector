import math
import pytest
from unitelabs.ur5e_connector.io import kinematics as k
from unitelabs.ur5e_connector.io.errors import CommandRejected

Q = [0.2, -1.3, -1.3, 0.2, 1.5, 0.1]


def test_nominal_fk_matches_published_zero_geometry():
    t = k.forward([0] * 6)
    assert [t[i][3] for i in range(3)] == pytest.approx([-0.8172, -0.2329, 0.0628])
    assert k.coordinates(k.transform([0.1, 0.2, 0.3, math.pi, 0, 0])) == pytest.approx([0.1, 0.2, 0.3, math.pi, 0, 0])


def test_tcp_offset_and_rotation_roundtrip():
    for angle in (0, 1e-9, 0.5, math.pi - 1e-7, math.pi):
        t = k.transform([0.1, 0.2, 0.3, 0, angle, 0])
        rebuilt = k.transform(k.coordinates(t))
        assert max(abs(a - b) for r, s in zip(t, rebuilt, strict=False) for a, b in zip(r, s, strict=False)) < 1e-6
    t = k.forward(Q, [0, 0, 0.12, 0, 0, 0.3])
    solution = k.inverse(k.coordinates(t), [v + 0.02 for v in Q], [0, 0, 0.12, 0, 0, 0.3])
    assert solution["position_error_m"] < 0.0002
    assert solution["orientation_error_rad"] < 0.002
    assert max(abs(a - b) for a, b in zip(solution["joints"], Q, strict=False)) < 0.03


@pytest.mark.parametrize("bad", [[0] * 5, [True] * 6, [float("nan")] * 6])
def test_reject_malformed_tcp(bad):
    with pytest.raises(CommandRejected):
        k.inverse(bad, Q)


def test_reject_unreachable_without_changing_seed():
    before = Q.copy()
    with pytest.raises(CommandRejected):
        k.inverse([10, 10, 10, 0, 0, 0], Q)
    assert before == Q


def test_task_has_semantic_phases_and_no_gripper_action():
    target = k.coordinates(k.forward(Q))
    result = k.task_waypoints({"target": target, "approach_m": 0.03, "lift_m": 0.04}, Q)
    rows = result["waypoints"]
    assert 6 <= len(rows) <= 24
    assert rows[0]["name"] == "Approach"
    grasp = next(r for r in rows if r["name"].startswith("Simulate grasp"))
    assert grasp["hold"] == 1
    assert rows[-1]["degrees"] == pytest.approx([math.degrees(v) for v in Q])
    for row in rows:
        if row["name"] == "Approach":
            tcp = k.coordinates(k.forward([math.radians(v) for v in row["degrees"]]))
            assert tcp[:3] == pytest.approx([target[0], target[1], target[2] + 0.03], abs=0.0002)
