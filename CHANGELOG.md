# Changelog

All notable changes to Message-Service are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The project is requirements-driven: every change traces to an L1/L2/L3 SHALL
statement in `docs/L1-REQ.md` / `docs/L2-REQ.md` / `docs/L3-REQ.md`, with
verification status in `docs/TRACE-MATRIX.md`. Forward-looking work is tracked
in `docs/ROADMAP.md`, not here.

## [Unreleased]

Post-v0.16.0 review-and-fix pass (rounds 1 & 2) toward a 1.0.0 cut. No new
features; correctness, security, and requirements-document fixes.

### Fixed

- **`iso_z` timestamp format is now fixed-width.** `datetime.isoformat()` omits
  the fractional-seconds field when microseconds are zero, which made `iso_z`
  variable-width; because timestamps are stored/compared as TEXT under SQLite's
  BINARY collation, a whole-second value sorted *after* a same-second fractional
  one, inverting chronological order. This broke the `sessions` CHECK
  (`last_activity_at >= created_at`) on an ordinary session touch, the
  `sweeper_actions` CHECKs, `delete_expired` filtering, and every `ORDER BY` on
  persisted timestamps. `iso_z` now always emits six-digit microseconds (form
  `…T00:00:00.000000Z`); `L3-RUN-025` strengthened to mandate it.
- **gRPC now returns `INVALID_ARGUMENT` for malformed client requests** instead
  of `INTERNAL`. Request-adaptation failures (empty `pipeline_type`, empty/negative
  declared-stage fields, empty `run_id`/`stage_id`, missing/empty template refs,
  and unknown/future proto enum values) previously raised `pydantic.ValidationError`
  / `ValueError`, which the translator mapped to `INTERNAL` with a spurious ERROR
  stack-trace log. They now map to `INVALID_ARGUMENT` with
  `ERROR_CODE_MALFORMED_REQUEST` (validation errors surfaced as field/rule pairs,
  never echoing the offending input value).
- **`asyncio.CancelledError` (and other non-`Exception` `BaseException`s) now
  propagate** out of the gRPC translator instead of being turned into a bogus
  `INTERNAL` status + ERROR log, restoring cooperative RPC cancellation.
- **`${env:VAR}` substitution now reaches `[auth.admin].password`.** The config
  path-walker only descended into plain nested models, skipping union-typed
  optional sections — so a configurable local admin with an env-substituted
  password was provisioned at startup with the *literal* `"${env:…}"` string,
  and the real secret was never read. The walker now unwraps unions
  (`AdminAccountConfig | None`), fixing this for any `SubstitutableStr` under an
  `Optional[...]` section.
- **Disabling an account or resetting its password now revokes its live
  sessions.** Neither action previously touched the session store, so a disabled
  (or credential-reset) user's existing cookie kept authenticating until
  idle-timeout — the account-disable control did not actually revoke access.
  Both now delete the target's sessions (new `SessionRepository.delete_by_user_id`)
  in the same transaction as the account change, and `require_admin` additionally
  rejects a disabled user (new `L3-AUTH-020` / `L3-AUTH-021`).
- **The report viewer and manual resend are now administrator-only.**
  `GET /runs/{id}/report`, the per-stage `/fragment` viewer, and
  `POST /runs/{id}/resend` were gated by `require_session`, so any authenticated
  non-admin recipient could read any run's rendered report and trigger a resend
  (a mass-mail). They now require `require_admin`, matching `L1-DASH-003`.
