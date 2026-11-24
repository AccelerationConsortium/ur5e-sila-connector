import time

import pytest

GRIPPER = "io.unitelabs/robot/PlateGripperController/v1"
SBS_WIDTH = 85.48


async def test_grip_and_release_follow_the_simulated_scene(sila_client):
    gripper = await sila_client.load_feature(GRIPPER)
    assert await gripper.properties["GripperState"].read() == "OPEN"
    assert await gripper.properties["OpeningMillimeters"].read() == 140
    assert "Simulated" in await gripper.properties["GripperModel"].read()
    grip = {"WidthMillimeters": SBS_WIDTH, "ForceNewtons": 20.0, "ExpectedSimulationMode": True}

    # Nothing at the grasp point: the grip is refused and the fingers end open.
    with pytest.raises(Exception, match="without meeting a plate") as error:
        await gripper.commands["GripPlate"].execute(grip)
    assert type(error.value).__name__ == "CommandRejected"
    assert await gripper.properties["GripperState"].read() == "OPEN"

    # Something of another width: refused, not held.
    await gripper.commands["SetSimulatedObject"].execute({"WidthMillimeters": 127.76})
    with pytest.raises(Exception, match="stopped at 127.76 mm"):
        await gripper.commands["GripPlate"].execute(grip)
    assert await gripper.properties["GripperState"].read() == "OPEN"

    await gripper.commands["SetSimulatedObject"].execute({"WidthMillimeters": SBS_WIDTH})
    started = time.monotonic()
    response = await gripper.commands["GripPlate"].execute(grip)
    # 140 mm to 85.48 mm at 100 mm/s: the simulated grip takes real time.
    assert time.monotonic() - started >= 0.5
    assert response["OpeningMillimeters"] == pytest.approx(SBS_WIDTH)
    assert await gripper.properties["GripperState"].read() == "HOLDING"

    for command, params, reason in [
        ("GripPlate", grip, "already holds"),
        ("SetSimulatedObject", {"WidthMillimeters": 0.0}, "Release the held plate"),
        ("ReleasePlate", {"OpeningMillimeters": 80.0, "ExpectedSimulationMode": True}, "Open wider"),
        ("ReleasePlate", {"OpeningMillimeters": 120.0, "ExpectedSimulationMode": False}, "mode changed"),
    ]:
        with pytest.raises(Exception, match=reason):
            await gripper.commands[command].execute(params)
    assert await gripper.properties["GripperState"].read() == "HOLDING"

    response = await gripper.commands["ReleasePlate"].execute(
        {"OpeningMillimeters": 120.0, "ExpectedSimulationMode": True}
    )
    assert response["OpeningMillimeters"] == 120
    assert await gripper.properties["GripperState"].read() == "OPEN"


async def test_out_of_range_grip_is_refused_before_it_runs(sila_client):
    gripper = await sila_client.load_feature(GRIPPER)
    with pytest.raises(Exception):
        await gripper.commands["GripPlate"].execute(
            {"WidthMillimeters": 20.0, "ForceNewtons": 20.0, "ExpectedSimulationMode": True}
        )
    assert await gripper.properties["GripperState"].read() == "OPEN"


@pytest.mark.parametrize("sila_client", ["real"], indirect=True)
async def test_real_mode_has_no_gripper_driver(sila_client):
    gripper = await sila_client.load_feature(GRIPPER)
    assert await gripper.properties["GripperState"].read() == "UNAVAILABLE"
    for command, params in [
        ("GripPlate", {"WidthMillimeters": SBS_WIDTH, "ForceNewtons": 20.0, "ExpectedSimulationMode": False}),
        ("ReleasePlate", {"OpeningMillimeters": 120.0, "ExpectedSimulationMode": False}),
    ]:
        with pytest.raises(Exception, match="No gripper driver") as error:
            await gripper.commands[command].execute(params)
        assert type(error.value).__name__ == "DeviceUnavailable"
    with pytest.raises(Exception, match="simulation mode only"):
        await gripper.commands["SetSimulatedObject"].execute({"WidthMillimeters": SBS_WIDTH})
