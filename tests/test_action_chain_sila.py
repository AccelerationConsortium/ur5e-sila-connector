import asyncio
import json
import math
import time

import pytest
from unitelabs.ur5e_connector.ui.service import PanelService

ROBOT = 'io.unitelabs/robot/RobotController/v1'


async def test_action_chain_through_public_sila_and_panel(sila_client, tmp_path):
    robot = await sila_client.load_feature(ROBOT)
    start = await robot.properties['JointPositions'].read()
    origin = [math.degrees(v) for v in start]
    points = [{'name': f'Pose {i}', 'degrees': [v+i*3 for v in origin], 'duration': 5, 'hold': 0} for i in (1,2)]
    plan = json.loads((await robot.commands['PrepareActionChain'].execute({
        'WaypointsJson': json.dumps(points), 'Speed': 10, 'ExpectedSimulationMode': True}))['Plan'])
    assert plan['kind'] == 'action_chain'
    assert await robot.properties['JointPositions'].read() == start  # preparation is read-only
    args = {'PlanId': plan['id'], 'RequestId': 'chain-sila', 'ExpectedSimulationMode': True,
            'ExpiresAtUnixSeconds': time.time()+20, 'RemoteControlConfirmed': False}
    receipt = json.loads((await robot.commands['RunContinuousMotion'].execute(args))['Receipt'])
    assert receipt['status'] == 'simulated'
    assert receipt['sample_count'] == 1251
    assert receipt['after_radians'] == pytest.approx([math.radians(v) for v in points[-1]['degrees']])
    with pytest.raises(Exception):
        await robot.commands['RunContinuousMotion'].execute(args)
    panel = PanelService(tmp_path/'panel', sila_client.config.port, sila_client.config.port)
    await panel.open()
    try:
        check = await panel.check_motion()
        assert check['action_chains_enabled']
        plan = await panel.preview_action_chain({'speed': 10, 'waypoints': [
            {'name': 'Return', 'degrees': origin, 'duration': 8, 'hold': 0}]})
        job = await panel.start_continuous({'plan_id': plan['id'], 'run_id': 'chain-panel'})
        await asyncio.wait_for(panel.task, 15)
        assert panel.jobs[job['id']]['status'] == 'completed'
        assert panel.jobs[job['id']]['receipt']['status'] == 'simulated'
        assert await robot.properties['JointPositions'].read() == pytest.approx(start)
    finally:
        await panel.close()


async def test_action_preview_preserves_dance_reference(sila_client, tmp_path):
    panel = PanelService(tmp_path/'mixed', sila_client.config.port, sila_client.config.port)
    await panel.open()
    try:
        dance = await panel.preview_continuous({'speed':20, 'duration':30})
        origin = [math.degrees(v) for v in dance['start']]
        chain = await panel.preview_action_chain({'speed':10, 'waypoints':[
            {'name':'Offset', 'degrees':[v+3 for v in origin], 'duration':5, 'hold':0}]})
        assert chain['kind'] == 'action_chain'
        reset = await panel.preview_continuous({'reset':True, 'speed':10, 'duration':30})
        assert reset['checkpoint_id'] == dance['checkpoint_id']
        assert reset['target'] == pytest.approx(dance['start'])
    finally:
        await panel.close()


async def test_timing_advice_through_sila_does_not_move_or_edit(sila_client, tmp_path):
    robot = await sila_client.load_feature(ROBOT)
    before = await robot.properties['JointPositions'].read()
    points = [{'degrees':[180, -86, -86, 0, 86, 0], 'duration':6, 'hold':0}]
    panel = PanelService(tmp_path/'advice', sila_client.config.port, sila_client.config.port)
    await panel.open()
    try:
        with pytest.raises(Exception) as rejected:
            await panel.preview_action_chain({'speed':30,'waypoints':points})
        assert 'Action requires' in str(rejected.value)
        assert 'TimingRejected' not in str(rejected.value)
        advice = await panel.suggest_action_chain_timing({'speed':30,'waypoints':points})
        assert advice['status'] == 'timing_rejected'
        assert advice['timing_suggestion']['peak_speed'] <= 30
        assert points[0]['duration'] == 6
        assert panel.continuous_plan is None
        assert await robot.properties['JointPositions'].read() == before
    finally:
        await panel.close()


async def test_speed_only_chain_via_public_sila_and_panel(sila_client, tmp_path):
    robot = await sila_client.load_feature(ROBOT)
    start = await robot.properties['JointPositions'].read()
    original = [math.degrees(v) for v in start]
    points = [{'name':'Turn', 'degrees':[original[0]+90,*original[1:]], 'hold':0},
              {'name':'Return', 'degrees':original, 'hold':.5}]
    plan = json.loads((await robot.commands['PrepareActionChainAtSpeed'].execute({
        'WaypointsJson':json.dumps(points),'Speed':30,'ExpectedSimulationMode':True}))['Plan'])
    assert plan['timing_mode'] == 'automatic' and plan['peak_speed'] <= 30
    assert await robot.properties['JointPositions'].read() == start
    panel = PanelService(tmp_path/'speed-only', sila_client.config.port, sila_client.config.port)
    await panel.open()
    try:
        plan = await panel.preview_action_chain({'speed':30,'timing_mode':'automatic','waypoints':points})
        assert plan['timing_mode'] == 'automatic'
        assert plan['waypoints'][0]['duration'] > 1
        assert [r['degrees'] for r in plan['waypoints']] == [r['degrees'] for r in points]
        job = await panel.start_continuous({'plan_id':plan['id'],'run_id':'speed-only-sim'})
        await asyncio.wait_for(panel.task,15)
        assert panel.jobs[job['id']]['status'] == 'completed'
        assert await robot.properties['JointPositions'].read() == pytest.approx(start)
    finally:
        await panel.close()
