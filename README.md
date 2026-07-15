# Message-Service

A Python service that collects per-stage reports from external ETL pipelines during a run, aggregates them into composite HTML reports, and delivers them by email to subscribed users when the pipeline signals completion.

## Status

**v0.1.0 — first tagged release.** The full v1 feature scope is implemented: domain + application use cases, persistence/SMTP/templating/scheduler/auth adapters, gRPC servicer + bootstrap, the FastAPI dashboard (subscription CRUD, past-run views, resend, admin template/user/audit management), the sweeper outbox with stuck-claim recovery, report + audit-log retention pruners, and Prometheus metrics. Of 67 L1 requirements, 62 are Implemented; the remaining 5 are deliberate v2 deferrals, each documented in `docs/ROADMAP.md` with a re-evaluation trigger. This first tag deliberately starts a 0.x line with a long runway up toward 1.0.0. See `CHANGELOG.md` for release history, `docs/ROADMAP.md` for forward-looking work, and `docs/TRACE-MATRIX.md` for live requirement-to-test traceability (the matrix is the single source of truth for status, per Increment 25a).

Requirement counts: **67 L1 · 192 L2 · 393 L3** across 16 categories.

## Key characteristics

- **gRPC ingest** for pipeline-facing submissions (unary RPCs over plaintext TCP in v1; mTLS on the ROADMAP)
- **FastAPI dashboard** for user-facing subscription management, past-report viewing, resend, admin template inspection, user management, audit log, and embedded Prometheus metrics
- **Jinja2 sandboxed rendering** with pre-registered manifest-managed templates referenced by name and version
- **SQLite persistence** (WAL mode) for users, subscriptions, audit log, template metadata, and in-flight run state; **filesystem persistence** for rendered reports
- **Two-slot stage contribution model** — each stage contributes a report fragment (attachment) and optional email body content
- **Two attachment modes** per run — `SINGLE_AGGREGATED` (one composite attachment) or `PER_STAGE` (one attachment per stage)
- **Asyncio orphan sweeper** with configurable global timeout and policy-driven disposition (send partial, discard, notify subscribers or admins)
- **Cross-platform** — Linux (systemd) and Windows (NSSM) deployment

## Repository layout

```
Message-Service/
├── .github/workflows/          # CI pipelines
├── config/                     # example configuration files
├── deploy/
│   ├── linux/                  # systemd unit file
│   └── windows/                # NSSM installation procedure
├── docs/
│   ├── L1-REQ.md               # Level 1 SHALL statements (65 reqs)
│   ├── L2-REQ.md               # Level 2 SHALL statements (186 reqs)
│   ├── L3-REQ.md               # Level 3 SHALL statements (361 reqs)
│   ├── TRACE-MATRIX.md         # forward trace + coverage summary
│   ├── LOGGING-AND-EXCEPTIONS.md  # exception hierarchy and log conventions
│   ├── adr/                    # architecture decision records
│   ├── analysis/               # verification-method Analysis artifacts
│   ├── diagrams/               # PlantUML sources
│   ├── procedures/             # verification-method Demonstration artifacts
│   └── reviews/                # verification-method Inspection artifacts
├── scripts/                    # maintenance and tooling scripts
├── src/
│   └── message_service/
│       ├── domain/             # pure business logic, no I/O
│       ├── application/        # use cases and port interfaces
│       │   └── ports/          # abstract repository and adapter interfaces
│       ├── infrastructure/     # concrete adapters (SQLite, SMTP, Jinja2, etc.)
│       ├── interfaces/         # inbound adapters (gRPC, FastAPI, CLI)
│       ├── config/             # TOML loading and schema validation
│       ├── observability/      # logging and metrics setup
│       └── templates/          # shipped Jinja2 templates and manifest
├── tests/
│   ├── unit/                   # pure, fast, isolated
│   ├── integration/            # cross-component, local fixtures
│   ├── e2e/                    # full service exercised end-to-end
│   └── fixtures/               # shared test fixtures
├── pyproject.toml              # Poetry project + tooling config
├── CHANGELOG.md                # release history (Keep a Changelog)
└── README.md                   # this file
```

## Architectural philosophy

The codebase follows **ports-and-adapters (hexagonal)** architecture to satisfy Fowler's separation-of-concerns principle:

- `domain/` — pure business logic. No I/O, no framework imports. Dependencies flow *inward*; nothing in `domain/` imports from any other layer.
- `application/` — orchestrates use cases. Defines *ports* (abstract interfaces) in `application/ports/` for all outbound dependencies.
- `infrastructure/` — implements ports. All I/O lives here (SQLite, SMTP, filesystem, Jinja2, Prometheus).
- `interfaces/` — inbound adapters. gRPC servicer, FastAPI routes, CLI entry point.

A CI lint rule (see L2-PERS-010) enforces the dependency direction.

## Technology stack

- **Python 3.12+** baseline (tested on 3.12 and 3.13)
- **gRPC** via grpcio 1.78, grpcio-tools 1.80 (unary RPCs in v1)
- **FastAPI 0.136 + Starlette 0.47** for the dashboard
- **Pydantic 2.12** for config schemas
- **aiosqlite** for async SQLite persistence (no ORM)
- **aiosmtplib 4.0** for SMTP delivery
- **Jinja2 3.1** SandboxedEnvironment for template rendering
- **structlog 25.1** for structured JSON logging
- **argon2-cffi** for password hashing
- **prometheus-client** for embedded metrics

Dev tooling: **ruff 0.15 · mypy 1.20 · pytest 9.0 · pytest-cov 7.1 · pre-commit 4.0**.

## Quickstart

```bash
# install
poetry install
poetry run pre-commit install

# run tests with coverage (gated at ≥85%)
poetry run pytest

# lint and type-check (what the pre-commit hooks run)
poetry run ruff format .
poetry run ruff check . --fix
poetry run mypy src tests

# regenerate the requirements-to-tests trace matrix
poetry run python scripts/build-trace-matrix.py

# run the service (gRPC server runs today; FastAPI dashboard pending Increment 17+)
poetry run message-service --config config/default.toml
```

See `CONTRIBUTING.md` for the full pre-commit-passing command sequence.

## Further reading

- `docs/L1-REQ.md` / `L2-REQ.md` / `L3-REQ.md` — what the service does, how it's structured, and the implementation-level SHALL statements
- `docs/TRACE-MATRIX.md` — auto-generated requirements-to-tests forward trace
- `docs/LOGGING-AND-EXCEPTIONS.md` — exception hierarchy and structured logging conventions
- `CONTRIBUTING.md` — pre-commit-passing command sequence and coding conventions
- `CHANGELOG.md` — release history (Keep a Changelog format)
- `docs/ROADMAP.md` — forward-looking deferred items and future work
