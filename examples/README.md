# Message-Service — Runnable demonstration examples

Eight self-contained scenarios that drive the service end-to-end on your laptop with no external dependencies. Each scenario is one subdirectory: a `config.toml`, a `run.py` orchestrator, a `README.md` with verbatim expected output, and any templates the scenario needs.

## **No mail server is required**

Every scenario uses an **in-process SMTP server** ([`aiosmtpd`](https://aiosmtpd.aio-libs.org/), which is already a dev dependency of this project) to capture messages the service "sends." There is **no real SMTP relay**, **no Docker**, **no MailHog**, **no Mailtrap account**, **no internet access required**. The mock SMTP server starts when the demo starts, captures every message the Message-Service mailer adapter delivers, prints a preview to your terminal, and shuts down when the demo exits.

The implementation lives at [`_lib/smtp_capture.py`](_lib/smtp_capture.py); each scenario's `run.py` opens an `SmtpCapture(port=...)` context manager around the rest of the demo. You can read the captured messages as plain Python objects (`mail_from`, `rcpt_tos`, parsed `email.message.Message`, body text, attachment filenames). Nothing leaves your machine.

If you want to verify this for yourself before running anything: read `_lib/smtp_capture.py` (~150 lines) and `01-hello-world/run.py` (~80 lines). The full SMTP path is visible in those two files.

## Prerequisites

- **Python 3.12+** (the project requires it).
- **`poetry install`** completed at the repo root — this materializes `aiosmtpd`, the `message-service-proto` stubs, the gRPC client, etc.
- **Three free TCP ports per scenario**: gRPC (50051 by default), dashboard (8080 by default), SMTP capture (1025 by default). Each scenario uses non-overlapping ports if you want to run multiple scenarios in parallel — see each scenario's `config.toml` header for its specific port choice.
- **No internet access required.** Examples are designed to run on an air-gapped laptop.

Quickly check that your ports are free:

```bash
# Linux / macOS
ss -ltn | grep -E ':(1025|8080|50051) '

# Windows (PowerShell)
Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in @(1025, 8080, 50051) }
```

If anything is listening, edit the scenario's `config.toml` and shift the ports.

## How to run a scenario

From the repo root:

```bash
poetry run python examples/01-hello-world/run.py
```

Each `run.py` is the single entry point — no flags required for the basic run. The script:

1. Creates a fresh tmp SQLite database (deletes any prior).
2. Starts the in-process SMTP capture server.
3. Starts `python -m message_service` as a subprocess pointed at the scenario's config.
4. Waits for both gRPC + dashboard listeners to bind.
5. Drives the gRPC + HTTP calls.
6. Prints captured emails / dashboard responses inline so you see cause + effect on the same screen.
7. Asserts the demo's expectations using [`_lib/expectations.py`](_lib/expectations.py); prints a summary; exits 0 on success, 1 on miss.
8. Tears down the service subprocess + SMTP capture cleanly.

Expected duration: ~5–15 seconds for most scenarios; ~10 seconds for the orphan-detection scenario (which deliberately waits for the sweeper timeout).

## Scenarios

Walk these in numbered order if you're new to the service. Each builds on the last.

| # | Scenario | What it shows | Duration |
|---|---|---|---|
| 01 | [`01-hello-world/`](01-hello-world/) | Single-stage pipeline. BeginRun → SubmitStageReport → FinalizeRun → email. | ~5 s |
| 02 | [`02-multi-stage-aggregated/`](02-multi-stage-aggregated/) | 4-stage ETL with `SINGLE_AGGREGATED`. One email; aggregation template combines all stages. | ~8 s |
| 03 | [`03-per-stage-attachments/`](03-per-stage-attachments/) | Same shape as 02 but `PER_STAGE`. Each stage becomes its own attachment. | ~8 s |
| 04 | [`04-retry-flow/`](04-retry-flow/) | Stage submitted twice; second submission overwrites with `was_retry=true`. | ~6 s |
| 05 | [`05-tag-routing/`](05-tag-routing/) | Two subscribers with different tag preferences; only the matching subscriber receives the email. | ~6 s |
| 06 | [`06-orphan-detection/`](06-orphan-detection/) | Run never finalized; sweeper transitions to ORPHANED after `run_timeout_seconds=5`. | ~12 s |
| 07 | [`07-manual-resend/`](07-manual-resend/) | Complete a run, then trigger `POST /runs/{run_id}/resend`. | ~10 s |
| 08 | [`08-error-recovery/`](08-error-recovery/) | Three error cases: unknown pipeline, unknown tag, malformed declared_stages. | ~5 s |

**Recommended walk-through for an unfamiliar reader:** 01 → 02 → 06 → 05 → others as desired. 01 grounds the basic flow; 02 introduces the aggregation template; 06 demonstrates the orphan path that nobody hand-tests; 05 introduces the subscriber model.

## Layout convention

Every scenario directory contains:

- `README.md` — *Prerequisites*, *What this demonstrates*, *How to run*, *Expected output* (verbatim, with run_ids replaced by `<run_id>`), *What to look for* (3-5 lines that prove it worked), *Cleanup*, *Troubleshooting*.
- `config.toml` — service config for this scenario, with comments at the top explaining what differs from the project default.
- `run.py` — the orchestrator. Run with `poetry run python examples/NN-name/run.py`.
- `templates/` — Jinja2 templates the scenario uses (sometimes shared with `_lib/common.py`'s defaults).

Shared helpers are under [`_lib/`](_lib/) — read these once and you can predict what each scenario's `run.py` is doing:

- [`_lib/smtp_capture.py`](_lib/smtp_capture.py) — the in-process aiosmtpd server (the SMTP mock).
- [`_lib/service_runner.py`](_lib/service_runner.py) — `running_service` context manager that starts the service subprocess and waits for both listeners to bind.
- [`_lib/pretty.py`](_lib/pretty.py) — colorized timestamped output (stdlib only; respects `NO_COLOR`).
- [`_lib/expectations.py`](_lib/expectations.py) — small DSL for asserting "this happened" with ✓/✗ output and exit code 0/1.
- [`_lib/common.py`](_lib/common.py) — config / template / tag-vocabulary helpers.

## What these examples are not

These scenarios are **for understanding, not for production deployment**. The configs use simple defaults; the in-process SMTP capture is not a real mail server (no relay, no DKIM, no bounce handling); SQLite paths are tmp directories that get wiped on each run; and the demo scripts assume nobody else is hammering the service while they're running.

For production deployment guidance, see [`docs/operator-runbook.md`](../docs/operator-runbook.md). For pipeline integration, see [`docs/pipeline-integration-guide.md`](../docs/pipeline-integration-guide.md).

## Idempotency and exit codes

Each scenario's `run.py` is idempotent: running it twice produces the same result. State directories are reset on entry; the SQLite file is deleted on entry; the SMTP capture is in-process and isolated.

Exit code 0 means every expectation passed. Exit code 1 means at least one expectation failed — the failure summary tells you which. Exit code from a service crash propagates from the subprocess.

## Troubleshooting

The most common issues are port collisions and stale processes. If a scenario fails with a port-already-in-use error:

1. Check whether you have an old `python -m message_service` lingering: `pgrep -f message_service` (Linux/macOS) or `Get-Process python | Where-Object { $_.CommandLine -like "*message_service*" }` (Windows). Kill it.
2. Check whether you have an old `aiosmtpd` lingering on port 1025 (a previous demo crashed before cleanup): same flow, kill it.
3. If the ports are persistently busy, edit the scenario's `config.toml` and shift the values.

The `run.py` scripts use `signal.SIGTERM` (Linux/macOS) or `Process.terminate()` (Windows) for cleanup; both are graceful and the service drains in-flight work before exiting. If you Ctrl-C'd a run mid-stream and a process survived, the trick above will resolve it.
