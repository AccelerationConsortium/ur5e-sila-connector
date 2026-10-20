import asyncio
import struct

import pytest

from unitelabs.ur5e_connector.io.rtde import JointMonitor
from unitelabs.ur5e_connector.io.errors import DeviceUnavailable


async def test_rtde_output_handshake_and_joint_sample():
    requests=[]
    async def handle(reader,writer):
        try:
            for expected, response in [
                (86,b'\x01'),
                (79,b'\x07DOUBLE,VECTOR6D,VECTOR6D,INT32,INT32,UINT32'),
                (83,b'\x01'),
            ]:
                size, kind=struct.unpack('>HB',await reader.readexactly(3))
                payload=await reader.readexactly(size-3)
                requests.append((kind,payload))
                assert kind==expected
                writer.write(struct.pack('>HB',len(response)+3,kind)+response)
                await writer.drain()
            payload=b'\x07'+struct.pack('>13diiI',1,*range(6),*([0]*6),7,1,1)
            writer.write(struct.pack('>HB',len(payload)+3,85)+payload)
            await writer.drain()
            await reader.read()
        finally:
            writer.close()
    server=await asyncio.start_server(handle,'127.0.0.1',0)
    monitor=JointMonitor('127.0.0.1',server.sockets[0].getsockname()[1])
    try:
        await monitor.open()
        data=await monitor.sample()
        assert data.positions==(0,1,2,3,4,5)
        assert data.robot_mode==7 and data.safety_mode==1 and data.runtime_state==1
        assert [kind for kind,_ in requests]==[86,79,83]
    finally:
        await monitor.close();server.close();await server.wait_closed()


async def test_rtde_rejects_invalid_frame():
    monitor=JointMonitor('unused')
    monitor.reader=asyncio.StreamReader()
    monitor.reader.feed_data(struct.pack('>HB',2,85))
    with pytest.raises(DeviceUnavailable,match='size'):
        await monitor.sample()
