# Action Composer

Open Control Desk → **02 Sequences** (`/#sequences`; `/composer` remains a compatible direct address) on the local Control Desk. Editing never moves the robot.

The control path is Action Composer → authenticated loopback HTTP → public SiLA → simulation or a separately enabled controller.

## One speed, automatic travel times

Set **Motion speed** once for the complete action. Save each pose with an optional **Hold**. You do not enter Travel time: the SiLA backend calculates it from the measured start, six-axis distances, selected speed and acceleration bounds. Each segment's calculated time appears as a read-only result after validation.

Motion speed is a peak joint-speed ceiling, not a promise that every joint always travels at that speed. Axes move together and slow down for starts, turns, acceleration limits and final stopping. The pendant also scales actual motion; the editor never changes the pendant slider.

1. Confirm the mode badge. A simulation panel cannot move the physical robot.
2. While stationary and NORMAL, select **Read current pose** to load the actual six-axis starting pose. Do this on the real panel when designing a real action. A simulator pose is not the real robot's pose.
3. Select an axis using the J1–J6 buttons, then rotate its ring or drag a joint handle in a circular arc. Use the separate rotary dial for overlapping or edge-on axes, a joint slider, or an exact angle input. A handle rotates one joint; it is not Cartesian inverse kinematics. Wrist 3 rotation is shown numerically because it does not change its own axis origin.
4. Enter a name and optional Hold, then **Add waypoint**. Use **Edit** and **Update selected** to change a saved node. Reorder with drag/drop or arrows. Duplicate and delete as needed. **Add return to reference** appends the captured reference pose.
5. Set **Motion speed**, then **Validate via SiLA**. The first segment starts at the real or simulated pose measured during validation. All travel times are calculated automatically. A fresh preview is required after any edit.
6. Inspect each calculated travel time, the complete six-axis excursion and **Play validated preview**. Zero hold allows continuous passage where directions permit; a reversal can stop that axis. A nonzero Hold stops all axes.
7. In simulation select **Run once via SiLA** and inspect the completed job. This runs every setpoint in accelerated kinematic time; it does not validate physical dynamics or collisions.
8. Export JSON for a portable copy. Browser drafts are also saved locally. Importing never executes movement.

## Saved actions and compatibility

New files use `ur5e.action-chain/2`, `units: degrees`, `timing_mode: automatic`, a speed, optional reference and a waypoint list. Each waypoint contains name, six degrees and hold. Travel durations are execution-preview results, not editing inputs.

```json
{
  "schema": "ur5e.action-chain/2",
  "timing_mode": "automatic",
  "units": "degrees",
  "reference": null,
  "speed": 10,
  "waypoints": [{"name":"Example", "degrees":[0,-90,-85,0,90,0], "hold":0}]
}
```

These example angles are illustrative and not qualified for hardware. Importing version 1 preserves the exact poses, order, names and Hold settings while replacing legacy fixed travel times with automatic timing. Export the old file first if you need to retain the earlier manual schedule.

The simulator example is [`examples/action-chain.simulation.json`](../examples/action-chain.simulation.json). It is not a real-device trajectory.

## Checks and interpolation

Actions have 1–24 waypoints; Hold is 0–10 seconds. Automatically calculated travel is bounded to 1–60 seconds per segment and 120 controller seconds for the whole action. The compiler rejects an action that cannot fit these bounds at the selected speed; it never silently raises the speed or modifies angles. Angle margins remain: Elbow strictly inside ±155°, other joints strictly inside ±355°. No angle wrapping. At least 0.5° of travel is required somewhere in the action.

The initial timing estimate uses quintic rest-to-rest velocity and acceleration extrema. The complete C2 quintic path with shared interior velocities is then checked at 125 Hz; travel times increase if needed to satisfy the selected 1–40°/s cap and acceleration cap of twice that value in °/s². Controller timing, including Hold, is quantized to 8 ms and reported. These are software checks, not certified dynamic safety limits.

