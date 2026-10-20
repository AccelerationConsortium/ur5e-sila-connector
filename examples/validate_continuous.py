"""Validate continuous dance and explicit reset through a simulation SiLA server only."""
import asyncio
import datetime
import json
import math
import pathlib
import time
import uuid
from sila.client import Client, ClientConfig

async def main():
    client = Client(ClientConfig('127.0.0.1',50052,tls=False,default_timeout=15000))
    await client.open()
    try:
        robot = await client.load_feature('io.unitelabs/robot/RobotController/v1')
        check = json.loads((await robot.commands['MotionPreflight'].execute({}))['Report'])
        assert check['simulation'] is True and check['ready'], 'Simulation preflight required; refusing all real movement.'
        start = await robot.properties['JointPositions'].read()
        plan = json.loads((await robot.commands['PrepareContinuousDance'].execute({'Speed':20,'Duration':60,'ExpectedSimulationMode':True}))['Plan'])
        async def run(plan):
            return json.loads((await robot.commands['RunContinuousMotion'].execute({'PlanId':plan['id'],'RequestId':str(uuid.uuid4()),'ExpectedSimulationMode':True,'ExpiresAtUnixSeconds':time.time()+20,'RemoteControlConfirmed':False}))['Receipt'])
        dance = await run(plan)
        await robot.commands['MoveJointAtSpeed'].execute({'Joint':'Base','DeltaDegrees':5,'SpeedDegreesPerSecond':10,'RequestId':str(uuid.uuid4()),'ExpectedSimulationMode':True,'ExpiresAtUnixSeconds':time.time()+20})
        reset_plan = json.loads((await robot.commands['PrepareReset'].execute({'CheckpointId':plan['checkpoint_id'],'Speed':10,'Duration':30,'ExpectedSimulationMode':True}))['Plan'])
        reset = await run(reset_plan)
        final = await robot.properties['JointPositions'].read()
        assert dance['status']==reset['status']=='simulated'
        assert max(abs(a-b) for a,b in zip(final,start))<1e-9
        evidence={'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'passed':True,'evidence_scope':'SiLA accelerated kinematic simulation; no real movement, collision or physics acceptance','preflight':check,'dance_plan':plan,'dance_receipt':dance,'reset_plan':reset_plan,'reset_receipt':reset,'final_degrees':[math.degrees(q) for q in final]}
        path=pathlib.Path('continuous-motion-simulation.json')
        path.write_text(json.dumps(evidence,indent=2)+'\n')
        print(f'PASS: {dance["sample_count"]} continuous setpoints, explicit reset verified. Evidence: {path.resolve()}')
    finally:
        await client.close()

if __name__=='__main__':asyncio.run(main())
