# Message-Service — Test Strategy

This document is the formal test-strategy reference for the project. It supersedes the layout sketch that previously lived at `tests/README.md`. Contributors writing new tests should start here; reviewers checking PRs should treat the conventions described below as binding.

## Tier definitions

The suite is split into five tiers plus shared fixtures. Every new test belongs in exactly one tier; the layer marker is auto-applied by `tests/conftest.py::pytest_collection_modifyitems` based on directory.

- **unit** — a single function or class, in isolation, no I/O. Target latency under 10 ms per test. The unit-tier `conftest.py` activates an I/O guard (see *I/O guard* below) that fails the test if real sockets or disk are touched.
- **integration** — multiple components cooperating against real local resources (SQLite on disk, real filesystem, in-process SMTP via `aiosmtpd`). Target latency under 1 s per test. SQLite databases live under `.pytest_tmp/` and are torn down between tests.
- **e2e** — the full service started up under `running_service` (a context manager that drives the same code path as `python -m message_service`), exercised through gRPC + HTTP, with assertions on externally-observable outcomes (email captured, audit row written, dashboard response shape). Auto-marked `slow`. Excluded from the PR-gate run; included on `main`.
- **conformance** — structural checks that the codebase itself obeys declared rules. AST scans, schema-shape inspections, marker-validity scans, and similar. These tests are the executable specification: a violation means a code change has drifted from the spec, and the fix is either reword the spec or revert the code.
- **benchmark** — performance measurements with thresholds. Excluded from default runs; opt in via `pytest -m benchmark`.

## Layout

```
tests/
├── conftest.py                     # root: marker auto-tagging, Windows event-loop quirk, report header
├── fixtures/                       # shared fixtures, imported by tier conftests
│   ├── clocks.py                   # FakeClock + frozen_clock
│   ├── config.py                   # default_config, config_file
│   ├── email.py                    # in-process aiosmtpd capture
│   ├── io_guard.py                 # unit-tier I/O guard (sockets + aiosqlite)
│   ├── loggers.py                  # null_logger, capture_logs, assert_no_sensitive_leaks
│   ├── persistence.py              # sqlite_db_path, temp_report_store
│   ├── proto_builders.py           # begin_run_request(), submit_stage_report_request(), …
│   └── service.py                  # running_service (e2e), grpc_stub, dashboard_client
│
├── unit/                           # I/O-guarded; pure
│   ├── conftest.py                 # activates I/O guard
│   ├── domain/                     # state machines, aggregates, value objects
│   ├── application/                # use cases, ports, port-contract checks
│   ├── infrastructure/             # adapters in isolation
│   ├── interfaces/                 # gRPC servicer logic, REST routes in isolation
│   ├── config/                     # loader, schema, env-var substitution
│   ├── observability/              # logging setup, redaction, metrics registration
│   └── bootstrap/                  # composition root
│
├── integration/                    # multi-component, real local resources
│   ├── conftest.py                 # tmp SQLite, temp fs, fake SMTP
│   ├── grpc/                       # in-process servicer + real grpc.aio channel
│   ├── rest/                       # httpx.AsyncClient against real FastAPI
│   ├── persistence/
│   │   ├── filesystem/             # report store, atomic-rename behaviour
│   │   └── (root)                  # connection, migration_runner, repos, UoW concurrency, pruners, audit log
│   ├── templating/                 # not yet populated; reserved for full manifest+renderer flow
│   └── (root)                      # full_pipeline, sweeper_pipeline, sweeper_action_repository
│
├── e2e/                            # full service as black box
│   ├── conftest.py                 # running_service, auto-slow marker, SMTP capture wired
│   ├── happy_path/                 # BeginRun → submissions → FinalizeRun → email
│   ├── orphan_path/                # sweeper fires → disposition applied
│   ├── resend/                     # admin-triggered resend
│   └── admin/                      # user mgmt, audit inspection
│
├── conformance/                    # structural CI gates
│   ├── test_architecture_boundaries.py     # domain/application import nothing from infrastructure/interfaces
│   ├── test_audit_log_sole_deleter.py      # only the pruner deletes audit rows
│   ├── test_clock_chokepoint.py            # only system_clock + migration_runner read the wall-clock
│   ├── test_deploy_artifacts.py            # systemd unit / NSSM README shape
│   ├── test_error_handling_discipline.py   # ruff BLE/S110/S112 enabled; no BaseException catches outside translator
│   ├── test_filterwarnings_policy.py       # `error` is in pyproject filterwarnings
│   ├── test_gitignore_hygiene.py           # the secrets the .gitignore must exclude
│   ├── test_io_guard_enforcement.py        # the unit-tier I/O guard itself
│   ├── test_pathlib_enforcement.py         # ruff PTH rule active; no string-concatenated paths
│   ├── test_report_pruner_sole_deleter.py  # only the report pruner unlinks report files
│   ├── test_sweeper_handler_registration.py
│   └── test_trace_matrix_check_mode.py     # docs/TRACE-MATRIX.md is up to date
│
└── benchmarks/                     # excluded from default run
```

