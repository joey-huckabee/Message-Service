# Message-Service Roadmap

> **This roadmap is forward-looking only.** Completed work is not tracked here —
> it lives in `CHANGELOG.md` (release history), `docs/L1-REQ.md` /
> `docs/L2-REQ.md` / `docs/L3-REQ.md` (the normative requirements), and
> `docs/TRACE-MATRIX.md` (verification status), all backed by git history.
>
> **Do not mint requirement IDs (`L1-*`, `L2-*`, `L3-*`) in this file.** An ID is
> born only when its requirement is written in the L-REQ docs; describe intended
> work in prose and let the requirement process own the IDs. The `R-XXX-NNN`
> handles below are *deferral tags*, not requirement IDs — they label a
> carved-out feature so spec docs and code comments can point at its rationale
> and trigger. Promoting one to a release mints a fresh L1/L2/L3 ID in the L-REQ
> docs.

## Queued for the next release (`[Unreleased]`)

`[Unreleased]` at the top of `CHANGELOG.md` is the live queue; it is emptied at
each release cut. The last cut was **v0.13.0** (rejecting concurrency limit
`L1-API-005` + the `R-ERR-001` structured-error envelope); the next cut is
**v0.14.0** — nothing is scheduled into it yet. Pull items from the **Deferred
features** backlog below (excluding the `→ 2.0.0`-tagged items, which are held for
the 2.0.0 milestone), promote each to real L1/L2/L3 requirements in the L-REQ
docs, implement, and record the shipped result under a new dated section in
`CHANGELOG.md`.

## Planned

| Version | Theme |
|---------|-------|
| 0.14.0 → | Work down the deferred-feature backlog toward the 1.0.0 scope; each release promotes one or more `R-XXX` items to real requirements. **The trust-boundary and multi-tenant hardening items are explicitly NOT part of this track — they are collected under 2.0.0 below.** |
| 1.0.0 | The stable single-node, trusted-ISOLAN feature-complete release. All v1 partials are resolved and all L1 requirements are Implemented (as of v0.13.0). The precise 1.0.0 feature line is being (re)defined — see **Toward 1.0.0** below; it deliberately excludes the 2.0.0 hardening items. |
| 2.0.0 | **Trust-boundary crossing + multi-tenant hardening.** The service graduates from the trusted-ISOLAN plaintext model to running where the gRPC ingress and dashboard cross a trust boundary. Collects: mutual TLS on gRPC, dashboard RBAC, per-pipeline concurrency caps / per-RPC weighting, backup & restore tooling, and webhook delivery transport. See **Toward 2.0.0** below. |

## The v1 partials (all resolved)

v0.1.0 shipped five L1 requirements **Partial**; **all five are now Implemented**:
`L1-AGGR-001` (v0.2.0, `R-AGGR-001`), `L1-ERR-002` (v0.3.0, `R-ERR-002`),
`L1-API-001` + `L1-OBS-001` (v0.8.0, `R-API-001`), and `L1-DASH-004` (v0.12.0,
`R-DASH-004` — the embedded metrics dashboard). As of v0.12.0 **all 67 of 67 L1
requirements are Implemented**, each with at least one linked verification
artifact, and `docs/uncovered-l1-allowlist.toml` is empty. v0.13.0 added
`L1-API-005` (rejecting concurrency limit) and the `R-ERR-001` structured-error
envelope — **68 of 68 L1 Implemented**. There are no requirement gaps.

## Toward 1.0.0

1.0.0 is the **stable single-node, trusted-ISOLAN feature-complete** release. It
is deliberately *not* gated on the trust-boundary hardening — those items moved
to **2.0.0** (below). The positive 1.0.0 feature line is being (re)defined; strong
candidates are the user-facing gaps that today exist only as JSON API surface
with no browser UI (e.g. a self-service subscription page and a run-status board
— see the **Feature extensions** notes). Pull the agreed items from the backlog,
promote each to real L1/L2/L3 requirements, ship, then cut 1.0.0.

## Toward 2.0.0 — trust-boundary crossing + multi-tenant hardening

