import asyncio
import math
import pytest
from unitelabs.ur5e_connector.ui.service import PanelService, PanelError


async def wait_status(panel, expected, timeout=10):
    async def wait():
        while True:
            job = next(iter(reversed(panel.jobs.values())))
            if job["status"] in expected:
                return job
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(wait(), timeout)


async def test_complete_task_teaching_library_episode_through_sila(sila_client, tmp_path):
    panel = PanelService(tmp_path / "panel", sila_client.config.port, sila_client.config.port)
    await panel.open()
    try:
        state = await panel.status(force=True)
        seed = state["JointPositions"]
        report = await panel.kinematics({"operation": "forward", "seed": seed, "tcp": [0] * 6})
        target = report["tcp_pose"]
        target[2] -= 0.02
        generated = await panel.kinematics(
            {
                "operation": "task",
                "seed": seed,
                "settings": {
                    "target": target,
                    "tcp": [0] * 6,
                    "approach_m": 0.02,
                    "lift_m": 0.03,
                    "return_to_start": True,
                },
            }
        )
        assert generated["waypoints"] and "no gripper" in generated["note"].lower()
        assert (await panel.status(force=True))["JointPositions"] == seed
        taught = await panel.teach("Cup station")
        document = {
            "schema": "ur5e.action-chain/2",
            "timing_mode": "automatic",
            "units": "degrees",
            "reference": [math.degrees(v) for v in seed],
            "speed": 10,
            "waypoints": generated["waypoints"],
        }
        saved = await panel.save_task({"name": "Cup rehearsal", "document": document, "teaching": taught})
        plan = await panel.preview_action_chain(
            {"timing_mode": "automatic", "speed": 10, "waypoints": generated["waypoints"]}
        )
        run = await panel.start_continuous({"plan_id": plan["id"], "run_id": "task-episode"})
        await asyncio.wait_for(panel.task, 15)
        assert panel.jobs[run["id"]]["status"] == "completed"
        episode = await panel.dataset(run["id"])
        assert episode["schema"] == "ur5e.episode/1" and episode["simulation"]
        assert episode["trajectory"]["samples"]
        assert episode["run"]["authored_waypoints"] == plan["waypoints"]
        assert (await panel.status(force=True))["JointPositions"] == pytest.approx(seed)
        assert saved["id"] in panel.task_library
        with pytest.raises(PanelError):
            await panel.dataset("../escape")
    finally:
        await panel.close()
    # Restart never grants an execution authorization, but preserves reusable teaching and episodes.
    panel = PanelService(tmp_path / "panel", sila_client.config.port, sila_client.config.port)
    await panel.open()
    try:
        assert saved["id"] in panel.task_library
        assert (await panel.dataset("task-episode"))["trajectory"]["samples"]
        assert panel.continuous_plan is None
    finally:
        await panel.close()


async def test_panel_pause_edit_remainder_continue_and_stop_via_sila(sila_client, tmp_path):
    panel = PanelService(tmp_path / "panel", sila_client.config.port, sila_client.config.port)
    await panel.open()
    try:
        seed = (await panel.status(force=True))["JointPositions"]
        origin = [math.degrees(v) for v in seed]
        rows = [{"name": f"Action {i}", "degrees": [origin[0] + i * 35, *origin[1:]], "hold": 0} for i in (1, 2, 3)]
        plan = await panel.preview_action_chain({"timing_mode": "automatic", "speed": 10, "waypoints": rows})
        run = await panel.start_continuous({"plan_id": plan["id"], "run_id": "pause-panel"})
        await asyncio.sleep(0.05)
        await panel.control_motion({"run_id": run["id"], "action": "pause_after_action"})
        # Status is responsive while the same SiLA backend owns the motion lock.
        live = await asyncio.wait_for(panel.status(force=True), 2)
        assert live["motion_progress"]["request_id"] == run["id"]
        assert live["motion_progress"]["source"]
        await asyncio.wait_for(panel.task, 15)
        job = panel.jobs[run["id"]]
        assert job["status"] == "paused"
        done = job["progress"]["completed_actions"]
        assert 0 < done < len(rows)
        remainder = rows[done:]
        remainder[-1] = {**remainder[-1], "name": "Edited tail", "degrees": [origin[0] + 80, *origin[1:]]}
        plan = await panel.preview_action_chain(
            {"timing_mode": "automatic", "speed": 10, "waypoints": remainder, "resume_job_id": run["id"]}
        )
        next_run = await panel.start_continuous({"plan_id": plan["id"], "run_id": "continued-panel"})
        await asyncio.sleep(0.05)
        await panel.control_motion({"run_id": next_run["id"], "action": "stop"})
        await asyncio.wait_for(panel.task, 15)
        final = panel.jobs[next_run["id"]]
        assert final["status"] == "stopped"
        assert final["parent_id"] == run["id"]
        assert final["authored_waypoints"][:done] == plan["waypoints"][:0] + job["authored_waypoints"][:done]
        assert final["authored_waypoints"][-1]["name"] == "Edited tail"
        assert panel.jobs[run["id"]]["status"] == "continued"
        assert (await panel.dataset(next_run["id"]))["trajectory"]["simulation"]
        with pytest.raises(PanelError):
            await panel.control_motion({"run_id": next_run["id"], "action": "pause_after_action"})
    finally:
        await panel.close()


async def test_stop_intent_before_connector_ownership_prevents_dispatch(sila_client, tmp_path):
    panel = PanelService(tmp_path / "queued", sila_client.config.port, sila_client.config.port)
    await panel.open()
    try:
        seed = (await panel.status(force=True))["JointPositions"]
        angles = [math.degrees(v) for v in seed]
        rows = [{"name": "Never sent", "degrees": [angles[0] + 20, *angles[1:]], "hold": 0}]
        plan = await panel.preview_action_chain({"timing_mode": "automatic", "speed": 10, "waypoints": rows})
        run = await panel.start_continuous({"plan_id": plan["id"], "run_id": "queued-stop"})
        await panel.control_motion({"run_id": run["id"], "action": "stop"})
        await asyncio.wait_for(panel.task, 5)
        assert panel.jobs[run["id"]]["status"] == "stopped"
        assert panel.jobs[run["id"]]["receipt"]["status"] == "not_sent"
        assert (await panel.status(force=True))["JointPositions"] == seed
    finally:
        await panel.close()
