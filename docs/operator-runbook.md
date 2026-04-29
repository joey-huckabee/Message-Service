# Message-Service — Operator Runbook

This document is the operational reference for running Message-Service in production. Pair it with `config/config.toml.example` (the annotated config template) and `docs/adr/001-sqlite-for-in-flight-state.md` (the architecture rationale).

## Service overview

One Python process. Two listeners (gRPC ingest + FastAPI dashboard). One SQLite database. A directory for rendered reports. An SMTP relay it talks to. That's the whole service surface.

| Surface | Default port | Protocol | Purpose |
|---|---|---|---|
| gRPC | 50051 | gRPC unary | Pipeline ingest (`BeginRun` / `SubmitStageReport` / `FinalizeRun`) |
| Dashboard | 8080 | HTTP+JSON | Operator dashboard (subscriptions, runs, audit log, admin) |
| `/metrics` | 8080 | HTTP+plain text | Prometheus scrape endpoint |

Three periodic background loops run in-process: the orphan sweeper (`L1-SWEEP-001`), the report-retention pruner (`L1-PERS-004`), and the audit-log retention pruner (`L1-OBS-003`). All three share the same `BackgroundTaskScheduler`; all three serialize through the same `asyncio.Lock`-protected SQLite connection.

## 1. Deploying

### Linux (systemd)

The shipped unit is `deploy/linux/message-service.service`. The deployment procedure is:

1. Copy the service binary tree to `/opt/message-service/` (or your chosen install root).
2. Run `poetry install --only main` inside the install root to materialize the dependency tree.
3. Create the service account: `useradd --system --no-create-home --shell /usr/sbin/nologin message-service`.
4. Create the data directories: `/var/lib/message-service/` (SQLite + rendered reports) and `/etc/message-service/` (config + tag vocabulary + template manifest), each owned by the service account with `0700` mode.
5. Drop a `config.toml` under `/etc/message-service/`, modelled on `config/config.toml.example`. Use the `${env:VARNAME}` substitution syntax for SMTP credentials so they come from `EnvironmentFile=` rather than the on-disk config.
6. Drop the systemd unit at `/etc/systemd/system/message-service.service`. The shipped unit reads `EnvironmentFile=-/etc/message-service/secrets.env` (the `-` makes the file optional so dev hosts without a secrets file still boot).
7. `systemctl daemon-reload && systemctl enable --now message-service`.

The unit declares `UMask=0077` so SQLite files inherit `0600` mode (per ADR-001 — v1 delegates file-permission setting to the deployment layer rather than `os.chmod`).

### Windows (NSSM)

The shipped artifacts are `deploy/windows/README.md` (operator instructions) and `docs/procedures/windows-install-demonstration.md` (a step-by-step transcript of a clean install for verification purposes). The procedure is:

1. Install Python 3.12+ and Poetry on the target host.
2. Copy the service tree to `C:\Program Files\Message-Service\` (or your chosen install root).
3. Run `poetry install --only main` from the install root.
4. Install [NSSM](https://nssm.cc/) and use it to register the service: `nssm install Message-Service "C:\Path\To\Python\python.exe" "-m message_service --config C:\ProgramData\Message-Service\config.toml"`.
5. Configure NSSM stdout/stderr capture if log aggregation is desired (the service writes structured JSON to stdout per `L3-OBS-002`).
6. Start the service: `nssm start Message-Service`.

The Windows-install-demonstration document includes verbatim expected output for each step, so an operator can confirm each command produced the expected result before continuing.

### Configuration verification before first start

Validate the config file standalone before starting the service:

```bash
poetry run python -c "from message_service.config.loader import load_config; load_config('/etc/message-service/config.toml')"
```

A missing key, malformed value, port collision, or unsupported value surfaces as a `ConfigurationError` with a numbered list of validation errors (per `L3-CFG-007`). Fix the config and re-run before letting systemd / NSSM cycle the service.

## 2. Day-2 operations

### Inspecting logs

The service writes structured JSON to stdout (`L3-OBS-002`). Each record carries at minimum `timestamp`, `level`, `logger`, `event`, plus call-site-specific structured fields. Notable event names:

| Event | Level | When |
|---|---|---|
| `service_starting` | INFO | Process boot, before listeners bind |
| `service_running` | INFO | Both listeners bound; periodic loops started |
| `service_stopping` | INFO | Shutdown signal received |
| `service_stopped` | INFO | All in-flight work drained, DB closed |
| `grpc_server_listening` | INFO | gRPC bound to host:port |
| `rest_server_listening` | INFO | uvicorn bound |
| `run_finalized` | INFO | `FinalizeRunUseCase` committed |
| `sweeper_tick_failed` | ERROR | Sweeper iteration's SQL query raised |
| `email_delivery_failed` | ERROR | All retries exhausted on a transient failure |
| `oversized_report_persist_failed` | WARNING | Filesystem persist of an oversized email failed (audit row still written) |
| `admin_notification_send_failed` | ERROR | The `EMAIL_SIZE_EXCEEDED` admin notification SMTP send failed |
| `signal_received` | INFO | SIGTERM / SIGINT delivered |

To filter by event:

```bash
journalctl -u message-service --since "1 hour ago" -o cat | jq 'select(.event=="run_finalized")'
```

Sensitive values are redacted by the `redact_sensitive_keys` helper before any structured log is emitted (per `L3-OBS-005` / `L3-OBS-006`). The redaction list includes `password`, `passwd`, `password_hash`, `pwd`, `secret`, `smtp_password`, `session_token`, `cookie`, `authorization`, `email_body`, `rendered_output`, `template_context`. Case-insensitive on the key.

### Metrics

Scrape `http://<dashboard-host>:8080/metrics` (no authentication required — the endpoint is intentionally unauthenticated per `L1-OBS-002`). Metrics use the `message_service_` prefix. The full set:

