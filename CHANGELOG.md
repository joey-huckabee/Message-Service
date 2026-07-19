# Changelog

All notable changes to Message-Service are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The project is requirements-driven: every change traces to an L1/L2/L3 SHALL
statement in `docs/L1-REQ.md` / `docs/L2-REQ.md` / `docs/L3-REQ.md`, with
verification status in `docs/TRACE-MATRIX.md`. Forward-looking work is tracked
in `docs/ROADMAP.md`, not here.

## [Unreleased]

## [0.6.0] — 2026-07-18

Resend subject conformance — a correctness fix closing a gap exposed by the
v0.4.0/v0.5.0 per-pipeline override work. The manual-resend path had hardcoded
its own `Subject:` header, so it ignored the v0.4.0 `subject_templates` override
and diverged from the `L2-MAIL-014` format — while v0.5.0's per-pipeline body
template *did* apply on resend, making the asymmetry visible. Resend now shares
a single subject-construction chokepoint with first delivery. **64 of 67 L1
requirements Implemented** at **95.05% branch coverage** over **1426 tests**;
three intentional partials remain toward 1.0.0.

### Fixed

- **Resend now conforms to `L2-MAIL-014` (`L3-MAIL-034`).** `ResendRunUseCase`
  previously set the subject to `Run {run_id} -- {pipeline_type}`, which bypassed
  the per-pipeline `subject_templates` override (v0.4.0) and the `pipeline_type`
  sanitization, and diverged from the canonical `[{pipeline_type}] run {run_id}`
  default. Both the first-delivery and resend paths now obtain the subject from a
  single shared `AssembleAndDeliverUseCase.build_subject(run)`, so the default
  format, the per-pipeline override, and sanitization apply identically on
  resend. **Behavior change:** resend emails now use the canonical subject format
  (and any configured override) instead of the old resend-only format.

## [0.5.0] — 2026-07-18

Per-pipeline email body templates — the next deferred-feature item
(`R-TMPL-001`) promoted to real requirements on the road to 1.0.0. Operators
can now render a different email body template per pipeline via an optional
configuration mapping, while pipelines without an override keep the service-wide
`templates.email_body_template_ref`. Additive: no proto change, and behavior is
byte-identical to v0.4.0 when the mapping is unset. **64 of 67 L1 requirements
Implemented** at **95.05% branch coverage** over **1422 tests**; three
intentional partials remain toward 1.0.0.

### Added

- **Per-pipeline email body templates (`L2-TMPL-015`).** A new optional
  `pipelines.email_body_template_overrides` mapping (`pipeline_type` →
  `(name, version)` template reference) overrides the email body template for
  matching pipelines. Each override reference is validated against the template
  manifest at startup (`L3-TMPL-034`) — a reference to a template absent from
  the manifest fails service start with `ConfigurationError`, honoring
  `L1-TMPL-001` at configuration time — and each key must be a registered
  pipeline (`L3-TMPL-033`). Because both the first-delivery and resend paths
  render through the same code, the override applies to resends too
  (`L3-TMPL-035`). Adds `L2-TMPL-015` and `L3-TMPL-033`/`-034`/`-035`.

## [0.4.0] — 2026-07-18

Per-pipeline email subject templates — the next deferred-feature item
(`R-MAIL-001`) promoted to real requirements on the road to 1.0.0. Operators
can now override the outbound email `Subject:` header per pipeline via an
optional configuration mapping, while pipelines without an override keep the
built-in `[{pipeline_type}] run {run_id}` format unchanged. Additive: no proto
change, and behavior is byte-identical to v0.3.0 when the mapping is unset.
**64 of 67 L1 requirements Implemented** at **95.02% branch coverage** over
**1412 tests**; three intentional partials remain toward 1.0.0.

### Added

- **Per-pipeline email subject templates (`L2-MAIL-014`).** A new optional
  `pipelines.subject_templates` mapping (`pipeline_type` → template string)
  overrides the default subject for matching pipelines. Templates may reference
  only the `{pipeline_type}` (sanitized via the same `_sanitize_filename_component`
  chokepoint as attachment filenames) and `{run_id}` placeholders. The mapping
  is validated at config-load time (`L3-MAIL-033`): keys must be registered
  pipelines, templates must reference only the two allowed placeholders and be
  valid for `str.format`, and raw CR/LF is rejected. Promotes `L3-MAIL-032` /
  `L3-MAIL-033` to real SHALLs and lifts the `L2-MAIL-014` "SHALL NOT be
  operator-configurable" deferral.

### Changed

- **`L2-MAIL-014`.** The `[{pipeline_type}] run {run_id}` format is now the
  *default* rather than the only possible subject; a configured
  `subject_templates` entry takes precedence for its pipeline.

## [0.3.0] — 2026-07-18

Error-code stability lockfile — the second deferred-feature item (`R-ERR-002`)
promoted to real requirements on the road to 1.0.0. The machine-readable error
codes that pipelines program against (surfaced in gRPC trailing metadata under
`x-message-service-error-code`) are now frozen by a committed lockfile and a CI
gate, so a removal or rename can no longer slip through unnoticed. This resolves
the `L1-ERR-002` v1 partial: **64 of 67 L1 requirements Implemented** at
**94.99% branch coverage** over **1402 tests**. Three intentional partials remain
toward 1.0.0.

### Added