## Auto-applied layer markers

`tests/conftest.py::pytest_collection_modifyitems` walks every collected test's path and applies one of `unit`, `integration`, `e2e`, `conformance`, `benchmark` based on the directory. **Do not hand-apply layer markers** — let the collector do it. Hand-applying creates inconsistency (a test under `unit/` with a manual `@pytest.mark.integration` would be doubly tagged).

The `slow` marker is auto-applied to everything under `e2e/` and may be added explicitly to integration tests that exceed ~1 s.

## Requirement markers

Every test that verifies an L1/L2/L3 SHALL statement carries a `@pytest.mark.requirement("L<N>-<CAT>-<NNN>")` decorator:

```python
@pytest.mark.requirement("L3-RUN-007")
def test_every_state_pair_respects_transition_table() -> None:
    ...
```

Multiple markers are allowed when one test verifies multiple requirements:

```python
@pytest.mark.requirement("L3-STAGE-002")
@pytest.mark.requirement("L3-STAGE-005")
def test_stages_table_primary_key_is_run_id_stage_id() -> None:
    ...
```

`scripts/build-trace-matrix.py` AST-scans every test file under `tests/` for these markers and generates `docs/TRACE-MATRIX.md`. Run it whenever you add or change markers, and commit the regenerated matrix:

```bash
poetry run python scripts/build-trace-matrix.py
```

The trace matrix `--check` mode (`tests/conformance/test_trace_matrix_check_mode.py`) fails the build if `docs/TRACE-MATRIX.md` is stale. The script's `Marker reference check` section flags markers pointing at requirement ids that don't exist in `docs/L1-REQ.md` / `L2-REQ.md` / `L3-REQ.md`.

To find every test for a given requirement (substring-prefix match):

```bash
poetry run python scripts/pytest-by-requirement.py L3-RUN-007
poetry run python scripts/pytest-by-requirement.py L3-STAGE-       # all L3-STAGE-*
```

## Fixture scoping

Fixtures live in `tests/fixtures/` as plain modules. Each tier's `conftest.py` imports the fixtures relevant to that tier — this keeps definitions in one place without forcing every tier to see every fixture (e.g., `running_service` is only visible under `e2e/`).

Default fixture scope is **function** unless an expensive build step justifies wider scope. Session-scope is reserved for immutable resources (compiled proto stubs, parsed schemas). Module-scope is for read-only resources within one file (a loaded template manifest, a parsed config).

## I/O guard (unit tier)

`tests/fixtures/io_guard.py` provides a unit-tier I/O guard activated in `tests/unit/conftest.py`. While the guard is active:

- Real socket creation raises (the `socket.socket` constructor is monkeypatched).
- `aiosqlite.connect(...)` raises (the unit tier uses pure mocks; real SQLite belongs to integration).

A small number of unit tests legitimately need real I/O — e.g., the system-clock adapter test, or a unit test of a fixture that itself opens a fake SMTP server. Those tests opt out via the `allow_io` marker:

```python
@pytest.mark.allow_io
async def test_real_grpc_aio_signal_handling() -> None:
    ...
```

The marker is registered in `pyproject.toml` and enforced by the I/O guard. Use it sparingly — it is a deliberate carve-out, not a comfort feature.

## SMTP capture

Integration and e2e tests that exercise email delivery use `aiosmtpd` running in-process to capture SMTP traffic. The fixture is `tests/fixtures/email.py::fake_smtp_server` (function-scoped — fresh server per test, no cross-test contamination). The fixture binds to a free port and exposes the captured messages as a list. e2e tests use the same shape via the `running_service` composition.

The chosen library is `aiosmtpd` (a dev dependency); the project does not require Docker, MailHog, or any external SMTP infrastructure.

## Windows event-loop quirks

`tests/conftest.py` installs a custom `_NoImplicitEventLoopPolicy` to work around a Windows-specific pytest-asyncio interaction. The default `asyncio.DefaultEventLoopPolicy.get_event_loop()` lazily creates a `ProactorEventLoop` when no loop is installed; pytest-asyncio's runner saves and restores this loop between tests but does not own or close it, so the loop's socket pair can survive until pytest's final GC pass and trigger a `PytestUnraisableExceptionWarning`. Under `filterwarnings = ["error"]` (the project-wide setting in `pyproject.toml`) that warning fails the run.

