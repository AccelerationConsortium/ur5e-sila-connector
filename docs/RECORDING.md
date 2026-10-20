# Record measured six-axis trajectories

On native Windows, run the same example using `.\scripts\run.ps1` instead of `bash scripts/run.sh`. See [Windows recording](WINDOWS.md#record-and-close-out-a-later-authorized-test) for the full command and workstation checklist.

Motion receipts and full trajectories are different evidence. A completion receipt may preserve only starting/final state and a sample count. Planned angle ranges are not measured ranges.

The output-only recorder below reads real-controller RTDE at 20 Hz. It sends no motion commands, and it is independent of the UI/SiLA execution path. Start it before submitting a reviewed sequence through the UI. Keep the pendant operator in place.

```bash
mkdir -p recordings
bash scripts/run.sh python examples/record_trajectory.py --robot-host YOUR_CONTROLLER_IP --output recordings/session.csv
```

Wait for **Recording started**, run the authorized sequence through the panel, wait for the stopped result, then press Ctrl-C in the recorder terminal. Alternatively use `--duration 240` for a bounded recording interval. The recorder refuses to overwrite existing output. Use a different name per session.

The CSV contains host UTC timestamps, advancing controller timestamps, all six measured joint angles in degrees, all six measured velocities in degrees per second, and numeric robot/safety/runtime states. The sibling `.summary.json` records measured ranges, peaks, maximum inter-sample gap, and final telemetry. Logs and recordings are ignored by Git.

Recorder failure does not cancel or stop a separately submitted UI job. The UI and connector monitor the command themselves; keep the on-site operator responsible for immediate stopping. A complete CSV alone does not establish collision safety, tracking quality, or the pendant's simulation setting. Preserve the UI run ID, associated receipts, and operator observations alongside the recording.

The development-session recording retained 3509 samples for the corrected 30-step Showreel. Raw session files remain in the owner's local backup; the repository includes only the aggregate evidence summary.
