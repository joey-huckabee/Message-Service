# Message-Service ROADMAP

Post-v1 work deferred from current scope. Items are grouped by
theme; ordering within a group reflects rough priority but is not
a commitment.

## Template selection

### R-TMPL-001 — Per-pipeline email body template
**Current behavior**: The email body template is a single
service-wide configuration value
(``templates.email_body_template_ref``) used for every finalized run
regardless of pipeline.

**Future option A — per-pipeline config**: extend
``[pipelines.registered.*]`` entries with an optional
``email_body_template_ref``; when present, overrides the service-wide
default. Backwards-compatible: pipelines without an explicit value
fall back to the default. Small schema change; no proto change; no
new port.

**Future option B — per-run declaration**: add an optional
``email_body_template_ref`` field to ``BeginRunRequest``. More
flexible but requires a proto change, a new field on the ``Run``
aggregate, additional validation at ``BeginRun``, and a schema
migration. Consider only if per-pipeline proves insufficient.

Either path is additive and will not invalidate existing behavior.

### R-TMPL-002 — Template hot-reload
**Current behavior**: The template manifest is loaded once at service
start (L2-TMPL-001); changes require a restart.

**Future option**: signal-driven reload (``SIGHUP``) that atomically
swaps the manifest while in-flight runs continue to render against
the old snapshot. Non-trivial: need a template-snapshot token carried
through the assembly workflow so ``BeginRun`` and ``FinalizeRun`` of
the same run see consistent template metadata.

## Delivery and coordination

### R-DELIVER-001 — Outbox-backed background tasks
**Current behavior**: ``FinalizeRunUseCase`` schedules the assembly
workflow via :class:`BackgroundTaskScheduler`, which is backed by
:func:`asyncio.create_task`. If the process dies after ``FinalizeRun``
commits but before the task completes, the delivery is lost (the run
is stuck in ``READY``/``SENDING``).

**Future option**: outbox-row pattern. ``FinalizeRun`` writes a row
to an ``outbox`` table inside the same transaction; a long-running
worker drains the outbox and retries on failure. The existing
:class:`BackgroundTaskScheduler` port can be retained; its adapter
simply reads from the outbox instead of accepting coroutines
directly.

Defer until multi-node deployment is in scope. Single-node ISOLAN
deployments can survive the current risk because the orphan sweeper
(L1-RUN-006) will eventually reclaim stuck runs, bounded by
``sweeper.run_timeout_seconds``.

### R-DELIVER-002 — Per-subscriber delivery
**Current behavior**: One email per run, recipient list via BCC
(adapter-configurable).

**Future option**: one email per subscriber with per-subscriber
personalization tokens in the body (``{{subscriber.name}}``,
``{{subscriber.unsubscribe_url}}``). Requires per-subscriber
rendering and a more involved failure model (one recipient fails,
does the whole run fail?). Likely paired with R-DELIVER-001.

### R-DELIVER-003 — Streaming stage report submission
**Current behavior**: ``SubmitStageReport`` is a unary RPC
(L1-API-002).

**Future option**: server-streaming variant for very large report
contributions that exceed unary message size limits (gRPC's default
is 4 MiB). Most stages fit comfortably; revisit only if concrete
submitters hit the limit.

## Persistence

### R-PERS-001 — Cross-host replication
**Current behavior**: Single-node SQLite. All state lives on the host
running the service.

**Future option**: Litestream-style continuous replication to a
standby host for HA. Requires a deployment-layer change only; no
application code changes. Orthogonal to the outbox pattern.

### R-PERS-002 — Audit log retention pruning
**Current behavior**: ``AuditLog.record`` inserts are not bounded;
``observability.audit.retention_days`` is in the config schema but
not yet enforced by a running process.

**Future option**: scheduled background task that deletes audit rows
older than the retention window. Small; can piggyback on the same
scheduler used for orphan sweeping.

## Observability

### R-OBS-001 — Distributed tracing
**Current behavior**: Structured logging via structlog with run_id
correlation; no trace spans.

**Future option**: OpenTelemetry-based spans across the RPC handler,
use case, UoW, and adapter calls. Useful primarily once the service
is part of a larger distributed system; low value standalone.

### R-OBS-002 — Real-time dashboard updates
**Current behavior**: The dashboard polls the REST API for run state.

**Future option**: server-sent events or WebSocket push for instant
updates on state transitions. Requires an event-bus abstraction the
service doesn't currently have.

## Dashboard

### R-DASH-001 — Role-based access control
**Current behavior**: Dashboard authentication (L1-AUTH-001) is
baseline only; every authenticated user can perform every dashboard
action.

**Future option**: roles (viewer, operator, admin) with per-role
action gates. Requires a ``user_role`` column and policy checks in
dashboard use cases.

---

*Document history*

- 2026-04-20: Initial version, seeded with items deferred through
  Increment 7a.