- `message_service_run_state_transitions_total{target_state}` (counter)
- `message_service_stage_state_transitions_total{target_state}` (counter)
- `message_service_email_delivery_outcomes_total{outcome}` (counter)
- `message_service_sweeper_iterations_total{outcome}` (counter)
- `message_service_email_size_bytes` (histogram, buckets at 1k/10k/100k/1M/10M/25M/50M)
- `message_service_run_duration_seconds` (histogram, buckets at 1/5/15/60/300/900/1800/3600)

Wire into Grafana / Prometheus per the deployment's standard. Alerts most operators want:

- `rate(message_service_email_delivery_outcomes_total{outcome="FAILURE"}[15m]) > 0.1` — sustained delivery failures
- `rate(message_service_sweeper_iterations_total{outcome="sweeper_error"}[15m]) > 0.0` — sweeper query is failing
- `histogram_quantile(0.99, message_service_run_duration_seconds_bucket) > 3600` — tail run duration creeping past one hour
- Stage state transitions counter is not advancing — pipelines have stopped submitting

### Admin operations

The dashboard exposes JSON endpoints (no HTML frontend in v1 — see `L3-DASH-005`). Operators authenticate via `POST /login` with email + password, receiving an `msp_session` cookie + a CSRF cookie. Subsequent state-changing requests must carry the CSRF token in `X-CSRF-Token` (`L3-DASH-018`).

Common admin commands (assuming `$BASE = http://localhost:8080` and successful login):

```bash
# Create an admin user (must be authenticated as admin)
curl -X POST $BASE/admin/users \
  -H "X-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" \
  --cookie "msp_session=$SESSION; msp_csrf=$CSRF" \
  -d '{"email":"alice@x","display_name":"Alice","password":"...","is_admin":true,"disabled":false}'

# Reset a user's password
curl -X POST $BASE/admin/users/42/reset-password \
  -H "X-CSRF-Token: $CSRF" \
  --cookie "msp_session=$SESSION; msp_csrf=$CSRF" \
  -d '{"new_password":"..."}'

# List recent audit events
curl "$BASE/admin/audit?limit=50&action=CREATE_USER&action=UPDATE_USER" \
  --cookie "msp_session=$SESSION"
```

The audit-log dashboard route supports query parameters: `limit` (1–200), `offset` (≥ 0), `action` (repeatable), `actor`, `resource`, `from`, `to` (both inclusive ISO-Z timestamps). Empty result sets return HTTP 200 with `[]`, not 404.

### Subscription management

Operators self-manage their delivery subscriptions via the `subscriptions` routes (`L1-SUB-001`/`002`/`003`). Tag values come from the configured `tags.vocabulary_path` TOML; unknown tags are rejected with HTTP 422. Pipeline values come from `pipelines.registered`. `GLOBAL` subscriptions match every run.

Disabling a user's account (`PATCH /admin/users/{id}` with `disabled=true`) removes them from the recipient resolution but **does not delete their subscription rows** (`L3-SUB-018`). Re-enabling restores delivery without re-opt-in.

## 3. Common failure modes

### SMTP relay unreachable

**Symptom**: `email_delivery_failed` ERROR logs; `message_service_email_delivery_outcomes_total{outcome="FAILURE"}` rising.

**Behaviour**: the mailer classifies (`L3-MAIL-005`/`006`/`007`) and retries transient failures with exponential backoff (`L3-MAIL-009` — `min(max_interval, initial_interval * 2^(attempt-1))`, default `2s, 4s, 8s, 16s, 32s, 64s`, capped at `300s`, max 5 retries). If retries exhaust, the run transitions to `FAILED` with `failure_reason="EMAIL_DELIVERY"` recorded in the audit row.

