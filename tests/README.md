# Message-Service — Test Suite

This directory is organized into five tiers plus shared fixtures, mirroring the structure of `src/message_service/` wherever the source tree has a corresponding testable unit.

## Layout

```
tests/
├── conftest.py                     # root: marker auto-tagging, report header
├── README.md                       # this file
│
├── unit/                           # pure, fast, isolated — no I/O allowed
│   ├── conftest.py                 # unit-level: I/O guard, pure fixtures
│   ├── domain/
│   │   └── state_machines/         # RunState, StageState, transition tables
│   ├── application/
│   │   └── ports/                  # ABC contract checks
│   ├── infrastructure/
│   │   ├── persistence/
│   │   │   ├── sqlite/             # migration parser, connection mgr internals
│   │   │   └── filesystem/         # path sanitization, atomic write logic
│   │   ├── templating/             # sandbox config, manifest parser, schema validator
│   │   ├── email/                  # MIME composition, retry classification
│   │   ├── scheduler/              # sweeper logic (clock-injected), cleanup
│   │   └── audit/                  # audit record serialization
│   ├── interfaces/
│   │   ├── grpc/                   # servicer logic, error translation
│   │   ├── rest/                   # route handlers in isolation
│   │   └── cli/                    # argument parsing, dispatch
│   ├── config/                     # loader, validator, env-var substitution
│   └── observability/              # logging setup, redaction, metrics registration
│
├── integration/                    # multi-component, real local dependencies
│   ├── conftest.py                 # integration-level: tmp SQLite, temp fs, fake SMTP
│   ├── grpc/                       # in-process servicer + fake channel
│   ├── rest/                       # httpx.AsyncClient against real FastAPI
│   ├── persistence/
│   │   ├── sqlite/                 # real SQLite file, migration runs
│   │   └── filesystem/             # real tmp filesystem, crash recovery
│   ├── templating/                 # real manifest + real Jinja2 renderer
│   ├── email/                      # aiosmtpd in-process SMTP server
│   └── scheduler/                  # sweeper with real clock advancement
│
├── e2e/                            # full service as black box
│   ├── conftest.py                 # e2e-level: running_service fixture, auto-slow marker
│   ├── happy_path/                 # BeginRun → submissions → FinalizeRun → email
│   ├── orphan_path/                # sweeper fires → disposition applied
│   ├── resend/                     # dashboard-initiated resend
│   └── admin/                      # user mgmt, audit inspection, template viewing
│
├── conformance/                    # CI gates: structure, trace, boundaries
│   ├── test_requirement_coverage.py     # every L2/L3 has a parent; markers match real reqs
│   ├── test_architecture_boundaries.py  # domain/application import nothing from infrastructure/interfaces
│   └── test_pathlib_enforcement.py      # no os.path.join or "/" concat in src/
│
├── benchmarks/                     # pytest-benchmark; excluded from default run
│   └── test_password_hashing_speed.py
│
└── fixtures/                       # shared fixtures, imported by level conftests
    ├── clocks.py                   # FakeClock, frozen_clock
    ├── uuids.py                    # frozen_uuid, uuid_sequence
    ├── loggers.py                  # null_logger, capture_logs, assert_no_sensitive_leaks
    ├── persistence.py              # sqlite_db_path, connection pool, temp_report_store
    ├── templating.py               # minimal_manifest, sandboxed_template_env
    ├── email.py                    # fake_smtp_server, smtp_config, mime builders
    ├── config.py                   # default_config, config_file
    ├── service.py                  # running_service, grpc_stub, dashboard_client
    └── proto_builders.py           # begin_run_request(), submit_stage_report_request(), ...
```

## The five tiers in one sentence each

- **unit** — a single function or class, in isolation, no I/O, under 10 ms per test.
- **integration** — multiple components cooperating against real local resources (SQLite on disk, filesystem, in-process SMTP), under 1 s per test.
- **e2e** — the whole service started up, driven through its public interfaces, assertions on externally-observable outcomes; auto-marked `slow`.
- **conformance** — structural checks that the codebase itself obeys declared rules (trace matrix completeness, architecture boundaries, pathlib enforcement).
- **benchmarks** — performance measurements with thresholds; excluded from the default run.

## Fixture scoping

Fixtures live in `fixtures/` as plain modules. Each tier's `conftest.py` imports and re-exports the ones relevant to that tier. This keeps fixture definitions in one place without forcing every tier to see every fixture — e.g., `running_service` is only visible under `e2e/`.

Default fixture scope is **function** unless an expensive build step justifies wider scope. Session-scope fixtures are reserved for immutable resources (compiled proto stubs, loaded schemas).

## Marking tests

Every test function that verifies a requirement SHALL carry a requirement marker:

```python
import pytest

@pytest.mark.requirement("L3-RUN-007")
def test_every_state_pair_respects_transition_table():
    ...
```

Multiple markers are permitted when one test covers multiple requirements:

```python
@pytest.mark.requirement("L3-STAGE-003")
@pytest.mark.requirement("L3-STAGE-018")
def test_in_progress_state_has_no_inbound_edges():
    ...
```

The layer markers (`unit`, `integration`, `e2e`, `conformance`, `benchmark`) are applied automatically by the root `conftest.py` based on directory. `slow` is applied automatically to everything in `e2e/` and can be added manually to slower integration tests.

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

# Just the benchmarks
poetry run pytest tests/benchmarks/ -m benchmark

# Tests for a specific requirement
poetry run pytest -m "requirement('L3-RUN-007')"

# Tests for every requirement in a category
poetry run pytest -k "L3-TMPL"
```

## Adding a new test

1. Decide the tier: does it need I/O? network? the full service?
2. Find the matching subdirectory that mirrors `src/message_service/`.
3. Create `test_<topic>.py` if it doesn't exist.
4. Add `@pytest.mark.requirement("L<N>-<CAT>-<NNN>")` linking to the relevant requirement.
5. Use fixtures from `tests/fixtures/` — don't invent new ones inside the test file unless they are truly local.
6. Keep test functions small and specific. One behaviour per test.

## What goes where — a few tricky cases

- **Servicer error translation** — the translation function itself is unit-tested in `tests/unit/interfaces/grpc/`. An actual gRPC round-trip asserting the client receives the right status code is integration-tested in `tests/integration/grpc/`.
- **Sweeper timeout math** — the elapsed-time comparison with a `FakeClock` is a unit test under `tests/unit/infrastructure/scheduler/`. The sweeper task actually waking up, scanning a real SQLite database, and transitioning a run to ORPHANED is an integration test under `tests/integration/scheduler/`.
- **Email size enforcement** — byte-counting a MIME message is a unit test under `tests/unit/infrastructure/email/`. The full path from FinalizeRun through size check to audit write and admin notification is an e2e test under `tests/e2e/happy_path/` (or a negative-path e2e).