- **Path-traversal via `stage_id` on the fragment route is closed.** `stage_id`
  went unvalidated into a filesystem path (`<root>/<run_id>/fragments/<stage_id>.html`),
  letting a crafted value escape the report tree — on Windows the fragment
  *read* route (`\` passes the URL path converter) and the fragment *write* path
  (a pipeline-declared stage id). The route now constrains `stage_id` to a safe
  charset, and the filesystem report store rejects any resolved path that escapes
  the configured report root (protecting read and write regardless of caller).
- **A delivery that finishes after the orphan sweeper reclaims its run no longer
  crashes the background task.** The sweeper classifies runs by age alone
  (`L1-SWEEP-002`) and can reclaim a slow `SENDING` delivery whose SMTP retries
  outlast `run_timeout_seconds`; when the delivery task then tried its
  `SENDING -> SENT` (or `-> FAILED`) transition, the run was already `ORPHANED`
  and the illegal transition raised an uncaught `InvalidStateTransitionError` —
  surfacing as a spurious internal error even though the email had been sent, with
  no `SEND_REPORT` audit row. `AssembleAndDeliverUseCase` now re-reads the run
  inside the finalizing transaction and, if it is already terminal, records a
  reconciliation `SEND_REPORT` audit row (with a `reconciled_terminal_state`
  detail) and leaves the swept state intact instead of raising (new `L3-RUN-034`).
  The SMTP mailer additionally bounds every connection with an explicit timeout
  (default 30 s) so a hung relay cannot hold a run in `SENDING` indefinitely.

## [0.16.0] — 2026-07-19

The admin console's **Subscriptions** tab goes live: an administrator can now
manage *any* recipient's notification subscriptions on their behalf. Adds one L1
(`L1-DASH-009`) — **73 of 73 L1 requirements Implemented** — at **95.34% branch
coverage** over **1569 tests**.

### Added

- **Admin-on-behalf subscription management (`L1-DASH-009` / `L2-DASH-022`,
  `L2-DASH-023`).** A new admin-gated API — `GET`/`POST`/`DELETE
  /admin/users/{user_id}/subscriptions` — lets an administrator list, create, and
  delete a recipient's `GLOBAL`/`PIPELINE`/`TAG` subscriptions. `PIPELINE`/`TAG`
  targets are validated against the registered pipelines and tag vocabulary
  exactly as self-service creation is (`422` on an unknown target); an unknown
  target user is `404`, a duplicate is `409`. It is served by dedicated
  `AdminSubscribe`/`AdminUnsubscribe` use cases that **audit to the acting
  administrator** (not the target recipient) and scope a delete to the target
  (a subscription owned by another user is `404` through that path, never a
  cross-user delete).
- **Subscriptions console page.** The console's previously-placeholder
  **Subscriptions** tab is now a live page (`GET /admin/subscriptions`): pick a
  recipient, then add (Global / Pipeline / Tag, with the target chosen from a
  dropdown of the live vocabulary) or remove their subscriptions. The registered
  pipelines and tag vocabulary are embedded server-side so invalid targets are
  impossible in the UI; the dynamic data is fetched from the admin APIs, writes
  carry the CSRF token, and a `401` redirects to `/login`. Recipients and
  Subscriptions cross-link. Hand-authored, no external dependencies.

### Fixed

- **Flaky happy-path e2e (`windows-latest` / py3.12).** The end-to-end delivery
  test asserted the run had reached `SENT` after only waiting on the SMTP-capture
  signal, but the background assemble+deliver task commits the terminal state
  *after* sending the email — so the run could still be `SENDING` when asserted.
  The test now drains the background task deterministically
  (`scheduler.await_all`) before asserting. Structural sequencing, not a longer
  timeout.

## [0.15.0] — 2026-07-19

The dashboard gets its first **browser login page** and an **admin console** for
managing notification recipients — backed by a **configurable local admin
account** so an operator can sign in without pre-seeding the database. This is
the admin-managed step toward a usable browser dashboard; it adds three L1s
(`L1-AUTH-004`, `L1-DASH-007`, `L1-DASH-008`) — **72 of 72 L1 requirements
Implemented** — at **95.29% branch coverage** over **1547 tests**.

### Added

- **Configurable local administrator account (`L1-AUTH-004`).** A new optional
  `[auth.admin]` config section (`email` + an environment-substitutable
  `password`, exactly like `smtp.password`). At startup the composition root
  reconciles it: it creates the account if absent (Argon2id-hashed password,
  admin privilege, enabled) and, if it already exists, re-asserts admin + enabled
  **without** overwriting a password rotated through the admin API. This breaks
  the bootstrap chicken-and-egg (every account-creation route already required an
  admin) and guarantees the operator can never be locked out. Omit the section to
  disable the behavior — fully backward compatible.
- **Browser login page (`L1-DASH-007`).** A new public `GET /login` route serves
  a hand-authored HTML sign-in page; its client code posts to the existing JSON
  `POST /login` (unchanged) and redirects to the admin console on success.
- **Admin notification console (`L1-DASH-008`).** A new admin-gated
  `GET /admin/console` page for managing notification recipients — listing local
  accounts with their email, role, and status, and creating / updating /
  disabling them and resetting passwords. It is a thin presentation layer over
  the existing admin account APIs (echoing the CSRF cookie on writes; redirecting
  to `/login` on a `401`), backed by a new admin-gated `GET /admin/users`
  listing endpoint (and a `UserRepository.list_paginated` query). Subscription
  management — assigning which notifications each recipient receives — is the next
  step (see `docs/ROADMAP.md`).
- All new dashboard pages are **hand-authored HTML/CSS/JS with no third-party
  library and no external/CDN reference**; the no-external-reference conformance
  scan now covers the login and console assets too.

### Configuration

- `[auth.admin]` (optional) — `email` + environment-substitutable `password`.
  Documented in `config/config.toml.example`; omit to disable.

## [0.14.0] — 2026-07-19

An embedded **run-status board** — the runs API, which was JSON-only, now has a
browser page. Operators can see which runs are in flight versus delivered,
filter by state, and drill into a run's stages, offline and with no external
dependencies. Adds one L1 (`L1-DASH-006`) — **69 of 69 L1 requirements
Implemented** — at **95.21% branch coverage** over **1512 tests**.

### Added

- **Run-status board (`L1-DASH-006` / `L2-DASH-017`, `L2-DASH-018` /
  `L3-DASH-037`, `L3-DASH-038`, `L3-DASH-039`).** A new session-gated page at
  `GET /runs/board` presents run status as an embedded browser view. Unlike the
  JSON `GET /runs` endpoint (which defaults to *terminal* runs — a history view),
  the board surfaces **in-flight runs** too: a per-state summary with an
  "In work" total, an In-work / All / Delivered filter, a table with a gently
  pulsing badge for actively-working states (`AGGREGATING`, `SENDING`), and a
  click-to-expand row that lazily fetches the run's stages from the existing
  `GET /runs/{run_id}`. The page is **hand-authored HTML/CSS/JS with no
  third-party library and no external/CDN reference** — the no-external-reference
  conformance scan now covers every shipped dashboard asset, not just the metrics
  ones. The server-side renderer (`interfaces/rest/runs_board.py`) is a pure
  function over the run-summary projection, fully unit-tested; the route is
  declared before `/{run_id}` so the literal `/runs/board` path resolves to the
  board rather than being parsed as a run id.

### Documentation

- **`docs/ui-previews/`** — self-contained, browser-openable design mockups of
  the dashboard pages (the metrics dashboard and the run-status board), so the
  team can see the intended UI without running the service.

## [0.13.0] — 2026-07-19

Two trust-boundary hardening items from the road to 1.0.0, brought forward: a
**rejecting concurrency limit** for the gRPC ingress and the **R-ERR-001
wire-contract upgrade** that ships a structured `google.rpc.Status` error
envelope. Both are additive and backward-compatible; existing clients are
unaffected. Adds one L1 (`L1-API-005`) — **68 of 68 L1 requirements Implemented**
— at **95.19% branch coverage** over **1496 tests**.

### Added

- **Rejecting concurrency limit (`L1-API-005` / `L2-API-012` / `L3-API-019`,
  `L3-API-020`).** A new config key `grpc.max_in_flight_rpcs` (default `0` =
  disabled) installs a `ConcurrencyLimitInterceptor` that bounds
  concurrently-executing RPCs and **rejects** excess with `RESOURCE_EXHAUSTED`
  rather than queuing it unboundedly, giving pipeline clients the standard
  backpressure signal to back off on. `grpc.max_concurrent_rpcs` (which only
  *queues*) is unchanged and orthogonal. The interceptor is ordered after the
  correlation-id interceptor so a rejection log record carries the RPC's
  `correlation_id`. The fine-grained cause rides the new R-ERR-001 envelope
  (below) as an `ErrorInfo.reason` string (`RESOURCE_EXHAUSTED_CONCURRENCY`,
  with `{limit, in_flight}` metadata) — **no new proto `ErrorCode` enum value**,
  so the external `Message-Service-Proto` contract is untouched.
- **Structured gRPC error envelope — R-ERR-001 (`L3-ERR-023`).** Every gRPC
  error now additionally carries a serialized `google.rpc.Status` (with a packed
  `google.rpc.ErrorInfo`) in the standard `grpc-status-details-bin` trailing-
  metadata key, alongside the retained legacy `x-message-service-error-code`
  key. `ErrorInfo.reason` is the machine-readable error code, `domain` is
  `"message-service"`, and `metadata` carries the (redacted) diagnostic details.
  A client reading only the legacy key is unaffected; a client using
  `grpc_status.rpc_status.from_call` now receives the full structured envelope.
  Built entirely on `grpcio-status` (already a dependency) — no proto change.

### Configuration

- `grpc.max_in_flight_rpcs` (int, default `0`) — the rejecting concurrency
  limit; `0` disables it, any positive value caps concurrently-executing RPCs.
  Documented in `config/config.toml.example` and `config/default.toml`.

## [0.12.0] — 2026-07-19

Metrics visualization — an embedded, dependency-free metrics dashboard plus a
pre-built Grafana dashboard. This resolves `L1-DASH-004`, the **last remaining
v1 partial**: **all 67 of 67 L1 requirements are now Implemented**, every one
with at least one linked verification artifact, at **95.17% branch coverage**
over **1486 tests**.

### Added

- **Embedded metrics dashboard (`L1-DASH-004`).** A new admin route
  `GET /admin/metrics` (behind `require_admin`) obtains the current Prometheus
  exposition server-side from the same source `/metrics` serves, parses it, and
  returns a self-contained HTML page that renders each metric as inline SVG —
  counters as labeled bars, histograms as count/sum/avg plus bucket bars. The
  charting is **hand-authored HTML/CSS/JS with no third-party library and no
  external/CDN reference** (a conformance test enforces the zero-dependency
  guarantee); it satisfies `L2-DASH-011`'s offline constraint directly. The
  Prometheus-exposition parser (`L3-DASH-036`) is a pure, DOM-free Python module
  so the parsing logic is fully unit-tested. Promotes `L3-DASH-016`/`L3-DASH-017`
  and reworded `L2-DASH-011` (from the "Chart.js" example to hand-authored SVG).
- **Pre-built Grafana dashboard.** `deploy/grafana/message-service-dashboard.json`
  — an importable dashboard for the `/metrics` endpoint (transition rates,
  delivery outcomes, average and p95 email size / run duration), self-contained
  so it imports on an offline Grafana. A drift-guard conformance test fails the
  build if a panel query ever references a metric the service no longer exposes.

### Changed

- **`docs/uncovered-l1-allowlist.toml` is now empty.** With `L1-DASH-004`
  resolved there are no `Draft` L1s, so the requirement-coverage gate's deferral
  allowlist carries no entries.

## [0.11.0] — 2026-07-19

Audit log archival — the "audit log archival" backlog item promoted to real
requirements. Retention deletion is irreversible; sites with long-term
investigative or compliance obligations can now have expired audit records
written to a durable archive *before* the retention pruner deletes them, opt-in
via a single config key. **66 of 67 L1 requirements Implemented** at **95.19%
branch coverage** over **1470 tests**.

### Added

- **Opt-in audit-record archival (`L2-OBS-019`).** When
  `observability.audit.archive_directory` is configured, each retention-pruner
  tick fetches the exact batch of expired rows it is about to delete, writes them
  to a durable archive (`audit-archive-<date>.jsonl`, one JSON object per line
  carrying `timestamp` / `action` / `actor` / `resource` / `outcome` /
  `details`), flushes to disk, and only then deletes them (`L3-OBS-043`). If the
  archive write fails, the tick deletes nothing — the rows are retained and
  retried next tick — so no record is ever deleted without first being archived.
  Deletion still flows through the existing `delete_older_than` path, preserving
  the `L3-OBS-039` sole-deleter invariant. The archive directory is created and
  probe-validated at startup (`L3-OBS-041`). When the key is unset (the default)
  the pruner deletes without archiving, exactly as before.
- **`AuditLog.fetch_older_than` (`L3-OBS-042`).** A read that returns precisely
  the rows `delete_older_than` would remove; both share an `audit_id` tiebreak so
  that even when timestamps tie at the batch boundary, "archived == deleted" is a
  structural guarantee. Reworded `L1-OBS-003`; added `L2-OBS-019` +
  `L3-OBS-041`/`-042`/`-043`.

## [0.10.0] — 2026-07-19

Template authoring documentation. A guide for adding, validating, versioning,
and testing Jinja2 templates — tying together the template system the recent
per-pipeline subject/body override work built on — plus fixes for two stale
docs surfaced while writing it. No code or requirement changes; **66 of 67 L1
requirements Implemented** at **95.14% branch coverage** over **1457 tests**.

### Added

- **`docs/template-author-guide.md`.** End-to-end authoring workflow: the three
  template kinds (`REPORT_FRAGMENT` / `AGGREGATION` / `EMAIL_BODY`) and the
  context each receives, manifest registration, `(name, version)` and `"latest"`
  resolution, JSON Schema context validation, the sandbox rules
  (`autoescape` / `StrictUndefined` / size limits), the per-pipeline
  `subject_templates` / `email_body_template_overrides` overrides, and how to
  test a template in isolation. Linked from `CLAUDE.md`.

### Fixed

- **Stale `config/templates.manifest.example.toml`.** The example would not
  load: it used `schema_path` (the loader field is `context_schema_path`) and
  omitted the required `kind`. Rewritten to match `manifest_loader.py`.
- **Stale requirement counts in `CLAUDE.md`** (`192 / 393` → `195 / 404`),
  which had drifted as L2/L3 statements were added across releases.

## [0.9.0] — 2026-07-19

Per-L1 requirement-coverage gate — the "requirement-level coverage enforcement"
backlog item promoted to a real CI gate. A release can no longer ship an L1
requirement with *zero* linked verification artifacts (a gap the aggregate
line/branch coverage number cannot see) unless that L1 is explicitly recorded,
with a rationale, on a deferral allowlist. Building the gate surfaced that
`L1-DASH-004` is genuinely `Draft` (both dashboard L2 children are uncovered) —
now recorded honestly on the allowlist rather than loosely called "Partial".
**66 of 67 L1 requirements Implemented** at **95.14% branch coverage** over
**1457 tests**.

### Added

- **Requirement-coverage CI gate (`L2-CICD-016` / `L3-CICD-018`).**
  `scripts/check-requirement-coverage.py` reads the committed
  `docs/TRACE-MATRIX.md`, collects every L1 whose rolled-up status is `Draft`
  (no linked verification artifact anywhere in its subtree), and fails the build
  on any such L1 not present on `docs/uncovered-l1-allowlist.toml`. Exit 0 / 1 /
  2 for clean / uncovered / unreadable. Wired into the existing trace-matrix CI
  job (after the freshness `--check`), so it reads a matrix already proven fresh.
  Reworded `L1-CICD-004` to add the coverage obligation; added `L2-CICD-016` +
  `L3-CICD-018`/`-019`.
- **Deferral allowlist (`L3-CICD-019`).** `docs/uncovered-l1-allowlist.toml` — a
  TOML `[[allowed]]` list where each tolerated `Draft` L1 carries an `id` and a
  mandatory `reason` (a reason-less entry is a parse failure). Its one entry is
  `L1-DASH-004` (`R-DASH-004`, the embedded Chart.js dashboard gated on a browser
  test harness; the `/metrics` scrape half ships under `L1-OBS`).

## [0.8.0] — 2026-07-18

Per-RPC / per-request correlation ids + proto-version gate (`R-API-001`) —
promoted to real requirements, **closing two of the three remaining v1 partials
at once**: `L1-API-001` and `L1-OBS-001`. **66 of 67 L1 requirements
Implemented** (was 64) at **95.14% branch coverage** over **1448 tests**. Only
`L1-DASH-004` (the embedded Chart.js metrics dashboard) remains partial toward
1.0.0.

### Added

- **gRPC per-RPC correlation interceptor (`L3-API-002` / `L3-OBS-003`).** A
  `grpc.aio.ServerInterceptor` binds a fresh `correlation_id` into the structlog
  context at the entry of every RPC (success *and* failure, not only the
  unexpected-error path as before) and clears it in a `finally`, so every log
  record emitted while handling an RPC carries the id and none leaks between
  RPCs on a shared worker task. The unexpected-error translator now **reuses**
  that bound id for its `x-message-service-correlation-id` trailing metadata, so
  a failed RPC surfaces to the client the same id its server-side logs carry.
- **FastAPI per-request correlation middleware (`L3-OBS-004`).** The dashboard
  analogue: a middleware (registered outermost) binds a fresh `correlation_id`
  per request and clears it afterward, so route logs carry it automatically.
- **Proto-version pin gate (`L3-API-004`).** `scripts/check-proto-version.py`
  asserts the installed `message_service_proto.__version__` matches the tag
  pinned in `pyproject.toml` (exit 0 / 1 / 2 for match / mismatch /
  undeterminable), wired as a new `proto-version` CI job — catching a lockfile
  that resolves a different proto version than the manifest pins.

### Fixed

- **gRPC interceptor handler factory.** During development the interceptor was
  written against the nonexistent `grpc.aio.unary_unary_rpc_method_handler`; the
  method-handler factory is transport-agnostic (`grpc.unary_unary_rpc_method_handler`).
  Corrected before release; caught by the new interceptor tests.

## [0.7.0] — 2026-07-18

Per-pipeline orphan disposition overrides — the next deferred-feature item
(`R-SWEEP-001`) promoted to real requirements, continuing the per-pipeline
override theme (`subject_templates`, `email_body_template_overrides` → now
`orphan_disposition_overrides`). Operators can give a pipeline its own orphan
disposition policy — e.g. `NOTIFY_ADMINS` for a production pipeline but
`DISCARD_SILENTLY` for a high-churn test pipeline — with the global policy as
fallback. This release also fixes stale documentation left by the v0.6.0 resend
change. **64 of 67 L1 requirements Implemented** at **95.07% branch coverage**
over **1434 tests**; three intentional partials remain toward 1.0.0.

### Added

- **Per-pipeline orphan disposition policy override (`L2-SWEEP-011`).** A new
  optional `pipelines.orphan_disposition_overrides` mapping (`pipeline_type` →
  ordered list of disposition action ids) overrides the global
  `sweeper.disposition_actions` for matching pipelines; pipelines without an
  entry use the global policy (so an empty mapping preserves prior behavior),
  and an empty list means "orphan but take no action". Override keys must be
  registered pipelines (`L3-SWEEP-022`) and every override action must have a
  registered handler — validated at startup with `ConfigurationError`, the same
  fail-fast guarantee as the global policy, which also rejects the
  reserved-but-unimplemented `SEND_PARTIAL_FLAGGED` / `NOTIFY_SUBSCRIBERS` ids in
  overrides (`L3-SWEEP-024`). The sweeper resolves the action list per orphaned
  run and uses it uniformly for the `SWEEP_ORPHAN` audit, the outbox rows, and
  the tick's action count (`L3-SWEEP-023`). Reworded `L1-SWEEP-003` (previously
  "globally configured") and added `L2-SWEEP-011` + `L3-SWEEP-022`/`-023`/`-024`.

### Fixed

- **`examples/07-manual-resend` documentation.** The README still described and
  showed the pre-v0.6.0 resend subject (`Run <run_id> -- <pipeline>`) and framed
  the demo around the two emails having different subjects — no longer true since
  v0.6.0 made the resend share the first-delivery subject. Updated the narrative,
  the expected-output block, and the "what to look for" notes; the resend is now
  correctly described as distinguished by its `RESEND_REPORT` audit action rather
  than its subject. (The demo's `run.py` was already correct.)

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

[Unreleased]: https://github.com/joey-huckabee/Message-Service/compare/v0.12.0...HEAD
[0.12.0]: https://github.com/joey-huckabee/Message-Service/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/joey-huckabee/Message-Service/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/joey-huckabee/Message-Service/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/joey-huckabee/Message-Service/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/joey-huckabee/Message-Service/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/joey-huckabee/Message-Service/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/joey-huckabee/Message-Service/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/joey-huckabee/Message-Service/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/joey-huckabee/Message-Service/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/joey-huckabee/Message-Service/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/joey-huckabee/Message-Service/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/joey-huckabee/Message-Service/releases/tag/v0.1.0
