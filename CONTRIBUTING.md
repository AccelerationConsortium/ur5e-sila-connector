# Contributing

Use Python 3.12 and uv. From the repository root:

```bash
bash scripts/setup.sh
bash scripts/run.sh pytest -q
bash scripts/run.sh ruff format --check src/
bash scripts/run.sh ruff check src/
```

CI runs the same offline suite on Ubuntu and macOS. CI never connects to a physical controller. Run the simulation quick start to verify the browser journey after changing the panel.

Keep all repository documentation, user-facing messages, comments, and UI copy in English. Preserve the io/features split: io owns hardware communication, conversions, state guards, receipts, and simulation; feature methods delegate and expose SiLA metadata. The panel calls SiLA instead of sending robot scripts directly.

Default to simulation, localhost endpoints, and disabled real authorization. Do not add automatic retries, resume-on-startup, or history deletion for unknown motion outcomes. Keep site configuration, request receipts, raw trajectories, tokens, and personal filesystem paths out of commits. Provide synthetic fixtures and clearly label measured versus simulated evidence.

For hardware-related changes, report offline tests, network simulation, physical observations, and unresolved qualification separately. A passing test suite does not establish collision safety. Submit focused pull requests with behavior changes and validation evidence.
