import asyncio
import json
import math
import time
import pytest
from unitelabs.ur5e_connector.io import action_chain, playback
from unitelabs.ur5e_connector.io.protocol import Ur5eProtocol
from unitelabs.ur5e_connector.io.errors import CommandRejected


def test_pause_is_optional_and_deferred_to_reachable_boundary():
    assert playback.advance(1, 1, None) == (1.008, 1)
    assert playback.pause_boundary(1, 1, [2, 6, 9]) == 6
    assert playback.pause_boundary(8, 1, [9]) is None
    for start in (0, 0.1, 1, 2.9):
        target = playback.pause_boundary(start, 1, [3, 7, 12])
        t, rate = start, 1
        for _ in range(10000):
            next_t, next_rate = playback.advance(t, rate, target)
            assert t <= next_t <= target
            assert 0 <= next_rate <= rate
            assert (rate - next_rate) / 0.008 <= 0.20001
            t, rate = next_t, next_rate
            if rate == 0:
                break
        assert t == target and rate == 0


def test_automatic_compiler_reserves_pause_derivative_headroom():
    q = [0, -1.5, -1.5, 0, 1.5, 0]
    origin = [math.degrees(v) for v in q]
    rows = [
        {"name": "One", "degrees": [origin[0] + 30, *origin[1:]], "hold": 0},
        {"name": "Two", "degrees": [origin[0] + 60, *origin[1:]], "hold": 0},
    ]
    plan = action_chain.build_at_speed(q, rows, 30)
    assert plan["peak_speed"] <= 24
    assert plan["peak_acceleration"] <= 48
    assert plan["playback_controls"]
    program = action_chain.script({**plan, "playback_token": 123})
    assert "read_input_integer_register(25) == 123" in program
    assert "read_input_integer_register(24) == 1" in program
    assert "pause_at = -1" in program
    assert "write_output_integer_register(25,3)" in program
    assert "sleep(" not in program


async def test_control_request_does_not_wait_for_motion_lock():
    p = Ur5eProtocol()
    p.active_motion = {"request_id": "owned", "stop": False, "pause": False, "supports_pause": True}
    async with p._lock:
        result = await asyncio.wait_for(p.control_active_motion("owned", "stop", True), 0.1)
        assert json.loads(result)["confirmed_stopped"] is False
        assert p.active_motion["stop"]
        with pytest.raises(CommandRejected):
            await p.control_active_motion("other", "stop", True)
        with pytest.raises(CommandRejected):
            await p.control_active_motion("owned", "pause_after_action", False)


async def test_simulated_boundary_pause_then_new_remainder_and_stop():
    p = Ur5eProtocol()
    await p.open()
    origin = [math.degrees(v) for v in p.device.joint_positions]
    rows = [{"name": str(i), "degrees": [origin[0] + i * 35, *origin[1:]], "hold": 0} for i in (1, 2, 3)]
    plan = json.loads(await p.prepare_action_chain(json.dumps(rows), 10, True, True))
    task = asyncio.create_task(p.execute_motion(plan["id"], "pause-test", True, time.time() + 20, False))
    for _ in range(100):
        if p.active_motion:
            break
        await asyncio.sleep(0.001)
    await p.control_active_motion("pause-test", "pause_after_action", True)
    receipt = json.loads(await asyncio.wait_for(task, 10))
    assert receipt["completion"] == "paused"
    done = receipt["progress"]["completed_actions"]
    assert 0 < done < len(rows)
    assert p.device.joint_positions == pytest.approx([math.radians(v) for v in rows[done - 1]["degrees"]])
    assert json.loads(await p.read_motion_trace("pause-test"))["samples"]
    plan = json.loads(await p.prepare_action_chain(json.dumps(rows[done:]), 10, True, True))
    task = asyncio.create_task(p.execute_motion(plan["id"], "stop-test", True, time.time() + 20, False))
    for _ in range(100):
        if p.active_motion:
            break
        await asyncio.sleep(0.001)
    await p.control_active_motion("stop-test", "stop", True)
    receipt = json.loads(await task)
    assert receipt["completion"] == "stopped"
    assert receipt["progress"]["completed_actions"] == 0
    await p.close()


@pytest.mark.parametrize("speed", [1, 1.2, 40])
def test_headroom_preserves_full_public_speed_range(speed):
    start = [0, -1.5, -1.5, 0, 1.5, 0]
    target = [math.degrees(v) for v in start]
    target[0] += 2
    plan = action_chain.build_at_speed(start, [{"name": "Small move", "degrees": target, "hold": 0}], speed)
    assert plan["speed"] == speed
    assert plan["peak_speed"] <= speed * 0.8
    assert plan["peak_acceleration"] <= speed * 1.6
