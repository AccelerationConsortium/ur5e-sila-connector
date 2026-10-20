import json
import math
import time
import pytest
from unitelabs.ur5e_connector.io import choreography
from unitelabs.ur5e_connector.io.protocol import Ur5eProtocol
from unitelabs.ur5e_connector.io.errors import CommandRejected

START = [-3.71,-.90,-1.51,4.66,4.08,6.03]


def test_continuous_joint_trajectory():
    plan = choreography.build(START,20,60)
    assert plan['target'] == pytest.approx(START, abs=1e-10)
    assert len(plan['samples']) == 7501
    assert plan['peak_speed'] < 20
    assert plan['peak_acceleration'] < 40
    # Joint targets evolve simultaneously, rather than a list of one-axis stopped moves.
    active = 0
    for a,b in zip(plan['samples'][500:-500],plan['samples'][501:-499]):
        if sum(abs(q-r) > 1e-7 for q,r in zip(a,b)) >= 3:
            active += 1
    assert active > 6000
    program = choreography.script(plan)
    assert program.count('servoj(') == 1
    assert 'while i <=' in program
    assert 'movej(' not in program
    assert program.count('stopj(') == 1
    assert '0.008,0.1,200' in program


@pytest.mark.parametrize('start',[[math.radians(359),0,0,0,0,0],[0,0,math.radians(159),0,0,0],[0]*5,[float('nan')]*6])
def test_reject_joint_limits(start):
    with pytest.raises(CommandRejected):
        choreography.build(start,20,60)


def test_reject_excursion_instead_of_wrapping():
    with pytest.raises(CommandRejected):
        choreography.build([0,0,0,0,0,math.radians(-350)],20,60)


def test_velocity_and_reset_limits():
    with pytest.raises(CommandRejected):
        choreography.build(START,1,20)
    target = START.copy(); target[0] += math.radians(20); target[2]-=math.radians(10)
    plan = choreography.build(START,20,30,target)
    assert plan['kind'] == 'reset'
    assert plan['samples'][0] == START
    assert plan['target'] == pytest.approx(target)
    assert 'b = 10*pow(u,3)' in choreography.script(plan)
    target[0]+=math.radians(100)
    with pytest.raises(CommandRejected):
        choreography.build(START,20,60,target)


async def test_protocol_expiry_and_mode_binding():
    protocol = Ur5eProtocol()
    await protocol.open()
    plan = json.loads(await protocol.prepare_motion(20,30,True))
    with pytest.raises(CommandRejected,match='expired'):
        await protocol.execute_motion(plan['id'],'expired',True,time.time()-1,False)
    with pytest.raises(CommandRejected,match='mode'):
        await protocol.execute_motion(plan['id'],'wrongmode',False,time.time()+20,False)
    assert protocol.device.joint_positions == tuple(plan['start'])
    await protocol.close()


async def test_real_motion_disabled_before_network(monkeypatch):
    protocol = Ur5eProtocol(simulation_mode=False)
    plan = choreography.build(START,20,60)
    plan.update(id='real-disabled',expires=time.time()+180,simulation=False)
    protocol.motion_plans[plan['id']]=plan
    with pytest.raises(CommandRejected,match='disabled'):
        await protocol.execute_motion(plan['id'],'no-send',False,time.time()+20,True)


@pytest.mark.parametrize('expiry',[float('nan'),float('inf'),True])
async def test_nonfinite_expiry_rejected(expiry):
    protocol=Ur5eProtocol()
    with pytest.raises(CommandRejected,match='finite'):
        await protocol.move_joint_at_speed('Base',5,10,'bad-expiry',True,expiry)
    with pytest.raises(CommandRejected,match='finite'):
        await protocol.execute_motion('plan','bad-expiry',True,expiry,False)


def test_low_speed_quarter_amplitude_matches_generated_script():
    normal=choreography.build(START,20,30)
    low=choreography.build(START,3,30,amplitude_scale=.25)
    assert low['peak_speed'] == pytest.approx(normal['peak_speed']*.25)
    assert low['peak_speed'] <= 3
    assert low['peak_acceleration'] <= 6
    assert low['target']==pytest.approx(START)
    assert '*0.250000*' in choreography.script(low)
    for normal_q,low_q in zip(normal['samples'][::100],low['samples'][::100]):
        assert low_q == pytest.approx([start+(q-start)*.25 for start,q in zip(START,normal_q)])
