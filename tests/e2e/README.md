# E2E Test Harness

This directory holds the project's end-to-end test suite. Where the `unit/` tier exercises a single function and `integration/` exercises a small graph of components, `e2e/` exercises the **fully-composed service** as a black box: the real bootstrap path, the real gRPC server on a real port, the real FastAPI app, the real SMTP send path through `aiosmtplib`, and a real on-disk SQLite database.

## What lives here

```
tests/e2e/
├── README.md                       # this file
├── conftest.py                     # auto-`slow` marker; re-exports fixtures
│
├── happy_path/                     # BeginRun → submissions → FinalizeRun → email delivered
├── orphan_path/                    # BeginRun → quiet stages → sweeper fires → ORPHANED
├── resend/                         # Happy path → admin POST /runs/{id}/resend → second email
└── admin/                          # Login → admin user CRUD → audit viewer → template inspection
```

Each subdirectory holds one or more test files. The shared fixtures live in `tests/fixtures/email.py` and `tests/fixtures/service.py`; this directory's `conftest.py` re-exports them.

## The four scenarios at a glance

| Path | Drives | Asserts |
|---|---|---|
| `happy_path` | `BeginRun` + 2× `SubmitStageReport` + `FinalizeRun` via gRPC | An SMTP message lands in `smtp_capture.messages`; the run reaches `RunState.SENT`; an `email.html` is on disk under the report store; `SEND_REPORT` audit row present. |
| `orphan_path` | `BeginRun` via gRPC; *no* stage submissions; start `sweeper_loop` with a tight timeout | Run transitions to `RunState.ORPHANED`; `SWEEP_ORPHAN` audit row present; no SMTP message captured. |
| `resend` | Full happy path → admin login → `POST /runs/{run_id}/resend` | A *second* SMTP message captured; `RESEND_REPORT` audit row present alongside the original `SEND_REPORT`. |
| `admin` | Admin login → `POST /admin/users` → `POST /admin/users/{id}/password` → `GET /admin/audit` → `GET /templates` | New user persisted; password hash rotated; audit viewer surfaces both `CREATE_USER` and `UPDATE_USER` records; template viewer enumerates all manifest entries. |

The four scenarios collectively exercise every public surface of v1: the gRPC pipeline-side API, the FastAPI dashboard's read + write routes, the admin surfaces (gate + user CRUD + audit viewer + template inspection), the SMTP delivery path, the filesystem report store, and the orphan sweeper.

## The `running_service` fixture

The centerpiece is `running_service`, defined in `tests/fixtures/service.py`. Per test it:

1. **Writes a TOML config tree** under `tmp_path` (top-level `config.toml`, `tags.toml`, `templates.toml`, plus three Jinja2 source files). The `[mail.smtp]` section points at the `smtp_capture` fixture's bound port. See `tests/fixtures/config.py::write_e2e_config`.
2. **Loads the config** through the production `load_config()` function (so the loader/validator chain is part of the e2e surface).
3. **Calls `build_service(config)`** — the production composition root. Real `SqliteUnitOfWorkFactory`, real `Argon2PasswordHasher`, real `AiosmtplibMailer`, real `FilesystemReportStore`, real `SystemClock`.
4. **Starts a `grpc.aio.server()`** on `127.0.0.1:0` (OS-assigned port) and registers the servicer.
5. **Builds the FastAPI app** via `create_app(service)` and wraps it in `httpx.AsyncClient(transport=ASGITransport(app=app))` — no real port for the dashboard, just an in-process ASGI surface.
6. **Yields a `RunningService` handle** with `service`, `grpc_stub`, `dashboard_client`, and `smtp_capture` attributes.
7. **Tears down in reverse order**: close the dashboard client, close the gRPC channel, stop the gRPC server, call `shutdown_service` (drains background tasks + closes the SQLite connection), and `await asyncio.sleep(0)` to let Windows ProactorEventLoop clean up sockets.

The sweeper loop is **constructed but not started** by default (matching the production bootstrap). Orphan-path tests start it explicitly — see the orphan_path test file.

## The SMTP capture

`tests/fixtures/email.py::smtp_capture` starts an `aiosmtpd.controller.Controller` on `127.0.0.1:0` and yields a `SmtpCapture` object whose `messages` list captures every successfully-received SMTP envelope. Helpers:

* `SmtpCapture.wait_for(count, *, timeout_seconds=5.0)` — busy-wait until N messages are captured. Necessary because the service's SMTP send happens asynchronously on the scheduler after `FinalizeRun` returns.
* `_CapturedMessage.parsed()` — the raw bytes parsed as a `email.message.Message`.
* `_CapturedMessage.subject` — convenience accessor for the `Subject:` header.
* `_CapturedMessage.body_html` — convenience accessor for the first `text/html` body part as a UTF-8 string.

Why aiosmtpd rather than a TCP-listener stub: aiosmtpd is the canonical reference SMTP server in the asyncio ecosystem (same maintainers as `aiosmtplib`), handles the SMTP grammar correctly out of the box (`EHLO` / `MAIL FROM` / `RCPT TO` / `DATA` / `QUIT`), and gives us a real `Envelope` we can introspect rather than a hand-rolled byte parser. Dev-dependency cost is small; test fidelity gain is large.

## Adding a new e2e test

1. **Pick the right subdirectory** (`happy_path`, `orphan_path`, `resend`, or `admin`). If your test doesn't fit any of the four, lean toward `happy_path` for "everything works" assertions or `orphan_path` for sweeper-related ones; if neither fits, propose a fifth directory in a ROADMAP follow-up rather than improvising.
2. **Write `test_<topic>.py`** in that directory. Use the `running_service` fixture; that's almost always the only fixture you need.
3. **Drive the service** through the public surfaces:
   * gRPC: `running_service.grpc_stub.BeginRun(...)`, `.SubmitStageReport(...)`, `.FinalizeRun(...)`.
   * Dashboard: `await running_service.dashboard_client.post("/login", json={...})` etc.
   * SMTP: `running_service.smtp_capture.wait_for(1)` + assert on `running_service.smtp_capture.messages`.
4. **Reach into `running_service.service`** when you need to assert internal state directly — e.g., open a UoW to query `audit_log`. Don't drive the service through internal use cases (that's an integration test, not an e2e test); always go through gRPC or the dashboard.
5. **Tag with `@pytest.mark.requirement(...)`** linking to the L1/L2/L3 statement(s) the test verifies. e2e tests typically anchor at the highest meaningful L1 since they exercise the full vertical (e.g., `L1-RUN-001` for the happy-path lifecycle).

## What e2e tests *don't* do

* **Don't mock anything inside the service.** If a test needs to mock the mailer, it's an integration test, not e2e. The whole point is exercising the real adapters.
* **Don't drive the service through internal use cases.** Use the public surfaces.
* **Don't rely on tight timing.** SMTP delivery is async; use `smtp_capture.wait_for(N)` with a generous timeout.
* **Don't share state between tests.** Each test gets a fresh `tmp_path`, fresh SQLite, fresh aiosmtpd, fresh Service. If you find yourself wanting session-scope, the test probably belongs at a different tier.

## Running the suite

```bash
# Just e2e (slow):
poetry run pytest tests/e2e/

# Everything except e2e (what CI runs on PRs):
poetry run pytest -m "not e2e"

# Everything (what CI runs on main):
poetry run pytest
```

E2E tests are auto-marked `slow`, so the standard "fast dev loop" command (`pytest -m "unit and not slow"`) skips them.
