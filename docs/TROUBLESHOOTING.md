# Troubleshooting

| Symptom | Check and next action |
| --- | --- |
| Dependency import hangs in Documents | Use scripts/setup.sh and scripts/run.sh; they keep the environment and bytecode outside the checkout. Re-run setup after changing the checkout path. |
| UI says disconnected | Start the relevant SiLA services first. Simulation uses 50052 for both clients; the real motion panel expects 50053 and 50054 unless overridden. |
| Ethernet has a self-assigned IP | Check cable/adapter and configure the Mac and controller on the intended common subnet. Read the controller address from the pendant. |
| Ping works but status fails | Verify Dashboard 29999 and RTDE 30004 reachability and firmware behavior. Do not diagnose a failed status read by sending a movement. |
| Base or Wrist preset is rejected | Inspect current unwrapped joint angles and all preview targets. The preset is relative to the actual pose; speed changes do not fix joint limits. Do not wrap an angle by 360° to evade checks. |
| Wrist3 +30° exceeds its range | Edit the reviewed route to use a negative excursion and positive return, if that swept path is safe. The supplied showreel does not adapt automatically. |
| Motion buttons disabled | Check backend mode, safety state, stopped state, unfinished jobs, authorization flags, receipt path, and OutcomeUnknown. Restart the service after editing config. |
| UI says REAL EXECUTION DISABLED | The separate continuous gate is false. This is intentional in shipped configs; it does not remove the preset. Stepped and continuous controls have different gates. |
| Existing preset seems missing | Open Sequences, then scroll below the Flow Dance card. Refresh resets unsaved builder rows; it does not delete built-in presets. |
| A 30°/s command looks slower | Check the pendant percentage, acceleration, bounded step lengths, and measured velocities. At 28%, 30°/s is at most nominally 8.4°/s. |
| Raising Flow Dance cap does not speed it up | Fixed duration defines the curve; cap is a constraint. Check duration and pendant scaling rather than raising only the cap. |
| No obvious physical movement | Read measured angle changes and the pendant Simulation switch, not just the connector mode. Low-speed ¼-range curves can be subtle. A receipt alone is not a visible demonstration. |
| ProgramState STOPPED but Running true | Dashboard observations may differ for an externally submitted script. Wait for actual measured velocities, runtime, and final stop verification; do not infer no motion from one label. |
| Deadline Exceeded / unknown outcome | Do not repeat the request. Inspect the existing receipt and actual controller state. The original operation may have run after the client stopped waiting. |
| Receipt scan is slow | Keep receipts on local storage; inspect index/migration results. Preserve history. Do not delete unresolved records or point at an empty directory. |
| Stale .active reservation after crash | Treat as unresolved ownership. Inspect original receipts and physical state with the maintainer; do not auto-remove it during startup. |
| Robot unavailable at shutdown | Mark the fresh state unavailable. Preserve the last known observation as historical evidence, stop local control services, and leave all motion flags disabled. |

No automatic replays, automatic resets, protective-stop clearing, or automatic move-to-zero are part of the UI workflow.
