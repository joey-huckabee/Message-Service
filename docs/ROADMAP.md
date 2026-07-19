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
each release cut. The last cut was **v0.10.0** (template author guide + stale-doc
fixes); the next cut is **v0.11.0** — nothing is scheduled into it yet. Pull
items from the **Deferred features** backlog below, promote each to real
L1/L2/L3 requirements in the L-REQ docs, implement, and record the shipped
result under a new dated section in `CHANGELOG.md`.

## Planned

| Version | Theme |
|---------|-------|
| 0.2.0 → | Work down the deferred-feature backlog toward feature-completeness; each release promotes one or more `R-XXX` items to real requirements. |
| 1.0.0 | Cut when the trust-boundary-gated hardening items (mTLS, RBAC, rate limiting, the `R-ERR-001` wire-contract upgrade) have either shipped or been explicitly scoped out, and the one remaining intentional v1 partial below is resolved. |

## The road to 1.0.0 — intentional v1 partials

v0.1.0 shipped five L1 requirements **Partial**. Resolved since: `L1-AGGR-001`
(v0.2.0, `R-AGGR-001`), `L1-ERR-002` (v0.3.0, `R-ERR-002`), and both
`L1-API-001` and `L1-OBS-001` (v0.8.0, `R-API-001` — per-RPC/per-request
correlation ids + proto-version gate). That leaves **one**:

| L1 | Deferred piece | Deferral tag |
|----|----------------|--------------|
| L1-DASH-004 | Embedded metrics dashboard — hand-authored, no external chart library (scrape endpoint already ships) | R-DASH-004 |

It is not a missing-functionality bug — the operationally important half (the
Prometheus `/metrics` scrape endpoint) already ships and is verified under the
`L1-OBS` requirements; only the embedded in-service visualization is deferred
(see the `R-DASH-004` entry below for the phased plan). `docs/TRACE-MATRIX.md`
shows `L1-DASH-004` itself as **Draft** (its own subtree — `L2-DASH-010`/
`L2-DASH-011` — carries no verification artifact yet), and it is recorded on
`docs/uncovered-l1-allowlist.toml` so the v0.9.0
uncovered L1 slip in.

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

- **Mutual TLS on gRPC** — v1 uses plaintext TCP on the trusted ISOLAN network.
  Promote when gRPC ingest crosses trust boundaries or when compliance
  requirements demand transport encryption.
- **Additional authentication backends** — LDAP/AD and OIDC. Current scope is
  local accounts only. LDAP is the likely first addition, consistent with broader
  ISOLAN architecture patterns.
- **Secrets handling review** — SMTP credentials and any future API keys currently
  live in the TOML configuration file. Consider integration with Vault CE if
  secret rotation becomes operationally significant.
- **In-flight RPC concurrency limits / per-pipeline rate limiting** — v1
  deliberately omits rate limiting because the trusted-ISOLAN deployment context
  assumes well-behaved pipeline clients (same rationale that justifies plaintext
  gRPC under L1-API-003). When the gRPC ingress crosses a trust boundary —
  concurrent with the mTLS item — author an `L1-API-005` ("the service SHALL bound
  concurrent in-flight RPCs by a configurable global limit; excess SHALL be
  rejected with `RESOURCE_EXHAUSTED` and an error code identifying the saturation
  cause") plus L2 derivations covering per-pipeline caps, per-RPC weight (BeginRun
  is cheap, FinalizeRun triggers assembly), and the rejection-error contract.
  Until then, a misbehaving pipeline can saturate the shared SQLite connection.
  Risk accepted in v1 scope (see **Out of Scope** below).
- **Host-clock validity hardening** — L2-RUN-016 records v1's assumption that the
  host clock is monotonically non-decreasing UTC, with backward-correction
  handling explicitly out of scope. If deployment contexts emerge where backward
  NTP corrections are expected (VM pause/resume, virtualized environments with
  imprecise clocks), promote: dual-clock reconciliation (record both
  `time.monotonic()` and wall-clock per event; cross-check), warn-and-continue on
  detected backward jumps larger than a configurable threshold, and L3 statements
  pinning the detection mechanism. The single `Clock` port is the chokepoint for
  this swap.
