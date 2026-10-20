import pytest

from unitelabs.ur5e_connector.io import Ur5eProtocol
from unitelabs.ur5e_connector.io.errors import CommandRejected, DeviceUnavailable


async def test_connection_lifecycle_does_not_start_program():
    protocol = Ur5eProtocol()
    with pytest.raises(DeviceUnavailable):
        await protocol.play()
    await protocol.open()
    assert await protocol.get_program_state() == "STOPPED"
    assert await protocol.get_loaded_program() == ""
    await protocol.load_program("demo.urp")
    await protocol.close()
    await protocol.open()
    assert await protocol.get_program_state() == "STOPPED"
    assert await protocol.get_loaded_program() == "demo.urp"
    await protocol.close()
    with pytest.raises(DeviceUnavailable):
        await protocol.get_robot_mode()


async def test_program_lifecycle_and_send_command_share_state():
    protocol = Ur5eProtocol()
    await protocol.open()
    await protocol.send_command("load demo.urp")
    await protocol.play()
    assert await protocol.get_running() is True
    with pytest.raises(CommandRejected):
        await protocol.load_program("other.urp")
    assert await protocol.get_loaded_program() == "demo.urp"
    await protocol.pause()
    assert await protocol.get_program_state() == "PAUSED"
    assert await protocol.get_running() is False
    await protocol.send_command("play")
    assert await protocol.get_program_state() == "PLAYING"
    await protocol.stop()
    await protocol.stop()
    assert await protocol.get_program_state() == "STOPPED"
    assert "SIMULATED" in await protocol.send_command("PolyscopeVersion")
    await protocol.close()


@pytest.mark.parametrize("command", [
    "play", "pause", "", " robotmode", "robotmode\nplay", "robotmode\r", "robotmode\x00",
    "load ", "load ../demo.urp", "load demo.txt", "shutdown", "movej([0,0,0,0,0,0])",
])
async def test_invalid_commands_preserve_state(command):
    protocol = Ur5eProtocol()
    await protocol.open()
    with pytest.raises(CommandRejected):
        await protocol.send_command(command)
    assert await protocol.get_program_state() == "STOPPED"
    assert await protocol.get_loaded_program() == ""
    await protocol.close()
