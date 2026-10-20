import asyncio
import json
import math

import pytest

from unitelabs.ur5e_connector.io import joint_step as base_step
from unitelabs.ur5e_connector.io.errors import CommandRejected, DeviceUnavailable, OutcomeUnknown
from unitelabs.ur5e_connector.io.rtde import JointSample

START = (-4.69486, -0.2287, -2.3122, 3.6121, 3.0807, 5.2667)


def sample(t, q=START, *, safety=1, runtime=1, speed=0):
    return JointSample(t, q, (speed, 0, 0, 0, 0, 0), 7, safety, runtime)


@pytest.fixture
def motion_fakes(monkeypatch):
    target, _ = base_step.build_base_step(START)
    state = {"frames": [sample(0), sample(0.3), sample(0.6), sample(0.65),
                        sample(0.7, (START[0]+0.02,*START[1:]), runtime=2, speed=0.01),
                        sample(1.0,target), sample(1.3,target), sample(1.6,target)],
             "scripts": [], "commands": []}

    class Monitor:
        async def open(self): pass
        async def close(self): pass
        async def sample(self):
            frame = state["frames"].pop(0)
            if isinstance(frame, Exception): raise frame
            return frame

    class Dashboard:
        async def open(self): pass
        async def close(self): pass
        async def execute(self, cmd):
            state["commands"].append(cmd)
            return {"PolyscopeVersion":"URSoftware 5.4.2.76197", "programState":"STOPPED", "safetystatus":"Safetymode: NORMAL", "stop":"Stopped"}[cmd]

    class Writer:
        def write(self, script): state["scripts"].append(script.decode())
        async def drain(self): pass
        def close(self): pass

    async def connect(host, port):
        assert host == "fake" and port == 30002
        return None, Writer()

    monkeypatch.setattr(base_step, "JointMonitor", lambda host: Monitor())
    monkeypatch.setattr(base_step, "DashboardConnection", lambda host: Dashboard())
    monkeypatch.setattr(asyncio, "open_connection", connect)
    return state


def test_script_only_changes_base_and_has_no_blend():
    target, script = base_step.build_base_step(START)
    assert target[0]-START[0] == pytest.approx(math.radians(5))
    assert target[1:] == START[1:]
    assert script.count("movej(") == 1
    assert "v=0.017453292520" in script
    assert "r=0" in script
    assert script.count("valid = False") == 6
    assert script.count("norm(actual[") == 6
    assert "abs(" not in script


@pytest.mark.parametrize("start", [(float('nan'),0,0,0,0,0), (6.27,0,0,0,0,0), (0,0)])
def test_invalid_start_rejected(start):
    with pytest.raises(CommandRejected): base_step.build_base_step(start)


async def test_completion_is_measured_and_receipt_prevents_replay(tmp_path, motion_fakes):
    path = tmp_path / "motion.json"
    result = json.loads(await base_step.execute_base_step("fake",path))
    assert result["status"] == "verified"
    assert result["actual_delta_degrees"][0] == pytest.approx(5)
    assert len(motion_fakes["scripts"]) == 1
    with pytest.raises(CommandRejected,match="replayed"):
        await base_step.execute_base_step("fake",path)
    assert len(motion_fakes["scripts"]) == 1
    assert json.loads(path.read_text())["status"] == "verified"


async def test_bad_preflight_never_sends_script(tmp_path,motion_fakes):
    motion_fakes["frames"][0] = sample(0,safety=3)
    path = tmp_path / "motion.json"
    with pytest.raises(CommandRejected): await base_step.execute_base_step("fake",path)
    assert motion_fakes["scripts"] == []
    assert json.loads(path.read_text())["status"] == "not_sent"


async def test_lost_monitoring_result_is_unknown_and_not_replayed(tmp_path,motion_fakes):
    motion_fakes["frames"][4] = DeviceUnavailable("stream lost")
    path = tmp_path / "motion.json"
    with pytest.raises(OutcomeUnknown): await base_step.execute_base_step("fake",path)
    assert len(motion_fakes["scripts"]) == 1
    result=json.loads(path.read_text())
    assert result["status"] == "outcome_unknown"
    assert result["stop_response"] == "Stopped"


async def test_post_send_storage_failure_still_stops_and_returns_unknown(tmp_path,motion_fakes,monkeypatch):
    import os
    original=os.fsync
    def fail_after_send(fd):
        if motion_fakes['scripts']:
            raise OSError('disk failure')
        return original(fd)
    monkeypatch.setattr(base_step.os,'fsync',fail_after_send)
    with pytest.raises(OutcomeUnknown,match='disk failure'):
        await base_step.execute_base_step('fake',tmp_path/'motion.json')
    assert len(motion_fakes['scripts'])==1
    assert 'stop' in motion_fakes['commands']


