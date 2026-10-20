# Real UR5e setup

The validated physical development baseline is a Mac and a UR5e with URSoftware 5.4.2.76197. The connector runs on the host computer; nothing is installed on the robot controller. The commands below use Bash. For native Windows equivalents, data paths, and the separate workstation acceptance checklist, follow [Windows setup](WINDOWS.md).

## Ethernet and read-only connection

1. Connect a supported Ethernet adapter from the Mac to the controller network port.
2. Assign unique static addresses on an isolated common subnet. The example host `192.0.2.2` is documentation-only: replace it with your actual controller address. Configure the Mac's Ethernet interface manually; a self-assigned 169.254 address may not reach a separately configured static subnet. Wi-Fi can remain available for Internet access if routing does not conflict.
3. Verify the controller address on the pendant and test connectivity. A successful ping alone does not prove that the Dashboard or RTDE service works.
4. Copy the read-only template and edit `robot_host`. Keep all five `enable_*` flags false.

```bash
cp real.example.json real.local.json
bash scripts/run.sh python -m unitelabs.ur5e_connector --config-path real.local.json
```

In another terminal:

```bash
bash scripts/run.sh python examples/read_robot.py --port 50053
bash scripts/run.sh python -m unitelabs.ur5e_connector.ui.server --read-port 50053 --control-port 50053 --port 8765
```

Both panel clients point at the read-only server in this mode, so no second server is required. For subsequent authorized motion, stop this panel and start it with the separate control endpoint described below.

| Endpoint | Purpose |
| --- | --- |
| Robot TCP 29999 | Dashboard identity, state, reviewed program operations |
| Robot TCP 30004 | Output-only RTDE joint/TCP/speed readings |
| Robot TCP 30002 | URScript submission for authorized bounded motion |
| Mac 127.0.0.1:50052 | Simulated SiLA server |
| Mac 127.0.0.1:50053 | Real read-only SiLA server |
| Mac 127.0.0.1:50054 | Separate real control SiLA server |
| Mac 127.0.0.1:8765 / 8766 | Real / simulated panel |

## Prepare a real control service without enabling movement

Stop the panel first. Keep the read-only server running on 50053.

```bash
cp motion.example.json motion.local.json
```

Edit `robot_host` to the same controller. Set `joint_test_receipt_directory` to an absolute, persistent, local directory, such as a dedicated `motion-receipts` directory under the wrapper's `UR5E_DATA_DIR`. The JSON path must be absolute: `~` and environment variables are not expanded inside JSON. You can print the wrapper's data path with:

```bash
bash scripts/run.sh python -c 'import os; print(os.environ["UR5E_DATA_DIR"])'
```

Keep every `enable_*` flag false initially. Start the control server and then the panel in separate terminals:

```bash
bash scripts/run.sh python -m unitelabs.ur5e_connector --config-path motion.local.json
```

```bash
bash scripts/run.sh python -m unitelabs.ur5e_connector.ui.server --read-port 50053 --control-port 50054 --port 8765
```

The panel can now read state and prepare plans. Real execution remains disabled. Configuration is read at startup: editing a file alone does not change a running server's permissions.

## Authorize one reviewed stepped session

Before authorizing real motion, an on-site operator must verify mounting, tool/payload settings, operating limits, clearance of the entire swept path, the pendant's real rather than Simulation setting, and access to the emergency stop. Set Remote Control at the pendant and reconnect the control service. The software's joint checks do not establish collision clearance.

With no active run, stop the control server. In `motion.local.json`, set **only** `enable_joint_testing` to true for the reviewed jog/stepped session. Keep `enable_program_control`, `enable_base_step`, `enable_elbow_step`, and `enable_continuous_motion` false. Restart the control server and refresh the panel. Do not start the simulation backend against real authorization files.

Review current position and every preview range before running. Request IDs and receipts prevent replay of the same command. They do not make an arbitrary new request safe. If a result becomes unknown, inspect and reconcile the original request; never clear the receipt directory to continue.

`enable_program_control` permits a separate approved `.urp` program workflow and requires an exact reviewed program path. It is not needed for UI jog or stepped sequences. Generic `SendCommand` is not a bypass for the motion gates. Continuous dance/reset requires its own `enable_continuous_motion` gate and separate hardware qualification; leave it disabled for this handoff.

## Shutdown

1. Wait for the job to finish or cancel remaining stepped moves. Use the pendant to stop immediately if needed.
2. Read actual stopped state and safety status while the connection is still available. Save measured trajectories and receipt IDs.
3. Set all five real authorization flags false in both local configuration files.
4. Stop the real control server with Ctrl-C. A configuration edit does not revoke a permission already loaded by a running process.
5. Stop the read-only server if the session is finished. The panel may remain open and show disconnected state.
6. Do not automatically start a paused or failed run next time. Reconnect read-only and re-check the current physical pose.

Stopping a server is not an emergency stop and must not interrupt a monitor while a trajectory is still active. If the robot has already become unreachable, record current state as unavailable; an old completion record is not a fresh stopped-state observation.