2.0.0 is where the service stops assuming the trusted-ISOLAN, well-behaved-client
model (the same assumption that justifies plaintext gRPC under `L1-API-003`) and
becomes safe to run where its ingress and dashboard cross a trust boundary. It
collects the following, each already described in **Deferred features** below and
tagged `→ 2.0.0`:

- **Mutual TLS on gRPC** — transport encryption + client-cert auth for ingest.
- **Dashboard RBAC (`R-DASH-001`)** — viewer / operator / admin roles with
  per-action gates (today every authenticated user can do everything).
- **Per-pipeline concurrency caps / per-RPC weighting** — per-tenant fairness on
  top of the global rejecting limit shipped in v0.13.0.
- **Backup & restore tooling** — atomic snapshot/restore of the SQLite database
  and rendered-reports directory.
- **Webhook delivery transport** — an alternative to SMTP delivery for
  machine-to-machine notification.

These are grouped because they share one trigger — the ingress/dashboard crossing
a trust boundary — and are best specified and tested together rather than dribbled
across point releases.

## Deferred features

Each entry is a candidate, not a commitment. `R-XXX-NNN`-tagged items are
referenced by spec docs and code comments; keep the tags stable.

### Performance and profiling

- **In-flight run state backing profiling** — v1 co-locates in-flight run state in
  SQLite, relying on SQLite's WAL journal for durability. If profiling later shows
  SQLite write latency is a bottleneck on the gRPC ingest hot path, evaluate an
  in-memory store with a custom write-ahead log. The repository-pattern
  abstraction (L1-PERS-003) makes this swap possible without touching domain code.
- **Email size distribution analysis** — once the Prometheus email-size histogram
  has collected production data, analyze for patterns that would justify
  per-pipeline-type size limits or automatic compression strategies.
- **R-DELIVER-001 — Outbox-backed background tasks** — `FinalizeRunUseCase`
  schedules the assembly workflow via `BackgroundTaskScheduler`, backed by
  `asyncio.create_task`. If the process dies after `FinalizeRun` commits but
  before the task completes, the delivery is lost (the run is stuck in
  `READY`/`SENDING`). Future option: outbox-row pattern — `FinalizeRun` writes a
  row to an `outbox` table inside the same transaction; a long-running worker
  drains the outbox and retries on failure. The `BackgroundTaskScheduler` port can
  be retained; its adapter reads from the outbox instead of accepting coroutines.
  Defer until multi-node deployment is in scope. Single-node ISOLAN deployments
  survive the current risk because the orphan sweeper (L1-RUN-006) eventually
  reclaims stuck runs, bounded by `sweeper.run_timeout_seconds`.
- **R-OBS-001 — Distributed tracing** — v1 has structured logging via structlog
  with `run_id` correlation; no trace spans. Future option: OpenTelemetry-based
  spans across the RPC handler, use case, UoW, and adapter calls. Useful primarily
  once the service is part of a larger distributed system; low value standalone.

### Security hardening

- **Mutual TLS on gRPC → 2.0.0** — v1 uses plaintext TCP on the trusted ISOLAN
  network. Promote when gRPC ingest crosses trust boundaries or when compliance
  requirements demand transport encryption. Collected under the **2.0.0** milestone.
- **Additional authentication backends** — LDAP/AD and OIDC. Current scope is
  local accounts only. LDAP is the likely first addition, consistent with broader
  ISOLAN architecture patterns.
- **Secrets handling review** — SMTP credentials and any future API keys currently
  live in the TOML configuration file. Consider integration with Vault CE if
  secret rotation becomes operationally significant.
- **Per-pipeline rate limiting / per-RPC weighting → 2.0.0** — the *global*
  rejecting concurrency limit shipped in v0.13.0 (`L1-API-005`:
  `grpc.max_in_flight_rpcs` bounds concurrent in-flight RPCs and rejects excess
  with `RESOURCE_EXHAUSTED`, the saturation cause carried as an `ErrorInfo.reason`
  on the R-ERR-001 envelope). Still deferred: **per-pipeline caps** (a misbehaving
  pipeline can still consume the whole global budget) and **per-RPC weighting**
  (BeginRun is cheap; FinalizeRun triggers assembly, so counting all RPCs equally
  under-protects the expensive path). Author these as L2 derivations of
  `L1-API-005`. Collected under the **2.0.0** milestone (per-tenant fairness lands
  with the trust-boundary promotion).
