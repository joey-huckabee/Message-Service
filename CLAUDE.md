# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Message-Service is a Python 3.12+ service that collects per-stage reports from external ETL pipelines via gRPC, aggregates them into Jinja2-rendered HTML, and emails the result to subscribed users. A FastAPI dashboard covers subscription management, resend, admin, and audit. The project is requirements-driven: every test links back to an L1/L2/L3 SHALL statement via `@pytest.mark.requirement(...)`.

## Common commands

Run from the repo root. These mirror the pre-commit pipeline; run them in this order before committing.

```bash
poetry run ruff format .
poetry run ruff check . --fix
poetry run mypy src tests                       # strict mode, gated by pyproject
poetry run pytest                               # coverage gate is --cov-fail-under=85
poetry run python scripts/build-trace-matrix.py # regenerate docs/TRACE-MATRIX.md
poetry run pre-commit run --all-files           # sanity sweep
```

Running subsets of the test suite:

```bash
poetry run pytest -m "unit and not slow"         # fast dev loop (layer markers auto-applied by path)
poetry run pytest -m "not e2e"                   # what CI runs on PRs
poetry run pytest --no-cov                       # skip the 85% gate during rapid iteration
poetry run pytest tests/unit/domain/             # by path
poetry run pytest -k "state_machine"             # by name substring

# Find tests for a requirement (substring prefix match):
poetry run python scripts/pytest-by-requirement.py L3-RUN-007
poetry run python scripts/pytest-by-requirement.py L3-STAGE-          # all L3-STAGE-*
```

Running the service (once fully wired):

```bash
poetry run message-service --config config/default.toml
# or
MESSAGE_SERVICE_CONFIG=config/dev-config.toml python -m message_service
```

## Architecture

**Hexagonal / ports-and-adapters.** Dependencies flow *inward* only. The architecture boundary is enforced by a conformance test at `tests/conformance/test_architecture_boundaries.py`; do not add imports that violate it.

- `src/message_service/domain/` — pure business logic. No I/O, no framework imports. Contains the aggregates (`run`, `stage`, `subscription`, …), the `RunState` / `StageState` state machines, and the `MessageServiceError` hierarchy in `domain/errors.py`.
- `src/message_service/application/` — use cases (`begin_run`, `submit_stage_report`, `finalize_run`, `assemble_and_deliver`, `sweeper`) and the port interfaces they depend on (`application/ports/`: `Clock`, `Mailer`, repositories, `UnitOfWork`, `BackgroundTaskScheduler`, `DispositionHandler`, `TagVocabulary`, `TemplateRenderer`, `TemplateRepository`, `AuditLog`).
- `src/message_service/infrastructure/` — concrete adapters implementing the ports. SQLite (raw SQL via `aiosqlite`, no ORM; migrations in `persistence/migrations/*.sql` applied by `migration_runner.py`), `aiosmtplib` mailer, sandboxed Jinja2 renderer, asyncio scheduler + sweeper loop, `SystemClock`.
- `src/message_service/interfaces/` — inbound adapters: gRPC servicer + error translator, FastAPI routes/auth/html, CLI (`__main__.py` is the process entrypoint).
- `src/message_service/bootstrap/service.py` — composition root. Constructs every adapter and use case in a strict order (logging → SQLite/migrations → clock → stateless adapters → mailer → scheduler → UoW factory → use cases) and returns a frozen `Service` dataclass. No globals, no service locator. `build_service(config)` / `shutdown_service(service, timeout)` are the lifecycle hooks called from `__main__.py`.

The gRPC servicer and the FastAPI app both receive the `Service` and reach in for what they need. Adapter instances are exposed alongside use cases because some routes (e.g., dashboard "list runs") need repo access without a use case in front.

## Project-specific conventions

These are non-obvious and enforced either by CI, by reviewers, or by ruff configuration. New code MUST follow them.

### Every test carries a requirement marker

```python
@pytest.mark.requirement("L3-RUN-007")
def test_every_state_pair_respects_transition_table(): ...
```