async def test_reconciled_correction_preserves_original_absolute_target(tmp_path,motion_fakes):
    old_start=(START[0]-math.radians(0.01),*START[1:])
    old_target,_=base_step.build_base_step(old_start)
    old=tmp_path/'prior.json'
    old.write_text(json.dumps({'before':{'positions':old_start},'status':'outcome_unknown','stop_response':'Stopped',
                              'samples':[{'runtime_state':1,'positions':old_start}]}))
    result=json.loads(await base_step.execute_base_step('fake',tmp_path/'new.json',old))
    assert result['target_radians']==list(old_target)
    assert result['reconciles_receipt']==str(old)


async def test_reference_with_prior_movement_is_rejected(tmp_path,motion_fakes):
    old=tmp_path/'prior.json'
    moved=(START[0]+0.01,*START[1:])
    old.write_text(json.dumps({'before':{'positions':START},'status':'outcome_unknown','stop_response':'Stopped',
                              'samples':[{'runtime_state':2,'positions':moved}]}))
    with pytest.raises(CommandRejected,match='reconciled'):
        await base_step.execute_base_step('fake',tmp_path/'new.json',old)
    assert motion_fakes['scripts']==[]


def test_elbow_script_holds_five_other_joints_and_checks_start():
    target, script = base_step.build_elbow_step(START)
    assert target[2]-START[2] == pytest.approx(math.radians(5))
    assert all(target[i] == START[i] for i in range(6) if i != 2)
    assert script.count('movej(') == 1
    assert script.count('norm(actual[') == 6
    assert 'codex_elbow_positive_five' in script
    assert 'v=0.017453292520' in script and 'r=0' in script


@pytest.mark.parametrize('angle', [158, -161])
def test_elbow_joint_limit_checked_before_motion(angle):
    start=(*START[:2], math.radians(angle), *START[3:])
    with pytest.raises(CommandRejected, match='joint range'):
        base_step.build_elbow_step(start)


async def test_elbow_verified_once_without_moving_other_joints(tmp_path,motion_fakes):
    target,_=base_step.build_elbow_step(START)
    moving=(*START[:2], START[2]+0.02, *START[3:])
    motion_fakes['frames']=[sample(0),sample(.3),sample(.6),sample(.65),sample(.7,moving,runtime=2),sample(1,target),sample(1.3,target),sample(1.6,target)]
    path=tmp_path/'elbow.json'
    result=json.loads(await base_step.execute_elbow_step('fake',path))
    assert result['status']=='verified'
    assert result['actual_delta_degrees']==pytest.approx([0,0,5,0,0,0])
    assert result['operation']=='Elbow +5 degrees then stop'
    with pytest.raises(CommandRejected, match='replayed'):
        await base_step.execute_elbow_step('fake',path)
    assert len(motion_fakes['scripts'])==1


@pytest.mark.parametrize('joint,delta',[(0,1),(2,6),(2,-1)])
async def test_elbow_monitor_stops_unrequested_or_out_of_interval_motion(tmp_path,motion_fakes,joint,delta):
    q=list(START);q[joint]+=math.radians(delta)
    motion_fakes['frames'][4]=sample(.7,tuple(q),runtime=2)
    path=tmp_path/'elbow.json'
    with pytest.raises(OutcomeUnknown):
        await base_step.execute_elbow_step('fake',path)
    assert len(motion_fakes['scripts'])==1
    assert 'stop' in motion_fakes['commands']
    assert json.loads(path.read_text())['status']=='outcome_unknown'


async def test_base_authorization_does_not_enable_elbow(tmp_path):
    from unitelabs.ur5e_connector.io.protocol import Ur5eProtocol
    protocol=Ur5eProtocol(simulation_mode=False,enable_base_step=True,base_step_receipt=str(tmp_path/'base.json'))
    with pytest.raises(CommandRejected,match='not enabled'):
        await protocol.move_elbow_positive_five_degrees()
    assert not (tmp_path/'base.json').exists()


async def test_unknown_elbow_outcome_blocks_both_joint_commands(tmp_path,monkeypatch):
    from unitelabs.ur5e_connector.io import protocol as module
    async def fail(*args): raise OutcomeUnknown('lost monitoring')
    monkeypatch.setattr(module,'execute_elbow_step',fail)
    protocol=module.Ur5eProtocol(simulation_mode=False,enable_elbow_step=True,elbow_step_receipt=str(tmp_path/'elbow.json'),enable_base_step=True,base_step_receipt=str(tmp_path/'base.json'))
    with pytest.raises(OutcomeUnknown): await protocol.move_elbow_positive_five_degrees()
    assert protocol.control_outcome_unknown
    for command in [protocol.move_elbow_positive_five_degrees,protocol.move_base_positive_five_degrees]:
        with pytest.raises(CommandRejected,match='unknown'):
            await command()


@pytest.mark.parametrize('index',range(6))
@pytest.mark.parametrize('delta',[-10,-.5, .5,10])
def test_general_joint_builder_signed_angles_and_faster_profile(index,delta):
    target,script=base_step.build_joint_test(START,index,delta)
    assert math.degrees(target[index]-START[index])==pytest.approx(delta)
    assert all(target[i]==START[i] for i in range(6) if i!=index)
    assert 'v=0.174532925199' in script
    assert 'a=0.349065850399' in script
    assert script.count('norm(actual[')==6
    assert script.count('movej(')==1 and 'r=0' in script


