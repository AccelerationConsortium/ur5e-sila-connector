# Validation and handoff

## Evidence boundaries

The repository ships a simulated transport and automated unit/integration tests. Simulation includes SiLA commands and kinematic trajectory checks; it is not a physics simulation or collision check.

The 2026-10-20 hardware session used a UR5e with URSoftware 5.4.2.76197. A corrected Showreel was submitted through the panel's SiLA path at a 30°/s command cap, with the pendant at 28%. Its 30 bounded steps completed with verified receipts. Independent output-only RTDE recording captured 3509 samples and six measured excursion ranges of approximately 20°, 15°, 20°, 30°, 30°, 30°. Peak measured speed was approximately 7.46°/s, and maximum return error was about 0.008°. See [aggregate evidence](../evidence/showreel-2026-10-20.json).

That recording supports measured motion for the recorded run. It does not establish 30°/s achieved physical speed, simultaneous six-axis control in the stepped preset, arbitrary route safety, compatibility with other machines, or an approved production installation.

Continuous Flow Dance produced completion receipts in two low-amplitude trials, but the operator did not observe a clear physical dance. Complete per-axis measured extrema were not retained for those runs. Visible physical acceptance remains open. Do not increase motion permissions because a software receipt says verified.

The private local history also contains later completed stepped panel jobs; full 20 Hz trajectories were not separately retained for each. Raw histories, site addresses, device identity, and operator recordings are excluded from GitHub and preserved in the original local backup.

At closeout, all real motion flags were disabled and real SiLA services were stopped. The robot had become unreachable, so the closeout did not establish a new physical stopped-state observation. The recorded Showreel final state was stopped and normal.

## Next operator's first session

1. Run the complete simulation quick start and tests on the new checkout.
2. Connect read-only and verify actual hardware identity, firmware, current pose, safety, stopped state, pendant mode, and local storage location.
3. Review joint limits and every preview target from the new current pose. Do not replay yesterday's request IDs or reuse an expired plan.
4. Start measured trajectory recording before an explicitly reviewed real sequence.
5. Separately qualify continuous multi-axis movement with recorded per-axis tracking and operator observation before increasing amplitude or speed.
6. Preserve histories and return all motion flags to false at closeout.

## Development backlog

- Integrate full six-axis recording and measured summaries into each UI run.
- Select previewed preset excursion directions from current joint headroom, with operator review.
- Improve active-script state wording and distinguish returned receipts from visible physical acceptance.
- Add route/collision validation before treating choreography or saved-point routines as reusable motion plans.
- Keep the CLI recorder separate from motion authorization; recorder failure alone does not stop an independent job.

## Publication verification

The clean publication checkout installed successfully with Python 3.12.13. Local validation passed 139 tests in 20.33 seconds; source formatting and Ruff checks passed. The simulated SiLA demo, 7501-setpoint continuous/reset validation, and browser Showreel load → preview → run → completed (30/30 steps) were exercised from this checkout. No real controller was contacted during publication. GitHub CI is a separate check and must be inspected on the repository.
