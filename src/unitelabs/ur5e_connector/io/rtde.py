import asyncio
import contextlib
import dataclasses
import math
import struct

from .errors import DeviceUnavailable


@dataclasses.dataclass(frozen=True)
class JointSample:
    """Represent one controller-timestamped RTDE observation in radians."""

    timestamp: float
    positions: tuple[float, ...]
    velocities: tuple[float, ...]
    robot_mode: int
    safety_mode: int
    runtime_state: int


class JointMonitor:
    """Read joint state using an output-only RTDE v2 recipe."""

    def __init__(self, host: str, port: int = 30004, timeout: float = 3.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.recipe = 0

    async def open(self) -> None:
        """Negotiate RTDE outputs; no input registers or speed settings are written."""
        await self._open_outputs(
            b"timestamp,actual_q,actual_qd,robot_mode,safety_mode,runtime_state",
            b"DOUBLE,VECTOR6D,VECTOR6D,INT32,INT32,UINT32",
        )

    async def _open_outputs(self, names: bytes, types: bytes) -> None:
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout
            )
            await self._send(86, struct.pack(">H", 2))
            if await self._response(86) != b"\x01":
                msg = "RTDE protocol version 2 was rejected."
                raise DeviceUnavailable(msg)
            await self._send(79, struct.pack(">d", 20.0) + names)
            recipe = await self._response(79)
            if len(recipe) < 2 or recipe[0] == 0 or recipe[1:] != types:
                msg = f"Unexpected RTDE recipe: {recipe!r}"
                raise DeviceUnavailable(msg)
            self.recipe = recipe[0]
            await self._send(83, b"")
            if await self._response(83) != b"\x01":
                msg = "RTDE stream start was rejected."
                raise DeviceUnavailable(msg)
        except (OSError, TimeoutError, asyncio.IncompleteReadError, DeviceUnavailable, asyncio.CancelledError):
            await self.close()
            raise

    async def close(self) -> None:
        """Close the output-only stream."""
        writer, self.writer = self.writer, None
        self.reader = None
        if writer is not None:
            writer.close()
            with contextlib.suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), self.timeout)

    async def sample(self) -> JointSample:
        """Read the next RTDE sample, rejecting invalid or non-finite joint data."""
        payload = await self._response(85)
        if len(payload) != 117 or payload[0] != self.recipe:
            msg = "Unexpected RTDE data length or recipe identifier."
            raise DeviceUnavailable(msg)
        values = struct.unpack(">13diiI", payload[1:])
        if not all(math.isfinite(v) for v in values[:13]):
            msg = "RTDE returned non-finite joint data."
            raise DeviceUnavailable(msg)
        return JointSample(values[0], tuple(values[1:7]), tuple(values[7:13]), *values[13:])

    async def _send(self, kind: int, payload: bytes) -> None:
        if self.writer is None:
            msg = "RTDE socket is closed."
            raise DeviceUnavailable(msg)
        self.writer.write(struct.pack(">HB", len(payload) + 3, kind) + payload)
        await asyncio.wait_for(self.writer.drain(), self.timeout)

    async def _response(self, expected: int) -> bytes:
        async def receive() -> bytes:
            if self.reader is None:
                msg = "RTDE socket is closed."
                raise DeviceUnavailable(msg)
            for _ in range(40):
                size, kind = struct.unpack(">HB", await self.reader.readexactly(3))
                if not 3 <= size <= 8192:
                    msg = "Invalid RTDE packet size."
                    raise DeviceUnavailable(msg)
                payload = await self.reader.readexactly(size - 3)
                if kind == expected:
                    return payload
            msg = "RTDE did not return the requested packet type."
            raise DeviceUnavailable(msg)

        return await asyncio.wait_for(receive(), self.timeout)


class ToolMonitor(JointMonitor):
    """Read TCP pose and the pendant's target speed fraction without writing inputs."""

    async def open(self) -> None:
        """Negotiate the tool and pendant-speed output recipe."""
        await self._open_outputs(b"actual_TCP_pose,target_speed_fraction", b"VECTOR6D,DOUBLE")

    async def read_tool_state(self) -> tuple[list[float], float]:
        """Return TCP pose in metres/radians and the pendant fraction between zero and one."""
        payload = await self._response(85)
        if len(payload) != 57 or payload[0] != self.recipe:
            msg = "Unexpected tool telemetry packet."
            raise DeviceUnavailable(msg)
        values = struct.unpack(">7d", payload[1:])
        if not all(math.isfinite(v) for v in values) or not 0 <= values[6] <= 1:
            msg = "Invalid tool telemetry values."
            raise DeviceUnavailable(msg)
        return list(values[:6]), values[6]
