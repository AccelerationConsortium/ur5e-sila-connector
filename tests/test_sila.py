import pytest

from unitelabs.ur5e_connector import create_app
from unitelabs.ur5e_connector.config import Ur5eConnectorConfig

ROBOT = "io.unitelabs/robot/RobotController/v1"
SIMULATION = "org.silastandard/core/SimulationController/v1"


async def test_network_program_lifecycle(sila_client):
    robot = await sila_client.load_feature(ROBOT)
    assert await robot.properties["ProgramState"].read() == "STOPPED"
    assert await robot.properties["LoadedProgram"].read() == ""
    assert await robot.properties["RobotMode"].read() == "RUNNING"
    await robot.commands["LoadProgram"].execute({"Program": "demo.urp"})
    await robot.commands["Play"].execute({})
    assert await robot.properties["Running"].read() is True
    await robot.commands["Pause"].execute({})
    assert await robot.properties["ProgramState"].read() == "PAUSED"
    await robot.commands["Play"].execute({})
    await robot.commands["Stop"].execute({})
    assert await robot.properties["ProgramState"].read() == "STOPPED"
    response = await robot.commands["SendCommand"].execute({"Command": "load other.urp"})
    assert response["Response"] == "Loading program: other.urp"
    assert await robot.properties["LoadedProgram"].read() == "other.urp"


async def test_network_errors_are_declared_and_simulation_remains_usable(sila_client):
    robot = await sila_client.load_feature(ROBOT)
    simulation = await sila_client.load_feature(SIMULATION)
    for command, params in [("Play", {}), ("SendCommand", {"Command": "robotmode\nplay"})]:
        with pytest.raises(Exception) as error:
            await robot.commands[command].execute(params)
        assert type(error.value).__name__ == "CommandRejected"
    await robot.commands["LoadProgram"].execute({"Program": "demo.urp"})
    with pytest.raises(Exception) as error:
        await simulation.commands["StartRealMode"].execute({})
    assert type(error.value).__name__ == "StartRealModeFailed"
    assert await simulation.properties["SimulationMode"].read() is True
    await simulation.commands["StartSimulationMode"].execute({})
    assert await robot.properties["LoadedProgram"].read() == "demo.urp"
    await robot.commands["Play"].execute({})
    assert await robot.properties["Running"].read() is True
    await robot.commands["Stop"].execute({})


async def test_real_mode_requires_configured_controller():
    app = create_app(Ur5eConnectorConfig(simulation_mode=False))
    with pytest.raises(Exception, match="Configure the controller hostname"):
        await anext(app)
    await app.aclose()


@pytest.mark.parametrize("sila_client", ["real"], indirect=True)
async def test_network_real_transport_queries_and_control_block(sila_client, dashboard):
    robot = await sila_client.load_feature(ROBOT)
    simulation = await sila_client.load_feature(SIMULATION)
    assert await simulation.properties["SimulationMode"].read() is False
    assert await robot.properties["SoftwareVersion"].read() == "5.4.2.76197 (Jul 10 2019)"
    assert await robot.properties["SafetyStatus"].read() == "NORMAL"
    assert await robot.properties["ProgramState"].read() == "STOPPED"
    assert await robot.properties["Running"].read() is False
    with pytest.raises(Exception) as error:
        await robot.commands["SendCommand"].execute({"Command": "play"})
    assert type(error.value).__name__ == "CommandRejected"
    assert "play" not in dashboard[3]


@pytest.mark.parametrize("sila_client", ["control"], indirect=True)
async def test_network_outcome_unknown_is_defined_and_can_be_reconciled(sila_client, dashboard):
    robot = await sila_client.load_feature(ROBOT)
    simulation = await sila_client.load_feature(SIMULATION)
    await robot.commands["LoadProgram"].execute({"Program": "/programs/test.urp"})
    dashboard[4]["drop"] = "play"
    with pytest.raises(Exception) as error:
        await robot.commands["Play"].execute({})
    assert type(error.value).__name__ == "OutcomeUnknown"
    assert await robot.properties["ControlOutcomeUnknown"].read() is True
    await simulation.commands["StartRealMode"].execute({})
    assert await robot.properties["ProgramState"].read() == "PLAYING"
    assert dashboard[3].count("play") == 1
    await robot.commands["Stop"].execute({})
    assert await robot.properties["ControlOutcomeUnknown"].read() is False
    assert await robot.properties["ProgramState"].read() == "STOPPED"


async def test_sila_simulated_base_step_changes_only_base(sila_client):
    import json
    import math
    robot=await sila_client.load_feature(ROBOT)
    before=await robot.properties['JointPositions'].read()
    result=await robot.commands['MoveBasePositiveFiveDegrees'].execute({})
    assert json.loads(result['Receipt'])['status']=='simulated'
    after=await robot.properties['JointPositions'].read()
    assert after[0]-before[0]==pytest.approx(math.radians(5))
    assert after[1:]==before[1:]


@pytest.mark.parametrize('sila_client',['real'],indirect=True)
async def test_sila_base_motion_disabled_by_default(sila_client,dashboard):
    robot=await sila_client.load_feature(ROBOT)
    with pytest.raises(Exception) as error:
        await robot.commands['MoveBasePositiveFiveDegrees'].execute({})
    assert type(error.value).__name__=='CommandRejected'


async def test_sila_simulated_elbow_step_holds_other_joints(sila_client):
    import json,math
    robot=await sila_client.load_feature(ROBOT)
    before=await robot.properties['JointPositions'].read()
    response=await robot.commands['MoveElbowPositiveFiveDegrees'].execute({})
    assert json.loads(response['Receipt'])['status']=='simulated'
    after=await robot.properties['JointPositions'].read()
    assert after[2]-before[2]==pytest.approx(math.radians(5))
    assert all(after[i]==before[i] for i in range(6) if i!=2)


@pytest.mark.parametrize('sila_client',['real'],indirect=True)
async def test_sila_elbow_motion_disabled_by_default(sila_client):
    robot=await sila_client.load_feature(ROBOT)
    with pytest.raises(Exception) as error:
        await robot.commands['MoveElbowPositiveFiveDegrees'].execute({})
    assert type(error.value).__name__=='CommandRejected'


async def test_sila_general_joint_signed_angles(sila_client):
    import json,math
    robot=await sila_client.load_feature(ROBOT)
    for joint,index,delta in [('Wrist1',3,5),('Wrist2',4,-3),('Wrist3',5,2),('Shoulder',1,-.5)]:
        before=await robot.properties['JointPositions'].read()
        result=await robot.commands['MoveJoint'].execute({'Joint':joint,'DeltaDegrees':delta,'RequestId':f'test-{index}'})
        assert json.loads(result['Receipt'])['status']=='simulated'
        after=await robot.properties['JointPositions'].read()
        assert math.degrees(after[index]-before[index])==pytest.approx(delta)
        assert all(after[i]==before[i] for i in range(6) if i!=index)
    with pytest.raises(Exception) as error:
        await robot.commands['MoveJoint'].execute({'Joint':'Wrist1','DeltaDegrees':11,'RequestId':'invalid'})
    assert type(error.value).__name__=='CommandRejected'


@pytest.mark.parametrize('sila_client',['real'],indirect=True)
async def test_sila_general_joint_disabled_by_default(sila_client):
    robot=await sila_client.load_feature(ROBOT)
    with pytest.raises(Exception) as error:
        await robot.commands['MoveJoint'].execute({'Joint':'Wrist1','DeltaDegrees':5,'RequestId':'new'})
    assert type(error.value).__name__=='CommandRejected'
