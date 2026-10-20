import asyncio

import pytest

from unitelabs.ur5e_connector.io import Ur5eProtocol
from unitelabs.ur5e_connector.io.errors import CommandRejected, DeviceUnavailable, OutcomeUnknown



def protocol_for(dashboard, **kwargs):
    host, port, *_ = dashboard
    return Ur5eProtocol(simulation_mode=False, host=host, port=port, **kwargs)


async def test_real_queries_and_serialization(dashboard):
    protocol = protocol_for(dashboard)
    await protocol.open()
    assert dashboard[3] == ["PolyscopeVersion"]
    values = await asyncio.gather(
        protocol.get_software_version(), protocol.get_robot_mode(),
        protocol.get_safety_status(), protocol.get_program_state(),
        protocol.get_loaded_program(), protocol.get_running(),
    )
    assert values == ["5.4.2.76197 (Jul 10 2019)", "RUNNING", "NORMAL", "STOPPED", "", False]
    for cmd in ["play", "load /programs/test.urp", "stop", "pause", "shutdown", "robotmode\nplay"]:
        before = list(dashboard[3])
        with pytest.raises(CommandRejected):
            await protocol.send_command(cmd)
        assert dashboard[3] == before
    await protocol.close()


async def test_real_control_approval_state_and_denied_remote_response(dashboard):
    protocol = protocol_for(dashboard, enable_program_control=True, approved_program="/programs/test.urp")
    await protocol.open()
    with pytest.raises(CommandRejected):
        await protocol.play()
    await protocol.load_program("/programs/test.urp")
    dashboard[4]["play"] = "Command is not allowed due to safety reasons, please switch robot to Remote Control mode"
    with pytest.raises(CommandRejected, match="Remote Control"):
        await protocol.play()
    del dashboard[4]["play"]
    await protocol.play()
    assert await protocol.get_running() is True
    await protocol.pause()
    assert await protocol.get_program_state() == "PAUSED"
    await protocol.play()
    await protocol.stop()
    assert await protocol.get_program_state() == "STOPPED"
    await protocol.close()


async def test_lost_control_response_never_replays_and_requires_reconciliation(dashboard):
    protocol = protocol_for(dashboard, enable_program_control=True, approved_program="/programs/test.urp")
    await protocol.open()
    await protocol.load_program("/programs/test.urp")
    dashboard[4]["drop"] = "play"
    with pytest.raises(OutcomeUnknown):
        await protocol.play()
    assert protocol.control_outcome_unknown
    assert dashboard[3].count("play") == 1
    assert dashboard[2].program_state == "PLAYING"
    await protocol.open()
    with pytest.raises(CommandRejected, match="unknown"):
        await protocol.play()
    assert dashboard[3].count("play") == 1
    await protocol.stop()
    assert not protocol.control_outcome_unknown
    await protocol.close()


async def test_timeout_closes_transport_without_replaying(dashboard):
    protocol = protocol_for(dashboard, timeout=0.05)
    await protocol.open()
    dashboard[4]["delay"] = "robotmode"
    with pytest.raises(DeviceUnavailable):
        await protocol.get_robot_mode()
    assert not protocol.connected
    assert dashboard[3].count("robotmode") == 1
    with pytest.raises(DeviceUnavailable):
        await protocol.get_robot_mode()
    await protocol.close()


async def test_switching_modes_preserves_simulation_on_failed_connection(dashboard):
    protocol = Ur5eProtocol(host=dashboard[0], port=dashboard[1])
    await protocol.open()
    dashboard[4]["PolyscopeVersion"] = "unexpected device"
    with pytest.raises(DeviceUnavailable):
        await protocol.set_simulation_mode(False)
    assert protocol.simulation_mode
    assert await protocol.get_program_state() == "STOPPED"
    del dashboard[4]["PolyscopeVersion"]
    await protocol.set_simulation_mode(False)
    assert not protocol.simulation_mode
    dashboard[2].execute("load demo.urp")
    dashboard[2].execute("play")
    with pytest.raises(CommandRejected):
        await protocol.set_simulation_mode(True)
    assert not protocol.simulation_mode
    dashboard[2].execute("stop")
    await protocol.set_simulation_mode(True)
    assert protocol.simulation_mode
    await protocol.close()


@pytest.mark.parametrize("response", ["Safetystatus: REDUCED", "Safetymode: REDUCED"])
async def test_safety_response_prefix_compatibility(dashboard, response):
    protocol = protocol_for(dashboard)
    await protocol.open()
    dashboard[4]["safetystatus"] = response
    assert await protocol.get_safety_status() == "REDUCED"
    await protocol.close()


async def test_unsafe_safety_state_blocks_play_on_wire(dashboard):
    protocol = protocol_for(dashboard, enable_program_control=True, approved_program="/programs/test.urp")
    await protocol.open()
    await protocol.load_program("/programs/test.urp")
    dashboard[4]["safetystatus"] = "Safetymode: PROTECTIVE_STOP"
    with pytest.raises(CommandRejected):
        await protocol.play()
    assert "play" not in dashboard[3]
    await protocol.close()


async def test_simulated_stop_cannot_clear_unknown_real_outcome(dashboard):
    protocol = protocol_for(dashboard, enable_program_control=True, approved_program="/programs/test.urp")
    await protocol.open()
    await protocol.load_program("/programs/test.urp")
    dashboard[4]["drop"] = "play"
    with pytest.raises(OutcomeUnknown):
        await protocol.play()
    # Another operator stops the robot. The unresolved command still needs reconciliation.
    dashboard[2].execute("stop")
    await protocol.open()
    await protocol.set_simulation_mode(True)
    await protocol.stop()
    assert protocol.control_outcome_unknown is True
    await protocol.set_simulation_mode(False)
    with pytest.raises(CommandRejected, match="unknown"):
        await protocol.play()
    await protocol.stop()
    assert protocol.control_outcome_unknown is False
    await protocol.close()
