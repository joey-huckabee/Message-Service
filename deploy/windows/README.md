# Message-Service — Windows deployment via NSSM

This document describes the procedure for installing Message-Service as a Windows Service using [NSSM](https://nssm.cc/) (the Non-Sucking Service Manager).

## Prerequisites

- Windows Server 2019 or later (x86_64)
- Python 3.10 or later installed and on `PATH`
- Poetry installed and on `PATH`
- NSSM binary (`nssm.exe`) placed in a known location, e.g., `C:\Tools\nssm\nssm.exe`
- An administrator account for installation

## Installation procedure

### 1. Install application

Unpack the Message-Service distribution to `C:\Program Files\MessageService\`. The directory layout should match:

```
C:\Program Files\MessageService\
├── .venv\
├── src\
├── config\
│   └── config.toml
├── pyproject.toml
└── poetry.lock
```

From an elevated PowerShell in the install directory:

```powershell
poetry install --only main
```

### 2. Create service account (optional but recommended)

Create a local account with the minimum required privileges:

```powershell
net user MessageServiceSvc /add /passwordreq:yes /active:yes
```

Grant the account write access to the data and log directories it will use (as declared in `config.toml`).

### 3. Register the service

From an elevated PowerShell:

```powershell
C:\Tools\nssm\nssm.exe install MessageService `
  "C:\Program Files\MessageService\.venv\Scripts\message-service.exe" `
  --config "C:\Program Files\MessageService\config\config.toml"

C:\Tools\nssm\nssm.exe set MessageService DisplayName "Message Service"
C:\Tools\nssm\nssm.exe set MessageService Description "ETL pipeline report aggregator"
C:\Tools\nssm\nssm.exe set MessageService Start SERVICE_AUTO_START

# Redirect stdout/stderr to log files
C:\Tools\nssm\nssm.exe set MessageService AppStdout "C:\ProgramData\MessageService\logs\stdout.log"
C:\Tools\nssm\nssm.exe set MessageService AppStderr "C:\ProgramData\MessageService\logs\stderr.log"
C:\Tools\nssm\nssm.exe set MessageService AppRotateFiles 1
C:\Tools\nssm\nssm.exe set MessageService AppRotateBytes 10485760

# Graceful shutdown: send CTRL_BREAK, then wait 30s before force-kill
C:\Tools\nssm\nssm.exe set MessageService AppStopMethodConsole 30000

# Run under the service account
C:\Tools\nssm\nssm.exe set MessageService ObjectName ".\MessageServiceSvc" <password>
```

### 4. Start the service

```powershell
net start MessageService
```

## Standard lifecycle operations

| Operation | Command                                       |
|-----------|-----------------------------------------------|
| Start     | `net start MessageService`                    |
| Stop      | `net stop MessageService`                     |
| Restart   | `net stop MessageService && net start MessageService` |
| Status    | `sc query MessageService`                     |

## Uninstallation

```powershell
net stop MessageService
C:\Tools\nssm\nssm.exe remove MessageService confirm
```

## Troubleshooting

- **Service fails to start** — check `C:\ProgramData\MessageService\logs\stderr.log` for configuration validation failures (see L1-CFG-002).
- **Graceful shutdown hangs** — the service should respond to CTRL_BREAK within the configured shutdown grace period (see L2-DEP-006). If it does not, check that in-flight gRPC calls are completing.
- **Event log entries** — NSSM writes service lifecycle events to the Windows Application event log under source "nssm".
