# Linux install demonstration — verification procedure

This document is the Demonstration (D) verification artifact for a clean Linux
install of Message-Service via systemd, from unpack to running service and
graceful shutdown. It is the Linux counterpart of
`windows-install-demonstration.md` (which backs `L3-DEP-009`) and backs the
cross-platform deployment demonstration of `L1-DEP-001` / `L1-DEP-002`.

An operator follows this document on a fresh Linux host, ticks off the
checkpoints, and signs the attestation block. The completed (signed) document is
the demonstration evidence. It mirrors `deploy/linux/message-service.service`;
this document adds the **checkpoints** and **operator-attestation form** that turn
the unit file into a verification artifact.

## Pre-conditions

A clean Linux host (systemd-based; e.g. Ubuntu 22.04+ / RHEL 9+) with:

- Python 3.12 or later on `PATH`.
- Poetry installed.
- Root / `sudo` access to install a systemd unit and create a service account.

## Procedure + checkpoints

### Step 1 — Unpack distribution

Unpack the Message-Service distribution to `/opt/message-service/`.

```bash
sudo mkdir -p /opt/message-service
sudo tar -xzf message-service-X.Y.Z.tar.gz -C /opt/message-service --strip-components=1
```

**Checkpoint 1.1**: `test -f /opt/message-service/pyproject.toml && echo OK` prints `OK`.

**Checkpoint 1.2**: `test -f /opt/message-service/poetry.lock && echo OK` prints `OK`.

### Step 2 — Install dependencies

```bash
cd /opt/message-service
sudo poetry install --only main
```

**Checkpoint 2.1**: `poetry run message-service --help` exits 0 and prints help
text containing the word `config`.

### Step 3 — Provision configuration

Place the service config and (optionally) an env-file for secrets:

```bash
sudo mkdir -p /etc/message-service /var/lib/message-service /var/log/message-service
sudo cp config/default.toml /etc/message-service/config.toml
```

**Checkpoint 3.1**: `/etc/message-service/config.toml` exists and is readable by
the service account created in Step 4.

**Checkpoint 3.2**: if an env-file is used, it is at
`/etc/message-service/message-service.env` and references the environment
variable `MESSAGE_SERVICE_CONFIG` (the name the CLI reads) — NOT the short
`MSG_SERVICE_CONFIG` form.

### Step 4 — Create service account

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin message-service
sudo chown -R message-service:message-service /var/lib/message-service /var/log/message-service
```

**Checkpoint 4.1**: `id message-service` resolves; the data and log directories
are owned by that account.

### Step 5 — Register the service

```bash
sudo cp deploy/linux/message-service.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable message-service
```

**Checkpoint 5.1**: `systemctl cat message-service` shows the unit with
`Type=exec`, `Restart=on-failure`, `RestartSec=5s`, `TimeoutStopSec=30s`,
`KillSignal=SIGTERM`, and the sandboxing directives (`NoNewPrivileges=true`,
`ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`) per `L3-DEP-007`.

### Step 6 — Start the service

```bash
sudo systemctl start message-service
```

**Checkpoint 6.1**: `systemctl is-active message-service` prints `active`.

**Checkpoint 6.2**: `journalctl -u message-service --no-pager | tail` shows
`grpc_server_listening`, `rest_server_listening`, and `service_running` with no
ERROR records (exercising the single-process startup of `L1-DEP-001`).

### Step 7 — Verify graceful shutdown (L1-DEP-002)

```bash
sudo systemctl stop message-service
```

**Checkpoint 7.1**: the stop completes within `TimeoutStopSec` (no SIGKILL in the
journal); the journal shows `service_stopping` → `service_stopped`, confirming the
SIGTERM-driven graceful drain.

### Step 8 — Verify restart cleans up

```bash
sudo systemctl restart message-service
```

**Checkpoint 8.1**: the service returns to `active`; a fresh
`grpc_server_listening`/`service_running` sequence appears with no
leftover-lock or `SQLITE_BUSY` errors (the SQLite connection was closed cleanly
on the prior stop).

## Operator attestation

I confirm that I performed the procedure above on the environment described and
that every checkpoint passed as written.

- Operator name: ____________________________
- Environment (distro / systemd version): ____________________________
- Message-Service version (`git describe` or tag): ____________________________
- Date: ____________________________
- Signature: ____________________________

Any checkpoint that did NOT pass SHALL be recorded here with the observed
behavior; the demonstration is not complete until re-run to a full pass:

- Deviations: ____________________________
