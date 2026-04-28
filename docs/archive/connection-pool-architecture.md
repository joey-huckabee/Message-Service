# Archive — Connection-pool architecture for SQLite persistence

**Status**: Removed from active spec by Increment 27 (2026-04-27).
**Replaced by**: Single shared `aiosqlite.Connection` + `asyncio.Lock` around BEGIN/COMMIT (see L2-PERS-004 + L3-PERS-006/007/021 in their post-Increment-27 form).
**Why this document exists**: To preserve the original pool architecture verbatim — its requirements text, its diagram fragment, and its rationale — so a future increment that decides to revisit the pool design can lift the material back into the active spec rather than re-deriving it from scratch.

This is an *archive*, not a deferred-features entry. The pool architecture is not on the v1 ROADMAP. There is no committed plan to implement it. It may be revisited if and when the re-evaluation triggers below trip; that decision will be a deliberate architectural choice with its own ADR, not an automatic carry-over from this document.

---

## What was removed

### L2 requirement (verbatim, pre-Increment-27)

```
#### L2-PERS-004

**Parent**: L1-PERS-001
**Statement**: The service SHALL maintain a connection pool sized to accommodate concurrent gRPC servicer calls and FastAPI request handlers, with pool size controlled by configuration key `persistence.connection_pool_size`.
**Rationale**: Explicit pool sizing prevents connection exhaustion under concurrent load and gives operators a tuning knob.
**Verification Method**: Inspection (I)
```

### L3 requirements (verbatim, pre-Increment-27)

```
**L3-PERS-006** · Parent: L2-PERS-004 · Verification: T
The connection pool SHALL be backed by an `asyncio.Queue`; exhaustion blocks with `persistence.connection_acquire_timeout_seconds` (default 5s) before raising `PersistenceError`.

**L3-PERS-007** · Parent: L2-PERS-004 · Verification: T
Default `persistence.connection_pool_size` SHALL be 16; exhaustion events SHALL increment a Prometheus counter.

**L3-PERS-021** · Parent: L2-PERS-004 · Verification: T
Connection acquisition SHALL log DEBUG with the current pool depth; a test SHALL verify the debug line fires when the pool is near exhaustion.
```

### L1 config-knob enumeration (excerpt, pre-Increment-27)

From `docs/L1-REQ.md` under L1-CFG-003 ("The configuration schema SHALL include at minimum the following settings"):

```
- **Persistence**: SQLite database path; SQLite connection-pool size; rendered-report directory path; rendered-report retention duration (...); rendered-report pruner cadence and per-iteration cap.
```

The phrase "SQLite connection-pool size" was dropped from this bullet.

### C4 diagram fragment (verbatim, pre-Increment-27)

From `docs/diagrams/c4-component-persistence.puml`, line 21:

```plantuml
Component(conn_mgr, "Connection Manager", "aiosqlite", "Pool sized by persistence.connection_pool_size; WAL pragmas at startup.")
```

### Config schema field (verbatim, pre-Increment-27)

From `src/message_service/config/schema.py`:

```python
class PersistenceConfig(_FrozenForbid):
    """SQLite persistence for metadata + in-flight state (L2-PERS-001)."""

    sqlite_path: Path
    connection_pool_size: int = Field(default=16, ge=1, le=256)
    filesystem: FilesystemPersistenceConfig
```

The `connection_pool_size` field was defined with bounds `[1, 256]` and a default of 16, but no code path read it. Shipped configurations (`config/default.toml`, `config/dev-config.toml`, `config/config.toml.example`) all set `connection_pool_size = 16`.

---

## Why removed

The active v1 implementation has always been a single shared `aiosqlite.Connection` held by `SqliteUnitOfWorkFactory`, not a pool. The pool requirements / config field / diagram fragment described an architecture the code did not implement. Increment 27 was triggered by a real concurrency bug — two coroutines opening UoWs against the shared connection both call `BEGIN` and the second fails with `cannot start a transaction within a transaction` — and the survey at kickoff exposed the spec/implementation drift.

The architectural decision documented in Increment 27 is to keep single-connection + asyncio mutex for v1, on three grounds:

1. **SQLite serializes writers regardless of pool size.** A pool of N connections with N writers contending for the same database file does not deliver write parallelism — at most one transaction holds the file's write lock at any time. `aiosqlite` does not change this; the SQLite C library does not provide multi-writer semantics on a single file. The pool's main potential benefit was read parallelism in WAL mode (concurrent SELECTs across multiple connections do work), not write parallelism.