Multiple markers are permitted when a test verifies multiple requirements. `scripts/build-trace-matrix.py` AST-scans these markers to populate `docs/TRACE-MATRIX.md`; run it whenever you add/change markers or requirement statements, and commit the regenerated matrix. The `Marker reference check` section of its output flags markers pointing at nonexistent requirement ids.

Layer markers (`unit`, `integration`, `e2e`, `conformance`, `benchmark`) are auto-applied by `tests/conftest.py::pytest_collection_modifyitems` based on the test's path — do not hand-apply them.

### Exceptions: hierarchy + `error_code` + structured `details`

Every raised exception in the codebase derives from `message_service.domain.errors.MessageServiceError`. Each leaf class has a `ClassVar[str] error_code` that matches a value of the `ErrorCode` enum in `message_service.proto` (so the gRPC boundary can translate without the domain importing proto types). Always pass a `details` dict with machine-parseable diagnostic fields; that dict flows to gRPC trailing metadata, structured logs, and the dashboard error display.

```python
raise UnknownTagError(
    f"tag {tag!r} not in configured vocabulary",
    details={"tag": tag, "allowed_tags": sorted(vocabulary)},
)
```

See `docs/LOGGING-AND-EXCEPTIONS.md` for the full hierarchy and gRPC-status mapping.

### Clock is injected — never call `datetime.now` directly

Domain and application code takes a `Clock` port. Only `infrastructure/time/system_clock.py` (`SystemClock`) is allowed to call `datetime.now(tz=UTC)`. Persisted timestamps use the project's `iso_z()` helper (`"Z"` suffix, not `"+00:00"`).

### Filesystem access via `pathlib.Path`

The ruff `PTH` rule (flake8-use-pathlib) is enabled and enforced by `tests/conformance/test_pathlib_enforcement.py`. No `os.path.join`, no string-concatenation of paths.

### Structured logging

`structlog` events are snake_case nouns/verb phrases followed by keyword fields. No f-strings in the message.

```python
logger.info("run_finalized", run_id=run_id, stage_count=len(stages))
```

### Docstrings

Google convention (Args / Returns / Raises sections); pydocstyle rules `D*` are enabled by ruff. Tests are exempt (`tests/**/*.py = ["D"]`). `D100`/`D104` are globally ignored for init files.

## Coverage gate

`pyproject.toml` sets `--cov-fail-under=85` with branch coverage on `message_service`. HTML report lands in `.coverage_html/`. If the gate fails, either add tests for the missing branches (the terminal report shows the gaps, or open `.coverage_html/index.html`) or use `--no-cov` for local iteration. `ROADMAP.md` describes the intended ratchet; don't lower the gate to work around a drop.

## Windows event-loop quirk

`tests/conftest.py` installs a custom `_NoImplicitEventLoopPolicy` and closes the lingering Proactor bookkeeping loop in `pytest_sessionfinish`. This is a deliberate workaround for a `PytestUnraisableExceptionWarning` that appears under `filterwarnings = ["error"]` on Windows. If you touch event-loop handling in tests, keep these hooks working.

## Git conventions

Do **not** add `Co-Authored-By: Claude ...` trailers to commit messages on this repo, even if the harness's default instructions suggest it. Commit messages are the human-authored record of intent; tool attribution belongs in tool logs, not history. This overrides the default trailer behavior.

## Derived / generated files — do not hand-edit

| File | Regenerate with |
|------|------|
| `docs/TRACE-MATRIX.md` | `poetry run python scripts/build-trace-matrix.py` |
| `poetry.lock` | `poetry lock` (or `poetry add`/`remove`) |

## Further reading

- `CONTRIBUTING.md` — full pre-commit sequence, failure-mode troubleshooting, SLOC-reporting policy.
- `docs/L1-REQ.md` / `L2-REQ.md` / `L3-REQ.md` — authoritative SHALL statements (67 / 192 / 390 reqs across 16 categories).
- `docs/TRACE-MATRIX.md` — which tests verify which requirements.
- `docs/LOGGING-AND-EXCEPTIONS.md` — exception hierarchy and logging conventions.
- `tests/README.md` — test-tier layout and fixture-scoping conventions.
- `ROADMAP.md` — deferred items; scope decisions that deliberately live outside v1.
