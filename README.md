# Message-Service

A Python service that collects per-stage reports from external ETL pipelines during a run, aggregates them into composite HTML reports, and delivers them by email to subscribed users when the pipeline signals completion.

## Status

**Pre-implementation.** L1 and L2 requirements are drafted; L3 decomposition and implementation have not begun. See `docs/L1-REQ.md`, `docs/L2-REQ.md`, and `docs/TRACE-MATRIX.md`.

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
│   ├── L1-REQ.md               # Level 1 SHALL statements (52 reqs)
│   ├── L2-REQ.md               # Level 2 SHALL statements (144 reqs)
│   ├── TRACE-MATRIX.md         # forward trace + coverage summary
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
├── ROADMAP.md                  # deferred items
└── README.md                   # this file
```

## Architectural philosophy

The codebase follows **ports-and-adapters (hexagonal)** architecture to satisfy Fowler's separation-of-concerns principle:

- `domain/` — pure business logic. No I/O, no framework imports. Dependencies flow *inward*; nothing in `domain/` imports from any other layer.
- `application/` — orchestrates use cases. Defines *ports* (abstract interfaces) in `application/ports/` for all outbound dependencies.
- `infrastructure/` — implements ports. All I/O lives here (SQLite, SMTP, filesystem, Jinja2, Prometheus).
- `interfaces/` — inbound adapters. gRPC servicer, FastAPI routes, CLI entry point.

A CI lint rule (see L2-PERS-010) enforces the dependency direction.

## Quickstart (once implemented)

```bash
# install
poetry install

# run tests
poetry run pytest

# lint and type-check
poetry run ruff check .
poetry run mypy src tests

# run the service
poetry run message-service --config config/default.toml
```

## Further reading

- `docs/L1-REQ.md` — what the service does
- `docs/L2-REQ.md` — how the service is structured
- `docs/TRACE-MATRIX.md` — requirements-to-implementation traceability
- `ROADMAP.md` — deferred items and future work