- **Host-clock validity hardening** — L2-RUN-016 records v1's assumption that the
  host clock is monotonically non-decreasing UTC, with backward-correction
  handling explicitly out of scope. If deployment contexts emerge where backward
  NTP corrections are expected (VM pause/resume, virtualized environments with
  imprecise clocks), promote: dual-clock reconciliation (record both
  `time.monotonic()` and wall-clock per event; cross-check), warn-and-continue on
  detected backward jumps larger than a configurable threshold, and L3 statements
  pinning the detection mechanism. The single `Clock` port is the chokepoint for
  this swap.
- **R-DASH-001 — Role-based access control → 2.0.0** — dashboard authentication
  (L1-AUTH-001) is baseline only; every authenticated user can perform every
  dashboard action. Future option: roles (viewer, operator, admin) with per-role
  action gates. Requires a `user_role` column and policy checks in dashboard use
  cases. Collected under the **2.0.0** milestone.
- **R-DASH-002 — Subscription identifier promotion to UUID4** — v1 mints
  subscription IDs as `INTEGER PRIMARY KEY AUTOINCREMENT`. Per-user route scoping
  (L3-DASH-007) prevents cross-user access, but sequential integer IDs leak the
  system's subscription count to anyone who creates one. Promotion to UUID4
  (server-generated, stored as TEXT) defends against enumeration as defense in
  depth. Requires a schema migration, a `SubscriptionId` typedef change,
  repo/audit/route-validator updates, and an L3 reword. Likely paired with the
  mTLS / trust-boundary promotion.
- **R-DASH-003 — Audit-log substring search on actor / resource** — v1's
  `GET /admin/audit` supports exact-string matching only. Substring search
  (`actor=user:` to find every action by any user) requires a SQL `LIKE` rather
  than `=`, and may benefit from FTS5 indexing if audit volumes grow. Future work:
  extend `L2-DASH-015` with a `match_mode` query parameter (default `exact`;
  opt-in `substring`); evaluate whether the current index profile stays adequate.
- **Browser-based UI test harness (deferred, low priority)** — automated
  end-to-end testing of rendered dashboard pages (a headless-browser harness such
  as Playwright) is **deferred and deliberately kept late in the backlog**. The
  embedded metrics dashboard (shipped v0.12.0) is verified today by **local
  manual demonstration** plus unit tests of its non-DOM logic (the Python-side
  metrics parser, the route's admin gate, and a no-external-reference conformance
  scan of the static assets); the visual SVG rendering itself is the only part a
  browser harness would add automated coverage for. Revisit when the volume of
  browser-rendered UI justifies the harness.

### Feature extensions

- **Hot-reload of tag vocabulary** — v1 loads the tag configuration at service
  start. Hot-reload removes the need for restart to add tags.
- **R-TMPL-002 — Hot-reload of templates** — the template manifest is loaded once
  at service start (L2-TMPL-001); changes require a restart. Future option:
  signal-driven reload (`SIGHUP`) that atomically swaps the manifest while
  in-flight runs continue to render against the old snapshot. Non-trivial: needs a
  template-snapshot token carried through the assembly workflow so `BeginRun` and
  `FinalizeRun` of the same run see consistent template metadata.
- **R-TMPL-001 Option B — Per-run email body template declaration** — v0.5.0
  shipped the per-pipeline-config half (Option A: the optional
  `pipelines.email_body_template_overrides` mapping, `L2-TMPL-015`). The
  per-run-declaration half remains deferred: an optional `email_body_template_ref`
  on `BeginRunRequest` would let a single pipeline vary its body template per run,
  but needs a proto change, a new aggregate field, extra validation, and a
  migration. Revisit if concrete per-run variation demand emerges.
- **Subscription granularity extensions** — beyond `GLOBAL`, `PIPELINE`, `TAG`:
  consider per-severity, per-submitter, or boolean combinations if use cases
  emerge.
- **Alternative delivery transports** — v1 delivers via SMTP. The **webhook
  delivery transport → 2.0.0** is collected under the 2.0.0 milestone (a
  capture-double-testable adapter alongside the SMTP mailer, selected by
  subscription/config). Further options remain unscheduled backlog: direct API
  hooks into ticketing systems, Slack/Teams relays.
