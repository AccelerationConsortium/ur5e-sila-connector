import asyncio
import json
import math
import time
import pytest
from unitelabs.ur5e_connector.ui.service import PanelService

ROBOT = 'io.unitelabs/robot/RobotController/v1'


async def test_continuous_sila_dance_and_reset(sila_client, tmp_path):
    robot = await sila_client.load_feature(ROBOT)
    checks = json.loads((await robot.commands['MotionPreflight'].execute({}))['Report'])
    assert checks['ready'] and checks['simulation']
    assert checks['continuous_enabled']
    plan = json.loads((await robot.commands['PrepareContinuousDance'].execute({'Speed':20,'Duration':30,'ExpectedSimulationMode':True}))['Plan'])
    assert plan['kind']=='continuous_dance'
    result = json.loads((await robot.commands['RunContinuousMotion'].execute({'PlanId':plan['id'],'RequestId':'dance-sila','ExpectedSimulationMode':True,'ExpiresAtUnixSeconds':time.time()+20,'RemoteControlConfirmed':False}))['Receipt'])
    assert result['status']=='simulated'
    assert result['sample_count']==3751
    assert result['after_radians']==pytest.approx(plan['start'])
    assert 'accelerated' in result['wall_clock']
    receipt = json.loads((await robot.commands['InspectMotionRequest'].execute({'RequestId':'dance-sila'}))['Receipt'])
    assert receipt['sample_count']==3751
    with pytest.raises(Exception):
        await robot.commands['RunContinuousMotion'].execute({'PlanId':plan['id'],'RequestId':'dance-sila','ExpectedSimulationMode':True,'ExpiresAtUnixSeconds':time.time()+20,'RemoteControlConfirmed':False})
    # Move away from the reference through SiLA, then explicitly preview and execute reset.
    await robot.commands['MoveJointAtSpeed'].execute({'Joint':'Base','DeltaDegrees':5,'SpeedDegreesPerSecond':10,'RequestId':'move-away','ExpectedSimulationMode':True,'ExpiresAtUnixSeconds':time.time()+20})
    reset = json.loads((await robot.commands['PrepareReset'].execute({'CheckpointId':plan['checkpoint_id'],'Speed':10,'Duration':30,'ExpectedSimulationMode':True}))['Plan'])
    assert reset['kind']=='reset'
    assert abs(reset['start'][0]-reset['target'][0])==pytest.approx(math.radians(5))
    assert (await robot.properties['JointPositions'].read())[0]==pytest.approx(reset['start'][0])
    await robot.commands['RunContinuousMotion'].execute({'PlanId':reset['id'],'RequestId':'reset-sila','ExpectedSimulationMode':True,'ExpiresAtUnixSeconds':time.time()+20,'RemoteControlConfirmed':False})
    assert await robot.properties['JointPositions'].read()==pytest.approx(plan['start'])


async def test_panel_continuous(sila_client,tmp_path):
    port=sila_client.config.port
    panel=PanelService(tmp_path/'panel',port,port)
    await panel.open()
    try:
        assert (await panel.check_motion())['ready']
        plan=await panel.preview_continuous({'speed':20,'duration':30})
        assert plan['kind']=='continuous_dance'
        job=await panel.start_continuous({'plan_id':plan['id'],'run_id':'panel-flow'})
        await asyncio.wait_for(panel.task,15)
        assert panel.jobs[job['id']]['status']=='completed'
        assert panel.jobs[job['id']]['receipt']['status']=='simulated'
        reset=await panel.preview_continuous({'reset':True,'speed':10,'duration':30})
        assert reset['kind']=='reset'
    finally:
        await panel.close()


async def test_low_speed_sila_preset(sila_client,tmp_path):
    robot=await sila_client.load_feature(ROBOT)
    plan=json.loads((await robot.commands['PrepareLowSpeedDance'].execute({'Duration':30,'ExpectedSimulationMode':True}))['Plan'])
    assert plan['speed']==3
    assert plan['amplitude_scale']==.25
    assert plan['peak_speed']<=3
    result=json.loads((await robot.commands['RunContinuousMotion'].execute({'PlanId':plan['id'],'RequestId':'low-speed-sim','ExpectedSimulationMode':True,'ExpiresAtUnixSeconds':time.time()+20,'RemoteControlConfirmed':False}))['Receipt'])
    assert result['status']=='simulated'
    assert result['after_radians']==pytest.approx(plan['start'])
    panel=PanelService(tmp_path/'low-speed-panel',sila_client.config.port,sila_client.config.port)
    await panel.open()
    try:
        ui_plan=await panel.preview_continuous({'low_speed':True,'duration':30,'speed':20})
        assert ui_plan['speed']==3
        assert ui_plan['amplitude_scale']==.25
    finally:await panel.close()
