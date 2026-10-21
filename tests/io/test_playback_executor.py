import json
import time
import pytest
from unitelabs.ur5e_connector.io import motion_program, action_chain
from unitelabs.ur5e_connector.io.rtde import JointSample
from unitelabs.ur5e_connector.io.errors import OutcomeUnknown


@pytest.mark.parametrize("ack", ["Stopped", "Failed to execute stop"])
async def test_stop_uses_run_owned_dashboard_and_requires_stationary_ack(monkeypatch, tmp_path, ack):
    q = [0, -1.5, -1.5, 0, 1.5, 0]
    plan = action_chain.build(
        q, [{"name": "Move", "degrees": [5, -85.94, -85.94, 0, 85.94, 0], "duration": 3, "hold": 0}], 10
    )
    plan.update(id="prepared", dispatch_expires_at=time.time() + 20, slider_fraction=1)
    control = {"stop": False, "pause": False}
    commands = []

    class Monitor:
        def __init__(self, host):
            self.i = 0

        async def open(self):
            pass

        async def close(self):
            pass

        async def sample(self):
            self.i += 1
            return JointSample(self.i * 0.3, tuple(q), (0,) * 6, 7, 1, 1)

    class Dashboard:
        def __init__(self, host):
            pass

        async def open(self):
            pass

        async def close(self):
            pass

        async def execute(self, command):
            commands.append(command)
            return ack if command == "stop" else "STOPPED <unnamed>"

    class Writer:
        def write(self, value):
            assert b"servoj" in value

        async def drain(self):
            control["stop"] = True

        def close(self):
            pass

    async def connect(*args):
        return None, Writer()

    monkeypatch.setattr(motion_program, "JointMonitor", Monitor)
    monkeypatch.setattr(motion_program, "DashboardConnection", Dashboard)
    monkeypatch.setattr(motion_program.asyncio, "open_connection", connect)
    reports = []
    if ack == "Stopped":
        receipt = json.loads(await motion_program.execute("fake", tmp_path, "owned", plan, control, reports.append))
        assert receipt["status"] == "verified" and receipt["completion"] == "stopped"
        assert receipt["after"]["positions"] == q
        assert reports[-1]["controller_state"] == "stopped"
        assert commands.count("stop") == 1
        assert (tmp_path / "owned.trajectory.jsonl").read_text()
    else:
        with pytest.raises(OutcomeUnknown):
            await motion_program.execute("fake", tmp_path, "owned", plan, control, reports.append)
        assert json.loads((tmp_path / "owned.json").read_text())["status"] == "outcome_unknown"
    assert not (tmp_path / ".active").exists()