- **R-DELIVER-002 — Per-subscriber email delivery** — v1 sends one email per run
  with the recipient list via BCC. Future option: one email per subscriber with
  personalization tokens (`{{subscriber.name}}`, `{{subscriber.unsubscribe_url}}`).
  Requires per-subscriber rendering and a more involved failure model. Likely
  paired with R-DELIVER-001.
- **Streaming gRPC RPCs** — v1 uses unary RPCs only. Two distinct extensions: a
  server-streaming `WatchRun` endpoint for live run-progress streaming, and
  **R-DELIVER-003 — Streaming `SubmitStageReport`** for very large report
  contributions that exceed the unary message-size limit (gRPC default 4 MiB).
  Revisit the latter only if concrete submitters hit the limit.
- **R-OBS-002 — Real-time dashboard updates** — the dashboard polls the REST API
  for run state. Future option: server-sent events or WebSocket push. Requires an
  event-bus abstraction the service doesn't currently have.
- **Custom WAL for in-flight state** — dependent on the profiling item above.
  Would replace SQLite-backed in-flight state with an in-memory representation
  plus an append-only log file.

### Operations

- **High availability and multi-node** — v1 is single-node. Multi-node introduces
  leader election, shared state, and coordinated orphan sweeping; substantial
  scope.
- **R-PERS-001 — Cross-host replication** — v1 stores all state on the host running
  the service. Future option: Litestream-style continuous replication of the
  SQLite database to a standby host for disaster recovery. A deployment-layer
  change only; no application code changes. Orthogonal to the outbox pattern
  (R-DELIVER-001) and to multi-node HA (which is leader-election, not
  DR-replication).
- **Air-gapped installer bundle** — a single-archive offline installer for ISOLAN
  deployment bundling the Poetry-locked dependency tree, NSSM on Windows, and the
  systemd unit on Linux.
- **Backup and restore tooling → 2.0.0** — scripts to snapshot and restore the
  SQLite database and rendered-reports directory as an atomic unit. Collected
  under the **2.0.0** milestone.

### Documentation

- _(No documentation items currently queued — the template author guide shipped
  in v0.10.0; see `docs/template-author-guide.md`.)_

## Shared commitments

Stable contracts current and future work must preserve:

- **Hexagonal boundary.** Dependencies flow inward only; the conformance test at
  `tests/conformance/test_architecture_boundaries.py` enforces it. No new import
  may violate it.
- **Requirements-driven tests.** Every test carries a
  `@pytest.mark.requirement(...)` marker linking to an L1/L2/L3 SHALL statement;
  `docs/TRACE-MATRIX.md` is the single source of truth for status and is
  regenerated, never hand-edited.
- **Coverage gate stays at 85%.** `pyproject.toml` sets `--cov-fail-under=85` with
  branch coverage on `message_service`. Don't lower the gate to work around a
  drop; add the missing tests. (The historical 60% → 75% → 85% ratchet has
  completed.)
- **Clock is injected.** Only `SystemClock` calls `datetime.now`; domain and
  application code take the `Clock` port. Persisted timestamps use `iso_z()`.
- **Structured exceptions.** Every raised error derives from `MessageServiceError`,
  carries a proto-enum `error_code` ClassVar and a machine-parseable `details`
  dict.

## Out of scope (pinned)

- **Per-pipeline concurrency caps.** The *global* rejecting concurrency limit
  shipped in v0.13.0 (`L1-API-005`, opt-in via `grpc.max_in_flight_rpcs`). Finer
  granularity — per-pipeline caps and per-RPC weighting — is out of scope for the
  1.0.0 line: the trusted-ISOLAN deployment model assumes well-behaved pipeline
  clients (the same constraint that justifies plaintext gRPC under L1-API-003).
  Collected under the **2.0.0** milestone (see **Toward 2.0.0** above).
- **Backward host-clock corrections.** L2-RUN-016 pins v1's assumption of a
  monotonically non-decreasing UTC host clock; behavior under backward NTP
  corrections larger than the tolerance is unspecified. Promotion path in
  **Security hardening** above.
