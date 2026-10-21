import json
import math
import time

import pytest

from unitelabs.ur5e_connector.io import action_chain, choreography
from unitelabs.ur5e_connector.io.errors import CommandRejected
from unitelabs.ur5e_connector.io.protocol import Ur5eProtocol

START = [0, -1.5, -1.5, 0, 1.5, 0]


def rows():
    origin = [math.degrees(v) for v in START]
    return [{"name": f"Pose {i}", "degrees": [v + i * 3 for v in origin], "duration": 5, "hold": 0}
            for i in (1, 2, 3)]


def test_chain_flows_through_knots_and_matches_controller_polynomials():
    plan = action_chain.build(START, rows(), 10)
    assert len(plan["samples"]) == 1876
    assert plan["target"] == pytest.approx([v + math.radians(9) for v in START])
    assert plan["peak_speed"] < 10
    for segment in plan["segments"]:
        for k in (0, 1, segment["count"] // 2, segment["count"]):
            assert action_chain.evaluate(segment["coefficients"], k / segment["count"]) == pytest.approx(
                plan["samples"][segment["start_index"] + k], abs=1e-12)
    for left, right in zip(plan["segments"], plan["segments"][1:]):
        for a, b in zip(left["coefficients"], right["coefficients"]):
            t1, t2 = left["count"] * .008, right["count"] * .008
            v1 = sum(i * c for i, c in enumerate(a)) / t1
            v2 = b[1] / t2
            assert v1 == pytest.approx(v2, abs=1e-12)
            assert abs(v1) > 0  # no artificial stop at the intermediate pose
            assert sum(i * (i - 1) * c for i, c in enumerate(a)) / t1**2 == pytest.approx(0, abs=1e-12)
            assert b[2] == 0
    script = choreography.script(plan)
    assert script.count("servoj(") == 1 and script.count("stopj(") == 1
    assert "elif" in script and "movej(" not in script
    assert "samples" not in choreography.public_plan(plan)


def test_hold_stops_all_axes_and_is_included_in_duration():
    points = rows()
    points[0]["hold"] = 1
    plan = action_chain.build(START, points, 10)
    assert plan["duration"] == 16
    hold = plan["segments"][1]
    assert hold["count"] == 125
    assert all(row[1:] == [0, 0, 0, 0, 0] for row in hold["coefficients"])
    assert all(sum(i*c for i,c in enumerate(row)) == pytest.approx(0, abs=1e-12)
               for row in plan["segments"][0]["coefficients"])


@pytest.mark.parametrize("bad", [[], [None], [{"degrees": [0]*5, "duration": 5}],
    [{"degrees": [True]*6, "duration": 5}], [{"degrees": [0, 0, 156, 0, 0, 0], "duration": 5}],
    [{"degrees": [0]*6, "duration": float("nan")}], [{"degrees": [0]*6, "duration": 5, "hold": -1}],
    [{"degrees": [0]*6, "duration": 5, "script": "power on"}], rows()*9])
def test_malformed_or_out_of_limits_input(bad):
    with pytest.raises(CommandRejected):
        action_chain.build(START, bad, 10)


def test_derivative_duration_and_zero_travel_rejections():
    with pytest.raises(CommandRejected, match="requires"):
        action_chain.build(START, [{**rows()[0], "duration": 1}], 1)
    with pytest.raises(CommandRejected, match="120"):
        action_chain.build(START, [{**rows()[0], "duration": 60}] * 3, 10)
    with pytest.raises(CommandRejected, match="0.5"):
        action_chain.build(START, [{"degrees": [math.degrees(v) for v in START], "duration": 5}], 10)


async def test_real_chain_gate_is_separate_from_dance_gate():
    protocol = Ur5eProtocol(simulation_mode=False, enable_continuous_motion=True)
    plan = action_chain.build(START, rows(), 10)
    plan.update(id="chain-disabled", expires=time.time()+180, simulation=False)
    protocol.motion_plans[plan["id"]] = plan
    with pytest.raises(CommandRejected, match="disabled"):
        await protocol.execute_motion(plan["id"], "chain-no-send", False, time.time()+20, True)
    assert "chain-no-send" not in protocol.motion_requests


async def test_stale_simulation_pose_rejected_without_movement():
    protocol = Ur5eProtocol()
    await protocol.open()
    try:
        plan = json.loads(await protocol.prepare_action_chain(json.dumps(rows()), 10, True))
        protocol.device.joint_positions = tuple(v+.01 for v in plan["start"])
        before = protocol.device.joint_positions
        with pytest.raises(CommandRejected, match="changed"):
            await protocol.execute_motion(plan["id"], "chain-stale", True, time.time()+20, False)
        assert protocol.device.joint_positions == before
    finally:
        await protocol.close()


def test_emitted_controller_numeric_expressions_match_all_segment_edges():
    points = rows()
    points[1]['hold'] = 1
    plan = action_chain.build(START, points, 10)
    expressions = [line.strip()[4:] for line in action_chain.script(plan).splitlines()
                   if line.strip().startswith('q = ')]
    assert len(expressions) == len(plan['segments'])
    for segment, expression in zip(plan['segments'], expressions):
        for k in (0, 1, segment['count']//2, segment['count']):
            u = k / segment['count']
            generated = eval(expression, {'__builtins__': {}}, {'u': u})
            assert generated == pytest.approx(plan['samples'][segment['start_index']+k], abs=1e-12)


def test_timing_suggestions_preserve_all_authored_values():
    points = [{'name':'Large turn', 'degrees':[180, -86, -86, 0, 86, 0], 'duration':6, 'hold':1}]
    original = json.loads(json.dumps(points))
    advice = action_chain.recommend(START, points, 30)
    assert advice['status'] == 'timing_rejected'
    assert advice['changes_applied'] is False and advice['positions_changed'] is False
    assert advice['speed_suggestion'] is None  # requires >40 deg/s at fixed times
    assert points == original
    timing = advice['timing_suggestion']
    assert timing['peak_speed'] <= 30 and timing['peak_acceleration'] <= 60
    assert timing['travel_seconds'][0] > 6
    revised = [{**points[0], 'duration':timing['travel_seconds'][0]}]
    assert action_chain.build(START, revised, 30)['peak_speed'] == pytest.approx(timing['peak_speed'])
    assert timing['total_seconds'] == pytest.approx(timing['travel_seconds'][0]+1)


def test_fixed_time_cap_advice_and_no_room_to_retime():
    points = [{'degrees':[30, -86, -86, 0, 86, 0], 'duration':6, 'hold':0}]
    advice = action_chain.recommend(START, points, 5)
    assert 5 < advice['speed_suggestion']['cap'] <= 40
    assert action_chain.build(START, points, advice['speed_suggestion']['cap'])['peak_speed'] < 40
    constrained = [{'degrees':[180*(-1)**i, -86, -86, 0, 86, 0], 'duration':30} for i in range(4)]
    advice = action_chain.recommend(START, constrained, 1)
    assert advice['timing_suggestion'] is None


async def test_suggestion_never_stores_or_dispatches_a_motion_plan():
    protocol = Ur5eProtocol()
    await protocol.open()
    try:
        before = protocol.device.joint_positions
        points = [{'degrees':[180, -86, -86, 0, 86, 0], 'duration':6}]
        advice = json.loads(await protocol.suggest_action_chain_timing(json.dumps(points), 30, True))
        assert advice['status'] == 'timing_rejected'
        assert protocol.device.joint_positions == before
        assert not protocol.motion_plans and not protocol.motion_requests
    finally:
        await protocol.close()


def test_advice_retains_original_time_for_small_moves():
    points = [{'degrees':[10,-86,-86,0,86,0],'duration':6},
              {'degrees':[180,-86,-86,0,86,0],'duration':6}]
    suggestion = action_chain.recommend(START, points, 30)['timing_suggestion']
    assert suggestion['travel_seconds'][0] == 6
    assert suggestion['travel_seconds'][1] > 6
    assert suggestion['peak_speed'] <= 30


@pytest.mark.parametrize('speed', [5, 10, 30, 40])
def test_automatic_timing_uses_one_speed_and_preserves_all_poses(speed):
    points = [{k:v for k,v in r.items() if k != 'duration'} for r in rows()]
    points[1]['hold'] = .5
    original = json.loads(json.dumps(points))
    plan = action_chain.build_at_speed(START, points, speed)
    assert points == original
    assert plan['timing_mode'] == 'automatic'
    assert plan['peak_speed'] <= speed
    assert plan['peak_acceleration'] <= 2*speed
    assert [r['degrees'] for r in plan['waypoints']] == [r['degrees'] for r in points]
    assert [r['hold'] for r in plan['waypoints']] == pytest.approx([r['hold'] for r in points], abs=.00400001)
    assert all(1 <= r['duration'] <= 60 for r in plan['waypoints'])
    for segment in plan['segments']:
        for k in [0, segment['count']//2, segment['count']]:
            assert action_chain.evaluate(segment['coefficients'], k/segment['count']) == pytest.approx(
                plan['samples'][segment['start_index']+k], abs=1e-12)


def test_higher_motion_speed_reduces_automatic_travel_time():
    points = [{'degrees':[180,-86,-86,0,86,0], 'hold':0}]
    slow = action_chain.build_at_speed(START, points, 10)
    fast = action_chain.build_at_speed(START, points, 30)
    assert fast['duration'] < slow['duration']
    assert fast['peak_speed'] <= 30
    assert fast['target'] == pytest.approx(slow['target'])
    with pytest.raises(CommandRejected, match='60 seconds'):
        action_chain.build_at_speed(START, points, 1)
    with pytest.raises(CommandRejected, match='do not supply'):
        action_chain.build_at_speed(START, [{**points[0], 'duration':6}], 30)


@pytest.mark.parametrize('bad', [[], [None], [{'degrees':[0]*5}], [{'degrees':[True]*6}],
    [{'degrees':[0,0,156,0,0,0]}], [{'degrees':[0]*6,'hold':float('nan')}],
    [{'degrees':[0]*6,'name':'', 'hold':0}], [{'degrees':[0]*6, 'script':'power on'}]])
def test_automatic_timing_rejects_invalid_waypoints(bad):
    with pytest.raises(CommandRejected):
        action_chain.build_at_speed(START, bad, 30)