@pytest.mark.parametrize('joint,delta,request_id',[
    ('bad',5,'ok'),('Wrist1',float('nan'),'ok'),('Wrist1',0,'ok'),('Wrist1',11,'ok'),
    ('Wrist1',-.1,'ok'),('Wrist1',5,'../escape'),('Wrist1',5,''),('Wrist1',True,'ok'),
])
def test_general_invalid_inputs_rejected(joint,delta,request_id):
    with pytest.raises(CommandRejected):base_step.validate_joint_test(joint,delta,request_id)


async def test_general_negative_move_measured_and_duplicate_id_blocked(tmp_path,motion_fakes):
    target,_=base_step.build_joint_test(START,3,-5)
    moving=(*START[:3],START[3]-.02,*START[4:])
    motion_fakes['frames']=[sample(0),sample(.3),sample(.6),sample(.65),sample(.7,moving,runtime=2),sample(1,target),sample(1.3,target),sample(1.6,target)]
    directory=tmp_path/'tests'
    result=json.loads(await base_step.execute_joint_test('fake',directory,'first',3,-5))
    assert result['actual_delta_degrees']==pytest.approx([0,0,0,-5,0,0])
    assert result['max_joint_speed_deg_s']==10
    with pytest.raises(CommandRejected,match='replayed'):
        await base_step.execute_joint_test('fake',directory,'first',3,-5)
    assert len(motion_fakes['scripts'])==1
    assert not (directory/'.active').exists()


@pytest.mark.parametrize('record',[{'status':'sent_monitoring'},{'status':'outcome_unknown'},[],{'status':'preflight'}])
async def test_general_incomplete_receipt_blocks_new_id(tmp_path,motion_fakes,record):
    (tmp_path/'old.json').write_text(json.dumps(record))
    with pytest.raises(CommandRejected,match='unknown'):
        await base_step.execute_joint_test('fake',tmp_path,'new',3,5)
    assert motion_fakes['scripts']==[]


async def test_general_directory_lock_blocks_concurrent_or_crashed_process(tmp_path,motion_fakes):
    (tmp_path/'.active').mkdir()
    with pytest.raises(CommandRejected,match='active or interrupted'):
        await base_step.execute_joint_test('fake',tmp_path,'new',3,5)
    assert motion_fakes['scripts']==[]
    assert (tmp_path/'.active').is_dir()


async def test_general_joint_testing_requires_its_own_authorization(tmp_path):
    from unitelabs.ur5e_connector.io.protocol import Ur5eProtocol
    p=Ur5eProtocol(simulation_mode=False,enable_elbow_step=True,elbow_step_receipt=str(tmp_path/'elbow.json'))
    with pytest.raises(CommandRejected,match='not enabled'):
        await p.move_joint('Wrist1',5,'new')


async def test_expired_authorization_after_slow_preflight_never_sends(tmp_path, motion_fakes, monkeypatch):
    import time
    def slow_records(directory):
        time.sleep(.06)
    monkeypatch.setattr(base_step, '_check_test_receipts', slow_records)
    with pytest.raises(CommandRejected, match='expired'):
        await base_step.execute_joint_test('fake',tmp_path,'expiry-late',0,5,expires_at=time.time()+.02)
    assert motion_fakes['scripts'] == []
    record = json.loads((tmp_path/'expiry-late.json').read_text())
    assert record['status'] == 'not_sent'


async def test_continuous_telemetry_loss_stops_and_records_unknown(tmp_path, monkeypatch):
    from unitelabs.ur5e_connector.io import choreography, motion_program
    state={'scripts':[],'commands':[]}
    plan=choreography.build(list(START),20,30)
    plan.update(id='prepared',slider_fraction=1,dispatch_expires_at=__import__('time').time()+20)
    frames=[sample(0),sample(.3),sample(.6),asyncio.IncompleteReadError(b'',117)]
    class Monitor:
        async def open(self): pass
        async def close(self): pass
        async def sample(self):
            value=frames.pop(0)
            if isinstance(value,Exception):raise value
            return value
    class Dashboard:
        async def open(self):pass
        async def close(self):pass
        async def execute(self,command):
            state['commands'].append(command)
            return 'Stopped' if command=='stop' else 'STOPPED'
    class Writer:
        def write(self,program):state['scripts'].append(program)
        async def drain(self):pass
        def close(self):pass
    async def connect(host,port):return None,Writer()
    monkeypatch.setattr(motion_program,'JointMonitor',lambda host:Monitor())
    monkeypatch.setattr(motion_program,'DashboardConnection',lambda host:Dashboard())
    monkeypatch.setattr(asyncio,'open_connection',connect)
    with pytest.raises(OutcomeUnknown):
        await motion_program.execute('fake',tmp_path,'loss',plan)
    assert len(state['scripts'])==1
    assert 'stop' in state['commands']
    record=json.loads((tmp_path/'loss.json').read_text())
    assert record['status']=='outcome_unknown'
    assert record['stop_response']=='Stopped'