- **Error-code stability lockfile (`L1-ERR-002`).** `docs/error-codes.lock`
  records the proto `ErrorCode` enum — the single enumerated set shared with the
  exception hierarchy (per `L1-ERR-002`, asserted at startup by `L3-ERR-008`).
  `scripts/check-error-code-stability.py` diffs the current enum against the
  lockfile and exits `0` (clean), `1` (stability violation — a released code was
  removed or renamed), `2` (stale lockfile — a code was added; regenerate and
  commit), or `3` (lockfile missing/unreadable); a removal outranks an addition
  so a rename fails as a violation. `scripts/update-error-codes-lock.py`
  regenerates the lockfile deterministically. Promotes `L3-ERR-010` /
  `L3-ERR-011` from deferred stubs to real SHALLs.

### Changed

- **CI.** A new `error-code-stability` job runs the check on every push and PR,
  surfacing error-code adds, removals, and renames at review time.

## [0.2.0] — 2026-07-17

Custom per-stage email body contributions — the first deferred-feature item
(`R-AGGR-001`) promoted to real requirements on the road to 1.0.0. A stage's
`SubmitStageReport` may now carry an email body contribution with a `position`
(`BEFORE_STAGES_SUMMARY` / `AFTER_STAGES_SUMMARY`), and assembly places each
contribution relative to the run-level stage summary accordingly. This resolves
the `L1-AGGR-001` v1 partial: **63 of 67 L1 requirements Implemented** at
**94.86% branch coverage** over **1385 tests**. Four intentional partials remain
toward 1.0.0.

### Added

- **Per-stage email body contributions (`L1-AGGR-001`).** `SubmitStageReport`'s
  optional `email_body_contribution` now carries a `position` enum. The gRPC
  boundary resolves the proto `UNSPECIFIED` default to `AFTER_STAGES_SUMMARY`
  (with a DEBUG log); assembly groups contributions into `before_contributions`
  / `after_contributions` buckets, each sorted by `(stage_order, stage_id)`, and
  passes them to the email body template, which renders them before and after
  the stage summary. The reference template
  (`config/dev-templates/email_body.html.j2`) demonstrates the placement.

### Changed

- **`stages` schema.** Migration `004` adds a nullable `email_body_position`
  column (set iff an email body contribution is present); pre-existing
  context-bearing rows are backfilled to `AFTER_STAGES_SUMMARY`.

### Fixed

- **Flaky `test_main` gRPC lifecycle tests on Windows.** The three real-server
  tests hard-coded ports inside a Windows reserved range (Hyper-V/WSL/Docker),
  which no process may bind; they now bind an OS-assigned free port. No runtime
  impact — test harness only.

## [0.1.0] — 2026-07-14

First official release — the full v1 feature scope: collecting per-stage reports
from external ETL pipelines over gRPC, aggregating them into Jinja2-rendered HTML,
and emailing the result to subscribed users, with a FastAPI dashboard for
subscription management, resend, admin, and audit. Requirements-driven throughout:
this tag ships **62 of 67 L1 requirements Implemented** (67 L1 / 192 L2 / 393 L3
across 16 categories) at **94.88% branch coverage** over **1370 tests**. The
remaining 5 L1s are deliberate v2 deferrals, each documented in `docs/ROADMAP.md`
with a re-evaluation trigger. This is the start of a 0.x line with a runway toward
1.0.0.

### Added

- **gRPC ingest for per-stage pipeline reports.** Unary `BeginRun` /
  `SubmitStageReport` / `FinalizeRun` RPCs over plaintext TCP, sized for the
  trusted-ISOLAN deployment model, with a typed error-code contract surfaced in
  gRPC trailing metadata (`x-message-service-error-code`).
- **FastAPI dashboard.** Subscription CRUD, paginated past-run views with a
  report viewer, manual resend (re-renders from saved stage context), and admin
  template-registry / user-management / audit-log screens behind an admin gate.
  Local-account auth with Argon2 password hashing and server-side sessions.
- **Jinja2 sandboxed rendering.** Manifest-managed templates referenced by name +
  version, JSON Schema context validation at render time, and case-sensitive
  `"latest"` version resolution frozen per run.
- **Aggregation model.** Two attachment modes per run (`SINGLE_AGGREGATED`
  composite, or `PER_STAGE` one attachment per stage) and a two-slot stage
  contribution model (report fragment + optional email body), both slots optional.
- **SQLite persistence** (WAL, single-connection + `asyncio.Lock` writer mutex)
  for in-flight run state, users, subscriptions, audit log, and template metadata;
  **filesystem persistence** for rendered HTML reports. Raw SQL via `aiosqlite`,
  no ORM; migrations applied by a migration runner.
- **Asyncio orphan sweeper** with an exactly-once outbox (`sweeper_actions`
  table), stuck-claim recovery, a bounded per-tick candidate limit, and
  policy-driven disposition (`DISCARD_SILENTLY` / `NOTIFY_ADMINS` registered in
  v1).
- **Retention pruners** for rendered reports and the audit log, each on the
  shared background scheduler with configurable windows and sole-deleter
  conformance guards.
- **Observability.** Structured `structlog` events, a full audit-action taxonomy,
  and a Prometheus `/metrics` scrape endpoint.
- **Deployment.** Cross-platform — Linux (systemd unit) and Windows (NSSM) — with
  graceful shutdown, an operator runbook, a pipeline-integration guide, and two
  architecture decision records.
- **Runnable examples.** Eight self-contained demonstration scenarios
  (`01-hello-world` … `08-error-recovery`) that need no external mail server.

[Unreleased]: https://github.com/joey-huckabee/Message-Service/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/joey-huckabee/Message-Service/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/joey-huckabee/Message-Service/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/joey-huckabee/Message-Service/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/joey-huckabee/Message-Service/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/joey-huckabee/Message-Service/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/joey-huckabee/Message-Service/releases/tag/v0.1.0
