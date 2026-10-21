# Control Desk user guide

## Open the correct panel

Windows uses this same English UI. Use the PowerShell commands in the [Windows guide](WINDOWS.md) to start its services; the navigation, presets, and workflow below are unchanged.

Simulation: start the connector on 50052, then the panel with `--simulation --port 8766`; open http://127.0.0.1:8766/.

Real robot: follow [Hardware setup](HARDWARE.md), then open http://127.0.0.1:8765/. The header must say **REAL ROBOT**. A simulation label describes the connector backend; it does not independently confirm the pendant's own Simulation switch.

Starting either panel does not send motion. Keep the operator at the pendant during real tests. The web page is not an emergency-stop interface.

## Live control

The left navigation has **Live control**, **Sequences**, and **Saved positions**. The header reports controller version, robot mode, safety state, and the last successful refresh.

1. In **Live control**, review measured joint angles and tool center point.
2. Select **Jog step**: 0.5°, 1°, 5°, or 10°.
3. Set the right-hand **JOG / STEPPED SPEED** slider. The panel shows the pendant percentage and estimated scaled cap.
4. A joint's plus or minus button submits one explicit relative move through SiLA. It is not a press-and-hold jog. Wait for completion before requesting another.

Joint angles are shown in degrees. TCP coordinates are millimetres; orientation values are radians. A robot mode named RUNNING does not mean a user program is moving: also inspect actual motion and execution state.

## Sequences and existing presets

Select **Sequences** in the left navigation. Scroll below the **Flow Dance** card to find **Stepped Dance** and the three smaller preset buttons. They are not on the Live control page.

| Preset | Contents |
| --- | --- |
| Joint sweep | Wrist1 +10°, then −10° |
| Six-axis showreel | Base +20°/−20°, Shoulder −15°/+15°, Elbow +20°/−20°, each Wrist +30°/−30° |
| Gentle round trip | Each joint +5°, then −5° |
| Load Stepped Dance | 24 rows: opening, left sway, right sway, return; loads 30°/s command speed |

The showreel is **one joint at a time**. Each axis returns before the next starts. The standard preset uses positive wrist excursions, which may be invalid near an upper joint limit. For example, Wrist3 at +345° cannot take +30°. Edit its two rows to −30° then +30° and review the full preview. The preset does not automatically choose another direction.

To run a stepped sequence:

1. Click a preset to load rows, or choose **Add move** and edit joint/relative angle.
2. Set **JOG / STEPPED SPEED**. Loading Stepped Dance changes this slider; check it again.
3. Click **Preview sequence**. Review the six target angles and full excursion ranges in **NEXT RUN**.
4. Click **Run once** only for the reviewed route. This starts real motion when the panel is in real mode and the corresponding server authorization is enabled.
5. Watch **EXECUTION** and **RECENT ACTIVITY**. Large rows are split into at most 10° moves; every move settles and is verified before the next. The total count may exceed the number of visible rows.

Previews expire after five minutes. Editing rows or speed invalidates the displayed preview. If the robot changes position, create a fresh preview. An out-of-range preview sends no motion; changing only the speed does not fix an invalid target.

## Continuous Flow Dance

Flow Dance changes all six joint targets on one shared controller-local timeline. It has no intended stops between phrases. It is separate from the stepped presets.

1. Click **Run preflight** and inspect each result.
2. Choose a duration and **Peak speed cap**. Duration is controller time; pendant scaling can make wall time longer.
3. Click **Preview continuous dance**, or **Preview low-speed test (¼ range, 30 s)**.
4. Inspect ranges, the relative-angle chart, peak speed, and peak acceleration. These are joint-space calculations, not collision checking.
5. For an explicitly authorized real test, confirm Remote Control on the pendant and check the corresponding checkbox. The checkbox does not switch the robot into Remote Control.
6. **Run prepared motion once** is available only when the real continuous-motion gate is separately enabled. Default shipped configs leave it disabled. Do not enable it merely because simulation passed.

**Preview return to reference** prepares a return to the pose captured by the current session's preview. It does not move automatically and it is not a move to the pendant's Zero Position. A server restart loses session references; prepare a new plan.

The low-speed Flow Dance has software completion receipts, but its visible physical demonstration was not accepted by the operator. Do not describe it as hardware-qualified continuous choreography.

## Saved positions and fixed-point routines

With the robot stationary, open **Saved positions**, name the current pose, and record it. Recording reads the pose; it does not move the robot.

In **Sequences**, choose distinct **Approach** and **Target** saved positions, then **Create routine**. Preview the resulting approach → target → approach route before running. The route is compiled as sequential joint moves, not Cartesian path planning. It may be rejected if a required single row is too large or a trim is below the supported step size. No gripper actions are included; do not call this an actual pick-and-place operation.

## Cancellation, failure, and end of session

- **Cancel after current move** cancels remaining stepped moves. The current bounded move finishes first.
- Continuous motion cannot be cancelled between internal setpoints from this page. Use the pendant for an immediate stop.
- On an uncertain result, do not click Run again. Inspect the original request receipt. A client timeout does not prove that the robot did not move.
- **Inspect last failed run** compares the latest failed panel job with its existing SiLA receipt. It does not replay a command or clear a fault.
- Refreshing the page resets unsaved builder rows. It does not cancel an active server-owned run.
- At closeout, confirm stopped state, disable real authorization flags, stop the real connector services, and save recordings. See [Hardware shutdown](HARDWARE.md#shutdown).

## Action Composer

Select **Open Action Composer** or open `/composer` on the same panel. This editor supports continuous, simultaneous six-axis waypoint chains, joint-handle dragging, exact angle inputs, saved browser drafts and portable JSON files. Follow the [complete Action Composer guide](ACTION_COMPOSER.md) before preparing or executing a chain. Simulation and real device execution remain explicitly separated.

## TCP tasks, progress, interruption and reusable datasets

Open **Action Composer** for the integrated workflow. Use **TCP targets & fixed-location tasks** for position/orientation authoring and no-gripper rehearsal. **Live execution** provides optional Stop all and Pause after action. **Task library & episodes** saves reusable teaching and exports simulated or measured run data. Follow the complete [Task workflows guide](TASK_WORKFLOWS.md), including coordinate units, continuation semantics and the separate real-controller qualification boundary.

## Integrated Action Composer

Open Control Desk and choose **02 Sequences**. Action Composer replaces Flow Dance in this workspace. Design six-axis poses, save waypoints, validate through SiLA, preview, and explicitly run once. Its speed and optional Stop/Pause controls are independent of jog and stepped routines. Changing workspace tabs preserves the editor; browser drafts and task storage remain on the same origin. The direct `/composer` address remains compatible.

Expand **Stepped routines & saved-position sequences** for the previous one-axis routines, their speed slider, preview and execution controls. A page refresh or navigation never starts movement.
