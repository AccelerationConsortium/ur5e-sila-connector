import asyncio
import contextlib

from .errors import DeviceUnavailable


class DashboardConnection:
    """Exchange newline-framed Dashboard messages without retrying requests."""

    def __init__(self, host: str, port: int = 29999, timeout: float = 3.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def open(self) -> None:
        """Connect and validate the controller greeting without sending commands."""
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port, limit=8192), self.timeout
            )
            greeting = await self._read_line()
            if greeting != "Connected: Universal Robots Dashboard Server":
                msg = f"Unexpected Dashboard greeting: {greeting}"
                raise DeviceUnavailable(msg)
        except (OSError, TimeoutError, ValueError, DeviceUnavailable) as error:
            await self.close()
            raise DeviceUnavailable(str(error)) from error
        except asyncio.CancelledError:
            await self.close()
            raise

    async def close(self) -> None:
        """Release the socket with a bounded shutdown wait."""
        writer, self.writer = self.writer, None
        self.reader = None
        if writer is not None:
            writer.close()
            with contextlib.suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), self.timeout)

    async def execute(self, command: str) -> str:
        """Send one request and read one response; close on any transport failure."""
        if self.reader is None or self.writer is None:
            msg = "Dashboard connection is closed."
            raise DeviceUnavailable(msg)
        try:
            self.writer.write((command + "\n").encode("utf-8"))
            await asyncio.wait_for(self.writer.drain(), self.timeout)
            return await self._read_line()
        except (OSError, TimeoutError, ValueError, UnicodeError, DeviceUnavailable) as error:
            await self.close()
            raise DeviceUnavailable(str(error)) from error
        except asyncio.CancelledError:
            await self.close()
            raise

    async def _read_line(self) -> str:
        if self.reader is None:
            msg = "Dashboard connection is closed."
            raise DeviceUnavailable(msg)
        data = await asyncio.wait_for(self.reader.readline(), self.timeout)
        if not data.endswith(b"\n"):
            msg = "Dashboard closed the connection before a complete response."
            raise DeviceUnavailable(msg)
        return data.decode("utf-8").rstrip("\r\n")