The controller evaluates the same numeric polynomials in one local `servoj` loop with a final stop. Same-direction knot tangents allow passage through intermediate waypoints; endpoints and held poses stop. The page displays nominal centerlines, not link surfaces, and does not validate self-collision, people, fixtures, cables, tools, table clearance or robot dynamics.

## Real-device preparation and execution

Design real poses from **Read current pose** on the real panel. Absolute simulator angles can require nearly a full revolution from the actual robot, even if the on-screen pose appears similar. The connector deliberately does not wrap these angles or insert a hidden reset/rebase.

Real user-authored chains require the separate `enable_action_chains` configuration gate, disabled by default. The dance flag does not enable them. Preparation works while execution is disabled. Separately review and qualify the actual mounting, tool/load and entire physical motion envelope before enabling this gate.

Dispatch also requires manual Remote Control confirmation, a persistent receipt directory, fresh stationary NORMAL/RUNNING telemetry, matching mode and initial pose, an unexpired plan and no unresolved earlier outcome. Initial-pose tolerance is 0.1°; preview expiry is 180 seconds. Plans and IDs are single-use. A lost response must be inspected, never replayed.

Use the physical pendant for an immediate stop. Closing the page does not stop an already dispatched controller program. Existing queue cancellation cannot interrupt this local continuous loop.

## SiLA API

Feature: `io.unitelabs/robot/RobotController/v1`.

- `PrepareActionChainAtSpeed`: `WaypointsJson` (up to 32 KiB, list of name/degrees/hold without duration), `Speed`, `ExpectedSimulationMode` → checked `Plan` with computed durations.
- `RunContinuousMotion`: execute the prepared plan once using its ID, unique request ID, expected mode, dispatch expiry and Remote Control confirmation.
- `InspectMotionRequest`: read the existing execution receipt without replay.
- Legacy `PrepareActionChain` and `SuggestActionChainTiming` retain the explicit-duration API for compatibility. The Composer uses the new speed-only command.

The UI sends `POST /api/action-chain/preview` with `timing_mode: automatic` and uses `/api/motion/run` for explicit dispatch.

## Evidence boundary

Tests cover automatic speed-dependent timing, sampled derivatives, preserved input poses, bounded durations, C2/script parity, holds, invalid inputs, real execution gates, stale starts, one-shot replay rejection and public SiLA simulation. Hardware acceptance of a user-authored chain remains separate. Simulation must never be reported as observed physical movement.


## Rotary editing

The six axis buttons select an exact joint even when projected nodes overlap. Drag the selected ring or the separate dial clockwise/counterclockwise to change its unwrapped angle. Holding Shift reduces drag sensitivity to 20%; the dial's Left/Right keys adjust by 1°, or 0.1° with Shift. Optional **Snap to 1°** affects the editor only. Escape cancels an active gesture; **Undo pose edit** restores the previous editor pose without changing saved waypoints.

The ring is a screen-space angle control. X–Z and Y–Z remain orthographic nominal centerline projections; this is not Cartesian inverse kinematics or a collision model. The flange-axis markers show orientation changes, including Wrist 3 rotation that leaves the flange position unchanged. Angle limits remain in force, with explicit feedback at the boundary and immediate reversal when dragging back. Joint angles never silently wrap at ±180° or ±360°.

Rendering updates existing SVG nodes on animation frames. Draft persistence and waypoint-list updates happen at edit boundaries rather than on each pointer event. Saved documents can include the current unsaved editor pose and selected axis so reloads preserve the editing context. None of these gestures sends a SiLA motion command.

Run the pure geometry/gesture regression checks with `node --test tests/ui/rotation.test.cjs` (Node 18+). These are UI math checks, not physical robot validation.

## TCP tasks, optional interruption and reusable recordings

See [Task workflows](TASK_WORKFLOWS.md) for TCP/IK authoring, fixed-location pickup rehearsal, optional Stop/Pause controls, live card progress, task library and episode export. Playback stays continuous by default. The new real playback program requires separate `enable_feedback_motion` qualification; it is not enabled automatically by the previous action-chain gate.
