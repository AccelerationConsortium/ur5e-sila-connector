# TCP authoring, fixed-location tasks, feedback and reusable episodes

The Action Composer remains continuous by default. Neither opening the page nor editing, teaching, solving IK, generating a task, loading a saved task or validating a trajectory starts movement. A separately enabled real backend and an explicit one-time Run are required.

## 1. TCP position and orientation authoring

Open **TCP targets & fixed-location tasks**. The target uses the robot **base frame**. X/Y/Z inputs are millimetres. RX/RY/RZ are an **axis-angle rotation vector in radians**, as used by UR poses; they are not roll/pitch/yaw Euler angles.

The virtual TCP offset is a flange-to-tool transform in metres and rotation-vector radians. It is only an authoring setting: it does not modify the pendant's TCP, payload or installation.

- **Use editor TCP** computes nominal forward kinematics from the six editor angles.
- Edit the target position/orientation and select **Solve nominal IK via SiLA**. The nearest converged joint branch is seeded from the editor; no angle wrapping is applied. Unreachable, non-finite, singular/nonconvergent and limit-violating targets are rejected.
- A successful solve changes the editor pose only. Select Add waypoint or Update selected, then Validate the complete chain.
- The displayed residual is a numerical solver residual, not physical accuracy.

Geometry uses the published [UR5e nominal DH parameters](https://www.universal-robots.com/articles/ur/application-installation/dh-parameters-for-calculations-of-kinematics-and-dynamics/). It does not include this particular arm's calibration, link thickness, self-collision, surrounding obstacles, dynamics or load. The existing projection is not a collision model. Changing the seed may choose a different solution branch.

## 2. Fixed-location pickup rehearsal

Without a camera, a task target must remain in its taught physical location. A moved cup or changed fixture requires another teaching operation.

1. Stop the robot and give the task a name and instructions.
2. **Teach current pose** records stationary joint telemetry and the controller's TCP observation. The target fields use nominal FK from the recorded joints plus the authoring TCP. The actual controller TCP is kept separately, because the pendant TCP may differ from the virtual TCP.
3. Alternatively, enter a known target or select Use editor TCP.
4. Set Approach and Lift heights, each 10–300 mm along **base Z**.
5. Select **Generate pickup rehearsal via SiLA**. It creates Approach → Descend → Simulated grasp (one-second hold) → Lift → Retreat → optional Return to start.
6. Review every joint pose and the entire physical swept volume, validate and inspect the preview before an explicit Run.

This is a **no-gripper rehearsal**. It does not grasp, detect contact, verify object pickup or claim task success. Vertical task phases insert Cartesian target samples at intervals of at most 25 mm; their interpolation remains in joint space, so these are not guaranteed straight Cartesian MoveL paths. The initial approach and return are joint-space transfers. A hold at the mock grasp is intentional; other zero-hold nodes remain continuous where their joint directions permit.

Generating a task replaces the editable chain. During a verified pause it replaces only the unexecuted remainder. Completed nodes remain immutable. Maximum chain size is 24 nodes.

## 3. Optional stop, pause and measured progress

- **Stop all** requests Dashboard Stop through the public SiLA run-control command for the exact active request. It cancels the remainder. The panel reports Stopping until stopped program state and stationary telemetry are verified. It does not wait behind the motion command lock. This is not an emergency stop; use the physical pendant for an immediate safety response.
- **Pause after action** is optional. The controller follows the original geometry while reducing path-time rate to zero at a reachable action boundary. If the current boundary is too close for bounded braking, it selects a later one and reports which. If no eligible boundary remains, the chain reaches its ordinary final stop.
- Default playback does not insert node stops. Automatic timing reserves derivative headroom for optional braking; the requested speed remains a cap, not a guaranteed actual speed. The pendant also scales motion.
- On a verified pause, the program terminates at rest. **Validate remaining actions** creates a fresh one-shot trajectory from the measured current pose. Edit remaining nodes first if desired; then validate and explicitly Run. Completed prefix nodes are not replayed. This is a new child run with a parent ID, not Dashboard Play on an old program.
- The active card has a green signal; completed cards change colour. Paused and unknown states have explicit labels. Real progress comes from a nonce-bound controller output register plus measured RTDE joints, not browser elapsed time. Simulation progress is labelled as simulation.
- A stale/disconnected channel never creates a completion claim. Reloading a page does not stop an active run. Restarting the panel never automatically resumes paused or interrupted work.

### Real-controller qualification boundary

The original `enable_action_chains` flag does not automatically authorize the new controller program. New feedback playback also requires **`enable_feedback_motion: true`**, which is false by default. Leave it false until the new program has been qualified on the intended controller and installation. Local and fake-transport tests do not establish physical acceptance.

Feedback playback exclusively acquires RTDE input integer registers **24/25** and uses output integer registers **24–27**. Other URCaps or clients must not use these registers. If input ownership is rejected or types do not match, dispatch is rejected. The upper register range is available starting with PolyScope 5.3 according to the [UR RTDE documentation](https://www.universal-robots.com/manuals/EN/HTML/SW5_23/Content/Prod-RTDE/Real_Time_Data_Exchange_RTDE.htm); the actual 5.4 controller program still needs hardware qualification.

## 4. Teaching, task library and datasets

**Save task** stores the authored v2 document, virtual TCP and task settings, instructions, speed, reference and teaching provenance in the panel's persistent data directory. **Load for editing** never restores an execution authorization; validate against current state again. Browser JSON export/import remains a portable way to move drafts between computers. Mac and Windows share the same formats and SiLA commands.

**Export run dataset** downloads `ur5e.episode/1` with:

- task instructions/settings and teaching provenance;
- exact validated plan, start, authored waypoints, speed and automatic durations;
- request ID, parent-child interruption history and final completion classification;
- actual six-axis telemetry and controller progress, or explicitly simulated samples;
- target samples and maximum joint tracking error where available;
- verification receipt, recording errors and evidence boundary.

Simulation is a nominal kinematic calculation, not a physics/dynamics or hardware test. Reaching a pose is distinct from successfully manipulating an object. No camera stream is collected, and no data is uploaded to an AI provider.

The panel stores `tasks/`, `positions/`, `runs/` and `trajectories/` below its selected `--data-dir`. The real connector also retains its primary motion receipt and JSONL telemetry trace in its configured receipt directory. Preserve primary receipts even after exporting a dataset. If dataset export fails after a verified motion, the run keeps its verified outcome and records the export error; it must not replay the motion to obtain a new dataset.

## Public SiLA additions

`SolveTcp(PayloadJson)` supports `forward`, `inverse` and `task` operations. All vectors are metres/radians and seeds are six joint radians. It is read-only with respect to hardware.

`MotionProgress` returns a run-bound JSON report. `ControlActiveMotion(RequestId, Action, ExpectedSimulationMode)` accepts only `stop` and `pause_after_action` for the exact active connector-owned run; acknowledgement is not a stopped-state verification. `ReadMotionTrace(RequestId)` exports that configured connector's retained trace.

Existing manual-timing and joint commands remain available. Automatic Action Composer plans use the new feedback-capable program and therefore require its additional real qualification flag.
