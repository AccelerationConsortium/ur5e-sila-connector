"""Read robot identity and state through SiLA without sending any control commands."""

import argparse
import asyncio
import datetime
import json
from pathlib import Path

from sila.client import Client, ClientConfig


async def read_status(host: str, port: int) -> dict:
    client = Client(ClientConfig(host, port, tls=False, default_timeout=5000))
    try:
        skipped = await client.open()
        if skipped:
            raise RuntimeError(f"Feature loading failed: {skipped}")
        simulation = await client.load_feature("org.silastandard/core/SimulationController/v1")
        robot = await client.load_feature("io.unitelabs/robot/RobotController/v1")
        report = {
            "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "server": await client.get_server_name(),
            "sila_endpoint": f"{host}:{port}",
            "simulation_mode": await simulation.properties["SimulationMode"].read(),
        }
        for name in ("SoftwareVersion", "JointPositions", "RobotMode", "SafetyStatus", "ProgramState", "LoadedProgram", "Running", "ControlOutcomeUnknown"):
            report[name] = await robot.properties[name].read()
        report["evidence_boundary"] = "SiLA read-only queries. No load, play, pause, stop, power or motion commands sent."
        return report
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50053)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(asyncio.wait_for(read_status(args.host, args.port), timeout=30))
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n")


if __name__ == "__main__":
    main()