**Operator action**: investigate the relay (DNS, network, SMTP auth). Once recovered, the failed runs do NOT auto-retry — manual resend via `POST /runs/{run_id}/resend` is the recovery path. The resend path re-renders from the saved Stage context (it does not require pipeline-side resubmission).

### SQLite write contention

**Symptom**: gRPC handlers see elevated latency; dashboard responses slow.

**Cause**: every state-changing operation acquires the `asyncio.Lock` from `SqliteUnitOfWorkFactory` before opening a transaction (per `L2-PERS-004`). The lock serializes all writers across the gRPC ingest, the sweeper, both pruners, and dashboard write paths. Sustained contention indicates throughput exceeding what the single-shared-connection model handles.

**Operator action**: examine `message_service_run_state_transitions_total` rate. If sustained > 10/s with multiple concurrent ETL pipelines, the workload is approaching the pool-vs-mutex re-evaluation threshold (per `docs/archive/connection-pool-architecture.md`). Short-term: stagger pipeline schedules. Long-term: open the architectural discussion to revisit `L2-PERS-004`.

### Sweeper-tick error patterns

**Symptom**: `sweeper_tick_failed` ERROR logs; `message_service_sweeper_iterations_total{outcome="sweeper_error"}` advancing.

**Cause**: the sweeper's `list_expired` query raised. Most commonly: SQLite database file became unreadable (filesystem permission flip), or a migration was applied that broke the query's column references.

**Behaviour**: the sweeper loop catches the error, increments the counter, logs at ERROR, and continues to the next tick (per `L3-SWEEP-016`). It does NOT crash the service. But until fixed, no orphans get reclaimed.

**Operator action**: Check the SQLite file's permissions (`ls -l /var/lib/message-service/*.db*`). Check the DB schema via `sqlite3 /var/lib/message-service/service.db .schema` and confirm it matches the latest migration. Restart the service if needed; the sweeper resumes ticking on the next interval.

### Orphan-path investigation

**Symptom**: a pipeline reports a successful `BeginRun` but a run never receives the corresponding email.

**Investigation flow**:
1. Look up the run in the dashboard: `GET /runs/{run_id}`. Check the `state` field.
2. If `INITIATED`/`AGGREGATING`/`READY`/`SENDING` past the `sweeper.run_timeout_seconds` window: the sweeper either hasn't ticked yet or is failing (see above).
3. If `ORPHANED`: check the audit log for the `SWEEP_ORPHAN` row — `details.pending_stage_ids` shows which stages never submitted. Check the audit log for the disposition handler events (`NOTIFY_ADMINS` / `DISCARD_SILENTLY` per `disposition_actions` config).
4. If `FAILED`: check the audit log's `SEND_REPORT` row for `failure_reason`. The vocabulary is closed: `TEMPLATE_RENDER`, `RENDERED_SIZE_EXCEEDED`, `CONTEXT_SIZE_EXCEEDED`, `EMAIL_DELIVERY`, `EMAIL_SIZE_EXCEEDED` (per `L3-RUN-029`). Each maps to a different remediation:
   - `TEMPLATE_RENDER`: a template bug or missing context field. Check the structured log at the failure timestamp for the underlying Jinja2 exception.
   - `RENDERED_SIZE_EXCEEDED` / `CONTEXT_SIZE_EXCEEDED`: increase the relevant `templates.max_*_bytes` knob OR slim the report.
   - `EMAIL_DELIVERY`: SMTP issue (see above).
   - `EMAIL_SIZE_EXCEEDED`: report exceeded `mail.max_email_size_bytes`. The oversized rendered body is persisted to the report store (per `L3-MAIL-017`); admins receive a notification via the `mail.admin_recipients` channel.
5. Manual resend if appropriate: `POST /runs/{run_id}/resend` (only valid for `SENT` or `FAILED` states per `L3-DASH-028`).

### Authentication failures

**Symptom**: operators report 401s on dashboard requests.

**Common causes**:
- Session timeout. Default `auth.session_idle_timeout_seconds = 3600`. Re-login.
- Disabled user. Check via `GET /admin/users/{id}` (admin-only).
- Stale CSRF token. Cookies and `X-CSRF-Token` header must match. Reload the dashboard to mint a fresh CSRF cookie.

The `WWW-Authenticate: Session realm="Message-Service"` header on 401 responses identifies the auth scheme (per `L3-AUTH-012`).

## 4. Backup and restore

### What to back up

