# Windows install demonstration — verification procedure

This document is the verification artifact required by **L3-DEP-009**: a procedural walkthrough of a clean Windows install of Message-Service via NSSM, from unpack to running service. An operator follows this document on a fresh Windows host, ticks off the checkpoints, and signs the attestation block at the end. The completed (signed) document is the demonstration evidence.

The procedure mirrors `deploy/windows/README.md`; this document adds the **checkpoints** and **operator-attestation form** that turn the README into a verification artifact.

## Pre-conditions

A clean Windows host meeting the prerequisites listed in `deploy/windows/README.md`:

- Windows Server 2019 or later (x86_64).
- Python 3.12 or later installed and on `PATH`.
- Poetry installed and on `PATH`.
- NSSM binary (`nssm.exe`) at a known location (e.g., `C:\Tools\nssm\nssm.exe`).
- An administrator account.

## Procedure + checkpoints

### Step 1 — Unpack distribution

Unpack the Message-Service distribution to `C:\Program Files\MessageService\`.

```powershell
Expand-Archive -Path message-service-X.Y.Z.zip -DestinationPath "C:\Program Files\MessageService"
```

**Checkpoint 1.1**: `Test-Path "C:\Program Files\MessageService\pyproject.toml"` returns `True`.

**Checkpoint 1.2**: `Test-Path "C:\Program Files\MessageService\poetry.lock"` returns `True`.

### Step 2 — Install dependencies

```powershell
cd "C:\Program Files\MessageService"
poetry install --only main
```

**Checkpoint 2.1**: `poetry run python -c "import message_service; print(message_service.__file__)"` prints a path under the install directory.

**Checkpoint 2.2**: `poetry run message-service --help` exits with code 0 and prints help text containing the word `config`. (This exercises **L3-DEP-016**.)

### Step 3 — Provision configuration

Place a valid `config.toml` at `C:\Program Files\MessageService\config\config.toml`. (Use `config/config.toml.example` from the distribution as a starting point.)

**Checkpoint 3.1**: The config file exists at the expected path.

**Checkpoint 3.2**: `poetry run python -c "from message_service.config.loader import load_config; from pathlib import Path; load_config(Path('C:/Program Files/MessageService/config/config.toml')); print('config valid')"` exits 0 and prints `config valid`.

### Step 4 — Create service account (recommended)

```powershell
net user MessageServiceSvc /add /passwordreq:yes /active:yes
```

Grant the account write access to the data and log directories declared in `config.toml`.

**Checkpoint 4.1**: `Get-LocalUser MessageServiceSvc` returns the account.

### Step 5 — Register the service

Run the full sequence of `nssm` commands from `deploy/windows/README.md` Step 3. The required commands per **L3-DEP-008** are:

- `nssm.exe install MessageService <path-to-message-service.exe> --config <path-to-config.toml>`
- `nssm.exe set MessageService DisplayName "Message Service"`
- `nssm.exe set MessageService Description "ETL pipeline report aggregator"`
- `nssm.exe set MessageService Start SERVICE_AUTO_START`
- `nssm.exe set MessageService AppStdout <log-path>`
- `nssm.exe set MessageService AppStderr <log-path>`
- `nssm.exe set MessageService AppRotateFiles 1`
- `nssm.exe set MessageService AppRotateBytes 10485760`
- `nssm.exe set MessageService AppStopMethodConsole 30000`
- `nssm.exe set MessageService ObjectName ".\MessageServiceSvc" <password>`

**Checkpoint 5.1**: `sc query MessageService` returns the service in `SERVICE_STOPPED` state.

**Checkpoint 5.2**: `nssm.exe get MessageService AppStopMethodConsole` returns `30000` (the **L3-DEP-008** required value).

### Step 6 — Start the service

```powershell
net start MessageService
```

**Checkpoint 6.1**: `sc query MessageService` returns `SERVICE_RUNNING`.

**Checkpoint 6.2**: `Test-Connection -TargetName 127.0.0.1 -TcpPort <grpc.port-from-config>` succeeds.

**Checkpoint 6.3**: `Invoke-WebRequest "http://127.0.0.1:<dashboard.port>/healthz"` returns HTTP 200 with body `{"status":"ok"}`.

**Checkpoint 6.4**: `Invoke-WebRequest "http://127.0.0.1:<dashboard.port>/metrics"` returns HTTP 200 with `Content-Type: text/plain; version=0.0.4; charset=utf-8` (per **L3-OBS-007**).

### Step 7 — Verify graceful shutdown

```powershell
net stop MessageService
```

**Checkpoint 7.1**: `net stop MessageService` returns within 30 seconds (the `AppStopMethodConsole` window). This exercises **L3-DEP-010** + **L3-DEP-011** + **L3-DEP-012**.

**Checkpoint 7.2**: The stdout log (`AppStdout` path from Step 5) contains a `service_stopping` event followed by `service_stopped`.

**Checkpoint 7.3**: `sc query MessageService` returns `SERVICE_STOPPED`.

### Step 8 — Verify restart cleans up

```powershell
net start MessageService
# Wait for "service_running" in the log.
net stop MessageService
net start MessageService
```

**Checkpoint 8.1**: The second start succeeds without manual intervention (no orphaned PID files, lock files, etc.).

**Checkpoint 8.2**: A second visit to `/healthz` returns `{"status":"ok"}`.

## Attestation

The operator filling in the attestation below confirms that every checkpoint above passed on the host described.

| Field | Value |
|---|---|
| Operator name | _____________________________________ |
| Operator role / title | _____________________________________ |
| Host (FQDN or hostname) | _____________________________________ |
| Windows version | _____________________________________ |
| Python version | _____________________________________ |
| Message-Service version (commit hash) | _____________________________________ |
| Date of demonstration (YYYY-MM-DD) | _____________________________________ |
| Time taken from Step 1 to Step 8 (minutes) | _____________________________________ |
| Issues encountered (or `none`) | _____________________________________ |

**Operator attestation**: I confirm that I executed Steps 1–8 above on the host described, and that every checkpoint passed. The completed log files for the run are archived at the path noted in "Issues encountered".

Operator signature (initial + date): _____________________________________

## Recording the demonstration

After completing the procedure:

1. Archive the stdout/stderr log files (the `AppStdout` / `AppStderr` paths from Step 5) to a location declared in your operations runbook.
2. Save a copy of this completed (signed) document next to the archived logs.
3. Reference the archive path in the next ROADMAP / release-gating review.

## Re-running the demonstration

The demonstration SHOULD be re-run on each new major Windows version the deployment expands to (e.g., Windows Server 2022 from 2019), and on each major Python version bump (e.g., 3.13 from 3.12). The expected re-run frequency is otherwise once per release: producing a fresh signed artifact at v1 tag, then again whenever a non-trivial change in `deploy/windows/README.md`, `__main__.py`, or the bootstrap composition root invalidates the prior demonstration.