The custom policy raises `RuntimeError` instead of creating a loop, and `pytest_sessionfinish` closes any lingering bookkeeping loop. **If you touch event-loop handling in tests, keep these hooks working** — drop them and the Windows test run gets flaky.

## Coverage gate

`pyproject.toml` sets `--cov-fail-under=85` with branch coverage on the `message_service` package. The HTML report lands in `.coverage_html/`. If the gate fails, either add tests for the missing branches (the terminal report shows the gaps; `.coverage_html/index.html` has the line-by-line view) or use `--no-cov` for rapid local iteration. The current coverage is well above the gate (94.88% as of the v1 cut). Don't lower the gate to work around a drop — diagnose the cause first.

## Running subsets

```bash
# Fast dev loop — unit only, skip slow
poetry run pytest -m "unit and not slow"

# Integration smoke before commit
poetry run pytest tests/integration/

# Full suite minus e2e (what CI runs on PRs)
poetry run pytest -m "not e2e"

# Everything (what CI runs on main)
poetry run pytest

# Skip the coverage gate during rapid iteration
poetry run pytest --no-cov

# Tests for a specific requirement (substring prefix match)
poetry run python scripts/pytest-by-requirement.py L3-RUN-007
poetry run python scripts/pytest-by-requirement.py L3-STAGE-

# By path
poetry run pytest tests/unit/domain/

# By name substring
poetry run pytest -k "state_machine"
```

## Adding a new test

1. Decide the tier: does the test need real I/O? real network? the full service?
2. Find the matching subdirectory that mirrors `src/message_service/`.
3. Create `test_<topic>.py` if it doesn't exist.
4. Add `@pytest.mark.requirement("L<N>-<CAT>-<NNN>")` linking to the relevant requirement (always — every requirement-verifying test carries one).
5. Use fixtures from `tests/fixtures/` — don't invent new ones inside the test file unless they are truly local to one test.
6. Keep test functions small and specific. **One behaviour per test.**
7. Run `poetry run python scripts/build-trace-matrix.py` and commit the regenerated `docs/TRACE-MATRIX.md`.

## Conformance tests are the executable spec

The `tests/conformance/` set is intentionally not a behavioural test suite — it asserts structural invariants about the codebase itself: which modules call which primitives, which directories exist, which ruff rules are enabled, which paths are pure-pathlib. A conformance failure means the spec and code have diverged; the fix is either to revert the code change that caused the drift or, if the new behaviour is intentional, reword the corresponding L3 statement in `docs/L3-REQ.md` and adjust the conformance test in the same commit. Spec changes commit alongside code changes — never one without the other.

## Tricky cases — what goes where

- **Servicer error translation.** The translation function itself is unit-tested in `tests/unit/interfaces/grpc/test_error_mapping.py` (a `_FakeServicerContext` captures the `await context.abort(...)` call). An actual gRPC round-trip asserting the client receives the right status code is an integration test under `tests/integration/grpc/test_servicer.py`.
- **Sweeper timeout math.** The elapsed-time comparison with a `FakeClock` is a unit test under `tests/unit/application/use_cases/test_sweeper.py`. The sweeper task actually waking up, scanning a real SQLite database, and transitioning a run to ORPHANED is the e2e `orphan_path` suite.
- **Email size enforcement.** Byte-counting a MIME message is a unit test under `tests/unit/infrastructure/email/test_aiosmtplib_mailer.py`. The full path from FinalizeRun through size check to audit write and admin notification is exercised in unit tests under `test_assemble_and_deliver.py` (with mocked mailer) plus an e2e flow.
- **Concurrency contracts.** The `L3-PERS-021` UoW serialization contract is verified via real concurrent writes in `tests/integration/persistence/test_unit_of_work_concurrency.py`. Two coroutines open a UoW against a shared factory, each performs a non-trivial write, and both must commit cleanly. The lock is structural — without it the second coroutine's `BEGIN` raises `cannot start a transaction within a transaction`, which is exactly the failure the contract guards against.
- **Race-prone test patterns.** Don't poll. Drive deterministic sequencing instead — `service.sweeper.tick()` + `dispatch_pending()` synchronously rather than awaiting their effects via background loops + polling. The orphan-path e2e suite is the canonical example (Increment 27h refactor).

## SLOC reporting

`scripts/build-trace-matrix.py`'s output includes a per-category `(verified / total)` count. The matrix's `Status rollup` section gives the authoritative L1 / L2 / L3 status. Test-count and coverage numbers come from pytest itself; report them from the run output rather than hand-counting.