2. **The codebase's UoWs are write-heavy.** Every UoW in this codebase wraps writes — at minimum it writes to the audit log alongside whatever it reads — so even the "read-mostly" dashboard paths are write transactions. The fraction of operations that would actually benefit from pooled read parallelism is small.

3. **Workload doesn't justify the complexity.** A single-node ETL reporting service with a low expected concurrent dashboard usage and bounded gRPC ingest from a known set of pipelines does not need the operational complexity of pool sizing, exhaustion handling, acquire timeouts, and pool-depth observability. The v1 mutex is simpler-and-equivalent for this workload.

Failure modes:

- **Mutex** — under contention, coroutines queue on the lock. Failure mode is *latency*: a slow UoW makes everyone wait. No errors, no timeouts, no `SQLITE_BUSY`. Predictable and easy to reason about.
- **Pool** — under contention, writers either block at SQLite's level (waiting on the file lock until `busy_timeout` expires) or fail with `SQLITE_BUSY`. Failure mode is *latency or errors*. Harder to debug ("which connection has my transaction?", `SQLITE_BUSY` retries, pool-exhaustion timeouts).

For v1 traffic levels, the mutex's failure mode is the better one. The pool's observability advantages (counters, depth metrics) are real but solve a problem the workload does not have.

---

## Re-evaluation triggers

The pool architecture should be revisited if any of the following become true. None are expected for v1.

1. **Dashboard P95 latency under sustained sweeper load exceeds the operational SLA.** This is the strongest signal — if the dashboard freezes for the duration of a sweeper tick because read queries serialize behind write transactions on the single connection, the pool's read-parallelism benefit becomes load-bearing rather than nice-to-have. The trigger metric: dashboard request P95 during a 50-orphan sweeper tick (or equivalent worst-case load).

2. **A second non-audit read-only query path is added.** The current dashboard paths bundle reads with audit-log writes inside a UoW (so they are write transactions and would not benefit from pool parallelism). If a future increment adds a true read-only path (e.g., a public-status JSON endpoint that does not write to the audit log), pool reads would parallelize that path against in-flight writes. Whether the volume justifies the pool complexity is a judgment call at that point.

3. **The single-connection mutex becomes a measurable bottleneck.** This would manifest as `asyncio.Lock` queue depth or wait time showing up in profiling — coroutines waiting on the mutex for non-trivial durations. Add a debug-level "uow_lock_acquired" log with wait time and watch for sustained tail-latency in production.

4. **The deployment model changes from single-node to multi-node.** A multi-node deployment cannot share an in-process mutex; coordination would have to move to SQLite (or to a different RDBMS). The pool design (or a pool-equivalent) becomes the right choice when in-process serialization is no longer sufficient. This is unrelated to the L1-DEP-* current single-node assumption — it would be a v2-scoped change.

---

## Migration path if re-instated

The current implementation localizes connection acquisition inside `SqliteUnitOfWorkFactory.__call__()`. Switching from single-connection-with-mutex to pool-with-acquire would change one method:

```python
# Current (post-Increment-27): single connection + lock
def __call__(self) -> SqliteUnitOfWork:
    return SqliteUnitOfWork(conn=self._conn, lock=self._lock, ...)

# Hypothetical pool form
async def __call__(self) -> SqliteUnitOfWork:
    conn = await self._pool.acquire(timeout=self._acquire_timeout)
    return SqliteUnitOfWork(conn=conn, release=self._pool.release, ...)
```

The factory's call signature does not change for non-async callers (the use cases). Repository factories already accept a connection — they're agnostic to whether it came from a pool or a single shared instance. The UoW itself would gain a `release` callback to return its connection on `__aexit__`. The bootstrap composition would build a pool with the WAL pragmas applied per-connection at acquire time, instead of one connection at startup.

The integration test for concurrency authored in Increment 27f would need to be supplemented (not replaced) with pool-exhaustion tests and pool-depth observability tests, mirroring the original L3-PERS-006 and L3-PERS-021 verification methods.

---

## Cross-references

- Active mutex requirements: L2-PERS-004 + L3-PERS-006/007/021 (post-Increment-27 form, in `docs/L2-REQ.md` and `docs/L3-REQ.md`).
- Increment 27 entry: `ROADMAP.md` — sub-steps 27a through 27i.
- Future ADR (planned for Increment 24): `docs/adr/001-sqlite-for-in-flight-state.md` will reference this archive when discussing the connection-handling choice.
