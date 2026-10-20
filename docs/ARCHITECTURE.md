# SiLA and architecture

```text
English browser panel (loopback HTTP)
  -> PanelService (SiLA client)
  -> RobotController feature
  -> Ur5eProtocol (authorization, state, parsing, simulation selection)
  -> Dashboard / output-only RTDE / reviewed URScript
```

`src/unitelabs/ur5e_connector/io/` owns hardware communication, conversions, trajectory construction, persistent receipts, and simulation. `features/robot_controller/` exposes thin SiLA methods. `ui/` translates local panel actions to SiLA commands; it does not submit URScript directly.

## Feature identifiers

- `io.unitelabs/robot/RobotController/v1`
- `org.silastandard/core/SimulationController/v1`

Important read-only properties include `SoftwareVersion`, `JointPositions`, `ToolPose`, `SpeedSliderFraction`, `RobotMode`, `SafetyStatus`, `ProgramState`, `LoadedProgram`, `Running`, `ControlOutcomeUnknown`, and `MotionControlsEnabled`. JointPositions is radians; the UI converts it to degrees. The `MotionPreflight` command reports readiness and the continuous-motion gate separately.

## Commands

| Command | Purpose |
| --- | --- |
| MotionPreflight | Stationary telemetry, safety/program state, limits, receipt readiness |
| MoveJointAtSpeed | Explicit 0.5–10° single-joint command, 1–40°/s cap |
| InspectMotionRequest | Read original request result without replay |
| PrepareContinuousDance | Prepare a fixed-duration shared six-axis curve |
| PrepareLowSpeedDance | Prepare ¼ amplitude, 3°/s cap |
| PrepareReset | Prepare explicit return to a session checkpoint |
| RunContinuousMotion | Consume a prepared plan once with mode, expiry, and confirmation |
| LoadProgram / Play / Pause / Stop | Separately gated approved-program workflow |
| SendCommand | Restricted Dashboard interface; not arbitrary URScript |

Legacy single Base/Elbow test methods remain available in the feature but are disabled in shipped configs. Use the reviewed UI/session path for handoff.

MoveJointAtSpeed takes Joint, DeltaDegrees, SpeedDegreesPerSecond, RequestId, ExpectedSimulationMode, and ExpiresAtUnixSeconds. The panel creates a new ID per step and a short-lived dispatch authorization. The server checks the current state before sending. A lost response is not automatically retried.

RunContinuousMotion takes PlanId, RequestId, ExpectedSimulationMode, ExpiresAtUnixSeconds, and RemoteControlConfirmed. Plans are session-owned, expire quickly, and are consumed once. The real implementation submits one controller-local script and monitors actual RTDE state. Kinematic validation does not include collision geometry.

## Durable data

The panel stores jobs and named positions under its data directory. The control server stores motion receipts in the configured persistent directory. The receipt index stores file signatures and terminal statuses, so unchanged history does not require reading every JSON body before a new command. Changed, deleted, malformed, or unresolved indexed records cause a failure rather than being silently forgotten.

Use an ordinary local directory outside managed/cloud-synchronized Documents storage. An earlier session measured about 105 seconds spent reading historical receipt bodies, versus milliseconds for a warmed local indexed scan. `examples/migrate_receipts.py --help` describes an offline migration that verifies copied bytes and does not clear invalid history. Preserve existing history and use fresh request IDs for genuinely new, reviewed commands only.

On Windows, the file signature also hashes receipt content because creation time is not POSIX change time. This reads bytes even for cached entries, while skipping JSON parsing for unchanged receipts. The derived index uses a flushed, closed-file replacement without POSIX directory fsync; primary receipt validation and fail-closed behavior are preserved.

HTTP changes require a same-origin session token, and both HTTP and SiLA bind to localhost by default. These local development endpoints have no network-user authentication and should not be exposed externally.