- **SQLite database** at `persistence.sqlite_path` plus its sidecar files: `<sqlite_path>-wal` and `<sqlite_path>-shm`. The WAL must be backed up alongside the main file or a recovery may lose recent transactions.
- **Config file** at the path passed to `--config` (or `MESSAGE_SERVICE_CONFIG`).
- **Tag vocabulary** at `tags.vocabulary_path`.
- **Template manifest** at `templates.manifest_path`, plus all referenced `source_path` and `context_schema_path` files.
- **Rendered reports** under `persistence.filesystem.report_directory` if forensic / resend access matters past the `report_retention_days` window (default 90).

### Backup procedure

The safe way to back up SQLite under WAL mode is the [SQLite online backup API](https://www.sqlite.org/backup.html). The simplest invocation:

```bash
sqlite3 /var/lib/message-service/service.db ".backup /var/backup/message-service/$(date -Iseconds).db"
```

This produces a consistent point-in-time copy without requiring service downtime. Repeat on whatever cadence your retention policy requires.

A naive `cp` of the database file is **NOT safe** under active load — the WAL may contain committed transactions that the main file doesn't yet reflect.

### Restore procedure

1. Stop the service: `systemctl stop message-service`.
2. Replace `persistence.sqlite_path` with the backup. **Delete the `-wal` and `-shm` sidecar files** if present in the live directory — they belong to the in-place state being replaced.
3. Start the service: `systemctl start message-service`.
4. Verify the migration runner applies cleanly via the structured-log `migration_applied` events.
5. Verify a smoke `BeginRun` → `SubmitStageReport` → `FinalizeRun` round-trip succeeds.

## 5. Upgrade procedure

1. Stop the service: `systemctl stop message-service` (Linux) / `nssm stop Message-Service` (Windows). Wait for in-flight runs to drain via the `service.shutdown_grace_period_seconds` window.
2. Back up the SQLite database (see above).
3. Pull the new release:
   ```bash
   git fetch && git checkout <new-tag>
   poetry install --only main
   ```
4. Inspect any new migrations under `src/message_service/infrastructure/persistence/migrations/`. Migrations are forward-only (`L3-PERS-005`); each runs in its own transaction.
5. Start the service. The migration runner applies any new migrations automatically on first connection.
6. Verify trace matrix is clean post-upgrade:
   ```bash
   poetry run python scripts/build-trace-matrix.py
   git diff docs/TRACE-MATRIX.md   # SHOULD be empty if the release was committed cleanly
   ```
7. Verify the service responds: `curl http://localhost:8080/metrics | grep '^message_service_'` should return non-empty.

If a migration fails, the transaction rolls back (per `L3-PERS-020`) and the service refuses to start. Inspect the error, fix forward, and retry.

## 6. Where to look for what

| Question | Where the answer lives |
|---|---|
| What does this requirement mean? | `docs/L1-REQ.md` / `L2-REQ.md` / `L3-REQ.md` |
| Which test verifies requirement X? | `docs/TRACE-MATRIX.md` |
| Why does v1 use SQLite instead of Postgres? | `docs/adr/001-sqlite-for-in-flight-state.md` |
| Why is the architecture-boundary check a static AST scan? | `docs/adr/002-hexagonal-boundary-enforcement.md` |
| What's the exception/logging philosophy? | `docs/LOGGING-AND-EXCEPTIONS.md` |
| How do I integrate a pipeline? | `docs/pipeline-integration-guide.md` |
| What's deferred to v2? | `ROADMAP.md` Part 2 (R-XXX-NNN entries) |
| How do I add a test? | `docs/test-strategy.md` |
| Is feature X intentionally missing? | `docs/L3-REQ.md` (search for "Deferred to v2") + ROADMAP Part 2 |

## 7. Five intentional v1 Partials

The trace matrix shows five L1 requirements as "Partially Implemented" by design at v1 release. Each has a named deferral entry in ROADMAP Part 2 with an explicit re-evaluation trigger:

| L1 | What's deferred | Trigger |
|---|---|---|
| L1-API-001 | Per-RPC `correlation_id` interceptor | `R-API-001` — proto package gains release cadence + trust boundary widens past ISOLAN |
| L1-AGGR-001 | Per-stage email body contributions with `position` enum | `R-AGGR-001` — pipelines need per-stage email content |
| L1-DASH-004 | Embedded Chart.js metrics dashboard | `R-DASH-004` — Playwright (or similar) test-harness in place |
| L1-ERR-002 | Error-code stability lockfile + helper script | `R-ERR-002` — external pipelines start pinning specific codes |
| L1-OBS-001 | Per-RPC + per-route correlation interceptor | `R-API-001` (shares with L1-API-001) |

These are not gaps to be papered over operationally — they're documented carve-outs. If you need any of them, ROADMAP Part 2 has the work plan and the trigger that justifies promoting the work into a future release.
