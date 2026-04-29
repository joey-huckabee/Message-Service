# ADR-001 — SQLite + WAL for in-flight run state

- **Status**: Accepted
- **Date**: 2026-04-29 (initial v1 release)
- **Deciders**: Project lead
- **Related requirements**: L1-PERS-001 (durable state), L2-PERS-002 (WAL pragmas), L2-PERS-004 (single-shared-connection serialization), L3-PERS-005 (forward-only migrations), R-DELIVER-001 (deferred outbox), R-PERS-001 (deferred cross-host replication)
- **Supersedes**: N/A
- **Superseded by**: N/A

## Context

Message-Service collects per-stage reports from external ETL pipelines via gRPC, aggregates them into HTML reports, and emails the result. The collection-and-aggregation phase is **stateful**: between `BeginRun` and `FinalizeRun`, the service must remember the run's identity, the set of declared stages, the rendered context for each submitted stage, the run's lifecycle state, an audit trail of every transition, and the orphan-detection sweeper's cursor over active runs. Stage submissions arrive over arbitrary timeframes (minutes for fast pipelines; hours for slow ones), and pipelines must survive a service restart without losing in-flight runs.

The deployment target is **single-node ISOLAN**: the service is one Python process running on a host inside a trusted network alongside the ETL pipelines and SMTP relay it integrates with. There is no plan to horizontally scale the v1 service, no plan to share state across hosts, and no plan for the trusted-network assumption to relax inside the v1 lifetime.

## Decision

**Use SQLite as the in-flight state store, with WAL journaling, accessed through a single connection serialized by an `asyncio.Lock`.**

Concrete shape:

- **Database engine**: SQLite via `aiosqlite`. One database file per service instance, configured by `persistence.sqlite_path`.
- **Journal mode**: `PRAGMA journal_mode=WAL` (write-ahead logging). Set at startup; if the filesystem rejects WAL (e.g., some NFS), the service logs WARNING and continues with the fallback journal mode.
- **Synchronous level**: `PRAGMA synchronous=NORMAL`. Pairs safely with WAL; durable across crashes for committed transactions.
- **Concurrency**: one shared `aiosqlite.Connection` per service process. All writers (gRPC use cases, the orphan sweeper, the report retention pruner, the audit-log retention pruner) acquire an `asyncio.Lock` owned by `SqliteUnitOfWorkFactory` before opening a transaction. The lock is constructed lazily on first `__call__` so the factory remains event-loop-agnostic at construction.
- **Schema evolution**: forward-only migrations under `src/message_service/infrastructure/persistence/migrations/NNN_<description>.sql`. Each migration runs in its own transaction. Applied versions tracked in a `_migrations(version, name, applied_at)` table.
- **Repository pattern**: every domain aggregate has a corresponding port (`RunRepository`, `StageRepository`, `SubscriptionRepository`, `AuditLog`, etc.) defined in `application/ports/` as `abc.ABC`. The SQLite-backed implementations live under `infrastructure/persistence/`. Use cases consume the port; never the connection directly.

## Alternatives considered

### A. In-memory state with a custom write-ahead log

Hold the run state in process memory (Python dataclasses), and append every state-changing operation to an append-only log file for crash recovery. On startup, replay the log to rebuild memory state.

**Why rejected.** v1 doesn't need the throughput an in-memory store would offer (the gRPC ingest hot path is dominated by template rendering and SMTP, not persistence). The recovery code (log replay, log compaction, log corruption detection) would add substantial complexity for marginal gain. SQLite's WAL provides equivalent durability with a battle-tested implementation. If profiling later shows persistence is a bottleneck on the gRPC ingest hot path, the repository-pattern abstraction (L1-PERS-003) makes this swap possible without touching domain code; the deferred work is captured in ROADMAP Part 2 as the *In-flight run state backing profiling* item.

### B. External RDBMS (PostgreSQL)

Run the service against a PostgreSQL instance, getting MVCC, true concurrent writers, network-accessible state, and operationally familiar tooling.

**Why rejected.** Conflicts with the single-node ISOLAN deployment model. Adds an operational dependency (PostgreSQL deployment, backup, upgrade, monitoring) for a workload that doesn't exercise the engine's strengths. v1's write volume is bounded by the number of in-flight ETL runs at a given moment (small) and the audit-log insertion rate (one row per state transition, not user-volume). SQLite handles this comfortably. If a future deployment ever spans multiple hosts or shares state with other services, the repository ports allow swapping in a PostgreSQL adapter — but that's a v2-or-later concern, not a v1 design driver.

### C. Connection pool

The original requirement set described a connection-pooled SQLite — `connection_pool_size` was a configurable knob, with multiple pooled connections obtained from a depth-bounded queue. The pool had explicit exhaustion handling, observability hooks, and a tested-shape design.

