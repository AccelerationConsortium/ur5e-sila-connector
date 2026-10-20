import asyncio
import contextlib
import json
import os
from pathlib import Path
import signal
import socket
import sys

import pytest
from sila.client import Client, ClientConfig

from unitelabs.ur5e_connector.io.errors import CommandRejected
from unitelabs.ur5e_connector.io.simulation import SimulatedDashboard


@pytest.fixture
async def sila_client(tmp_path, request, dashboard):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = tmp_path / "local.json"
    overrides = {}
    if getattr(request, "param", None) in {"real", "control"}:
        overrides = {"simulation_mode": False, "robot_host": dashboard[0], "dashboard_port": dashboard[1]}
    if getattr(request, "param", None) == "control":
        overrides.update(enable_program_control=True, approved_program="/programs/test.urp")
    config.write_text(json.dumps({
        "simulation_mode": True,
        "sila_server": {"hostname": "127.0.0.1", "port": port, "tls": False},
        "cloud_server_endpoint": [], "discovery": None,
        **overrides,
    }))
    with (tmp_path / "server.log").open("w+") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "unitelabs.ur5e_connector", "--config-path", str(config),
            stdout=log, stderr=log,
            env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
        )
        client = Client(ClientConfig("127.0.0.1", port, tls=False, default_timeout=5000))
        try:
            skipped = await asyncio.wait_for(client.open(), timeout=15)
            assert skipped == []
            yield client
        finally:
            await client.close()
            if process.returncode is None:
                process.send_signal(signal.SIGINT)
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            log.seek(0)
            output = log.read()
            assert process.returncode == 0, output
            assert "Duplicate handler" not in output, output
            assert "UserWarning" not in output, output


@pytest.fixture
async def dashboard():
    device = SimulatedDashboard()
    requests = []
    behavior = {}
    writers = set()
    tasks = set()

    async def handle(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        writers.add(writer)
        try:
            writer.write(b"Connected: Universal Robots Dashboard Server\n")
            await writer.drain()
            while data := await reader.readline():
                cmd = data.decode().strip()
                requests.append(cmd)
                if behavior.get("drop") == cmd:
                    # Apply a mutation but lose its response, as on a real disconnected socket.
                    device.execute(cmd)
                    return
                if behavior.get("delay") == cmd:
                    await asyncio.sleep(0.3)
                if cmd in behavior:
                    response = behavior[cmd]
                elif cmd == "PolyscopeVersion":
                    response = "URSoftware 5.4.2.76197 (Jul 10 2019)"
                else:
                    try:
                        response = device.execute(cmd)
                    except CommandRejected:
                        response = "Failed to execute: " + cmd
                writer.write((response + "\n").encode())
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writers.discard(writer)
            writer.close()
            tasks.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()
    try:
        yield host, port, device, requests, behavior
    finally:
        server.close()
        await server.wait_closed()
        for writer in list(writers):
            writer.close()
        for task in list(tasks):
            task.cancel()
        await asyncio.wait_for(asyncio.gather(*list(tasks), return_exceptions=True), timeout=3)
