# Native Windows setup and first-session checklist

Windows uses the same Python package, SiLA features, presets, and English browser UI as macOS. The PowerShell scripts provide Windows environment and persistent-data paths. A physical Windows-to-UR5e session still needs on-site acceptance; automated Windows CI does not establish robot or Ethernet-adapter compatibility.

## Requirements

- Windows 10/11 x64 for the intended operator workstation. Other Windows architectures are not qualified by this guide.
- **PowerShell 7.4 or later** (`pwsh`), Git, and uv on PATH. The scripts do not target the older built-in Windows PowerShell 5.1 (`powershell.exe`).
- Python 3.12; uv can download a managed interpreter using the repository's `.python-version`.
- Repository access if cloning the private GitHub repository.
- For hardware only: a supported Ethernet adapter, the controller address, and an on-site pendant operator.

Install tools from their official distributions: [PowerShell on Windows](https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell-on-windows) and [uv installation](https://docs.astral.sh/uv/getting-started/installation/). Open a new PowerShell 7 terminal after installation. The scripts do not install global tools, change execution policy, or alter the firewall. If organizational script policy blocks execution, use your approved IT procedure rather than disabling the policy globally.

```powershell
$PSVersionTable.PSVersion
Get-Command git, uv, pwsh
git clone https://github.com/SissiFeng/ur5e-sila-connector.git
Set-Location .\ur5e-sila-connector
.\scripts\setup.ps1
```

Choose a short local checkout path, such as `C:\src\ur5e-sila-connector`, outside OneDrive or other synchronized folders. Setup installs the locked dependencies and checks package import. It does not connect to a robot or start a server. Re-running setup is supported.

## Run simulation first

Run commands from the repository root. Each long-running service needs its own **PowerShell 7** terminal. No activation of a virtual environment is required.

**Terminal 1 — simulated SiLA server:**

```powershell
.\scripts\run.ps1 python -m unitelabs.ur5e_connector --config-path simulation.example.json
```

**Terminal 2 — simulated English UI:**

```powershell
.\scripts\run.ps1 python -m unitelabs.ur5e_connector.ui.server --simulation --port 8766
```

Open **http://127.0.0.1:8766/** in Edge or Chrome. Verify **SIMULATED DEVICE**, open **Sequences**, load **Six-axis showreel**, preview the full range, and choose **Run once**. Wait for all 30 bounded moves to complete. This is a simulated sequence, not a physical movement.

**Terminal 3 — optional read-only query, smoke test, continuous simulation, and tests:**

```powershell
.\scripts\run.ps1 python examples/read_robot.py --port 50052
.\scripts\run.ps1 python examples/demo_client.py --port 50052
.\scripts\run.ps1 python examples/validate_continuous.py
.\scripts\run.ps1 pytest -q
```

The smoke example requires simulation mode and refuses a real backend. The continuous example also requires simulation and verifies a return to its captured reference. Press Ctrl-C in each service terminal after all simulated runs are finished; confirm the processes exit. Do not close a terminal to interrupt monitoring of an active real trajectory.

## Persistent paths and overrides

Defaults are outside the checkout:

| Data | Default |
| --- | --- |
| Environment | `%LOCALAPPDATA%\UR5eConnector\<checkout-id>\runtime\venv` |
| Bytecode | `%LOCALAPPDATA%\UR5eConnector\<checkout-id>\runtime\pycache` |
| Panel data | `%LOCALAPPDATA%\UR5eConnector\<checkout-id>\data\panel-data` |

The checkout ID is derived from the normalized, case-insensitive absolute checkout path. Moving a checkout changes the default ID. Preserve the existing data path explicitly when relocating a working installation; do not accidentally create an empty history to bypass an unresolved motion.

Print the current runtime-data root:

```powershell
.\scripts\run.ps1 python -c 'import os; print(os.environ["UR5E_DATA_DIR"])'
```

Optional overrides must be absolute Windows paths and must be set consistently in **every service terminal** before setup/run:

```powershell
$env:UR5E_RUNTIME_DIR = 'D:\UR5eConnector\runtime'
$env:UR5E_DATA_DIR = 'D:\UR5eConnector\data'
.\scripts\setup.ps1
```

Do not store motion receipts on a network share or cloud-synchronized directory. Keep a separate backup. Windows receipt signatures hash file contents because Windows creation time cannot reliably detect rewritten content with restored modification times. The index still avoids reparsing unchanged JSON. Index files are flushed and replaced after closing; the POSIX-only directory-fsync step is not used on Windows. Primary motion receipts remain the source of truth, and a missing derived index is rebuilt by validating them.

Receipt indexes are bound to their absolute directory. Do not copy another installation's `.receipt-index` and assume it is valid. Have the maintainer perform a verified offline history migration when moving an existing installation, preserving primary receipts and checking unresolved results before enabling control.

## Ethernet: read-only hardware session

1. Check the controller's current address on the pendant. Connect the Windows Ethernet adapter to the intended controller network.
2. In Windows network settings, configure that adapter's IPv4 address and subnet to match the intended controller subnet. Assign a unique host address; do not reuse the robot's address or an address still used by the Mac.
3. Leave existing Internet connectivity alone when possible. Avoid overlapping Wi-Fi and robot subnets; inspect routing if traffic goes through the wrong adapter.
4. Enter the real address below. A successful connection check does not authorize movement.

```powershell
$robotAddress = Read-Host 'Controller IP address'
Test-NetConnection -ComputerName $robotAddress -Port 29999
Test-NetConnection -ComputerName $robotAddress -Port 30004
Copy-Item .\real.example.json .\real.local.json
$config = Get-Content .\real.local.json -Raw | ConvertFrom-Json
$config.robot_host = $robotAddress
$config | ConvertTo-Json -Depth 10 | Set-Content .\real.local.json -Encoding utf8
.\scripts\run.ps1 python -m unitelabs.ur5e_connector --config-path real.local.json
```

The template contains a documentation-only address; replace it. All real authorization flags must remain false. In another terminal:

```powershell
.\scripts\run.ps1 python examples/read_robot.py --port 50053
.\scripts\run.ps1 python -m unitelabs.ur5e_connector.ui.server --read-port 50053 --control-port 50053 --port 8765
```

Open **http://127.0.0.1:8765/**. This read-only panel uses one SiLA server for both clients. Verify actual firmware, current angles, normal safety state, and stopped state. The connector's REAL ROBOT label does not independently verify the pendant's Simulation switch. Confirm that switch physically, and separately confirm Remote Control when preparing any later motion.

Dashboard 29999, RTDE 30004, and authorized URScript 30002 are controller-side TCP endpoints. Local SiLA and HTTP remain on 127.0.0.1. Do not expose local servers to the LAN or disable Windows Firewall; follow local IT policy if outbound robot traffic is blocked.

## Prepare a disabled motion service

Stop the read-only panel before changing its control endpoint. Keep the read-only server running in its own terminal. In a preparation terminal, collect the address again if needed and create a separate disabled configuration:

```powershell
$robotAddress = Read-Host 'Controller IP address'
$dataPath = .\scripts\run.ps1 python -c 'import os; print(os.environ["UR5E_DATA_DIR"])'
Copy-Item .\motion.example.json .\motion.local.json
$config = Get-Content .\motion.local.json -Raw | ConvertFrom-Json
$config.robot_host = $robotAddress
$config.joint_test_receipt_directory = Join-Path ($dataPath.Trim()) 'motion-receipts'
$config | ConvertTo-Json -Depth 10 | Set-Content .\motion.local.json -Encoding utf8
.\scripts\run.ps1 python -m unitelabs.ur5e_connector --config-path motion.local.json
```

Then start the panel in another terminal:

```powershell
.\scripts\run.ps1 python -m unitelabs.ur5e_connector.ui.server --read-port 50053 --control-port 50054 --port 8765
```

This leaves real motion disabled. Follow [Hardware setup](HARDWARE.md#authorize-one-reviewed-stepped-session) before enabling a reviewed session. Config changes take effect only after restarting the corresponding idle server. Do not enable continuous motion as part of first-day Windows acceptance. The known Wrist3 +30° limit issue still applies; review every preview against the current pose.

## Record and close out a later authorized test

Use a fresh output name and replace the address placeholder. Start recording before submitting the reviewed UI motion:

```powershell
.\scripts\run.ps1 python examples/record_trajectory.py --robot-host YOUR_CONTROLLER_IP --output .\recordings\session.csv
```

Recording is output-only; it does not submit motion or stop an independent UI job if it fails. After the job is stopped, stop recording with Ctrl-C. Preserve both the CSV and summary, the panel run ID, the motion receipts, and the on-site observation.

At closeout, verify actual stopped state while connected, set all real `enable_*` flags false, and stop the real control server. Stop the read-only server when finished. If the connection has already been lost, record current state as unavailable. Do not call a historical final-state record a fresh stop verification.

## Tomorrow's workstation acceptance

- [ ] Install PowerShell 7.4+, Git, and uv; complete setup from a fresh local checkout.
- [ ] Confirm simulation server, English UI, preset preview/run, and tests.
- [ ] Confirm Ctrl-C closes an idle simulation server and the recorder without leaving child processes.
- [ ] Confirm data persists across a restart, including a path containing spaces.
- [ ] Establish Ethernet and read real firmware/angles with all motion flags false.
- [ ] Confirm the Mac's real services are stopped and prior outcomes are reconciled before taking control of the same robot.
- [ ] If authorized on site, review one bounded motion, record all six axes, observe physical motion, and verify the final stop.
- [ ] Save evidence and return real controls to disabled.

CI validates the Windows wrappers and offline tests. Its simulated server fixture uses process termination for headless cleanup because console SIGINT delivery is not reliable there. It does not certify interactive Ctrl-C handling, physical stopping, driver behavior, or safety of a proposed route.