**Why rejected (Increment 27).** SQLite serializes writers regardless of connection pool size: only one writer can hold the database lock at a time (WAL mode permits concurrent readers, but writers still serialize). v1's UoW pattern always writes (every state transition writes both an audit row AND a state-update row, in the same transaction), so even "read-mostly" dashboard paths hit the writer queue. A pool would deliver no write parallelism for this workload, while adding pool sizing, exhaustion handling, and depth observability complexity. Increment 27 made the architectural decision to use **single connection + asyncio.Lock**, archiving the pool design verbatim with re-evaluation triggers in `docs/archive/connection-pool-architecture.md`. The four re-evaluation triggers documented there describe the operational signals that would justify revisiting (sustained writer queue depth, multi-tenant deployment, etc.).

## Consequences

### Positive

- **Operational simplicity.** No external database to deploy, monitor, or upgrade. Backup is a file copy (with WAL handling — see the operator runbook). Restore is a file replace. Air-gapped deployments are feasible.
- **Durable across restarts.** A run started before a service crash is recoverable on the next boot. The orphan sweeper handles the residual case where the service died mid-aggregation: any run stuck past `sweeper.run_timeout_seconds` is reclaimed and routed through the configured disposition handlers.
- **Single binary deployment.** The service is one Python process with a SQLite file, an SMTP target, and a config file. The deployment artifacts (`deploy/linux/message-service.service`, `deploy/windows/`) reflect this.
- **Cheap to develop against.** Tests run against real SQLite (no mocking the persistence layer), giving high confidence that the production code path is the path under test.

### Negative

- **No horizontal scale within v1.** The `asyncio.Lock` serializes writers within a single process; running two service instances against the same database file would corrupt state. The deployment model accepts this.
- **At-least-once delivery is best-effort.** `FinalizeRunUseCase` schedules the assembly + delivery via `BackgroundTaskScheduler` (a thin wrapper around `asyncio.create_task`). If the process dies after `FinalizeRun` commits but before the assembly task completes, the email is lost (the run sticks in `READY` / `SENDING` and is reclaimed by the orphan sweeper as a `READY` orphan, which is observable but not redelivered). The future `R-DELIVER-001` outbox-row pattern would close this — the assembly use case would read from a persisted outbox table populated inside the `FinalizeRun` UoW, so a process death between commit and dispatch is recoverable on the next worker tick. v1 accepts the current at-most-once-with-orphan-recovery shape.
- **Single-host failure domain.** No replication; if the host loses its disk, in-flight state is gone. v1 accepts this; `R-PERS-001` (Litestream-style continuous replication to a standby host) is the documented evolution path. No application-layer change is required.
- **Concurrent dashboard reads serialize with writers.** Dashboard list/detail queries hold the same `asyncio.Lock` as writers (since SQLite WAL still serializes via the connection mutex). The dashboard's response latency is bounded by the slowest writer in flight. v1's audit-log + run-list query patterns are fast enough that this hasn't surfaced as an issue, but it would matter at higher operator-query volumes.

### Forces

- **Operational simplicity > horizontal scale within v1 scope.** This is the dominant force. Choosing the simpler engine costs us nothing v1 needs and saves us a deployment dependency.
- **Recovery semantics > delivery throughput.** The orphan sweeper's design (L1-RUN-006, L1-SWEEP-*) was authored before this ADR; its existence relies on persistent state surviving restarts. SQLite with WAL provides that.
- **Testability > engine power.** Tests run against real SQLite under `.pytest_tmp/`. No fakes, no mocks of the persistence layer, no engine-specific test infrastructure. A future RDBMS swap would lose this.

## Re-evaluation triggers

The decision should be revisited if any of the following hold:

1. **Multi-host deployment becomes in scope.** Single-process serialization no longer suffices.
2. **Dashboard query volume saturates the writer queue.** If operators experience dashboard latency under nominal write load, the read/writer co-serialization assumption is breaking.
3. **The audit-log table becomes the dominant write hotspot.** v1's audit pruner (Increment 30) keeps the table bounded in size; if pruning isn't fast enough, the write contention model needs revisiting.
4. **A profiling pass demonstrates SQLite write latency dominates the gRPC ingest hot path.** The deferred *In-flight run state backing profiling* item triggers; consider an in-memory store backed by a custom WAL.

When any trigger fires, the linked deferred-features entries (`R-DELIVER-001`, `R-PERS-001`, the in-memory profiling item) become the candidate evolution paths.

## References

- `docs/L2-REQ.md` — L2-PERS-002 (WAL pragmas), L2-PERS-004 (serialization), L2-PERS-005 (atomic-rename for filesystem reports)
- `docs/L3-REQ.md` — L3-PERS-001 through L3-PERS-035 (the persistence-layer obligations)
- `docs/archive/connection-pool-architecture.md` — the archived pool design with re-evaluation triggers
- `src/message_service/infrastructure/persistence/connection.py` — pragma-application code
- `src/message_service/infrastructure/persistence/unit_of_work.py` — the `asyncio.Lock`-protected UoW factory
- `tests/integration/persistence/test_unit_of_work_concurrency.py` — five tests verifying the L2-PERS-004 contract via real concurrent writes
- ROADMAP Part 2 — `R-DELIVER-001` (outbox), `R-PERS-001` (cross-host replication), and the "In-flight run state backing profiling" item
