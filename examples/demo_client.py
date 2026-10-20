"""Exercise the simulated robot through a real SiLA network connection."""

import argparse
import asyncio
import json
from pathlib import Path

from sila.client import Client, ClientConfig

ROBOT = "io.unitelabs/robot/RobotController/v1"
SIMULATION = "org.silastandard/core/SimulationController/v1"


async def exercise(host: str, port: int) -> dict:
    client = Client(ClientConfig(hostname=host, port=port, tls=False, default_timeout=5000))
    try:
        skipped = await client.open()
        if skipped:
            raise RuntimeError(f"Features could not be loaded: {skipped}")
        simulation = await client.load_feature(SIMULATION)
        if await simulation.properties["SimulationMode"].read() is not True:
            raise RuntimeError("Demo requires simulation mode.")
        robot = await client.load_feature(ROBOT)
        trace = []

        async def snapshot(label: str) -> None:
            trace.append({
                "step": label,
                "program_state": await robot.properties["ProgramState"].read(),
                "running": await robot.properties["Running"].read(),
                "loaded_program": await robot.properties["LoadedProgram"].read(),
            })

        await robot.commands["Stop"].execute({})
        await snapshot("stopped")
        await robot.commands["LoadProgram"].execute({"Program": "demo.urp"})
        await snapshot("loaded")
        await robot.commands["Play"].execute({})
        await snapshot("playing")
        await robot.commands["Pause"].execute({})
        await snapshot("paused")
        await robot.commands["Play"].execute({})
        await snapshot("resumed")
        await robot.commands["Stop"].execute({})
        await snapshot("stopped_final")
        response = await robot.commands["SendCommand"].execute({"Command": "robotmode"})
        return {
            "server": await client.get_server_name(),
            "simulation_mode": True,
            "endpoint": f"{host}:{port}",
            "trace": trace,
            "send_command": dict(response),
            "evidence_boundary": "Real SiLA network calls to an in-process simulated device; no robot connected.",
        }
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50052)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(asyncio.wait_for(exercise(args.host, args.port), timeout=30))
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n")


if __name__ == "__main__":
    main()