- **R-DASH-001 — Role-based access control** — dashboard authentication
  (L1-AUTH-001) is baseline only; every authenticated user can perform every
  dashboard action. Future option: roles (viewer, operator, admin) with per-role
  action gates. Requires a `user_role` column and policy checks in dashboard use
  cases.
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
- **R-DASH-004 — Embedded metrics dashboard (hand-authored, zero external
  dependencies)** (the visualization half of L1-DASH-004) — v1 implements the
  **scrape-endpoint half**: `GET /metrics` returning Prometheus exposition
  format. The **embedded-visualization half** — an admin route serving an HTML
  page that fetches `/metrics`, parses the text format in JavaScript, and renders
  charts — is deferred. The charting is **hand-authored HTML/CSS/JS with inline
  SVG**, shipped as packaged static assets with **no third-party charting library
  and no CDN**; this satisfies the offline/no-external-dependency constraint of
  `L2-DASH-011` directly (its "e.g., Chart.js" is only an example) and keeps the
  dependency surface minimal. The implementation draws on established charting
  conventions (labelled axes, gridlines, hover tooltips, a small line/bar/gauge
  vocabulary) but is written from scratch. When this increment lands it will
  reword the `L2-DASH-011` / `L3-DASH-017` "Chart.js" example to the
  hand-authored approach.

  **Incremental plan** (execute in order when development starts):
  1. **Route + shell.** `GET /admin/metrics` behind `require_admin`, serving a
     static HTML shell (semantic HTML + a hand-written CSS stylesheet as a
     packaged static asset). No data yet.
  2. **Metrics parser (pure, unit-testable).** A standalone JS module that parses
     the Prometheus text exposition format into a structured model. Kept free of
     DOM access so it is unit-testable in isolation *without a browser* (Node or
     a lightweight JS test runner, or a Python-side golden test over sample
     `/metrics` output) — this is how the JS logic gets automated coverage while
     the browser harness is deferred.
  3. **Server-side fetch.** The page (or route) retrieves values by a server-side
     HTTP GET against the service's own `/metrics` (`L2-DASH-010`), so the
     internal view matches what external scrapers see.
  4. **Hand-authored SVG rendering.** Render the parsed series as inline SVG
     (time-series line, per-label bar, single-value gauge) with axes/gridlines/
     tooltips built from scratch. No canvas library, no chart library.
  5. **Verification.** Author the `L2-DASH-010`/`L2-DASH-011` L3 children: unit
     tests for the parser (step 2) and a server-side test that the route is
     admin-gated and returns the shell + expected data hooks; the visual
     rendering is verified by **Inspection / Demonstration** (run locally and
     review) until the browser test harness below is in place. Promote
     `L1-DASH-004` off `docs/uncovered-l1-allowlist.toml` once its subtree has
     at least the parser + route tests linked.
- **Browser-based UI test harness (deferred, low priority)** — automated
  end-to-end testing of rendered dashboard pages (a headless-browser harness such
  as Playwright) is **deferred and deliberately kept late in the backlog**. In
  the interim, UI-rendering aspects (the SVG dashboard, any future rendered
  admin pages) are verified by **local manual demonstration** plus unit tests of
  the underlying non-DOM logic (e.g. the R-DASH-004 metrics parser). This item
  does **not** block R-DASH-004 — that work proceeds with the parser unit tests
  and Inspection/Demonstration verification described above. Revisit when the
  volume of browser-rendered UI justifies the harness.
- **R-ERR-001 — gRPC error envelope upgrade to `google.rpc.Status` + `ErrorInfo`**
  — v1's error translator returns `context.abort(status, details=message,
  trailing_metadata=(("x-message-service-error-code", code),))`. The richer shape
  — `google.rpc.Status` with a `google.rpc.ErrorInfo` carrying `reason=error_code`
  and `metadata` from the exception's `details` — gives clients structured access
  but is a wire-format change. Future work, when the trust boundary widens: switch
  the translator to construct `google.rpc.Status`. Strictly additive server-side;
  the same trailing-metadata key can be carried in both shapes during a phased
  rollout, so existing clients keep working.

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
- **Alternative delivery transports** — v1 delivers via SMTP. Future options:
  webhooks, direct API hooks into ticketing systems, Slack/Teams relays.
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
- **Backup and restore tooling** — scripts to snapshot and restore the SQLite
  database and rendered-reports directory as an atomic unit.
- **Audit log archival** — once retention expires (enforced by the audit-log
  pruner), archive rather than delete, to satisfy long-term investigative needs.
- **Metrics dashboard templates** — ship pre-built Grafana dashboards alongside
  the (deferred) embedded in-service visualization.

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

- **Rate limiting / per-pipeline concurrency caps in v1.** Deliberately omitted:
  the trusted-ISOLAN deployment model assumes well-behaved pipeline clients (the
  same constraint that justifies plaintext gRPC under L1-API-003). This is a
  *risk accepted in v1 scope*, not an oversight — see the **Security hardening**
  entry above for the promotion path (`L1-API-005`) when the ingress crosses a
  trust boundary.
- **Backward host-clock corrections.** L2-RUN-016 pins v1's assumption of a
  monotonically non-decreasing UTC host clock; behavior under backward NTP
  corrections larger than the tolerance is unspecified. Promotion path in
  **Security hardening** above.
