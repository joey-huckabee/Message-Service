# Message-Service — ROADMAP

This document has two parts:

1. **Upcoming v1 increments** — the planned next steps within v1 scope. Order is the current best guess; subject to team re-prioritization.
2. **Deferred from v1** — items explicitly carved out of v1 during requirements elicitation, retained here for the rationale and the trigger that would prompt reconsideration. Items in this section are **not** requirements; promotion to a future release moves them into `docs/L1-REQ.md` with a fresh requirement identifier.

---

## Part 1 — Upcoming v1 increments

Last full increment merged: **28 — Runnable demonstration examples**. After 28 a v1 release-readiness pass surfaced the proto-dependency portability blocker (35) and its public-repo follow-up (36); both are tracked under **v1 release-readiness fixes** below. With that pass closed, the first official release is tagged **v0.1.0** — the start of a 0.x line that works up toward 1.0.0 as the five intentional v2-deferrals (see Part 2) are resolved or explicitly scoped out.

**v1 release-readiness fixes** (in flight):

- **35 — Proto dependency portability** *(✅ done — this commit)*. The `message-service-proto` runtime dependency was declared as `{ path = "../message-service-proto", develop = true }`, which baked an absolute `file:///.../Message-Service-Proto` URL into the built wheel's `Requires-Dist` metadata. CI was correspondingly broken: `actions/checkout@v4` only fetches the current repo, so a fresh runner couldn't resolve the path. Fix: cut `v0.1.1` on the proto repo (one commit ahead of `v0.1.0`, primarily a packaging refresh that bumped the protobuf constraint to match this consumer's `^6.33`); switch `pyproject.toml` to `{ git = "https://github.com/joey-huckabee/Message-Service-Proto.git", tag = "v0.1.1" }`; lockfile records the resolved SHA `8d6cf99...`; CI gains a narrowly-scoped `git config --global url."https://x-access-token:${PROTO_REPO_TOKEN}@github.com/joey-huckabee/".insteadOf` step in each of the three `poetry install`-running jobs (pytest, precommit, trace-matrix). New repo secret required: `PROTO_REPO_TOKEN` — a fine-grained PAT with read-only access to the proto repo. **(Superseded by 36: the proto repo was made public, so the `insteadOf` steps and the `PROTO_REPO_TOKEN` secret were removed — a public `git+https` URL installs without auth.)** Wheel rebuilt; `Requires-Dist: message-service-proto @ git+https://github.com/joey-huckabee/Message-Service-Proto.git@v0.1.1` (no `file://`). Trace impact: closes item (2) of `R-API-001` (pinned-tag proto dependency); L3-API-003 reworded from Deferred → Implemented with new conformance test in `test_deploy_artifacts.py` rejecting the local-path / develop-mode shapes; L3-API-004 (CI version-mismatch check) reworded — still deferred but the "tautologically passes" rationale (a property of the local-path dep) replaced with "small enough failure mode to defer relative to implementation cost." L1-API-001 stays Partial (L3-API-002 + L3-API-004 still deferred per R-API-001).

- **36 — Proto repo made public + v0.1.0 tag** *(✅ done — this commit)*. The `joey-huckabee/Message-Service-Proto` repo was made public, which removes the only reason CI needed authenticated access to it. The three `Configure git access to private proto repo` steps (one per `poetry install`-running job: pytest, precommit, trace-matrix) and the `PROTO_REPO_TOKEN` secret dependency were deleted from `.github/workflows/ci.yaml`; the `git = "https://github.com/joey-huckabee/Message-Service-Proto.git", tag = "v0.1.1"` dependency now resolves anonymously on a fresh runner. The `test_deploy_artifacts.py` conformance test (git+tag form required; path/develop forbidden) is unaffected — it never asserted anything about auth. Also refreshed the stale README `Status` section (was "through Increment 16" with `65 / 186 / 361` counts) to the real post-v1 state (`67 / 192 / 393`). `pyproject.toml` version left at `0.1.0` — this is the first *officially tagged* release, deliberately starting the 0.x line low with a long runway to 1.0.0. Trace impact: none (CI/docs only; no requirement wording changes).

Trace state pre-28 (unchanged through release-readiness fixes that don't promote L1s): 67 L1 / 192 L2 / 393 L3; 62 of 67 L1s Implemented (5 intentional v1 Partials each with a named R-XXX-NNN deferral entry in Part 2). Six v1-cycle deferred-features captured: R-ERR-001, R-ERR-002, R-DASH-004 (original 3) plus R-API-001 (gRPC infrastructure hardening, now reduced to 2 of original 3 items), R-AGGR-001 (custom email body contributions, pre-existing).

### Status snapshot (as of 2026-04-30)

**Five intentional v1 Partials** (matrix shows Partial because the matrix script has no notion of "Deferred to v2"; each L1 has all-but-one L2 children Implemented and the deferred L2 carries a `(Deferred to v2 — see ROADMAP R-XXX-NNN.)` prefix on its L3 wording):

| L1 | Why Partial | Deferral entry |
|---|---|---|
| L1-API-001 | L3-API-002/004 deferred (per-RPC interceptor + CI version check); L3-API-003 closed by v1 release-readiness proto-pinning | R-API-001 |
| L1-AGGR-001 | L3-AGGR-004/005/018 deferred (per-stage email body contributions with `position` enum) | R-AGGR-001 |
| L1-DASH-004 | L3-DASH-016/017 deferred (embedded Chart.js metrics dashboard frontend) | R-DASH-004 |
| L1-ERR-002 | L3-ERR-010/011 deferred (error-code-stability lockfile + helper script) | R-ERR-002 |
| L1-OBS-001 | L3-OBS-003/004 deferred (per-RPC + per-FastAPI-route correlation interceptor — same as L1-API-001) | R-API-001 |

These 5 do not represent missing functionality — they represent deferred work documented in Part 2 with explicit re-evaluation triggers (mostly: trust-boundary widening past the ISOLAN deployment assumption + the proto package gaining a published release cadence).

Done:

- **Cluster 14 (sweeper hardening + test isolation)** — 14a `04a88dc`, 14b.1 `460d127`, 14b.2 `7c33c87`, 14b.3 `3b48d38`, 14b.4 `5456f2e`, 14c.1 `9b28e2b`, 14c.2 `3fd0673`, 14d `4b24818`, 14e `fb54f98`, 14f `1cdfc3d`, 14h `1b14b92`. 14g superseded by 25a; 14c.3 obviated by 14b's post-transition fetch.
- **Cluster 25 (requirements spec cleanup)** — 25a `1f26f2f`, 25b `eb5f537`, 25c `c5b9854`, 25d `3f45426`, 25e `d67539a`, 25f `5614aa8`, 25g `2406dd1`.
- **Cluster 26 (CI/CD requirements + workflows)** — 26a `220c1d5`, 26b `c22ebc9`, 26c `f99f795`, 26d `aa6550c`, 26e `af59b43`. Cluster fully closed: all seven L1-CICD now Implemented.
- **Increment 15** — Prometheus metrics adapter (`fe5c3a4`).
- **Increment 16** — Local-account auth adapter, Argon2 + sessions (`7ede66c`).
- **Increment 17** — FastAPI app factory + bootstrap wiring (`aa3902e`).
- **Increment 18** — Subscription CRUD routes (`310ce2d`).
- **Increment 19a** — Past-runs paginated list + run-detail metadata view (`9b0a87b`).
- **Increment 19b** — Manual resend (re-renders from saved Stage context) (`f3d7509`).
- **Increment 19c** — Filesystem report store + report viewer (`b900ce3`); closes clauses 1+2 of L1-DASH-003.
- **Increment 20a** — Admin gate (`require_admin`) + template registry inspection (`fd27acf`); closes clause 3 of L1-DASH-003 — L2-DASH-007 + L2-DASH-009 promoted to Implemented.
- **Increment 20b** — Admin user management (CREATE_USER / UPDATE_USER routes; password reset) (`39c99a0`); closes net-new L1-AUTH-003 — L2-AUTH-007/008/009 all promoted to Implemented; clears the L3-OBS-035 deferred-to-Increment-20 tag.
- **Increment 20c** — Admin audit-log viewer (`GET /admin/audit`) (`33a4a56`); closes net-new L1-DASH-005 — L2-DASH-015 + L2-DASH-016 promoted to Implemented. Admin stream (20a/b/c) complete.
- **Increment 21** — E2E harness + four test suites (happy_path, orphan_path, resend, admin) (`ef10488`); real grpc.aio + httpx + tmp SQLite + in-process aiosmtpd. New dev dep: `aiosmtpd`. 4 new e2e tests at the L1-tier marker level.
- **Increment 22** — Error-mapping + servicer tests, exception-detail coverage (`41974a7` + `c82f4a6` + `4c59b4a`); L1-ERR-001..004 all promoted Draft → Partially Implemented. DomainError intermediate; http_status + log_level ClassVars; bootstrap proto-enum self-check; details redaction in translator; ruff BLE/S110/S112 enabled. R-ERR-001 (wire-format upgrade) + R-ERR-002 (error-code lockfile) captured as deferred work.
- **Increment 20d (partial)** — Prometheus `/metrics` scrape endpoint (`4517ba8`); promotes L2-OBS-004 + L3-OBS-007 Draft → Implemented; L1-OBS-002 rolls up to Implemented. The embedded Chart.js dashboard half of L1-DASH-004 is deferred to R-DASH-004 (test-harness blocker — needs Playwright or similar before the frontend code can be built reliably).
- **Increment 23** — Deployment polish (`2e5cdbb` + `7bae9da`); promotes **all three L1-DEP-*** Draft / Partially Implemented → Implemented. First commit: 15 DEP items across systemd-unit + NSSM-README + Windows-install-demo conformance, graceful-shutdown integration tests, CLI smoke + LF/CRLF tests, pyproject/poetry.lock conformance, `EnvironmentFile=-` operator passthrough, architecture-boundary + pathlib stub replacement, `[tool.poetry.scripts]` bug fix. Second commit: closed the four remaining L3 gaps under L1-DEP-001 — L3-DEP-001 (CI workflow matrix inspection), L3-DEP-002 (skipif `reason=` AST scanner), L3-DEP-004 (path-separator literal scanner over `src/`), L3-DEP-011 (real-grpc.aio integration test asserting new RPCs return UNAVAILABLE during the grace window + sweeper-loop stop-event observation).
- **Increment 27** — UoW serialization correctness + spec alignment (`410aa90` rescope, `ee69b87`, `d48b4c8`, `0f13927`, `388bdf2`, `88279e5`, `f69e95a`, `2e014b3`, `950d754`). Survey at kickoff exposed pool-vs-actual drift (L2-PERS-004 + L3-PERS-006/007/021 + L1-REQ.md:578 config-knob + `c4-component-persistence.puml:21` all described a pool architecture v1 didn't implement; the BEGIN-collision bug existed *because* the implementation was single-connection without the synchronization that design required). Architectural decision for v1: single-connection + asyncio.Lock (SQLite serializes writers regardless of pool size; workload doesn't justify pool complexity). Pool architecture preserved verbatim with re-evaluation triggers in `docs/archive/connection-pool-architecture.md`. Spec rewritten (L2-PERS-004 + L3-PERS-006/007/021 reworded; `connection_pool_size` dropped from L1-CFG-003 enumeration; C4 diagram updated; unused config field removed; stale `R-PERS-001` cross-references corrected). Mutex implementation lands lazy `asyncio.Lock` on the factory + acquire-before-BEGIN + try/finally release on every transaction-closing path. Five new tests in `tests/integration/persistence/test_unit_of_work_concurrency.py` cover the contract via real concurrent writes (no manufactured proof-of-effectiveness); L2-PERS-004 promotes Draft → Implemented (L3-PERS-006/007/021 first-time verified). Orphan-path e2e test refactored to drive `sweeper.tick()` + `dispatch_pending()` synchronously, eliminating a second timing race (dispatcher-completion vs. ORPHANED-state) by structural sequencing rather than polling. Stability: 30/30 passes --no-cov + 20/20 passes with full coverage instrumentation.
- **Increment 29** — Rendered-report retention pruner (`fb3ab75`, `a800336`, `affbca3`, `e9990ac`, `1659f87`). 9 new L3 statements (L3-PERS-027..035) authored as the first sub-step (the three parent L2s had zero L3 children before). Three new config keys (`report_retention_days`, `prune_interval_seconds`, `max_prunes_per_iteration`) on `FilesystemPersistenceConfig`. New `PRUNE_REPORT` audit action; new `ReportPrunerUseCase` (per-tick: cutoff arithmetic via injected Clock → list_expired-by-cutoff with TERMINAL_STATES → per-run file walk → delete-then-audit per file → rmdir cleanup). New `ReportPrunerLoop` infrastructure adapter mirroring `SweeperLoop` shape on the same `BackgroundTaskScheduler`; bootstrap constructs both and the CLI starts both after listener bind. 11 real-filesystem + real-SQLite integration tests + 3 AST-scan conformance tests for the L3-PERS-035 sole-deleter invariant (allow-list = report_pruner.py + bootstrap/service.py's writable-test probe; spec wording for L3-PERS-035 updated to acknowledge the latter). Two impl bugs caught by the integration tests and fixed in 29e: SQL candidate cap was being applied on the query (starving newly-eligible runs after first tick) — fixed via generous `_CANDIDATE_QUERY_LIMIT=10_000` constant; already-pruned candidates were counted in `runs_processed` — fixed via skip-without-counting. Trace impact: L2-PERS-013 promotes Draft → Implemented; L2-PERS-011/012 promote Draft → Partially Implemented; L1-PERS-004 promotes Draft → Partially Implemented. The remaining L3-PERS-030 lifecycle marker is 32-territory; L1-PERS-004 → Implemented after 32 lands.
- **Increment 30** — Audit-log retention pruner (`42455b4`, `70eee76`, `62b09c4`, `891ca60`, `bce7479`, `16eed55`); closes R-PERS-002 deferred-features entry. **Important spec finding during 30a:** the 2026-04-27 Agent-1 audit was wrong about L2-OBS-018 having dangling cross-references to L3-OBS-037/038 — both L3s exist and are properly authored. Increment 31 has one fewer spec-cleanup item to address. Two new L3s authored: L3-OBS-039 (sole-deleter conformance — only the audit_log_pruner module + the audit-log adapter may issue DELETE/UPDATE against `audit_log`) and L3-OBS-040 (anti-recursion — the pruner SHALL NOT emit audit rows for its own delete activity, since recording each prune as an audit row would create a self-referential growth pattern). Two new config keys (`cleanup_interval_hours` default 24, `cleanup_batch_size` default 10000) on `AuditConfig`. New `delete_older_than` method on the `AuditLog` port (reserved for the pruner per L3-OBS-039); SqliteAuditLog implements it via a sub-select on `audit_id` because stdlib sqlite3 lacks `SQLITE_ENABLE_UPDATE_DELETE_LIMIT`. New `AuditLogPrunerUseCase` + `AuditLogPrunerLoop` mirroring the 29 pattern on the same `BackgroundTaskScheduler`. 7 integration tests + 4 conformance tests (oldest-first ordering, strict-less-than boundary, multi-tick draining at cap=2 across 5 rows, anti-recursion verification both behaviorally and via static enum-set assertion, sole-deleter SQL string-literal scan, sole-deleter port-method call-site scan). R-PERS-002 entry closed in-place with the implementing-commit hashes (closure-traceable from the deferred-features section). Trace impact: L2-OBS-008 promotes Draft → Partially Implemented (10 test markers cover L3-OBS-014/015/016/039/040); L2-OBS-009 stays Draft (L3-OBS-017 lifecycle marker is 32-territory); L1-OBS-003 stays Partially Implemented pending 32's marker work.
- **Increment 31** — L1-MAIL-004 admin notification + spec inconsistencies (`94864ef`, `95ed01d`, `2d480fb`, `5e503cc`). Closes the third and final audit-identified drift. Two new L3s authored in 31a (L3-MAIL-030 triggering sequence, L3-MAIL-031 admin recipient list); existing L3-MAIL-014/015/016/017/024 already covered the audit-row schema, template, and persistence. New `EmailSizeExceededError(EmailDeliveryError)` subclass; mailer raises the subtype on size violation while staying backwards-compatible with generic `except EmailDeliveryError` handlers. New service-internal admin-notification template at `src/message_service/templates/email/admin_notification.j2` (Jinja2 with autoescape, accepts only `{run_id, failure_reason, timestamp}` per L3-MAIL-015). `AssembleAndDeliverUseCase` now catches `EmailSizeExceededError` BEFORE the generic `EmailDeliveryError` block and executes the L3-MAIL-030 four-step sequence: persist rendered body via existing `_save_email_body` helper → audit + transition in single UoW → admin notification AFTER the UoW commits (failures here log but do not roll back) → no SMTP retry of the failing email. Empty `admin_recipients` logs a WARNING and skips the notification per L3-MAIL-031. 7 new unit tests cover the L3-MAIL-030/031 contract end-to-end (FAILED transition + L3-MAIL-014 audit details, oversized-report persistence, admin notification recipients + subject + rendered body, empty-recipients skip, fail-soft on admin SMTP failure, no-retry of the failing email, autoescape behavior). Two spec-hygiene edits (31e + 31f): L2-OBS-007 verification method I → T,I to match its L3 child's testability; L3-MAIL-001 reworded to drop the speculative "pooling on the ROADMAP pending profiling" wording (same anti-pattern as the pre-27 pool drift). 31d was REMOVED as a sub-step — the 30a finding showed L3-OBS-037/038 already exist; no work needed. Trace impact: **L1-MAIL-004 promotes Draft → Implemented** (first time fully Implemented since spec authoring); L2-MAIL-009/010/011 each promote Draft → Implemented; L2-OBS-007 verification-method updated; L3-MAIL-001 reworded.
- **Increment 33** — L1-TMPL-002 "latest" version resolution (`9880971`, `a141e02`, `5596ea6`, `33345cb`). Closes the L1-TMPL-002 sentinel-resolution drift surfaced by the 2026-04-27 audit. 33a reworded L3-TMPL-009/010/011/012 to match the simpler v1 design (resolved versions stored on existing TemplateRef fields of the Run aggregate; no separate `resolved_templates_json` column needed) and dropped the speculative-deferral wording from L3-TMPL-009/010 in favor of explicit pinning of the case-sensitive `"latest"` sentinel + `packaging.version.Version` ordering rule. 33b adds `TemplateRepository.resolve_latest(name) -> TemplateRef` to the port and implements it on `InMemoryTemplateRepository` (gather entries with matching name; max via `packaging.version.Version` to honor PEP 440 pre-release ordering; canonical `str(Version(...))` form returned). 33c hooks resolution into `BeginRunUseCase` via a private `_maybe_resolve_latest(ref, *, role, stage_id)` helper that pass-throughs non-sentinel inputs and re-raises `UnknownTemplateError` from `resolve_latest` with the role-aware details shape the existence check uses. Resolution happens BEFORE the existence check (so `template_repo.exists(...)` operates on the resolved ref) and BEFORE aggregate construction (so the persisted Run carries the canonical version, frozen for the run's lifetime per L3-TMPL-011). 33d adds 11 unit tests across `tests/unit/infrastructure/templating/test_manifest_loader.py` (6 tests of `resolve_latest` semantics: missing name → UnknownTemplateError; single entry; max-of-many; pre-release ordering; canonical form; name filtering) and `tests/unit/application/use_cases/test_begin_run.py` (5 tests of BeginRun behavior: aggregation-template "latest" resolves before persistence; per-stage "latest" resolves before persistence; explicit version passes through; UnknownTemplateError envelope shape; persisted-Run-as-source-of-truth). 33d also narrowed L3-TMPL-012 wording to acknowledge that the BEGIN_RUN audit details currently include only `declared_stage_ids` (per L3-OBS-013); widening the audit-detail surface is out of scope for 33. Trace impact: L2-TMPL-005 + L2-TMPL-006 each promote Draft → Implemented (14 test markers across 2 files); L1-TMPL-002 stays Partially Implemented because L2-TMPL-004 (sibling) is independent.
- **Increment 34** — L1-TMPL-004 JSON Schema context validation. Closes the audit-identified gap that `Jinja2SandboxedTemplateRenderer.render()` was performing size checks but never validating context against the manifest's declared schema (the `jsonschema` library was not even imported). 34a authored four new L3 statements (L3-TMPL-029 render-time validation timing; L3-TMPL-030 templates without `context_schema_path` skip validation; L3-TMPL-031 service-start eager compile + ConfigurationError on bad schemas; L3-TMPL-032 RFC 6901 JSON Pointer derivation from `ValidationError.absolute_path`) and refined L3-TMPL-018 to be explicit about eager-at-load construction; L3-TMPL-020 reworded to use `json_pointer` (avoids name collision with `meta.context_schema_path`) and to specify the three required detail fields. 34b confirmed `jsonschema = "^4.25"` + `types-jsonschema = "^4.25"` were already declared in pyproject.toml. 34c wired validation into the renderer: a `_build_validators(repository)` static method runs at `__init__` time, iterating `repository.list_all()` and for each entry with `context_schema_path` reading the file, parsing JSON, running `Draft202012Validator.check_schema`, and caching a `Draft202012Validator` instance keyed by `(name, version)`; any failure (missing file / JSON parse error / `SchemaError`) raises `ConfigurationError` with `{name, version, schema_path, reason}` aborting service start. Render-time validation slots in between the size pre-check and the Jinja2 invocation: looks up the cached validator (skips if absent), calls `validator.validate(context)`, and on `jsonschema.ValidationError` raises `ContextSchemaViolationError` with `{name, version, json_pointer, validator, instance_value, message}`. Module-level `_to_json_pointer(absolute_path)` helper renders the path per RFC 6901 (`["foo", "bar", 0]` → `"/foo/bar/0"`; empty deque → `""`; escapes `~`→`~0`, `/`→`~1` inside segments). 34d added a new test file `tests/unit/infrastructure/templating/test_renderer_schema_validation.py` with 16 tests covering all five new/refined L3s plus the L3-TMPL-022 size-before-schema ordering invariant and the L3-TMPL-019 `$defs`-internal `$ref` resolution path; added 2 gRPC translator tests in `tests/unit/interfaces/grpc/test_error_mapping.py` pinning `ContextSchemaViolationError` → `INVALID_ARGUMENT` with `ERROR_CODE_CONTEXT_SCHEMA_VIOLATION` in trailing metadata; renderer's existing test fixture updated to set `repo.list_all.return_value` (was previously a default MagicMock that became iterable only by the new constructor's contract). 34e: ruff format + ruff check + mypy clean; full non-e2e suite at 1159 passed / 0 failed; coverage holds at 94.88% (above the 85% gate); trace matrix regenerated. **Trace impact**: L1-TMPL-004 promotes Draft → Implemented (first time fully Implemented); L2-TMPL-010 promotes Draft → Implemented (5 L3 children, 8 test markers); L2-TMPL-011 promotes Draft → Implemented (2 L3 children, 7 test markers); L3 totals: 386 → 390. Recommended next: 32 (trace-matrix coverage pass).

**Done in v1 closeout cycle**:

- **Increment 32** *(✅ done — 13 sub-step commits)* — Trace-matrix coverage pass. 48 L1 promotions across 32a-m. Sub-step commits: 32a `b81df1f` (API+RUN), 32b `6e70641` (STAGE+SWEEP+AGGR), 32c `304eadc` (SUB+AUTH), 32d `284c227` (MAIL+TMPL), 32e `873a0b5`, 32f `6ac6095` (AGGR residuals), 32g `e59b224` (SWEEP residuals), 32h `c9a2d5d` (L2-RUN-016), 32j `5eaf478` (DASH residuals), 32k `c471109` (PERS residuals), 32l `abb395b` (OBS residuals), 32m `cb73a94` (ERR+CFG residuals), 32i `f86c536` (closeout). Final state: 393 L3 statements; 1263 tests at 94.88% branch coverage; trace matrix --check clean.
- **Increment 24** — Documentation deliverables (`034b161`). Five new docs: `docs/test-strategy.md` (promoted from `tests/README.md`, expanded with I/O guard / SMTP capture / Windows event-loop quirk / pytest-by-requirement helper); `docs/adr/001-sqlite-for-in-flight-state.md` (rationale for SQLite + WAL + single-connection mutex; alternatives, consequences, four re-evaluation triggers); `docs/adr/002-hexagonal-boundary-enforcement.md` (rationale for the static AST conformance test approach; four alternatives, consequences); `docs/operator-runbook.md` (deployment, day-2 ops, common failure modes, backup/restore, upgrade procedure, the five intentional v1 Partials); `docs/pipeline-integration-guide.md` (per-RPC contract, lifecycle state machine, error code table, idempotency + retry guidance, end-to-end Python example). `tests/README.md` slimmed to a pointer at `docs/test-strategy.md`; CLAUDE.md and CONTRIBUTING.md updated to point at the new location. Verification: 1263 passed in -m "not e2e"; coverage 94.88% (gate 85%); trace matrix --check clean (no requirement promotions — docs only).
- **Increment 28** *(✅ done — this commit; sub-step commits `4568288`, `5632275`, `a737eee`, `57c9690`)* — Runnable demonstration examples. New top-level `examples/` directory with eight self-contained scenarios (`01-hello-world` through `08-error-recovery`), shared helpers under `examples/_lib/`, and a new conformance test `tests/conformance/test_examples_present.py` verifying the layout (106 inspection assertions; no demo execution in CI). 28a `4568288` lands the scaffolding: `_lib/smtp_capture.py` (in-process `aiosmtpd` controller exposing `CapturedMessage` with parsed-MIME accessors), `_lib/service_runner.py` (subprocess-based `running_service` context manager with port-readiness probes + SIGTERM teardown), `_lib/pretty.py` (stdlib-only timestamped/colored output, NO_COLOR-respecting, UTF-8 stdout reconfigure for Windows cp1252), `_lib/expectations.py` (✓/✗ DSL), `_lib/common.py` (template/manifest/tag-vocabulary writers + state-dir reset), and the top-level `examples/README.md` opening with a prominent "No mail server is required" section. 28b `5632275` ships scenarios 01–03 (hello-world / multi-stage SINGLE_AGGREGATED / PER_STAGE-attachments). 28c `a737eee` ships scenarios 04 (idempotent-overwrite retry; was_retry=False→True; second submission's payload visible in attachment, first replaced) and 05 (TAG-granularity routing; production-watcher matches, staging-watcher filtered out at the repo before the mailer ever sees the address). 28d `57c9690` ships scenarios 06 (orphan: tight `run_timeout_seconds=5`/`poll_interval_seconds=1`; deliberately skipped FinalizeRun; SQLite-direct read verifies state==ORPHANED; no SMTP sent), 07 (full operator flow: Argon2-hashed user → BeginRun→Submit→Finalize→first email → POST /login (httpx) → POST /runs/{run_id}/resend with X-CSRF-Token → second email; both deliveries captured), 08 (three deliberate gRPC misuse cases asserting status code + `x-message-service-error-code` trailer for UNKNOWN_PIPELINE_TYPE / UNKNOWN_TAG / DUPLICATE_STAGE_ID). Each scenario uses non-overlapping ports (50051+N gRPC / 8080+N dashboard / 1025+N SMTP) so all eight can run side-by-side without collision. State directories are reset on entry so re-running is idempotent; `examples/.gitignore` excludes per-scenario `.tmp/` from version control. **Trace impact**: none — examples are documentation-tier deliverables. The conformance test verifies layout invariants (every scenario has README/run.py/config.toml; every README has the seven required sections; `examples/_lib/` has the six required helpers; the top-level README lists every scenario; `.gitignore` excludes `.tmp/`).

**Status: all v1 work landed.** Recommended next: tag v1 from this commit.

**Recommended sequence (historical, all done)**: ~~29~~ → ~~30~~ → ~~31~~ → ~~33~~ → ~~34~~ → ~~32~~ → ~~24~~ → ~~28~~ → **v1 tag.**

The list below is keyed off `docs/TRACE-MATRIX.md` (now authoritative for status, per 25a) and the empty source/test directories under `src/message_service/interfaces/rest/{auth,routes}/`, `tests/e2e/`, and `docs/adr/`.

Re-order freely. Each item names the requirement category it closes so trace-matrix impact is visible.

The completed-increment sections (14a–f, 25a–e, 26a–d) are retained below as historical record. They describe the rationale for each landed change; future readers may find them useful as ADR-adjacent context.

### Increment 14a — Default sweeper config aligned with implemented handlers  *(✅ done — commit `04a88dc`)*

**Problem**

The schema default and shipped config reference a handler that is not implemented:

- `src/message_service/config/schema.py:184` — `disposition_actions` defaults to `["SEND_PARTIAL_FLAGGED", "NOTIFY_ADMINS"]`.
- `config/default.toml:47` mirrors that default.
- `src/message_service/bootstrap/service.py:271-276` registers `SendPartialFlaggedHandler` under that action id.
- `src/message_service/infrastructure/sweeper/handlers.py:100-105` (and `NotifySubscribersHandler` at `:117-122`) raise `NotImplementedError`.
- `config/config.toml.example:103-106` correctly documents both as "NOT YET IMPLEMENTED -- will raise" and ships `["NOTIFY_ADMINS", "DISCARD_SILENTLY"]`.

A service started with the default config hits `NotImplementedError` on every orphaned run. The L3-SWEEP-013 "handlers SHALL NOT raise — failures logged at ERROR and swallowed" contract converts this from a crash to a silent guaranteed-failure on every disposition. The shipped example config and the runtime default disagree.

**Work** (in order — defense in depth)

1. Change the schema default and `config/default.toml` to match the example: `["NOTIFY_ADMINS", "DISCARD_SILENTLY"]`. Two-line fix.
2. Make `bootstrap/service.py` register only handlers that are actually implemented. The two placeholders (`SendPartialFlaggedHandler`, `NotifySubscribersHandler`) should not be in the `handlers_by_id` dict at all until they have real implementations.
3. Reuse the L3-SWEEP-012 pattern: configs that reference an unregistered action id raise `ConfigurationError` at startup listing the unknown name and the allowed (registered) set. The `SweeperUseCase` constructor's existing validation already validates against `handlers_by_id` — once step 2 lands, that check now correctly rejects misconfiguration before the service starts accepting traffic, instead of failing per-orphan at runtime.
4. Add a conformance test that every action id in the schema's *default* `disposition_actions` is registered in bootstrap's `handlers_by_id` — prevents this drift from recurring.

**Verification**

- Unit: `Config.model_validate({})` produces a sweeper config whose every action id maps to a non-placeholder handler.
- Unit: starting bootstrap with a config that references `"SEND_PARTIAL_FLAGGED"` raises `ConfigurationError` at startup, not at first orphan.
- Conformance: schema default ⊆ bootstrap registered ids.

When `SEND_PARTIAL_FLAGGED` and `NOTIFY_SUBSCRIBERS` are actually implemented (Part 2 may eventually demand them), reverse step 2 and update the conformance test.

### Increment 14b — Sweeper exactly-once: atomic transition + outbox table  *(✅ done — commits `460d127`, `7c33c87`, `3b48d38`, `5456f2e`)*

Closes the real gap behind **L2-SWEEP-006**, which is currently mis-rolled-up as Implemented in `docs/TRACE-MATRIX.md:177` even though its L3 children (`L3-SWEEP-009`, `L3-SWEEP-010`) are still Draft.

**Problem**

- `src/message_service/application/use_cases/sweeper.py:199` transitions the run to `ORPHANED`, commits, then dispatches handlers afterward. The inline comment ("Dispatch AFTER commit … best-effort beyond that boundary") contradicts L2-SWEEP-006's atomic-enqueue contract.
- A crash between the commit and the dispatch loses dispositions silently; a sweeper retry after the same crash window re-fires handlers — no exactly-once guarantee.
- L3-SWEEP-010 mandates a `sweeper_actions` outbox table that the assembly task consumes from. It does not exist; handlers are invoked directly in-process.

**Work**

- Add a `sweeper_actions` table in a new migration: `(action_id PK, run_id, action_name, enqueued_at, claimed_at NULL, completed_at NULL, attempts, last_error)`. Index on `(claimed_at IS NULL, enqueued_at)`.
- In one UoW, perform: the conditional `UPDATE runs SET state='ORPHANED' WHERE state IN (...) AND run_id=?` (per L3-SWEEP-009 — zero affected rows means race lost, skip silently), the audit insert, **and** one `sweeper_actions` insert per configured disposition action. Commit them together.
- Replace the in-tick handler dispatch with a separate `SweeperActionDispatcher` (in `application/use_cases/`) that the existing `SweeperLoop` ticks alongside the orphan scan. The dispatcher claims pending rows via `UPDATE … RETURNING` (or `UPDATE … WHERE claimed_at IS NULL` then `SELECT changes()`), runs the handler, and stamps `completed_at` (or bumps `attempts` + records `last_error`).
- L3-SWEEP-013's "handlers SHALL NOT raise" still applies — failures stay logged + swallowed, but now they're recorded on the action row so the dispatcher can decide retry vs. give up.

**Verification**

- Unit: atomic-update returns 0 rows when the run state isn't eligible; the UoW rolls back the action inserts on failure.
- Integration: kill the dispatcher between claim and complete, restart, confirm the action runs exactly once (covers the crash-mid-dispatch case the current code can't handle).
- Promotes `L2-SWEEP-006`, `L3-SWEEP-009`, `L3-SWEEP-010` from Draft → Implemented; correct the rollup in `docs/TRACE-MATRIX.md`.

**Trace-matrix correction (do alongside, not after)**

The current entry for L2-SWEEP-006 should be downgraded to Draft until this increment lands, so the matrix doesn't claim a guarantee the code doesn't deliver. `scripts/build-trace-matrix.py` regenerates the file; the misclassification is upstream of that — likely a marker on a sweeper test that needs removing or retargeting. Audit the markers under `tests/integration/test_sweeper_pipeline.py` and `tests/unit/.../sweeper*` for `@pytest.mark.requirement("L2-SWEEP-006")` claims that don't actually verify atomicity.

### Increment 14c — Sweeper conformance fixes  *(✅ 14c.1 `9b28e2b`, 14c.2 `3fd0673`; 14c.3 obviated by 14b's post-transition fetch)*

Three smaller deviations from the SWEEP requirements that don't fit inside 14a or 14b but should land before the sweeper category is declared done.

**14c.1 — Permit empty `disposition_actions` (L3-SWEEP-011)**

L3-SWEEP-011 (`docs/L3-REQ.md:437`) says "Empty `disposition_actions` SHALL be permitted, causing orphaned runs to receive no action beyond the state transition (equivalent to `DISCARD_SILENTLY`)". Today:

- `src/message_service/config/schema.py:184` enforces `min_length=1`.
- `tests/unit/config/test_schema.py:227-232` asserts the *opposite* — empty is rejected.

L2-SWEEP-007 is currently rolled up as Implemented in the trace matrix despite this contradiction with one of its L3 children.

**Work**: drop `min_length=1`, invert the schema test to assert that an empty list is accepted and produces a config whose orphaned-run path becomes a no-op transition. Confirm the `SweeperUseCase`'s handler-validation step doesn't trip on the empty list (it iterates configured ids; an empty iter is fine).

**14c.2 — Rename metric to match L3-SWEEP-004**

L3-SWEEP-004 (`docs/L3-REQ.md:416`) mandates `message_service_sweeper_iterations_total`. The code declares `message_service_sweeper_ticks_total` (`src/message_service/infrastructure/sweeper/loop.py:51`) and the test (`tests/unit/infrastructure/sweeper/test_loop.py:294`) asserts the wrong name.

**Work**: rename the `Counter` and the test assertion. No external dashboards exist yet, so this is a free rename now and a forced migration later. The `outcome` label values (`no_orphans_found`, `orphans_detected`, `sweeper_error`) already match the requirement.

**14c.3 — Hand the post-transition `Run` aggregate to handlers**

`application/ports/disposition_handler.py:52-56` documents the parameter as the run *after* transition to `ORPHANED`. `application/use_cases/sweeper.py:202` passes `candidate` — the pre-transition snapshot from `list_expired`. Current handlers happen not to read mutable fields, so the bug is latent.

**Work**: have `_transition_and_audit` return the post-commit `Run` (load it fresh inside the same UoW after the conditional update), and pass that to `_dispatch_handlers` instead of `candidate`.

**Sequencing note vs. 14b**: 14b moves dispatch out of the tick path entirely (handlers run from the `sweeper_actions` outbox dispatcher, not in-process after commit). When 14b lands, the dispatcher will fetch the run fresh anyway, so 14c.3 becomes redundant in that path. If 14b is going to ship soon, skip 14c.3 and let 14b handle it. If 14b is more than a sprint out, do 14c.3 now — it's a small, contained fix and the latent bug is real. 14c.1 and 14c.2 stand independent of 14b.

### Increment 14d — Stuck-claim recovery for the sweeper outbox  *(✅ done — commit `4b24818`)*

**Problem**

`SweeperActionDispatcherUseCase.dispatch_pending` claims rows in phase 1 and settles them in phase 3 with the handler invocation in between. A crash anywhere between claim and settle leaves a row in `(claimed_at IS NOT NULL, completed_at IS NULL)` — *in-flight* state. Without recovery, that row is stuck forever: the partial index `WHERE claimed_at IS NULL` skips it, so neither the next dispatcher tick nor a process restart will pick it up.

The crash semantics noted in `application/use_cases/sweeper_action_dispatcher.py` document this as a known limitation. v1 handlers (`NotifyAdminsHandler`, `DiscardSilentlyHandler`) are log-only and idempotent, so re-running them is benign — but the invariant only holds because we don't re-run them today. Future handlers (the deferred `SEND_PARTIAL_FLAGGED` and `NOTIFY_SUBSCRIBERS`) will issue real side effects and require a deliberate retry policy.

**Work**

- Add a "stale claim" threshold to `SweeperActionRepository`: a row is reclaimable if `completed_at IS NULL AND claimed_at < now - stale_threshold`. Configurable via `config.sweeper.stale_claim_threshold_seconds` (default 300).
- New repo method `reclaim_stuck(now, stale_threshold, limit)` — sets `claimed_at = now` on stuck rows and returns them as `ClaimedAction` (with `attempts` carrying the previous attempt count). Either folds into `claim_pending` (one query that picks up both pending and stale-in-flight) or runs as a separate phase. Folding keeps the contract simpler.
- Adjust `claim_pending` SQL accordingly. The existing partial index `idx_sweeper_actions_pending` no longer covers all claimable rows; either widen it (`WHERE completed_at IS NULL`) or add a sister index for the stuck-claim path.
- Bound retries: when `attempts >= max_attempts`, stop reclaiming the row and emit a `dispatcher_action_abandoned` log + audit event so operators know.
- Tests: a row whose `claimed_at` is older than the threshold gets reclaimed; the `attempts` counter is preserved across reclaims; rows under the threshold are not touched; rows past `max_attempts` stop reclaiming.

**Why deferred from 14b.3**

14b.3 was already large (port surface + adapter + use case + 20 tests). Stuck-claim recovery is a self-contained follow-up that doesn't change the L2-SWEEP-006 contract — it strengthens the at-least-once guarantee on the dispatch side. Better to land it as its own focused increment than bundle it into 14b.

**Trace impact when complete**

No new L1/L2/L3 statements yet — this is a quality refinement under the existing L2-SWEEP-006 / L3-SWEEP-013 umbrella. Consider whether to author an L3 statement pinning the stale-claim semantics so the contract is reviewable.

### Increment 14e — Wire `max_candidates_per_iteration` + L2-SWEEP-005 tests  *(✅ done — commit `fb54f98`)*

**Problem (correctness, not just traceability)**

`L3-SWEEP-008` (`docs/L3-REQ.md:428`) requires `sweeper.max_candidates_per_iteration` with default 1000 and a `LIMIT` clause on `list_expired`. Today:

- `SweeperConfig` (`src/message_service/config/schema.py:179`) has no such field.
- `_SQL_LIST_EXPIRED_BASE` (`src/message_service/infrastructure/persistence/run_repository.py:105`) has no `LIMIT` clause.

A large backlog (e.g., post-incident recovery against tens of thousands of stuck runs) is processed in one tick, holding the connection across thousands of per-run UoWs and starving everything else on the shared SQLite connection until the tick completes. This is an availability bug, not just a missing test.

The L2 parent (`L2-SWEEP-005`) is still rolled up as **Draft** in the trace matrix because both its L3 children (`L3-SWEEP-007` query shape and `L3-SWEEP-008` LIMIT) lack direct tests.

**Work**

1. Add `max_candidates_per_iteration: int = Field(default=1000, ge=1)` to `SweeperConfig`. Update `config.toml.example` + `default.toml`.
2. Plumb it through `SweeperUseCase` and into `RunRepository.list_expired(..., limit: int)`. Append `LIMIT ?` to `_SQL_LIST_EXPIRED_BASE`.
3. Tests under `tests/unit/infrastructure/persistence/test_run_repository.py`:
   - SQL shape: assert the `state IN (...)` clause holds exactly `INITIATED, AGGREGATING, READY, SENDING` — verify by mixed-state seed data and result inspection (per L3-SWEEP-007).
   - LIMIT honored: seed N+1 expired runs, call `list_expired(limit=N)`, assert the result has exactly N entries (per L3-SWEEP-008).
4. Test under `tests/unit/application/use_cases/test_sweeper.py`: a tick over a backlog larger than `max_candidates_per_iteration` SHALL drain in multiple ticks, not one.
5. **Promote the field upward** (team-flagged): `max_candidates_per_iteration` is currently invented at L3 only. Add it to the L1-CFG-003 enumerated minimum config keys, and add an L2-SWEEP statement under L1-SWEEP-001 covering "the sweeper SHALL bound per-tick work via a configurable max-candidates limit." Otherwise the L3 statement has no parent rationale at L1 or L2 and the config schema looks like an unjustified extra.

**Trace impact**: L3-SWEEP-007 + L3-SWEEP-008 Draft → Implemented; L2-SWEEP-005 rolls up to Implemented; L1-SWEEP-002 rollup becomes consistent (see 14g for the broader rollup fix); L1-CFG-003 enumeration grows by one entry.

### Increment 14f — Sweeper boundary alignment: L1↔L3↔SQL all inclusive  *(✅ done — commit `1cdfc3d`)*

**Problem**

The boundary semantics are *inconsistent across all three requirement layers and the SQL*:

- `L1-SWEEP-002` (`docs/L1-REQ.md:365`) says elapsed time must "**exceed**" the timeout — strict `>`.
- `L3-SWEEP-017` (`docs/L3-REQ.md:455`) says a run "**exactly** `run_timeout_seconds` ago" SHALL be classified as orphaned — inclusive `>=`.
- `_SQL_LIST_EXPIRED_BASE` (`src/message_service/infrastructure/persistence/run_repository.py:112`) uses `WHERE updated_at < ?` — strict `<` (matches L1, contradicts L3).

Pick one convention and propagate it through every layer. Recommendation: **inclusive (`>=` / `<=`)** since L3-SWEEP-017's prose is more specific than L1-SWEEP-002's "exceed" and aligns better with operator intent ("a run that's been silent for the full timeout has earned the orphan label, no extra grace period").

**Work**

1. Update `L1-SWEEP-002` wording: "exceeds" → "meets or exceeds" (or rephrase: "when the elapsed time since its last state transition is greater than or equal to the configured run-timeout"). Update Rationale to mention the inclusive boundary.
2. Confirm `L3-SWEEP-017` is unchanged — it already specifies inclusive.
3. Change `_SQL_LIST_EXPIRED_BASE`: `updated_at < ?` → `updated_at <= ?`.
4. Add the L3-SWEEP-017 boundary test under `tests/unit/infrastructure/persistence/test_run_repository.py`: seed a run with `updated_at` exactly equal to the cutoff; confirm `list_expired(cutoff=...)` returns it.
5. Mirror the test at the use-case level under `tests/unit/application/use_cases/test_sweeper.py`: tick a sweeper at `clock.now() == run.updated_at + run_timeout`; assert `result.orphaned_count == 1`.

**Trace impact**: L3-SWEEP-017 Draft → Implemented; L1-SWEEP-002 wording aligned; helps promote L2-SWEEP-002 (along with L3-SWEEP-003).

**Sequencing**: small SQL change + L1 wording fix + two new tests. Land as a single commit.

### Increment 14g — Trace-matrix rollup correctness  *(superseded by 25a `1f26f2f`)*

**Problem**

`docs/TRACE-MATRIX.md:164-165` shows L1-SWEEP-001 and L1-SWEEP-002 marked **Implemented** while three of their L2 children (L2-SWEEP-001, L2-SWEEP-002, L2-SWEEP-005) are **Draft**. The L1 status is computed independently of child status, so an L1 can claim Implemented despite gaps below it. That makes the matrix unreliable as a release-readiness signal — an Implemented L1 should mean every child is at least Implemented, otherwise the rollup misleads operators and reviewers.

**Work**

1. In `scripts/build-trace-matrix.py`, change the L1 rollup so an L1 is Implemented only if every L2 child is Implemented (or higher). Otherwise it's Draft. Same rule applied to the eventual Verified state once that's wired.
2. Apply the same propagation rule top-to-bottom on regen: L2 → L3 children.
3. Add a status legend update in `TRACE-MATRIX.md`'s preamble explaining the rollup rule so operators reading the matrix understand "Implemented at L1 means every child has at least one verification artifact."
4. Add a unit test under `tests/conformance/` (or under `scripts/`-adjacent tests if any exist) that builds a synthetic L1/L2/L3 graph with a Draft leaf and asserts the L1 root rolls up as Draft, not Implemented.

**Trace impact**: matrix becomes trustworthy. L1-SWEEP-001 / L1-SWEEP-002 / L1-SWEEP-003 will likely flip to Draft until 14e + 14f + a future increment cover the L2-SWEEP-001 / L2-SWEEP-002 children that don't yet have artifacts. That's the *correct* state — the matrix should make the gap visible, not hide it.

**Sequencing**: best to land 14g *after* 14e and 14f so the post-rollup state isn't a confusing flood of regressions in one PR.

### Increment 14h — Implement the unit-test I/O guard  *(✅ done; see commit log)*

**Problem**

`tests/unit/conftest.py:1-19` documents an I/O guard that "monkey-patches ``socket.socket`` and ``aiosqlite.connect`` to raise ``RuntimeError`` during unit-test collection." The fixture body (`tests/unit/conftest.py:40-48`) is just `yield`. The TODO at line 47 even admits it's deferred. The unit/integration boundary is currently aspirational, not enforced — a "unit" test that opens a SQLite database or a socket would silently pass.

**Work**

1. Implement the guard in a new `tests/fixtures/io_guard.py`. Patch `socket.socket.__init__` and `aiosqlite.connect` to raise `RuntimeError("unit tests forbid I/O — see tests/README.md")`.
2. Wire it into `tests/unit/conftest.py::_forbid_io` so the fixture actually applies the patches (and reverts on teardown).
3. The unit tests under `tests/unit/infrastructure/persistence/` legitimately use SQLite (against `:memory:`). Either:
   - Move them into `tests/integration/persistence/` where they belong (cleanest, but a bigger move).
   - Add a per-file opt-out marker (`@pytest.mark.allow_io` or similar) and have the guard skip patched modules in those files.

   Recommendation: option 1 — they ARE integration tests by definition (multiple components against real local resources, per `tests/README.md`). The current location is convenient but mislabeled.
4. Conformance test that the guard fires: a deliberately-violating unit test that tries to open `aiosqlite.connect(":memory:")` SHALL raise.

**Sequencing**: the fixture-implementation half is small; the test-relocation half is the bulk of the work. Could split into 14h.1 (implement guard, file the relocation as a follow-up) and 14h.2 (relocate). Either way doesn't block other work.

---

## Cluster 25 — Requirements specification cleanup

Born from two reviews (mine + the team's) of L1/L2/L3 source docs vs. the implemented code and the trace matrix. These are mostly docs-only edits, but several cross over into small code changes (added L1/L2 statements, added or reworded L3 statements, audit-log docstring fix). They should land **before** Cluster 15+ feature work — every new feature increment otherwise compounds the spec drift.

### Increment 25a — Source-of-truth for status + artifacts  *(✅ done — commit `1f26f2f`)*

Per team recommendation: **remove `Status` and `Verification Artifact` fields from L1/L2/L3 source docs entirely**, keep them only in `TRACE-MATRIX.md`, and make `scripts/build-trace-matrix.py` the sole authority. This is cleaner than auto-syncing two sources, which would forever risk drift between commits.

**Problem**

- All 57 L1, 157 L2, 315 L3 statements still carry `Status: Draft` and `Verification Artifact: (TBD)` in the source docs while `TRACE-MATRIX.md` is the live source. Two stores, drifting on every commit.
- Trace-matrix `Implemented` is too loose: it fires when *any* test marker exists, including for an L1 whose L2 children are all Draft. (Same root issue as Increment 14g, framed at the model level.)
- No `Partially Implemented` state, so a parent with some-but-not-all children done has nowhere accurate to land.

**Work**

1. Add a fourth status value — **`Partially Implemented`** — to the legend in `TRACE-MATRIX.md` and to the conventions section of L1-REQ.md.
2. **Rollup rule** (supersedes Increment 14g; merge them). Computed L1↔L2↔L3 by the script:
   - Every child `Implemented` (or higher) → parent `Implemented`.
   - At least one child `Implemented` and at least one `Draft` → parent `Partially Implemented`.
   - Every child `Draft` → parent `Draft`.
3. **Drop `Status:` and `Verification Artifact:` lines** from L1-REQ.md, L2-REQ.md, L3-REQ.md entirely. Add a note at the top of each: *"Status and verification artifacts are tracked in `docs/TRACE-MATRIX.md`; consult it for the live state of every requirement."*
4. `scripts/build-trace-matrix.py` becomes the single source of truth: reads `@pytest.mark.requirement` markers, computes leaf-level status, propagates upward, writes the matrix. The L1/L2/L3 docs become pure spec content (Statement, Rationale, Verification Method, Parent links).
5. Conformance test that the rollup propagation works (covers Increment 14g's test 4 — fold them).
6. The CI gate from Increment 26c will then enforce: build fails if the script's regenerated matrix differs from the committed one OR if any rollup is internally inconsistent.

**Sequencing**: largely supersedes Increment 14g; merge them. **Land 25a first** in Cluster 25 — it's the team's recommended step 1 and the foundation everything else's trace-matrix work depends on.

### Increment 25b — L1 contradictions and v1/v2 boundaries  *(✅ done — commit `eb5f537`)*

Per team recommended step 2. Four L1 fixes that resolve direct spec-vs-spec or spec-vs-implementation contradictions.

1. **L1-AGGR-001 vs L1-STAGE-003 contradiction.** AGGR-001 says report contribution is "required" per `SubmitStageReport`; STAGE-003 says a stage may submit no report and no email body content; L2-STAGE-006 confirms STAGE-003. Reword AGGR-001's "required" → "optional" (or "two content slots, both of which may be empty"), consistent with STAGE-003.
2. **L1-OBS-003 audit scope is too narrow.** It limits the audit log to "successful email deliveries… and failed delivery attempts." The implemented `AuditAction` enum has 14 categories (`BEGIN_RUN`, `SUBMIT_STAGE_REPORT`, `FINALIZE_RUN`, `RUN_STATE_TRANSITION`, `STAGE_STATE_TRANSITION`, `SWEEP_ORPHAN`, `SUBSCRIBE`, `UNSUBSCRIBE`, `CREATE_USER`, `UPDATE_USER`, `LOGIN`, `LOGIN_FAILED`, `LOGOUT`, `SEND_REPORT`). Widen L1-OBS-003 to cover the real scope; add L2 derivations under it for the run-lifecycle, stage-lifecycle, sweeper, subscription, and auth audit categories.
3. **L1-STAGE-001 IN_PROGRESS v1/v2 boundary.** L1 lists `IN_PROGRESS` as a regular state; the SQL `CHECK` constraint rejects it; the transition table forbids it; code comments mark it "reserved for v2." Mark `IN_PROGRESS` as explicitly reserved in L1-STAGE-001's Statement and Rationale so the L1 reads accurately. (L2-STAGE-002 already pins this — propagate the reserved framing up to L1.)
4. **L1-SWEEP-003 deferred actions.** L1-SWEEP-003 lists all four disposition actions as if all worked. After Increment 14a, only `DISCARD_SILENTLY` and `NOTIFY_ADMINS` are registered; configs that reference the others raise `ConfigurationError` at startup. Two sub-options:
   - **(a)** Annotate L1-SWEEP-003: "v1 implements `DISCARD_SILENTLY` and `NOTIFY_ADMINS`; the other two action ids remain valid in the type but raise `ConfigurationError` at startup until implemented (see ROADMAP)."
   - **(b)** Remove `SEND_PARTIAL_FLAGGED` and `NOTIFY_SUBSCRIBERS` from L1-SWEEP-003 entirely and move them to ROADMAP Part 2.

   Recommendation: **(a)** — keeps the type stable and makes the v1 implementation boundary explicit. Pair with the new L3 in 25c step 3 (known-but-unregistered → `ConfigurationError`).

### Increment 25c — Cross-layer drift fixes  *(✅ done — commit `c5b9854`)*

Per team recommended step 3. Three drift fixes between requirement statements and the code/L2 reality they describe.

1. **Audit-log port docstring misreferences.** `src/message_service/application/ports/audit_log.py`'s docstring cites `L2-OBS-002, L2-OBS-005` as the audit-contract requirements. Both are wrong: L2-OBS-002 is about contextvars-based logging-context propagation; L2-OBS-005 is about Prometheus metric naming. After 25b widens L1-OBS-003 and adds new L2 audit derivations, point the docstring at the correct L2 numbers. Then `grep -r "Requirement references"` for similar drift across other ports and use cases.
2. **L2-STAGE-007 stage-orphan wording vs. implementation.** L2-STAGE-007 says the sweeper "SHALL classify any stage in state PENDING at orphan-timeout evaluation as missing." The current sweeper queries `runs` only — never `stages`. The L2 promises a code path that doesn't exist. Two sub-options:
   - **(a) Reword** L2-STAGE-007 to match emergent behavior: "Any run containing PENDING stages at orphan-timeout SHALL be treated according to L1-SWEEP-002's run-level orphan rule."
   - **(b) Implement** stage-level orphan classification: add L3 statements under L2-SWEEP for "the sweeper SHALL record the list of PENDING stage_ids in the SWEEP_ORPHAN audit details," extend `SqliteRunRepository.list_expired` to surface them, extend the audit details payload accordingly.

   Recommendation: **(b)** per team framing — operator value of "which stages were missing when this orphaned" is real for incident investigation. (a) is the doc-only escape hatch if (b) feels too big.
3. **Sweeper action availability — new L3.** Pair with 25b.4: add an explicit L3 under L2-SWEEP-007 (or L2-SWEEP-008): *"Known disposition action identifiers in `DispositionAction` whose handlers are not registered SHALL raise `ConfigurationError` at startup with `details.unregistered_actions` listing the offenders."* This pins the runtime behavior Increment 14a already delivers and closes the spec gap the team flagged.

### Increment 25d — Net-new requirements: report retention, clock validity, rate limiting  *(✅ done — commit `3f45426`)*

Per team recommended step 4. Real gaps in the spine where the implementation either silently assumes or grows unbounded. **Two earlier proposed items dropped after team verification:**

- ~~Graceful shutdown~~ — *already covered by L2-DEP-006 + L3-DEP-010/-011/-012*. The L1 anchor (L1-DEP-002) could be made more explicit, but that's a minor wording polish, not a missing-requirement gap. Optional sub-task: add one sentence to L1-DEP-002 noting graceful shutdown is part of the start/stop/restart lifecycle.
- ~~Mail backoff formula~~ — *already pinned at L2-MAIL-006 and L3-MAIL-009*. No work needed.

Remaining real gaps:

1. **Rendered-report retention.** L1-OBS-003 has retention for the audit log; rendered HTML reports on disk grow forever. Add a new L1 (proposed **L1-PERS-004**): *"Rendered reports SHALL be retained on disk for at least `persistence.filesystem.report_retention_days` (default value TBD by ops); a background pruner SHALL evict reports older than the retention window."* L2/L3 derivations cover the pruner schedule, atomic delete semantics, and audit-log entry on each prune. A future implementation increment then writes the pruner.
2. **Clock validity assumption.** Every timestamp trusts the host clock; sweeper thresholds, SLA windows, audit ordering all depend on it. Add new L1 anchor (proposed **L1-DEP-004** if reusing the DEP category) or an L2 under L1-RUN-005: *"The service SHALL assume the host clock is synchronized to UTC within ±N seconds and is monotonically non-decreasing under normal operation; behavior under backward host-clock corrections greater than N seconds is unspecified."* Pair with a Rationale that points at the `Clock` port as the encapsulation boundary.
3. **Rate limiting decision.** No L1 covers per-pipeline concurrency caps or in-flight RPC limits. Two sub-options:
   - **(a)** Author L1-API-005: *"The service SHALL bound concurrent in-flight RPCs by a configurable global limit; excess SHALL be rejected with `RESOURCE_EXHAUSTED` and an error code identifying the saturation cause."*
   - **(b)** Document in ROADMAP Part 2 that v1 deliberately omits rate limiting because the trusted-ISOLAN deployment model assumes well-behaved clients; promote when a non-trusted ingress emerges.

   Recommendation: **(b)** for v1 — the trusted-ISOLAN context is a real constraint that justifies the omission and matches how L1-API-003 frames plaintext gRPC.

### Increment 25e — Smaller spec cleanup  *(✅ done — commit `d67539a`)*

Lower-impact catch-all so these don't get lost. Both team-corrected items removed.

1. **L2-AGGR-009 duplication note.** L2-AGGR-009's Rationale already says "this duplicates the statement here to anchor it under the AGGR category." Convert from a re-statement of L2-RUN-011 to an explicit "see L2-RUN-011" cross-reference so readers don't have to spot the dupe.
2. **Merge "L3-OBS (extension)"** section into L3-OBS proper. Remove the workaround note ("they are grouped separately… for clarity").
3. **L1-CFG-003 enumeration completeness** (folds the team's #C and my finding). Add to the L1-CFG-003 minimum config keys: `email_body_template_ref`, `pipelines.registered`, `mail.admin_recipients`, `templates.max_context_bytes`/`max_rendered_bytes`, `smtp.use_starttls`, `persistence.connection_pool_size`. (`max_candidates_per_iteration` lands via 14e; `service.shutdown_grace_period_seconds` is already implicit through L2-DEP-006.)

### Increment 25f — Audit-record L3 children for L2-OBS-013…017

**Context**

The 2026-04-25 requirements alignment audit (commit `0e07138`) found that five L2 statements under L1-OBS-003 (the audit-log scope L2s authored in 25b) had no L3 children: L2-OBS-013 (pipeline-initiated audits), L2-OBS-014 (state transitions), L2-OBS-015 (sweeper), L2-OBS-016 (subscriptions), L2-OBS-017 (auth and user management). The L2 statements claim audit-record format obligations (actor / resource / outcome / details for each `AuditAction` category) but have no implementation-level decomposition pinning the exact field formats.

**Work**

Author 12 new L3-OBS statements (`L3-OBS-025`…`L3-OBS-036`) covering each `AuditAction` value referenced by the five L2s:

- L3-OBS-025…027: `BEGIN_RUN`, `SUBMIT_STAGE_REPORT`, `FINALIZE_RUN` formats (L2-OBS-013).
- L3-OBS-028, 029: `RUN_STATE_TRANSITION`, `STAGE_STATE_TRANSITION` formats (L2-OBS-014). `STAGE_STATE_TRANSITION` is forward-spec — the enum value exists but no use case currently emits it.
- L3-OBS-030: `SWEEP_ORPHAN` format (L2-OBS-015).
- L3-OBS-031, 032: `SUBSCRIBE`, `UNSUBSCRIBE` formats (L2-OBS-016) — implementation deferred to Increment 18.
- L3-OBS-033: `LOGIN`, `LOGOUT` format (L2-OBS-017).
- L3-OBS-034: `LOGIN_FAILED` format with operator-only `reason` (L2-OBS-017).
- L3-OBS-035: `CREATE_USER`, `UPDATE_USER` format (L2-OBS-017) — implementation deferred to Increment 20.
- L3-OBS-036: cross-cutting password / token redaction obligation (L2-OBS-017).

Markers added to existing tests for the implemented cases. Forward-spec L3s (029, 031, 032, 035) carry no markers and will appear as Draft in the trace matrix; that's the correct state.

**Trace impact**

L2-OBS-013, L2-OBS-015 and L2-OBS-017 now have direct L3 children covering their core obligations. L2-OBS-014 and L2-OBS-016 are partially covered (the not-yet-implemented record types remain Draft). L3 total: 335 → 347.

### Increment 25g — Email subject format spec + impl  *(✅ done — commit `2406dd1`)*

**Problem**

The email Subject header is constructed by a hardcoded f-string at `src/message_service/application/use_cases/assemble_and_deliver.py:404` (`f"Run {run_id} — {run.pipeline_type}"`) with no L1/L2/L3 SHALL anchoring it. Every other piece of email content has spec coverage — body content via `templates.email_body_template_ref` + L2-AGGR-003, attachment naming via L2-AGGR-006 + L3-AGGR-010/011, MIME headers via L3-AGGR-020 — making subject the lone unspecced surface. Surfaced during the 19c L3-PERS-022 wording review (commit `9587852`). Independent of the dashboard stream and small enough for a single increment.

**Work**

Author one new L2 plus three L3 children, all parented at L1-MAIL-001:

- **L2-MAIL-014** — pin the subject format to literal `[{pipeline_type}] run {run_id}`. `pipeline_type` is sanitized using the same regex as L3-AGGR-010 (`[^a-zA-Z0-9._-]` replaced with `_`) so header-injection-style payloads are neutralized at construction time, before the `OutboundEmail` boundary's CR/LF assertion (defense in depth). The subject is NOT operator-configurable in v1; per-pipeline subject templates are deferred (see `R-MAIL-001`).
- **L3-MAIL-027** · Verification: T — a test SHALL render the subject for a known run and assert it equals the literal format string above.
- **L3-MAIL-028** · Verification: T — subject construction SHALL apply the L3-AGGR-010 sanitization helper to `pipeline_type` before insertion.
- **L3-MAIL-029** · Verification: T — a test SHALL exercise a `pipeline_type` containing CR or LF and assert the produced subject contains neither character (sanitization replaced them with `_` rather than `OutboundEmail` raising).

Implementation:

- Edit subject construction in `AssembleAndDeliverUseCase.execute` to call `_sanitize_filename_component(run.pipeline_type)` and emit `f"[{pipeline_safe}] run {run_id}"`.
- Add three new `@pytest.mark.requirement`-tagged tests in `tests/unit/application/use_cases/test_assemble_and_deliver.py`.
- Regenerate `docs/TRACE-MATRIX.md`.

**Verification**

- L2-MAIL-014 + L3-MAIL-027/028/029 promote from Draft → Implemented.
- All existing tests still pass (the OutboundEmail header-injection check is unchanged).
- Trace matrix --check clean.

---

## Cluster 26 — CI/CD requirements + workflows

The team flagged "Full requirements for CICD" as missing, which is true — there's no L1-CICD category, no L2/L3 derivations, and `.github/workflows/` is empty. This cluster authors the requirements then implements them.

### Increment 26a — Author L1-CICD requirements category  *(✅ done — commit `220c1d5`)*

Net-new category in `docs/L1-REQ.md`. Proposed L1 statements (final wording subject to spec review):

- **L1-CICD-001 — Cross-platform pytest matrix.** "The service's full pytest suite SHALL pass on both `ubuntu-latest` and `windows-latest` GitHub Actions runners on every push to `main` and on every pull request, with no `ResourceWarning` for unclosed sockets, file handles, or event loops."
- **L1-CICD-002 — Pre-commit gate.** "All pre-commit hooks (ruff format, ruff check, mypy strict, the standard whitespace/yaml/toml hygiene set) SHALL pass on CI on every push and pull request, with the same pinned hook versions as local development."
- **L1-CICD-003 — Coverage gate.** "Branch coverage on `src/message_service/` SHALL meet the threshold pinned in `pyproject.toml` (`--cov-fail-under`); CI SHALL fail if it drops."
- **L1-CICD-004 — Traceability gate.** "CI SHALL fail if any L1 row is `Implemented` while any of its L2/L3 descendants are `Draft` (per the propagation rule from 25a). The build SHALL fail with a list of inconsistent rows."
- **L1-CICD-005 — Test-temp isolation.** "Pytest temporary files SHALL be rooted in workspace-local `.pytest_tmp/` (already enforced via `--basetemp` in `pyproject.toml`); the directory SHALL be `.gitignore`d so test artifacts never enter source control."
- **L1-CICD-006 — Reproducibility.** "The Poetry lockfile (`poetry.lock`) SHALL be committed and SHALL produce identical dependency resolutions across runs; CI SHALL fail if `poetry lock --check` reports drift."
- **L1-CICD-007 — Build provenance.** "The CI workflow SHALL record the commit SHA, the Python version, the OS, and the timestamp of every test run as part of the workflow output, available for download as artifacts."

L2 derivations: workflow filename conventions, matrix entry shape, `ResourceWarning` filter configuration, allowed CI duration ceiling, scheduled re-runs on `main`, etc.

L3 derivations: specific YAML, the exact pytest invocation per OS (Windows path quoting!), the coverage XML upload path, etc.

### Increment 26b — CI/CD workflow implementation  *(✅ done — commit `c22ebc9`)*

Cash in the L1-CICD requirements as `.github/workflows/ci.yaml`. Matrix (`ubuntu-latest`, `windows-latest`) × (Python `3.12`, `3.13`). Per-job: `poetry install`, `poetry run pre-commit run --all-files`, `poetry run pytest`, `poetry run python scripts/build-trace-matrix.py --check` (new flag — exit non-zero if regenerated matrix differs from committed). Upload `coverage.xml` and `.coverage_html/` as artifacts. Schedule a nightly run on `main` to catch flakes that pass per-PR.

### Increment 26c — Traceability rollup CI gate  *(✅ done — commit `f99f795`)*

Implements **L1-CICD-004** specifically. `scripts/build-trace-matrix.py` gains a `--check` mode that re-derives the matrix in memory, compares against the committed `docs/TRACE-MATRIX.md`, and exits non-zero if they differ OR if any row violates the parent-status-bounded-by-children rule from 25a. Wired into the CI workflow from 26b.

### Increment 26d — Cross-platform pytest hygiene audit  *(✅ done — commit `aa6550c`)*

Implements **L1-CICD-001 / L1-CICD-005** specifically. Audit `pyproject.toml`'s `filterwarnings` (currently has `"error"` plus a Google-deprecation ignore) for completeness. Verify `.gitignore` includes `.pytest_tmp/` (likely already does — confirm). Run the suite on Windows with `-W error::ResourceWarning -W error::DeprecationWarning` and fix anything that surfaces. The recent Windows-event-loop work (`tests/conftest.py::_NoImplicitEventLoopPolicy`) suggests this surface is already partly clean, but a deliberate pass is worthwhile.

### Increment 26e — L1-CICD trace-matrix closure  *(✅ done — commit `af59b43`)*

**Problem (traceability, not feature work)**

Cluster 26's increments (26a/b/c/d) authored the L1-CICD requirements category, shipped the workflow YAML, implemented the trace-matrix `--check` mode, and ran the cross-platform pytest hygiene audit. The *implementation* is complete. But the *trace matrix* still shows L1-CICD-002 / L1-CICD-003 / L1-CICD-004 / L1-CICD-006 / L1-CICD-007 as **Draft**, and L1-CICD-001 / L1-CICD-005 as **Partially Implemented** — because most L3-CICD children are Verification-by-Inspection statements that lack `@pytest.mark.requirement(...)` tagged conformance tests, and the trace-matrix-check tests in `tests/conformance/test_trace_matrix_check_mode.py` and `test_trace_matrix_rollup.py` deliberately omitted markers (the file's docstring says markers were deferred "once L1-CICD-004's verification artifacts are wired up").

This is the same shape as the L1-DEP-001 gap closed in Increment 23's audit follow-up (`7bae9da`): code done, traceability plumbing missing.

**Work**

1. Add `@pytest.mark.requirement` markers to existing tests in `tests/conformance/test_trace_matrix_check_mode.py` (L3-CICD-010 / L3-CICD-011) and `tests/conformance/test_trace_matrix_rollup.py` (L3-CICD-012). Pure marker plumbing — closes L1-CICD-004.
2. Add inspection-style conformance tests for the workflow-YAML / pre-commit / pyproject directives that are Verification: I — L3-CICD-001 (workflow filename), L3-CICD-002 (matrix shape and `fail-fast: false`), L3-CICD-003 (full-suite invocation), L3-CICD-005 (push / pull_request / schedule triggers), L3-CICD-006 (pre-commit invocation with `--show-diff-on-failure`), L3-CICD-007 (pinned `rev:` in `.pre-commit-config.yaml`), L3-CICD-009 (artifact upload shape with `coverage-${{matrix.os}}-...` name), L3-CICD-013 (`--basetemp=.pytest_tmp` literal in pyproject `addopts`), L3-CICD-016 (provenance log shape: `provenance: sha=... os=... python=... trigger=... ts=...`), L3-CICD-017 (`retention-days: 30`). Most slot into `tests/conformance/test_deploy_artifacts.py` extending the existing `ci_workflow_text` fixture; `.pre-commit-config.yaml` gets its own fixture; pyproject already has one.
3. Add tests for the Verification: T statements that aren't yet covered: L3-CICD-008 (coverage gate enforced via pyproject `[tool.pytest.ini_options] addopts` containing `--cov-fail-under=<N>`), L3-CICD-015 (CI workflow runs `poetry check --lock`).

**Trace impact**: L1-CICD-001 / L1-CICD-002 / L1-CICD-003 / L1-CICD-004 / L1-CICD-005 / L1-CICD-006 / L1-CICD-007 all promoted Partially Implemented / Draft → Implemented. Cluster 26 fully closed.

**Sequencing**: pure marker plumbing + a focused set of inspection tests. One commit. Land before Increment 24 so the v1 release tag has a fully clean trace matrix.

---

### Increment 15 — Prometheus metrics adapter  *(✅ done — commit `fe5c3a4`)*

Closes **L1-OBS-002, L1-OBS-003** (currently Draft).

- Add `infrastructure/observability/metrics.py` with the counters/histograms named in L2-OBS-004…009 (run-state transitions, stage-submit latency, email size, sweeper rounds).
- Inject through a thin port so domain/application stay framework-free.
- Lifts `error_mapping.py` and `logging_setup.py` out of the 0%-covered gap noted in this file's Part 2.

### Increment 16 — Local-account auth adapter  *(✅ done — commit `7ede66c`)*

Closes **L1-AUTH-001, L1-AUTH-002** (Draft). `rest/auth/` is currently empty.

- `argon2-cffi` `PasswordHasher` adapter (`infrastructure/auth/argon2_hasher.py`),
  service-scoped singleton wired by `bootstrap.build_service`.
- `Password`, `User`, `Session` aggregates; `UserRepository`, `SessionRepository`,
  `PasswordHasher` ports.
- SQLite adapters + migration `003_auth_schema.sql` (adds `users.password_hash` +
  `users.is_admin`, creates `sessions`).
- `LoginUseCase` (mints `secrets.token_urlsafe(32)`, persists SHA-256, audits
  `LOGIN`/`LOGIN_FAILED` with operator-only `reason` per L3-AUTH-013) and
  `LogoutUseCase` (idempotent delete by token-hash, audits `LOGOUT`).
- Session-cookie + CSRF middleware deferred to Increment 17 with the FastAPI
  chassis. Admin user creation deferred to Increment 20 (admin surfaces).

### Increment 17 — FastAPI app factory + bootstrap wiring  *(✅ done; see commit log)*

`rest/routes/` is empty; `__main__.py` only spins up the gRPC server.

- `interfaces/rest/app.py` builds the FastAPI instance from `Service`.
- `__main__.py` runs uvicorn alongside `grpc.aio` under one shutdown event.
- No domain routes yet — chassis + login flow only.

### Increment 18 — Subscription management routes  *(✅ done; see commit log)*

Closes **L1-DASH-001, L1-SUB-002** (Draft).

- CRUD over `SqliteSubscriptionRepository` for the existing GLOBAL/PIPELINE/TAG granularity.
- Jinja screens under `rest/html/templates/`.

### Increment 19 — Past-runs / resend / report viewer

The original ROADMAP entry combined paginated runs list, resend, and rendered-report viewer into one increment. Survey before kickoff revealed the filesystem report store is **completely unimplemented** (no port, no adapter, no write path in `AssembleAndDeliverUseCase`); spec only goes as far as L2-PERS-005/006 on atomic-rename + directory creation. Bundling the store implementation alongside two REST features inflates 19 into ~1000 LOC of mixed concerns.

Split into three sub-increments. New L2/L3 statements authored upfront (this commit): L2-DASH-012/013/014, L3-DASH-022..030 (with L3-DASH-013 reworded), L3-PERS-024..026.

#### Increment 19a — Past-runs paginated list + run-detail metadata view  *(✅ done; see commit log)*

**Closes**: the "list / view metadata" portion of `L1-DASH-003`.

**Work**

- Add `RunRepository.list_paginated(*, limit, offset, states)` to the run-repo port and SQLite adapter (per `L3-DASH-024`'s ORDER BY + LIMIT/OFFSET shape).
- Add `ListPastRunsUseCase` and `GetRunDetailUseCase` (thin wrappers — most logic lives at the route + repo level).
- Routes under `interfaces/rest/routes/runs.py`:
  - `GET /runs` — paginated list with `limit`/`offset`/`states` query params per `L3-DASH-022/023/024`.
  - `GET /runs/{run_id}` — run detail per `L3-DASH-025/026`.
- Wire into `create_app` via `include_router` (mirrors the 18 subscription router pattern).
- Tests: integration tests for pagination semantics, default-states filter, ordering, run-not-found 404, malformed UUID 422.

**Verification**

- L3-DASH-022..026 promote from Draft → Implemented.
- L1-DASH-003 partial roll-up; remaining clauses ("view rendered reports", "trigger manual resends") covered by 19c and 19b respectively.

#### Increment 19b — Manual resend (re-renders from saved Stage context)  *(✅ done; see commit log)*

**Closes**: the "trigger manual resends to the current active subscriber list" portion of `L1-DASH-003`.

**Work**

- Add `AuditAction.RESEND_REPORT` to the `domain/aggregates/audit_event.py` enum. The audit-format L3 (L3-DASH-013) was reworded from `outcome=RESEND` to `action=RESEND_REPORT, outcome=SUCCESS/FAILURE` — the new enum value is the implementation hook.
- Add `ResendRunUseCase` that:
  - Looks up the run; reject with 409 if state ∉ `{SENT, FAILED}` per `L3-DASH-028`.
  - Re-resolves recipients via `SubscriptionRepository.list_recipients_for_run` (per `L3-DASH-012`).
  - Re-renders by replaying `AssembleAndDeliverUseCase` against the persisted `Stage.report_context_json` (per `L3-DASH-027`) — explicitly NOT reading the filesystem report store snapshot, so resend works even before 19c lands.
  - Re-delivers via `Mailer.send`; emits `AuditAction.RESEND_REPORT` audit per `L3-DASH-013`.
- Route: `POST /runs/{run_id}/resend` (CSRF-guarded by the existing middleware; the run-state-precondition check returns 409).
- Tests: integration tests covering happy path, 409-on-non-terminal, 409-on-orphaned, recipient-resolution-at-resend-time (the `L3-DASH-012` "new subscription added between send and resend gets the resent email" case), audit format matches `L3-DASH-013`.

**Verification**

- L3-DASH-012/013/027/028 promote from Draft → Implemented.
- L2-DASH-008 promotes from Draft → Implemented.

#### Increment 19c — Filesystem report store + report viewer  *(✅ done; see commit log)*

**Closes**: the "view past rendered reports" portion of `L1-DASH-003` and gives `L1-PERS-002` a concrete repository under it.

**Work**

- New port `application/ports/report_store.py` per `L3-PERS-024`.
- New adapter `infrastructure/persistence/filesystem/report_store.py` per `L3-PERS-025/026`. Atomic-write via `<final>.tmp` + `Path.replace()` per the existing `L2-PERS-005`.
- Wire `AssembleAndDeliverUseCase` to call `ReportStore.save_email_body(...)` after successful delivery and `ReportStore.save_fragment(...)` for each rendered fragment during render.
- Bootstrap: construct the report-store directory at startup per `L2-PERS-006`; expose the report-store on the `Service` dataclass.
- Routes: `GET /runs/{run_id}/report` and `GET /runs/{run_id}/stages/{stage_id}/fragment` per `L3-DASH-029/030`.
- Tests: integration tests for atomic-write semantics, directory layout, the 404-when-pre-existing case, and the route-level happy paths.

**Verification**

- L3-PERS-024/025/026 promote from Draft → Implemented.
- L2-DASH-014 + L3-DASH-029/030 promote from Draft → Implemented.
- L1-DASH-003 partially closed: clauses 1 ("view past rendered reports") and 2 ("trigger manual resends") are now implemented + tested. The third clause ("inspect the template registry contents in a read-only view") remains for Increment 20a.

### Increment 20 — Admin surfaces

The original ROADMAP entry incorrectly stated "Closes **L1-DASH-004** (Draft)" — but `L1-DASH-004` is about embedded Prometheus metrics visualizations, not admin features. The bullets that followed ("user management, audit-log viewer, template inspection") describe scope that lives under `L1-DASH-003` (clause 3) plus net-new admin obligations that have no existing L1 surface. Survey before kickoff (see session memory `2026-04-26`) confirmed the spec gap and the mislabeling.

Split into three admin sub-increments plus a separately-scoped embedded-metrics increment:

#### Increment 20a — Admin gate + template registry inspection  *(✅ done; see commit log)*

**Closes**: the third clause of `L1-DASH-003` ("inspect the template registry contents in a read-only view") plus `L2-DASH-007` (admin gate) and `L2-DASH-009` (template inspection read-only). After 20a lands, `L1-DASH-003` is fully closed.

**Spec readiness**: mostly ready. `L2-DASH-007` and `L2-DASH-009` are authored. L3 hooks: `L3-DASH-006`/`L3-DASH-007` cover the `require_admin` dependency; `L3-DASH-019` covers the HTTP-method allow-list for `/templates/*`. Likely needs 1-2 small L3 additions for the inspection-route response shape (list-templates and view-template-detail projections).

**Work**

- Author 1-2 small L3 children for `L2-DASH-009` covering the response shape of the list and detail routes (template id, version, source path, last-modified — but NOT the template body itself, which would re-introduce a content-injection surface).
- Implement `require_admin` FastAPI dependency in `interfaces/rest/app.py` (mirrors `require_session`; reads `is_admin` per request per `L3-DASH-020`).
- Add an `is_admin` flag access path on `Service` / on the user repo if not already wired. (User model already has the column per Increment 16.)
- New router under `interfaces/rest/routes/templates.py`:
  - `GET /templates` — list all registered templates with their (name, version, kind, source_path) projection.
  - `GET /templates/{name}/{version}` — detail view (same projection — body NOT included).
  - `POST/PATCH/DELETE /templates/*` returns 405 per `L3-DASH-019`.
- Wire into `create_app` via `include_router`.
- Tests: integration tests for admin-gate enforcement, list/detail projections, 405s on write methods, 401 unauthenticated, 403 non-admin.

**Verification**

- `L2-DASH-007` + `L2-DASH-009` promote from Draft → Implemented.
- `L1-DASH-003` fully closes (all three clauses).
- New 20a section in the Status snapshot.

#### Increment 20b — Admin user management (CREATE_USER / UPDATE_USER)  *(✅ done; see commit log)*

**Closes**: net-new L2/L3 anchored at `L1-AUTH-001` (or possibly a new L1) covering admin-driven user CRUD. Closes the `(Implementation deferred to Increment 20)` tag on `L3-OBS-035`.

**Spec readiness**: gap. Audit-record format (`L3-OBS-035`) is authored, but no L1/L2 obligations exist for the dashboard routes that perform user management. Needs spec authoring before code.

**Work**

- Author L1 (or extend an existing one) covering "the dashboard SHALL allow administrators to create users (with `is_admin` boolean), update existing users (display_name, is_admin, disabled), and reset passwords (admin-driven password set, distinct from the user's own change-password flow)."
- Author L2 derivations: route obligations, request/response shapes, password-handling rules (new password is hashed via the same `Argon2PasswordHasher` singleton; never stored or echoed in plaintext), validation rules (email uniqueness, display_name length).
- Author L3 implementation hooks: route paths, status codes, audit-record details (must align with the existing `L3-OBS-035` format: `actor=user:<admin_id>`, `resource=user:<target_user_id>`, `outcome=SUCCESS`, `details={target_user_id, mutated_fields}`).
- Implement `CreateUserUseCase` + `UpdateUserUseCase` (or extend an existing `RegisterUser` use case) under `application/use_cases/`.
- New router under `interfaces/rest/routes/admin_users.py` (or merge into an `admin.py` aggregate router).
- Remove the `(Implementation deferred to Increment 20)` tag from `L3-OBS-035`.
- Tests covering happy path, validation failures, audit-format assertions, admin-gate enforcement.

**Verification**

- New L2/L3 promote from Draft → Implemented.
- `L3-OBS-035` no longer carries the "deferred" tag.
- New 20b section in the Status snapshot.

#### Increment 20c — Audit-log viewer  *(✅ done; see commit log)*

**Closes**: net-new L1/L2/L3 covering an admin audit-log read API.

**Spec readiness**: full gap. No L1/L2/L3 coverage exists for an audit-log viewer route. The audit log itself is mature (governed by `L1-OBS-003`), but the dashboard surface for reading it is unspecified.

**Work**

- Author L1 (likely under existing `L1-OBS-003` or a new L1-DASH or L1-OBS sibling) covering "the dashboard SHALL allow administrators to read the audit log via a paginated, filtered, read-only API."
- Author L2 derivations: route obligations, filter parameters (`action`, `actor`, `resource`, `from`, `to`), pagination semantics (offset+limit, capped at a reasonable page size, default ordering most-recent-first).
- Author L3 implementation hooks: route paths, query-parameter validation, response shape, password/token redaction guarantees (must honor the existing `L3-OBS-036` redaction rule).
- Implement use case + router under `interfaces/rest/routes/audit.py`.
- Tests covering happy path, filter combinations, pagination, redaction, admin-gate enforcement.

**Verification**

- New L1/L2/L3 promote from Draft → Implemented.
- New 20c section in the Status snapshot.

#### Increment 20d — Embedded Prometheus metrics dashboard  *(partially done; remainder deferred to ROADMAP `R-DASH-004`)*

**Closes (partial)**: The `/metrics` scrape-endpoint half of `L1-DASH-004` (`L2-OBS-004` + `L3-OBS-007` — spec already authored). Implementation: a single FastAPI route returning `prometheus_client.generate_latest()` with content type `text/plain; version=0.0.4; charset=utf-8`. Unauthenticated for the v1 ISOLAN deployment (Prometheus scrapers run on the trusted network).

**Defers**: The embedded-visualization half — `L2-DASH-010` (server-side fetch from `/metrics`), `L2-DASH-011` (Chart.js bundling), `L3-DASH-016` (same-origin fetch), `L3-DASH-017` (Chart.js pinned at `static/js/chart.min.js`). These all remain Draft and are captured in ROADMAP as **`R-DASH-004` — Embedded Chart.js metrics dashboard** under "Deferred features" with the full work plan and the test-harness blocker that justifies the deferral.

**What landed in v1 (Increment 20d's narrow scope)**

- New `GET /metrics` route in `interfaces/rest/app.py`, between the `/healthz` and `/login` blocks. Returns `prometheus_client.generate_latest()` with `prometheus_client.CONTENT_TYPE_LATEST`. Three integration tests asserting unauthenticated reachability, the exact content-type string, and that the scrape surfaces metrics the recorder has actually emitted.

**Why partial-and-defer rather than full implementation**

The embedded-visualization piece needs frontend code (Chart.js calls, Prometheus text-format parsing in JavaScript) that doesn't fit the test patterns the rest of v1 uses. Doing it well needs a browser-based test harness (Playwright or similar) the project doesn't have today; doing it poorly risks shipping a flaky page. The external-scraper half — which is the operationally important half of `L1-DASH-004` for any deployment running Grafana / a Prometheus stack — is in v1; the embedded view becomes useful when there's no external Prometheus to consume the metrics, which is a different deployment shape than the ISOLAN model v1 targets.

The two halves of `L1-DASH-004` rationalize together (an operator either has Grafana, in which case they don't need the embedded view, or they don't, in which case they do); shipping the half that costs little and serves the bigger deployment shape, while deferring the half with a real test-harness gap, is the right v1 trade-off.

### Increment 21 — E2E happy-path + orphan-path harness  *(✅ done; see commit log)*

`tests/e2e/{happy_path,admin,orphan_path,resend}/` currently contain only `__init__.py`.

- Stand up the `running_service` fixture sketched in `tests/README.md` (real `grpc.aio` + httpx + tmp SQLite + `aiosmtpd`).
- BeginRun → submissions → FinalizeRun → email path.
- Sweeper-fires-and-disposes path.
- Moves a wave of L2 rows from "Implemented" to "Verified".

### Increment 22 — Error-mapping + servicer tests, exception-detail coverage  *(✅ done; see commit log)*

Closes **L1-ERR-001..004** (all Draft).

- Unit tests for `interfaces/grpc/error_mapping.py` (translation table, trailing-metadata population).
- `details=` assertions across the use-case raise sites.

### Increment 23 — Deployment polish  *(✅ done — commits `2e5cdbb` + `7bae9da`)*

Closed **all three L1-DEP-***: L1-DEP-001 (cross-platform portability), L1-DEP-002 (systemd + NSSM), L1-DEP-003 (Poetry packaging). Initial commit landed the seven planned steps; the audit follow-up commit closed the four remaining L3 gaps that L1-DEP-001 still needed.

What landed:

- **Systemd-unit conformance + env-file passthrough** — added `EnvironmentFile=-/etc/message-service/message-service.env` to `deploy/linux/message-service.service` so operators can drop credentials / per-host overrides into a sibling env-file without editing the unit. New `tests/conformance/test_deploy_artifacts.py` asserts every directive `L3-DEP-006` and `L3-DEP-007` requires (Type=exec, Restart=on-failure, RestartSec=5s, TimeoutStopSec=30s, KillSignal=SIGTERM, NoNewPrivileges, ProtectSystem, ProtectHome, PrivateTmp, ReadWritePaths) is present.
- **NSSM-README conformance** — same conformance file asserts every nssm command `L3-DEP-008` requires (`install MessageService`, `set DisplayName`, `set Description`, `AppStdout` / `AppStderr` / `AppRotateFiles` / `AppRotateBytes`, `AppStopMethodConsole 30000`, `ObjectName`) is documented.
- **Windows install demonstration (L3-DEP-009)** — `docs/procedures/windows-install-demonstration.md`: 8-step operator walkthrough (Unpack → Install deps → Provision config → Create service account → Register service → Start service → Verify graceful shutdown → Verify restart cleans up) with checkpoints + signed Attestation form. Conformance test asserts every required section heading is present.
- **Graceful-shutdown tests (L3-DEP-010 / L3-DEP-012)** — added unit tests for `_install_signal_handlers` and an integration test that patches `grpc.aio._server.Server.stop` to verify the configured `shutdown_grace_period_seconds` is propagated to the gRPC server-stop call.
- **CLI smoke + line-ending tests (L3-DEP-016 / L3-DEP-017)** — `tests/integration/test_cli_smoke.py`: `message-service --help` exits 0 with "config" in output, and an LF-encoded config and CRLF-encoded config produce equivalent loaded objects.
- **Pyproject / poetry.lock conformance (L3-DEP-013 / L3-DEP-014 / L3-DEP-015)** — same conformance file asserts the python constraint is `>=3.12,<4.0`, `poetry.lock` is committed and non-empty, and the `[tool.poetry.scripts]` `message-service` entry resolves correctly.
- **Architecture-boundary + pathlib conformance** — replaced the TODO stubs in `tests/conformance/test_architecture_boundaries.py` and `tests/conformance/test_pathlib_enforcement.py` with real AST/config inspection (L3-PERS-016, L3-DEP-003, L3-DEP-005, L3-DEP-018).
- **Real bug fix** — `pyproject.toml`'s `[tool.poetry.scripts]` entry pointed at a non-existent module (`message_service.interfaces.cli.main:main`). Created `src/message_service/interfaces/cli/main.py` re-exporting `__main__.main` so `poetry run message-service` resolves.

Audit follow-up commit (`7bae9da`) — closed L1-DEP-001's remaining children:

- **L3-DEP-001** (CI matrix inspection) — `test_deploy_artifacts.py` reads `.github/workflows/ci.yaml` and asserts both `ubuntu-latest` and `windows-latest` runners are present and that `poetry run pytest` is invoked (full-suite execution).
- **L3-DEP-002** (skipif convention) — AST-walks every `tests/**/*.py`, finds every `@pytest.mark.skipif(...)` decorator, and asserts each carries a non-empty `reason=` keyword argument.
- **L3-DEP-004** (path-separator literal scanner) — `test_pathlib_enforcement.py` AST-walks `src/`, examines module-level + class-level str-typed constants, and flags any value containing `/` or `\` outside URL contexts. Codebase is currently clean (zero violations).
- **L3-DEP-011** (long-running tasks observe shutdown event; new RPCs return UNAVAILABLE) — two new integration tests in `test_servicer.py`. The first stands up a real grpc.aio server, makes a baseline RPC, fires `server.stop(grace=2.0)` as a background task, and asserts the next RPC raises `AioRpcError(code=UNAVAILABLE)`. The second starts the sweeper loop, signals `stop()`, and asserts the scheduler's active task count drops to zero.

Out of scope (kept narrow per the original "deployment polish" framing):

- A minimal `.github/workflows/ci.yaml` was already substantial after Cluster 26 — no further work needed in this increment.

### Increment 27 — UoW serialization correctness + spec alignment  *(✅ done — commits `410aa90`, `ee69b87`, `d48b4c8`, `0f13927`, `388bdf2`, `88279e5`, `f69e95a`, `2e014b3`, `950d754`)*

**Problem (correctness, not just traceability)**

`SqliteUnitOfWorkFactory` (`src/message_service/infrastructure/persistence/unit_of_work.py:247`) shares a single `aiosqlite.Connection` across all UoW instances it produces. The factory's module docstring claims serialization happens "via the `busy_timeout` PRAGMA," which is wrong — `busy_timeout` is for cross-process file contention, not for serializing in-process transactions on the same connection. There is no mutex. When two coroutines concurrently enter UoW context, both call `await conn.execute("BEGIN")`. The second BEGIN executes against a connection already in a transaction state and fails with `sqlite3.OperationalError: cannot start a transaction within a transaction` (raised at `unit_of_work.py:133` and wrapped as `PersistenceError`).

The bug is masked in most tests (no concurrent UoW openings) but surfaces intermittently in `tests/e2e/orphan_path/test_sweeper_disposes_orphan.py` because the sweeper-loop tick runs `_tick_once` and `_dispatch_drain` on the same poll iteration — both open UoWs against the same shared connection. The test currently mitigates by starting the sweeper loop AFTER the BeginRun's UoW completes, but this only prevents one of the race orderings; the loop's own internal phases still race against each other every poll interval.

This is an availability bug under load: a misbehaving pipeline driving concurrent gRPC traffic + the sweeper running its normal cadence will produce sporadic `PersistenceError` 500s and orphan-path delays in production. The orphan-path e2e flake is the visible symptom; the underlying production risk is the actual reason to fix it.

**Rescope rationale (spec/implementation drift discovered at kickoff)**

Survey of the cited code path turned up a deeper inconsistency than the original problem statement captured. The active spec describes a connection-pool architecture:

- **L2-PERS-004** — "The service SHALL maintain a connection pool sized to accommodate concurrent gRPC servicer calls and FastAPI request handlers, with pool size controlled by configuration key `persistence.connection_pool_size`."
- **L3-PERS-006** — pool backed by `asyncio.Queue`, `connection_acquire_timeout_seconds` default 5s.
- **L3-PERS-007** — default `connection_pool_size` 16; exhaustion increments a Prometheus counter.
- **L3-PERS-021** — connection acquisition logs DEBUG with current pool depth.
- **L1-REQ.md:578** lists `connection-pool size` as a required config knob.
- **`docs/diagrams/c4-component-persistence.puml:21`** describes the Connection Manager as "Pool sized by `persistence.connection_pool_size`; WAL pragmas at startup."
- **`src/message_service/config/schema.py:110`** defines `connection_pool_size: int = Field(default=16, ge=1, le=256)` — but no code path reads this field. The shipped configs (`config/default.toml`, `config/dev-config.toml`, `config/config.toml.example`) all set the key.

v1 does NOT implement a pool — the actual mechanism is single shared `aiosqlite.Connection`. With a real pool, the original bug doesn't exist (BEGINs go to different connections); the bug exists *because* the implementation is single-connection without the synchronization that design requires.

**Architectural decision for v1**: keep single-connection + asyncio mutex. SQLite has at most one writer per database file regardless of pool size; pool would not deliver write parallelism for this codebase's write-heavy UoWs. Pool's main benefit (read parallelism in WAL mode) is real but does not justify the complexity for a single-node ETL reporting service with low concurrent dashboard usage. Pool architecture preserved as a future evolution path, with explicit re-evaluation triggers, in `docs/archive/connection-pool-architecture.md`.

The increment is broken into nine sub-steps so each stays reviewable in isolation. Spec cleanup commits land before code so the requirement set is internally consistent at every commit boundary.

**Sub-steps**

**27a — Pool architecture archive document** *(spec)*. Author `docs/archive/connection-pool-architecture.md`. Capture verbatim: the existing L2-PERS-004 statement; L3-PERS-006, L3-PERS-007, L3-PERS-021 statements; L1-REQ.md:578 config-knob bullet excerpt; the C4 PlantUML fragment for the Connection Manager component. Add forward-looking evolution-trigger rationale (when dashboard P95 latency under sustained sweeper load justifies revisiting). This is the authoritative record of the pool design — leaving the active spec but not lost.

**27b — Replace pool requirements with mutex requirements** *(spec)*. Reword L2-PERS-004 to describe single shared `aiosqlite.Connection` serialized via `asyncio.Lock` around BEGIN/COMMIT. Replace L3-PERS-006, L3-PERS-007, L3-PERS-021 with mutex-flavored L3 children: lock placement (acquire before BEGIN, release in `try/finally` on every exit path); exactly-once release on commit-then-rollback failure; concurrency-test verification. Drop `connection_pool_size` from the L1-REQ.md:578 config-knob bullet. Trace-matrix regen.

**27c — Update C4 PlantUML diagram** *(spec)*. Modify `docs/diagrams/c4-component-persistence.puml:21` to describe single `aiosqlite.Connection` + `asyncio.Lock` around BEGIN/COMMIT. Other diagrams reviewed; the SVG architecture overview does not mention pool — no change needed.

**27d — Config schema cleanup** *(code)*. Remove unused `connection_pool_size: int` field from `PersistenceConfig` in `src/message_service/config/schema.py`. Remove the corresponding key from `config/default.toml`, `config/dev-config.toml`, `config/config.toml.example`. Update fixture docstrings in `tests/fixtures/persistence.py` and `tests/integration/conftest.py` (TODO-stub docstrings referencing the pool fixture by name). Schema-shrink is operator-backwards-compatible because the field was never read by any code path.

**27e — Mutex implementation** *(code)*. Add lazy `asyncio.Lock` to `SqliteUnitOfWorkFactory` (constructed on first `__call__` so the factory remains event-loop-agnostic at construction — bootstrap may run before the running loop is established). Pass the lock into each `SqliteUnitOfWork` instance. In `__aenter__`: acquire BEFORE BEGIN. In `__aexit__`, `commit()`, `rollback()`: release in `try/finally` so the lock is released exactly once even when commit fails after rollback also fails. Update the misleading module docstring at `unit_of_work.py` lines 1-28 to describe the actual mechanism (asyncio.Lock around BEGIN/COMMIT) rather than the false `busy_timeout` claim.

**27f — Concurrency test** *(code)*. New `tests/integration/persistence/test_unit_of_work_concurrency.py`. Two coroutines concurrently `async with factory()` on a real migrated DB doing real inserts; assert no `PersistenceError`, both commit, both rows visible after the contention. Markers: the new mutex L3 IDs from 27b. The test naturally fails without the lock — the assertion is the proof; no manufactured proof-of-effectiveness.

**27g — Orphan-path test rationale update** *(code)*. Update fixture docstring (lines 49-54) and inline comment (lines 120-124) in `tests/e2e/orphan_path/test_sweeper_disposes_orphan.py`. Start-after-BeginRun ordering can stay for clarity; rationale references the new mutex (no longer a workaround for an unfixed bug — now an ordering convention for test clarity).

**27h — Verify orphan-path stability** *(verification)*. Loop `tests/e2e/orphan_path/` 10× consecutively (`for i in $(seq 1 10); do poetry run pytest tests/e2e/orphan_path/ --no-cov -x; done`); confirm 10/10 green.

**27i — Pre-commit pipeline + ✅ done + status snapshot** *(closeout)*. Run the full pre-commit pipeline (ruff format, ruff check --fix, mypy strict, pytest with 85% coverage gate, build-trace-matrix, pre-commit run --all-files). Mark `### Increment 27 — UoW serialization correctness + spec alignment *(✅ done — commits ...)*`; refresh banner; move 27 from "Still open" to "Done" in the Status snapshot with all sub-step commit hashes.

**Trace impact**: L2-PERS-004 + L3-PERS-006/007/021 are reworded (same parent L1-PERS-001, content changed). No L1 promotion/demotion. The new mutex L3s become Implemented through 27e + 27f, so L1-PERS-001's trace status moves toward Implemented (full status determined when the matrix regenerates after 27b).

**Sequencing**: 27a→27b→27c→27d are spec/diagram/code-cleanup; 27e→27f→27g are the impl code; 27h verifies; 27i closes out. Land before Increment 24 so the v1 release tag has a clean test suite and a self-consistent requirement set.

### Increment 24 — Documentation deliverables (release-gating) *(✅ done)*

**Problem**

Increment 24 is the final v1 release-gating increment. It produces operator/integrator-facing documentation and the first two ADRs. The original ROADMAP entry listed four bullet items; with Increments 23 + 26e + 27 closing all v1 trace-matrix and stability gaps, this is the last work before the v1 release tag, so it deserves a per-deliverable structure.

**Work**

1. **Promote `tests/README.md` into a formal Test strategy document.** Move/expand to `docs/test-strategy.md`. Sections: unit / integration / e2e / conformance / benchmark tier definitions; fixture-scoping conventions; the auto-applied layer markers (`tests/conftest.py::pytest_collection_modifyitems`); the `@pytest.mark.requirement(...)` convention and how it feeds `scripts/build-trace-matrix.py`; the I/O guard (`tests/fixtures/io_guard.py`) and what it forbids; the SMTP capture (`aiosmtpd`); the deliberate Windows event-loop quirks (`_NoImplicitEventLoopPolicy`); how to run subsets (the `pytest-by-requirement.py` helper). Reference the conformance-test set as the executable specification. Update `CONTRIBUTING.md` and `CLAUDE.md` to point at the new location.
2. **First two ADRs in `docs/adr/`.**
   - **`adr/001-sqlite-for-in-flight-state.md`** — record the decision to use SQLite + WAL for in-flight run state, vs. in-memory + custom WAL or an external RDBMS. Capture: motivation (single-node ISOLAN deployment, simplicity, durable across restarts), tradeoffs (single-process write-side; R-DELIVER-001 outbox pattern is the future evolution path; concurrency limited by the now-explicit asyncio.Lock from Increment 27), forces (operational simplicity > horizontal scale within v1 scope).
   - **`adr/002-hexagonal-boundary-enforcement.md`** — record the decision to enforce the hexagonal boundary via static AST-walk conformance test (`tests/conformance/test_architecture_boundaries.py`) rather than runtime checks or a separate-package layout. Capture: alternatives considered (separate Python packages with import-mocked boundaries, mypy plugin, runtime locks), the decision criterion (cheap to run, fail-fast on PRs, zero runtime cost), known limitations (won't catch dynamically-imported violations).
3. **Operator runbook** at `docs/operator-runbook.md`. Sections: "Deploying" (cross-references to `deploy/linux/message-service.service`, `deploy/windows/README.md`, and `docs/procedures/windows-install-demonstration.md`); "Day-2 operations" (log inspection — point to structured-log shape and key event names like `service_starting`, `run_finalized`, `sweeper_tick_failed`; metrics scrape via `/metrics`; admin operations — user create/reset/disable; audit-log queries via dashboard); "Common failure modes" (SMTP unreachable → retry behavior; SQLite write contention; sweeper-tick error patterns; orphan-path investigation); "Backup/restore" (SQLite file copy + WAL handling); "Upgrade procedure" (`poetry install --only main` + `migration_runner.py` execution; verify trace matrix is clean post-upgrade).
4. **Pipeline integration guide** at `docs/pipeline-integration-guide.md`. Sections: "BeginRun → SubmitStageReport → FinalizeRun lifecycle" (state-machine diagram); "Required vs. optional fields per RPC"; "Tag vocabulary" (refer to configured `TagVocabulary`); "Template references" (name + version selection rules and override behavior — point at L1-TMPL-* statements); "Error codes" (gRPC status mapping table from `error_mapping.py`; per-error-code expected client behavior); "Idempotency + retry" (`was_retry=true` semantics for SubmitStageReport); "Rate considerations" (single-node deployment, no v1 rate limiting per L1-API-005 deferral); "End-to-end example" (Python pseudocode walkthrough using the generated stubs).
5. **Optional consolidation: `docs/deferred-features.md`** — extract ROADMAP Part 2's R-FOO-NNN entries into a stable manifest. Each entry retains its current shape (R-ID title, parent L1, work plan, blocker rationale). The ROADMAP can then point at this file as the canonical deferred-features list, removing inline duplication. Decision deferred to during the increment — it's an organizational improvement, not a release blocker.

**Trace impact**: no requirement promotions — Increment 24 is documentation-only. With 23 + 26e + 27 ahead of it, every L1 should already be Implemented.

**Sequencing**: each deliverable can land in a separate commit. Recommended order: (1) Test strategy promotion (smallest, benchmarks the docs/ structure conventions); (2) ADRs (independent, can parallelize); (3) Operator runbook + Integration guide (related but independent). Final commit: tag v1.

### Increment 28 — Runnable demonstration examples  *(✅ done — sub-step commits `4568288`, `5632275`, `a737eee`, `57c9690`, plus the closeout commit landing this status update)*

**Problem**

Operators, evaluators, and new contributors lack a hands-on way to see the Message Service end-to-end. After Increment 24 the repo contains the spec docs, the trace matrix, deployment artifacts, ADRs, the operator runbook, and the pipeline-integration guide — but no executable demonstration. A reader who clones the repo cannot answer "what does this thing actually do?" without writing their own gRPC client. Pre-v1 evaluation, training new pipeline integrators, and post-incident "what is the expected behaviour again?" investigations all benefit from a small set of self-contained, fully-documented example scripts that:

- Run on a developer laptop with only the project's existing dependencies (no Docker, no MailHog, no external SMTP server, no internet).
- Each isolates one capability so cause-and-effect is unambiguous.
- Include exact expected output (log lines, captured emails, dashboard responses) verbatim, so an unfamiliar user knows when the example "worked" — no judgement calls, no assumptions.

**Work**

1. **`examples/` directory layout.** New top-level `examples/` directory, organized one subdirectory per scenario. Each subdirectory contains:
   - **`README.md`** — *Prerequisites* (versions, ports), *What this demonstrates* (1-paragraph plain-English answer), *How to run* (exact command, expected duration), *Expected output* (verbatim sample with timestamps and emails redacted only where they would be unique-per-run, e.g. UUIDs replaced with `<run_id>`), *What to look for* (the 3-5 lines that prove it worked), *Cleanup* (whether anything persists, how to reset), *Troubleshooting* (3-5 common failure modes with their fix).
   - **`config.toml`** — Service config for this scenario. Ports chosen to not collide between scenarios; comments at the top of the file explain what differs from the project default.
   - **`run.py`** — Orchestrator. Starts the service in a subprocess (`python -m message_service --config ./config.toml`), waits for it to bind, fires the demo's gRPC + HTTP calls, captures SMTP-delivered emails, prints what's happening, then shuts the service down cleanly. Single entry point, no flags needed for the basic run.
   - **`templates/`** — Jinja2 templates this scenario uses (where applicable; some scenarios reuse defaults).
   - **`seed/`** — initial seed data: tag vocabulary, user accounts, etc. (where applicable).

2. **Shared helpers under `examples/_lib/`** (single underscore prefix marks "internal to examples", not a Python package boundary):
   - **`smtp_capture.py`** — wraps `aiosmtpd` to run a local SMTP server on a chosen port, capture every delivered message in memory, and pretty-print messages as the demo runs. Same shape as the e2e test harness uses (`tests/fixtures/email.py`), exposed as a reusable script.
   - **`service_runner.py`** — context manager that starts `python -m message_service --config <path>` in a subprocess, polls until the gRPC + dashboard ports are bound (TCP-connect probe with timeout), and tears down on context exit. Surfaces stdout/stderr to the parent process so the user sees the service's structured logs interleaved with the demo's own output.
   - **`pretty.py`** — colorized, timestamped output formatter. Stdlib-only (no `rich`, no `colorama` dependency added). Lines emitted in the form `[12:34:56] Step 3: …`. Optional `--no-color` flag respected via `NO_COLOR` env var.
   - **`expectations.py`** — small DSL for "wait for log line matching X" / "expect email captured with subject Y" / "expect HTTP response shape Z". Prints a clear ✓/✗ for each expectation. The script exits non-zero if any expectation fails, making the examples runnable as smoke tests too.

3. **Scenario set.** Eight scenario subdirectories, in the order a new user should walk them:

   - **`01-hello-world/`** — single-stage pipeline. BeginRun → one SubmitStageReport → FinalizeRun. One subscriber. One delivered email. The "smoke test" of the service. Expected duration: ~5 seconds.
   - **`02-multi-stage-aggregated/`** — 4-stage ETL run with `ATTACHMENT_MODE_SINGLE_AGGREGATED`. Each stage submits a different report; the aggregation template combines them into one email body. Demonstrates the L2-AGGR-* combination flow.
   - **`03-per-stage-attachments/`** — same shape as 02 but `ATTACHMENT_MODE_PER_STAGE`, showing how each stage's report becomes its own attachment on the same email.
   - **`04-retry-flow/`** — first SubmitStageReport call, then a retry of the same stage with `was_retry=true`. Demonstrates that audit log records both submissions and only the latest counts toward aggregation.
   - **`05-tag-routing/`** — two subscribers with different tag preferences (one wants `production`, one wants `nightly`). Demonstrates how the run's tags route the email to the right subset of subscribers.
   - **`06-orphan-detection/`** — start a run, never submit any stages, never finalize. Sweeper config has `run_timeout_seconds=5`. Watch the sweeper transition the run to ORPHANED, emit the audit row, fire the configured `DISCARD_SILENTLY` / `NOTIFY_ADMINS` handlers. Expected duration: ~10 seconds (timeout + poll interval).
   - **`07-manual-resend/`** — complete a run normally, then trigger a manual resend via `POST /runs/{run_id}/resend` on the dashboard. Demonstrates that the email is re-rendered from the saved Stage context (no re-submission needed).
   - **`08-error-recovery/`** — call BeginRun with an unknown pipeline, then with an unknown tag, then with malformed `declared_stages`. Demonstrates each error code (`INVALID_ARGUMENT` × 2, `FAILED_PRECONDITION`) coming back through the gRPC channel with the structured `details` payload.

4. **Top-level `examples/README.md`.** Index page that:
   - Lists each scenario in walk order with its 1-line goal.
   - Documents the global prerequisites: `poetry install`, port-availability table (`1025` for SMTP capture, `8080` for dashboard, `50051` for gRPC), Python 3.12+, no internet access required.
   - Documents the shared layout convention (so a reader can predict where to look in any scenario).
   - States explicitly: "These examples are for understanding, not for production deployment. The configs use simple defaults and the in-process SMTP capture is not a real mail server."
   - Names the recommended order for an unfamiliar user: 01 → 02 → 06 → 05 → others as desired.

5. **CLI invariants per `run.py`.** Each scenario's runner SHALL:
   - Use only stdin/stdout/stderr — no GUI, no browser auto-open.
   - Tolerate Ctrl-C cleanly (cleanup handler closes the subprocess + SMTP capture).
   - Print a numbered "Step N: doing X" line before each significant action.
   - Print the captured email body / dashboard response inline after each action so the reader sees cause + effect on the same screen.
   - Exit code 0 on success, 1 on demo-script failure (any unmet expectation from `expectations.py`).
   - Be idempotent: running it twice produces the same result. Use a fresh tmp SQLite per run (delete on start).
   - Complete within 30 seconds for scenarios 01-05 / 07-08, 30 seconds for 06 (orphan timeout dominates).

6. **Verification artifact.** New conformance test `tests/conformance/test_examples_present.py` asserts:
   - `examples/` directory exists.
   - Each scenario subdirectory listed in `examples/README.md`'s index actually exists.
   - Each scenario contains the required files (`README.md`, `run.py`, `config.toml`).
   - Each `README.md` contains the required headings (`## Prerequisites`, `## What this demonstrates`, `## How to run`, `## Expected output`, `## What to look for`, `## Cleanup`, `## Troubleshooting`).
   - The shared helpers under `examples/_lib/` exist.
   - This is inspection-only — does not actually execute the demos in CI (would require SMTP capture + service subprocess + 30+ seconds; not worth the CI spend). The smoke-quality assertion is left to humans running the examples plus the fact that 24's pipeline-integration guide cross-references them.

7. **Optional but recommended:** asciinema-style terminal recordings (`*.cast` files) per scenario, embedded in each scenario README. Defer if `asciinema` isn't already a dev-tooling assumption — animated GIFs or static "expected output" blocks are an acceptable fallback.

**Trace impact**: no requirement re-classification. Examples are documentation-tier deliverables. Sits alongside Increment 24's pipeline-integration guide as user-facing material; together they form the "how to use this service" deck for v1.

**Sequencing**: each scenario can land in a separate commit. Recommended commit order: (1) shared helpers + `examples/_lib/`; (2) scenario 01 (smallest, validates the pattern); (3) scenarios 02 + 03 (attachment-mode pair); (4) scenario 06 (orphan, the most distinctive demo); (5) the remaining four scenarios (04, 05, 07, 08); (6) top-level `examples/README.md`; (7) the conformance test. Could land before or after Increment 24 — independent, but 24's pipeline-integration guide and this increment cross-reference each other, so co-landing them in either order is fine. Final commit: tag v1.

**Effort estimate**: largest of the v1 closing increments by line count (~2,000–3,000 lines of orchestration code + READMEs + templates), but the work is parallelizable per scenario and each scenario is small in isolation. Two week-equivalents from a single developer who already understands the spec.

### Increment 29 — Rendered-report retention pruner (L1-PERS-004 implementation)  *(✅ done — commits `fb3ab75`, `a800336`, `affbca3`, `e9990ac`, `1659f87`)*

**Problem**

L1-PERS-004 declares that rendered HTML reports SHALL be retained at least `persistence.filesystem.report_retention_days` (default 90), after which a background pruner SHALL evict expired reports with one audit row per file. L2-PERS-011 / L2-PERS-012 / L2-PERS-013 elaborate (configurable retention days; daily pruner cadence using the same `BackgroundTaskScheduler` as the orphan sweeper; per-tick eviction cap). The pre-Increment-29 audit (2026-04-27) confirmed:

- `FilesystemPersistenceConfig` (`src/message_service/config/schema.py:100-103`) only declares `report_directory`. None of `report_retention_days`, `prune_interval_seconds`, `max_prunes_per_iteration` exist.
- No pruner use case exists; bootstrap registers `sweeper_loop` only.
- No `PRUNE_REPORT` audit action is emitted anywhere.
- **L2-PERS-011 / L2-PERS-012 / L2-PERS-013 have ZERO L3 children** — the requirement tree is incomplete.

The matrix correctly shows L1-PERS-004 as Draft. This is the same shape as the connection-pool drift Increment 27 fixed: spec promises a feature; code never delivered. Path A (chosen 2026-04-27) is to implement properly.

**Sub-steps**

**29a — Author L3 children for L2-PERS-011 / L2-PERS-012 / L2-PERS-013** *(spec)*. Three new L2s currently have no L3 derivations. Likely shape: L3-PERS-027 (config-key constraints + defaults), L3-PERS-028 (pruner-task lifecycle on `BackgroundTaskScheduler`), L3-PERS-029 (per-tick batch claim + eviction loop), L3-PERS-030 (atomic-rename precondition: report file present at decision time + still present at delete), L3-PERS-031 (`PRUNE_REPORT` audit row shape: actor `system:report_pruner`, resource `run:<run_id>`, details with file path + retention threshold + decision timestamp), L3-PERS-032 (concurrency: pruner UoW serializes against gRPC + sweeper via the L2-PERS-004 mutex). Trace-matrix regen.

**29b — Config schema fields + shipped configs** *(code)*. Add `report_retention_days: int = Field(default=90, ge=1)`, `prune_interval_seconds: int = Field(default=86400, ge=1)`, `max_prunes_per_iteration: int = Field(default=1000, ge=1)` to `FilesystemPersistenceConfig`. Add the three keys to `config/default.toml`, `config/dev-config.toml`, `config/config.toml.example` with explanatory comments. Update L1-CFG-003 enumeration cross-reference where the audit found the config-knob bullet listed these but the schema didn't.

**29c — `PRUNE_REPORT` audit action + ReportPrunerUseCase** *(code)*. Add `PRUNE_REPORT` to `AuditAction` enum. Author `application/use_cases/report_pruner.py` with three phases: scan candidate `run_id`s with terminal-state transition older than `now - retention_days`; for each, attempt atomic-delete of `<report_directory>/<run_id>/` tree (or per-file); audit one row per evicted file. Per-tick batch capped at `max_prunes_per_iteration`.

**29d — Bootstrap wiring** *(code)*. Construct the `ReportPrunerUseCase`, register a periodic task on `BackgroundTaskScheduler` with cadence `prune_interval_seconds`. Mirror sweeper-loop construction shape. Lifecycle hooks (start at bootstrap end, stop on shutdown).

**29e — Integration tests** *(code)*. New `tests/integration/persistence/test_report_pruner.py`: real filesystem under `tmp_path`, real SQLite, real `SystemClock` advanced via injected `FakeClock` to push terminal runs past the threshold. Assertions on (a) eligible runs' report files removed, (b) ineligible runs' files preserved, (c) one `PRUNE_REPORT` audit row per evicted file, (d) per-tick cap respected (configure cap=2, seed 5 eligible, expect 2 deletions per tick, 3 surviving until next tick), (e) rollback on UoW failure leaves filesystem unchanged (audit-first ordering preserved).

**29f — Pre-commit + status snapshot + ✅ done** *(closeout)*. Full pre-commit pipeline (ruff format, ruff check, mypy strict, pytest with 85% gate, build-trace-matrix, pre-commit run --all-files). L1-PERS-004 promotes Draft → Implemented. Status snapshot follow-up moves 29 to Done.

**Trace impact**: L2-PERS-011 / L2-PERS-012 / L2-PERS-013 promote Draft → Implemented; new L3-PERS-027..032 (or whatever range) become first-time Implemented; L1-PERS-004 promotes Draft → Implemented.

**Sequencing**: Land first among the new v1-closing increments. Independent of 30 / 31 / 33 / 34 / 32, but its `BackgroundTaskScheduler`-based pattern sets the template for 30's audit-log pruner so doing 29 first benefits 30.

### Increment 30 — Audit-log retention pruner (L1-OBS-003 implementation; closes R-PERS-002)  *(✅ done — commits `42455b4`, `70eee76`, `62b09c4`, `891ca60`, `bce7479`, `16eed55`)*

**Problem**

L1-OBS-003 declares audit-log records SHALL be retained for a globally configurable duration. L2-OBS-008 says "Audit log retention SHALL be enforced by a daily cleanup task that deletes records whose `timestamp` is older than the configured retention duration." L2-OBS-009 pins the asyncio scheduling. L3-OBS-014 references config key `observability.audit.cleanup_interval_hours` (default 24); L3-OBS-015 references `observability.audit.cleanup_batch_size` (default 10000).

Pre-29 audit findings:

- `AuditConfig` (`src/message_service/config/schema.py:284-287`) declares `retention_days` (default 365) but neither `cleanup_interval_hours` nor `cleanup_batch_size` exists.
- No background pruner is constructed in bootstrap.
- The `AuditLog` port docstring says retention "is handled separately by a background pruner" — but no such pruner exists.
- **R-PERS-002 already exists** as a deferred-features entry: *"`observability.audit.retention_days` is in the config schema but not yet enforced by a running process. Future option: scheduled background task that deletes audit rows older than the retention window. Small; can piggyback on the same scheduler used for orphan sweeping."*

So the drift is already half-captured (R-PERS-002 acknowledges the gap) — but the active L2-OBS-008 / L2-OBS-009 statements still read as a non-deferred SHALL. Same shape as the pool drift before Increment 27. Path A (chosen 2026-04-27) is to implement the pruner and close R-PERS-002.

**Sub-steps**

**30a — L3 verification + authoring** *(spec)*. L3-OBS-014 / L3-OBS-015 already exist (per audit). Verify they're still accurate and don't need rewording. Author any additional L3s the implementation surfaces (e.g., per-batch transaction scope, audit-the-pruner audit row, monotonic-non-decreasing timestamp assumption per L2-RUN-016). Trace-matrix regen.

**30b — Config schema fields + shipped configs** *(code)*. Add `cleanup_interval_hours: int = Field(default=24, ge=1)`, `cleanup_batch_size: int = Field(default=10000, ge=100, le=1_000_000)` to `AuditConfig`. Add the keys to `config/default.toml`, `config/dev-config.toml`, `config/config.toml.example` with explanatory comments.

**30c — AuditLogPrunerUseCase** *(code)*. New `application/use_cases/audit_log_pruner.py`. Per-tick: compute cutoff `now - retention_days`; in one UoW, run `DELETE FROM audit_log WHERE timestamp < ? LIMIT batch_size` (SQLite supports `LIMIT` on DELETE via `SQLITE_ENABLE_UPDATE_DELETE_LIMIT` — verify in the build; if not, use a sub-select on `audit_id`); count rows deleted, return `PruneResult`. Repeat per cadence cycle until all-eligible-rows-deleted or per-tick cap reached. Per L1-OBS-003's append-only invariant, the pruner is the *only* code path that issues DELETE against `audit_log` (verified by a conformance test).

**30d — Bootstrap wiring** *(code)*. Mirror Increment 29d. Schedule the pruner on `BackgroundTaskScheduler` at `cleanup_interval_hours` cadence.

**30e — Integration tests** *(code)*. `tests/integration/persistence/test_audit_log_pruner.py`: seed audit rows at varied timestamps, advance fake clock, assert (a) only old rows deleted, (b) batch-size respected, (c) UoW rolls back cleanly on injected failure, (d) repeat-tick eventually empties beyond-retention rows. New conformance test asserts the pruner is the only DELETE-issuer against `audit_log` (AST scan of `src/`).

**30f — Close R-PERS-002 + reword if needed** *(spec)*. Move R-PERS-002 entry from active deferred-features section to a "Closed by Increment 30" sub-section (or remove with a note in the increment commit). Verify L2-OBS-008 / L2-OBS-009 statement wording still matches the implementation; update if drift is found.

**30g — Pre-commit + status snapshot + ✅ done** *(closeout)*. Full pre-commit pipeline. L1-OBS-003's audit-pruner blockers are removed; the L1's promotion from Partially Implemented → Implemented depends on the 32e Category-C marker work landing in parallel or after.

**Trace impact**: L2-OBS-008 / L2-OBS-009 promote Draft → Implemented; L3-OBS-014 / L3-OBS-015 verified by tests for the first time. R-PERS-002 closed.

**Sequencing**: Land after 29 (same `BackgroundTaskScheduler` pattern; 29 sets the template). Could parallelize but serializing simplifies the bootstrap diff.

### Increment 31 — L1-MAIL-004 admin notification + spec inconsistency fixes  *(✅ done — commits `94864ef`, `95ed01d`, `2d480fb`, `5e503cc`)*

**Problem**

Two adjacent gaps surfaced in the 2026-04-27 audit:

1. **L1-MAIL-004 (Draft)**: When a composed email exceeds `max_email_size_bytes`, the spec says the run SHALL transition to FAILED *and* the service SHALL persist the rendered report *and* SHALL notify administrators "via the same channel used for orphan administrator notifications." Code state: `assemble_and_deliver.py` does the FAILED transition and the size-check, but the admin-notification path is missing. The L2-MAIL-009 / L2-MAIL-010 / L2-MAIL-011 derivations describe the notification template + persistence semantics but no L3 verification artifacts exist for them.

2. **Spec inconsistencies** flagged in the requirements consistency audit (sub-agent 1, 2026-04-27):
   - **L2-OBS-018** was originally flagged as referencing non-existent L3-OBS-037 / L3-OBS-038. Inspection during Increment 30a confirmed the audit was wrong: both L3-OBS-037 (SEND_REPORT audit row shape) and L3-OBS-038 (DISPATCHER_ACTION_ABANDONED audit row shape) exist and are properly authored. **No action needed for this item; sub-step 31d removed.**
   - **L2-OBS-007** declares "Verification: Inspection (I)" only, but its L3 child L3-OBS-012 declares "Verification: T". Either add T to L2-OBS-007 or re-parent L3-OBS-012.
   - **L3-MAIL-001** hard-codes the "no SMTP pooling" position with deferred-pool wording mirroring the pre-27 pool drift; reword to describe the actual mechanism (instantiate-per-send) without the speculative deferral.

**Sub-steps**

**31a — L3 authoring for L2-MAIL-009 / L2-MAIL-010 / L2-MAIL-011** *(spec)*. Author L3 derivations: structured `EMAIL_SIZE_EXCEEDED` reason in audit details (measured + configured size); admin-notification template hard-coded (no user content interpolated) — the existing `L3-MAIL-006`-style permanent-failure pattern is the model; oversized rendered report stored under `<run_id>/email.html` so dashboard resend can find it. Trace-matrix regen.

**31b — Implement EMAIL_SIZE_EXCEEDED admin notification path** *(code)*. In `assemble_and_deliver.py`, on `EmailSizeExceededError` raise: (1) persist the rendered report via the existing `ReportStore` adapter (per L2-MAIL-011), (2) construct a fixed-template admin notification email (`MailerPort.send_admin_notification(...)` — may need new port method or reuse existing `Mailer.send` with a fixed template), (3) audit `EMAIL_SIZE_EXCEEDED` action with measured + configured + recipient list, (4) transition run to FAILED with structured reason. Order: persist → audit → notify → transition. Audit-first preserves L2-OBS-013-style ordering even on failure path.

**31c — Tests** *(code)*. Integration test in `tests/integration/test_full_pipeline.py` (or dedicated file): drive a run with intentionally oversized templates against `max_email_size_bytes=1024`; assert admin notification captured by SMTP harness with sanitized subject + measured size in body; assert run state == FAILED with `EMAIL_SIZE_EXCEEDED` reason; assert rendered report is at `<run_id>/email.html`.

**31d — REMOVED.** Originally "Resolve L2-OBS-018 dangling cross-references." Increment 30a verified that L3-OBS-037 and L3-OBS-038 already exist and are properly authored; the audit-1 finding was incorrect. No work needed; the sub-step letter is reserved (not re-used) so the 31a–31g letter sequence remains stable for cross-references in commit messages.

**31e — Resolve L2-OBS-007 verification-method mismatch** *(spec)*. L2-OBS-007 has `Verification: I`; L3-OBS-012 (its child) has `Verification: T`. Either add T to L2-OBS-007's verification methods or re-parent L3-OBS-012 if appropriate. Decision likely: add T (the L3 is correctly testable; the L2 was overly conservative).

**31f — Optional L3-MAIL-001 reword** *(spec, optional)*. Rewrite L3-MAIL-001 to describe the v1 actual mechanism (`aiosmtplib.SMTP` instantiated per send) without the "pooling on the ROADMAP" speculation — same anti-pattern as the pre-27 pool drift wording. If the SMTP-pooling decision is genuinely deferred, capture as a new R-MAIL-NNN entry; otherwise drop the speculation entirely. Defer to during the increment if scope creeps.

**31g — Pre-commit + status snapshot + ✅ done** *(closeout)*. Full pre-commit pipeline. L1-MAIL-004 promotes Draft → Implemented. Spec inconsistencies removed; trace matrix re-checked.

**Trace impact**: L2-MAIL-009 / L2-MAIL-010 / L2-MAIL-011 promote Draft → Implemented; L1-MAIL-004 promotes Draft → Implemented; L2-OBS-007 verification-method updated (no status change); L3-MAIL-001 optionally reworded (no status change). L2-OBS-018 unchanged (the audit-1 finding was incorrect; verified during 30a).

**Sequencing**: Independent of 29 / 30; can land in parallel. Recommended after 30 because both touch audit-row authoring patterns.

### Increment 33 — L1-TMPL-002 "latest" version resolution  *(✅ done — commits `9880971`, `a141e02`, `5596ea6`, `33345cb`)*

**Problem**

L1-TMPL-002 says template version SHALL be either an explicit semver matching a manifest entry OR the literal `"latest"`, in which case the service SHALL resolve it to the highest available semver for that template. L2-TMPL-005 pins resolution at BeginRun initiation time (not render time) using `packaging.version.Version`; L2-TMPL-006 pins recording the resolved version with run state and audit.

The 2026-04-27 audit found: **the string `"latest"` is mentioned only in `docs/L1-REQ.md`, `docs/L2-REQ.md`, `docs/L3-REQ.md`, and one diagram. It is absent from every Python source file and every test.** No code path implements the sentinel. Pipeline clients sending `template_version="latest"` would today fail with `UNKNOWN_TEMPLATE` because the manifest lookup is exact-match-only.

L1-TMPL-002 is currently Partially Implemented in the matrix; the audit recategorizes it as Category D (real implementation gap, no R-entry). Path A: implement.

**Sub-steps**

**33a — L3 authoring + verify** *(spec)*. Verify L3 children of L2-TMPL-005 / L2-TMPL-006 are accurate. Author missing L3s if needed (likely: sentinel-string-recognition; resolution-at-BeginRun ordering; resolution-uses-`packaging.version.Version`-not-string-compare; resolved-version-recorded-on-Run-aggregate; resolved-version-in-audit). Trace-matrix regen.

**33b — Implement "latest" resolution in `TemplateRepository`** *(code)*. New method on the port (or use case helper): `resolve_latest(name: str) -> TemplateRef`. Returns the manifest entry with the highest `packaging.version.Version`-parsed version for the given name. Raise `UnknownTemplateError` if zero entries match. Adapter implementation reads from the same in-memory manifest the existing exact-lookup uses.

**33c — Wire BeginRun to call resolution** *(code)*. In `BeginRunUseCase.execute`: for every `TemplateRef` in the request (stage templates, aggregation template, email body template ref), if `version == "latest"`, call resolve before validation; replace the request's TemplateRef with the resolved version; proceed. Store the *resolved* TemplateRef on the `Run` aggregate (the existing `aggregation_template_ref` field already supports this). Audit-row from `BEGIN_RUN` records the resolved version (not `"latest"`).

**33d — Tests** *(code)*. Unit test for `resolve_latest`: empty manifest → raise; one entry → that one returned; multiple → highest semver returned; pre-release vs final per `packaging` semantics. Integration test for BeginRun flow: submit `version="latest"`; verify the persisted `Run` aggregate has the resolved version; verify audit row has the resolved version not `"latest"`.

**33e — Pre-commit + status snapshot + ✅ done** *(closeout)*. Full pre-commit pipeline. L1-TMPL-002 promotes Partially → Implemented (assuming 32d's marker work for the rest of L1-TMPL-* lands first or parallel).

**Trace impact**: L2-TMPL-005 / L2-TMPL-006 promote Draft → Implemented; L1-TMPL-002 promotes Partially → Implemented.

**Sequencing**: Independent of 29 / 30 / 31. Recommended after them; can parallelize with 34 since both touch templating.

### Increment 34 — L1-TMPL-004 JSON Schema context validation

**Problem**

L1-TMPL-004 says each template manifest entry SHALL declare a JSON Schema and the service SHALL validate submitted context against that schema before rendering. L2-TMPL-010 pins use of the `jsonschema` library with Draft 2020-12; L2-TMPL-011 pins `INVALID_ARGUMENT` + error code `CONTEXT_SCHEMA_VIOLATION` + JSON-Pointer path in the detail.

The 2026-04-27 audit (Agent A on the 5 Draft L1s) found: **`Jinja2SandboxedTemplateRenderer.render()` (`src/message_service/infrastructure/templating/renderer.py:149-215`) performs size checks but never calls `jsonschema`. The library is not even imported.** `ContextSchemaViolationError` exists in `domain/errors.py` and `manifest_loader.py` accepts a `schema_path` field, but the validation logic was never wired. Manifest entries declaring `schema_path` have no effect at runtime.

L1-TMPL-004 is in the 8-Drafts set (Agent A disposition: hybrid — partial code, real gap). Path A: implement.

**Sub-steps**

**34a — L3 authoring + verify** *(spec)*. Verify L3 children of L2-TMPL-010 / L2-TMPL-011 are accurate (Draft 2020-12 specifically; INVALID_ARGUMENT + CONTEXT_SCHEMA_VIOLATION + JSON-Pointer). Author missing L3s if needed (likely: validation-fires-at-render-call-site or BeginRun-time; failed-validation-raises-`ContextSchemaViolationError`-with-pointer-in-`details`; pre-loaded-`Validator`-instances cached on manifest load to avoid per-call schema compile cost). Trace-matrix regen.

**34b — Add `jsonschema` Poetry dependency** *(code)*. `poetry add jsonschema`. Pin to a Draft 2020-12-supporting version. Update `poetry.lock`.

**34c — Implement validation** *(code)*. Two open questions decided during the increment: (1) does validation fire in `BeginRun` (early-fail per L2-TMPL-005's "resolve early" pattern) or in the renderer (late-fail at `render()` call time)? Lean late-fail because the context isn't known at BeginRun (it's submitted with `SubmitStageReport`). (2) compile schemas eagerly at manifest load, or lazily on first use? Lean eagerly — manifest load is the right time to surface bad schemas. Implementation: add `jsonschema.Draft202012Validator` instance per manifest entry; in `Jinja2SandboxedTemplateRenderer.render()` (or a new validating wrapper), call `validator.validate(context)` before passing to Jinja2; on `ValidationError`, raise `ContextSchemaViolationError` with `details={"json_pointer": str(error.absolute_path), "validator": error.validator, "schema_path": ...}`. The error mapper translates this to `INVALID_ARGUMENT` (already does, since it derives from `ValidationError` per the existing hierarchy).

**34d — Tests** *(code)*. Unit test of validator wiring: schema requires "name"; submit `{}` → raises with pointer. Integration test through the full renderer: invalid context → `ContextSchemaViolationError` with structured details. gRPC integration test: invalid context arrives via SubmitStageReport, returns `INVALID_ARGUMENT` with `CONTEXT_SCHEMA_VIOLATION` in trailing metadata.

**34e — Pre-commit + status snapshot + ✅ done** *(closeout)*. Full pre-commit pipeline. L1-TMPL-004 promotes Draft → Implemented.

**Trace impact**: L2-TMPL-010 / L2-TMPL-011 promote Draft → Implemented; L1-TMPL-004 promotes Draft → Implemented.

**Sequencing**: Can parallelize with 33 since both touch templating but they don't conflict (33 is BeginRun-time resolution; 34 is render-time validation).

### Increment 32 — Trace-matrix coverage pass (the "Category C" cleanup) *(✅ done)*

**Problem**

The 2026-04-27 Partials audit categorized 41 of the 44 Partially Implemented L1s as **Category C: code exists, only L3 statements / test markers / small inspection tests missing**. The implementations are real and exercised; the matrix shows them Partial only because the requirement tree is incomplete. This is dramatically less work per L1 than feature implementation, but applied to ~41 L1s (and 4 of the 5 Agent-A Draft L1s where code already exists), it's the largest absolute scope of any v1 closing increment.

Sub-divided into five chunks matching the audit's investigation chunks so each commit stays reviewable. Each chunk authors L3 statements where missing, attaches `@pytest.mark.requirement(...)` markers to the existing tests that already exercise the behavior, and adds small inspection tests where conventions need a marker (e.g., proto-method enumeration, `add_insecure_port` usage, `argon2id` library binding). Final sub-step does the matrix regen + the AUTH session-memo correction + closeout.

**Sub-steps**

**32a — API + RUN coverage** *(✅ done — commit `b81df1f`)*. Six L1s promoted: L1-API-002, L1-API-003, L1-API-004, L1-RUN-002, L1-RUN-003, L1-RUN-004 → Implemented. Drift dispositions: L3-API-002/003/004 deferred to v2 with new `R-API-001` ROADMAP entry (per-RPC structlog interceptor + pinned-tag proto dependency + CI version-mismatch check); L3-RUN-020/021/023/029 reworded to match implementation truth. Two small features landed: `GrpcConfig.max_concurrent_rpcs` config field + plumb-through to `grpc.aio.server(maximum_concurrent_rpcs=...)` (L3-API-001), and `GrpcConfig.port` default = 50051 (L3-API-009). 21 marker additions + 16 new inspection/spec tests in `test_grpc_server_construction.py` and `test_finalize_run.py` (L3-RUN-027 audit-fail rollback).

**Residuals carried to 32g**: L1-API-001 stays Partial because L3-API-002/003/004 are deferred — this is the same compromise L1-ERR-002 makes for L3-ERR-010/011 (also deferred to v2 / R-ERR-002), and is the correct outcome under the current matrix-script semantics. L1-RUN-005 stays Partial because L2-RUN-016 has zero L3 children authored (the host-clock-validity L2 — see Increment 25d's spec authoring); 32h adds them.

**32b — STAGE + SWEEP + AGGR coverage** *(✅ done — commit `6e70641`)*. Five L1s promoted: all four L1-STAGE-* (001/002/003/004) → Implemented; L1-SWEEP-002 → Implemented; L1-AGGR-004 Draft → Partial. Six L3-STAGE spec rewords (drift fixes): L3-STAGE-002 (table is `stages`, not `stage_state`); L3-STAGE-004 (no `caller_context` parameter — v1's three call sites are git-greppable); L3-STAGE-005 (PK provides unique guarantee; ON CONFLICT DO UPDATE consumes IntegrityError); L3-STAGE-014 (column is `declared_stages_json`); L3-STAGE-015 (details keys are `stage_id` + `declared_stages`, not the earlier draft's `submitted_stage_id` + sorted `declared_stage_ids`); L3-STAGE-017 (no per-stage `last_transition_at` column). 16 marker additions across STAGE/SWEEP/AGGR; new 6-test inspection file `test_stages_table_shape.py`.

**Residuals carried to 32f and 32g**: L1-AGGR-001/002/003 stay Partial — L3-AGGR-001/003/004/005/006/013/014/016/017/018/019/020 still need either small inspection tests (proto3-`Struct`-root validation, MIME header shape, manifest schema typing) or marker plumbing on tests that need authoring (position-default DEBUG log, lex tie-break with stage_order=0). L1-SWEEP-001/003 stay Partial — L3-SWEEP-001/004/007/008/010/011/012/013/014/015/018 same shape (bootstrap-wiring inspection on lifespan + post-migration startup; SQL string scans for the SELECT WHERE state IN clause; counter-name inspection).

**32c — SUB + AUTH coverage** *(code + spec)*. Targets: L1-SUB-001 (L2-SUB-001/002/003); L1-SUB-002 (L2-SUB-004/005, Agent-A Draft — opt-in default already enforced in `admin_users.py:203-212`); L1-SUB-003 (L2-SUB-007/008); L1-SUB-004 (L2-SUB-009/010); L1-AUTH-001 (L2-AUTH-001/002/003 — including `Password` redacted-repr at `domain/aggregates/password.py:44-50`); L1-AUTH-002 (L2-AUTH-006).

**32d — MAIL + TMPL coverage** *(code + spec)*. Targets: L1-MAIL-001 (L2-MAIL-001/002/003); L1-MAIL-002 (L2-MAIL-005/006); L1-MAIL-003 (L2-MAIL-007/008); L1-MAIL-005 (L2-MAIL-012/013); L1-TMPL-001 (L2-TMPL-002/003); L1-TMPL-003 (L2-TMPL-007/008/009); L1-TMPL-005 (L2-TMPL-012/013/014). Excludes L1-TMPL-002 (Increment 33) and L1-TMPL-004 (Increment 34) and L1-MAIL-004 (Increment 31).

**32e — DASH + PERS + OBS + ERR + CFG initial coverage** *(✅ partial — commit `873a0b5`)*. One L1 promoted (L1-PERS-003 Partial → Implemented via L3-PERS-015/017 markers on the architecture-boundary conformance tests). Six L3 spec rewords for v1 design honesty: L3-DASH-016/017 → R-DASH-004 deferral; L3-OBS-003/004 → R-API-001 deferral (same compromise as L3-API-002 / L1-API-001 stays Partial); L3-PERS-018 (umask-via-systemd instead of explicit chmod); L3-PERS-019 (SQLite auto-checkpoint instead of explicit `PRAGMA wal_checkpoint(TRUNCATE)`); L3-PERS-020 (one-tx-per-migration clarified); L3-PERS-023 (code-as-spec conformance tests instead of prose registry).

**Residuals from 32e (carried to 32j/k/l/m)**: 5 categories x 4-12 L3-id-each gaps each. Each residual sub-step targets a single L1 cluster and adds markers on existing tests + a small inspection test file where coverage genuinely needs new authoring. The two intentional Partials at v1 release (L1-DASH-004 per R-DASH-004; L1-ERR-002 per R-ERR-002) stay Partial as documented; everything else closes via 32j/k/l/m.

**32f — AGGR residuals** *(code + spec; new sub-step)*. Closes L1-AGGR-001/002/003 carried over from 32b. Targets: L3-AGGR-001 (proto `Struct` for context — inspection); L3-AGGR-003 (`HasField` / `is None` detection of omitted email_body_contribution); L3-AGGR-004 (`EMAIL_BODY_POSITION_UNSPECIFIED` → AFTER_STAGES_SUMMARY default + DEBUG log); L3-AGGR-005 (BEFORE → main → AFTER body order); L3-AGGR-006 (aggregation template context fields: `stages`, `run_id`, `run_metadata`, `pipeline_type`); L3-AGGR-013 (lex tie-break with `<` operator); L3-AGGR-014 (multi-stage same-stage_order ordering); L3-AGGR-016 (aggregation template manifest distinct JSON Schema); L3-AGGR-017 (`Struct` non-object roots raise MalformedRequest); L3-AGGR-018 (email body contribution `position` column); L3-AGGR-019 (aggregation render after per-stage); L3-AGGR-020 (MIME `Content-Type` + `Content-Disposition` headers). Expect: spec rewords for the items where v1 took a different shape than the L3 prescribed (especially around the `email_body_contribution` columnar storage — the current `email_body_context_json` may not carry a separate `position` column); new inspection tests for the renderer call shape and email construction.

**32g — SWEEP residuals + L1-API-001 status note** *(code + spec; new sub-step)*. Closes L1-SWEEP-001/003 carried over from 32b. Targets: L3-SWEEP-001 (sweeper task lifespan registration shape — likely reword to acknowledge `BackgroundTaskScheduler`-driven lifecycle rather than `contextlib.asynccontextmanager`); L3-SWEEP-004 (counter naming `message_service_sweeper_iterations_total` + label `outcome`); L3-SWEEP-007 (SQL `SELECT ... WHERE state IN (...)` with index on state); L3-SWEEP-008 (max_candidates_per_iteration cap default 1000); L3-SWEEP-010 (sweeper_actions outbox enqueue); L3-SWEEP-011 (empty disposition_actions permitted); L3-SWEEP-012 (unknown action raises ConfigurationError); L3-SWEEP-013 (handlers SHALL NOT raise — failures swallowed); L3-SWEEP-014 (handler registration `dict[str, Callable]` shape); L3-SWEEP-015 (canonical documented order); L3-SWEEP-018 (sweeper task starts after migrations). Plus a documentation note acknowledging that L1-API-001 stays Partial at v1 release — the L3-API-002/003/004 deferral pattern is intentional and the rollup mirrors L1-ERR-002's identical compromise.

**32h — L1-RUN-005 closure: author L2-RUN-016 L3 children** *(spec; new sub-step)*. L2-RUN-016 (host-clock-validity assumption, added in Increment 25d) currently has zero L3 children, blocking L1-RUN-005's roll-up to Implemented. Author L3-RUN-031..033 (or similar) covering: clock source is the injected `Clock` port (the single chokepoint); the assumption that the host clock is monotonically non-decreasing UTC is recorded as an inspection-verifiable invariant (no backward-correction handling code exists in v1); the deferred work for backward-NTP-correction handling is captured in the existing ROADMAP "Host-clock validity hardening" entry. Verification methods are mostly Inspection (I) since v1 deliberately doesn't implement clock-anomaly detection.

**32j — DASH residuals** *(code + spec; new sub-step)*. Closes L1-DASH-001/002/003 carried over from 32e. L1-DASH-004 stays Partial via R-DASH-004 deferral (documented in 32e). Targets: L3-DASH-001/002 (FastAPI factory shape); L3-DASH-003 (port collision check); L3-DASH-005/006 (static asset paths); L3-DASH-010 (tag selection UI); L3-DASH-012 (RecipientResolver reuse across resend); L3-DASH-020 (font shipping policy); L3-DASH-027 (resend re-renders from saved context). Expect inspection tests for FastAPI route shape + static-asset directory existence.

**32k — PERS residuals** *(code + spec; new sub-step)*. Closes L1-PERS-001/002/004 carried over from 32e. L1-PERS-003 closed in 32e. Targets: L3-PERS-001/018/019 (already reworded in 32e — need markers); L3-PERS-002/003 (already partially marked); L3-PERS-012 (pathlib enforcement — covered by existing conformance test, just needs marker); L3-PERS-027/028 (report retention pruner — partially marked); L3-PERS-029/030/032 (pruner config + scheduler integration). Mostly marker plumbing on existing tests.

**32l — OBS residuals** *(code + spec; new sub-step)*. Closes L1-OBS-001/003/004 carried over from 32e. Targets: L3-OBS-001/002 (structlog setup); L3-OBS-005/006 (sensitive-key redaction — partial marker, L3-OBS-006 covered by L3-AUTH-005 work in 32c); L3-OBS-012/013 (audit-log schema); L3-OBS-017 (audit-log retention pruner lifecycle marker); L3-OBS-019/020 (Prometheus metric shape); L3-OBS-022 (log level configurability); L3-OBS-023/024 (config-driven observability); L3-OBS-028/029/035 (audit details fields per category). Expect spec rewords for L3-OBS items where v1 design diverged.

**32m — ERR + CFG residuals** *(code + spec; new sub-step)*. Closes L1-ERR-001/003/004 + L1-CFG-001/002 carried over from 32e. L1-ERR-002 stays Partial via R-ERR-002 deferral (already documented). Targets: L3-ERR-003 (intermediate hierarchy — likely existing test); L3-ERR-012/013 (translation layer pattern); L3-ERR-018 (correlation id derivation); L3-ERR-019/020/021 (BLE rule enforcement, suppressed-exception tests); L3-CFG-001/002/003 (loader pattern + frozen models); L3-CFG-007 (validation error format); L3-CFG-009 (env-var substitution failures). Mostly marker plumbing on existing tests + small inspection tests for the BLE conformance.

**32i — Final matrix regen + session-memo correction + ✅ done** *(✅ done — this commit)*. Trace matrix regenerated after 32c/d/e/f/g/h/j/k/l/m all completed; `--check` clean. AUTH session-memo correction landed at `memory/session_2026_04_26.md:30` (the 2026-04-26 "AUTH category fully closed" claim was premature — only L1-AUTH-003 was Implemented at that snapshot; L1-AUTH-001/002 promoted to Implemented in 32c). Status snapshot at the top of this file refreshed with the post-32 v1 matrix state and the five intentional v1 Partials enumerated explicitly so v1 release ships with an honest matrix. Final counts: 67 L1 / 192 L2 / 393 L3; 62 of 67 L1s Implemented (5 intentional Partials with named R-XXX-NNN deferral entries: L1-API-001, L1-AGGR-001, L1-DASH-004, L1-ERR-002, L1-OBS-001). 1263 tests pass at 94.88% branch coverage. The five additional v1-cycle deferred-features (R-API-001 for the API/OBS interceptor + proto-pinning items; R-AGGR-001 for per-stage email body contributions, pre-existing) are now visible alongside the original three (R-ERR-001, R-ERR-002, R-DASH-004).

**Trace impact** (cumulative across 32a–32m): ~41 Partial L1s + 4 Draft L1s promote to Implemented, with the explicit exceptions of L1-API-001 (R-API-001 deferral), L1-DASH-004 (R-DASH-004 deferral), and L1-ERR-002 (R-ERR-002 deferral). Per-category L2-Implemented and L3-Implemented counts increase substantially. Overall verified-by-test percentage moves from 45.1% (post-27) and 51.9% (post-32a-b) toward 80%+.

**Sequencing**: Land **last** among the implementation increments (after 29, 30, 31, 33, 34) so its matrix regenerations capture all the upstream promotions in one final pass. Sub-steps 32a–32e are the audit-chunk-style coverage passes; 32f/g/h close the residuals surfaced during 32a–b; 32j/k/l/m close the residuals surfaced during 32e; 32i is the closeout.

### Cross-cutting tradeoffs (refreshed 2026-04-25)

The historical sequencing block has been pruned now that Clusters 14 (excluding 14h), 25, and 26, plus Increments 15 and 16, are merged. What remains:

**Feature stream (Increments 19a–22)**

- **19a → 19b → 19c → 20a → 20b → 20c** complete the dashboard, building on the chassis 17 delivered and the subscription CRUD 18 added. 19a delivers the past-runs paginated list + run-detail view (read-only metadata); 19b adds manual resend (re-renders from saved Stage context, no filesystem-store dependency); 19c lands the filesystem report store + the rendered-report viewer routes. 20a implements the admin gate + template registry inspection (closing L1-DASH-003's third clause). 20b adds admin-driven user management. 20c adds the audit-log viewer. The 20-split was driven by the survey before kickoff — the original entry mislabeled the closure target and bundled scopes that needed independent spec authoring.
- **20d** (embedded Prometheus metrics dashboard) is a separate stream from 20a-c and may slot anywhere relative to 21-24. It closes `L1-DASH-004`, which the original Increment 20 entry incorrectly referenced.
- **21** (E2E harness) can shift earlier — slotting it in after one or two more domain-router increments forces the FastAPI chassis to stay testable as routes accrete.
- **22** (error-mapping + servicer tests) is independent of the dashboard stream and can interleave whenever convenient.

**Recommended next-up sequencing**

1. **20a → 20b → 20c** — admin surfaces. (19a/19b/19c landed; L1-DASH-003 partially closed — clauses 1 and 2 done, clause 3 lands in 20a.)
2. **21** — E2E happy-path + orphan-path harness.
3. **22** — error-mapping + servicer tests; independent stream.
4. **20d** — embedded Prometheus metrics dashboard (closes `L1-DASH-004`). Recommended slot: after 22, before 23/24, so the spec deck is fully decomposed before release-gating documentation.
5. **23, 24** — deployment polish + documentation deliverables (release-gating).

---

## Part 2 — Deferred from v1

## Testing and verification

- **Test strategy document** — a top-level document covering unit test conventions, integration test harness for gRPC and FastAPI, end-to-end run-simulation fixtures, orphan-path test harness, and SMTP sandbox configuration. (Partially superseded by `tests/README.md`; still to be promoted to a formal top-level doc.)
- ~~**pytest marker auto-extraction tool**~~ — **Done.** `scripts/build-trace-matrix.py` now scans `@pytest.mark.requirement` markers and auto-populates `docs/TRACE-MATRIX.md`.
- ~~**Coverage ratchet**~~ — **Done.** Gate is at `--cov-fail-under=85` in `pyproject.toml`; the historical 60% → 75% → 85% ratchet has completed.
- **Coverage enforcement** — CI gate requiring every approved L1 requirement to have at least one linked verification artifact before release. (The `--cov-fail-under` gate enforces aggregate coverage; requirement-level coverage tracking is the separate item.)

## Performance and profiling

- **In-flight run state backing profiling** — v1 co-locates in-flight run state in SQLite, relying on SQLite's built-in WAL journal for durability. If profiling later shows SQLite write latency is a bottleneck on the gRPC ingest hot path, evaluate an in-memory store with a custom write-ahead log. The repository-pattern abstraction (L1-PERS-003) makes this swap possible without touching domain code.
- **Email size distribution analysis** — once the Prometheus email-size histogram has collected production data, analyze for patterns that would justify per-pipeline-type size limits or automatic compression strategies.
- **R-DELIVER-001 — Outbox-backed background tasks** — `FinalizeRunUseCase` schedules the assembly workflow via `BackgroundTaskScheduler`, which is backed by `asyncio.create_task`. If the process dies after `FinalizeRun` commits but before the task completes, the delivery is lost (the run is stuck in `READY`/`SENDING`). Future option: outbox-row pattern. `FinalizeRun` writes a row to an `outbox` table inside the same transaction; a long-running worker drains the outbox and retries on failure. The existing `BackgroundTaskScheduler` port can be retained; its adapter simply reads from the outbox instead of accepting coroutines directly. Defer until multi-node deployment is in scope. Single-node ISOLAN deployments can survive the current risk because the orphan sweeper (L1-RUN-006) will eventually reclaim stuck runs, bounded by `sweeper.run_timeout_seconds`.
- **R-OBS-001 — Distributed tracing** — v1 has structured logging via structlog with `run_id` correlation; no trace spans. Future option: OpenTelemetry-based spans across the RPC handler, use case, UoW, and adapter calls. Useful primarily once the service is part of a larger distributed system; low value standalone.

## Security hardening

- **Mutual TLS on gRPC** — v1 uses plaintext TCP on the trusted ISOLAN network. Promote when gRPC ingest crosses trust boundaries or when compliance requirements demand transport encryption.
- **Additional authentication backends** — LDAP/AD and OIDC. Current scope is local accounts only. LDAP integration is the likely first addition, consistent with broader ISOLAN architecture patterns.
- **Secrets handling review** — SMTP credentials and any future API keys currently live in the TOML configuration file. Consider integration with Vault CE if secret rotation becomes operationally significant.
- **In-flight RPC concurrency limits / per-pipeline rate limiting** — v1 deliberately omits rate limiting because the trusted-ISOLAN deployment context assumes well-behaved pipeline clients (same rationale that justifies plaintext gRPC under L1-API-003). When the gRPC ingress crosses a trust boundary — concurrent with the mTLS item above — author **L1-API-005** ("the service SHALL bound concurrent in-flight RPCs by a configurable global limit; excess SHALL be rejected with `RESOURCE_EXHAUSTED` and an error code identifying the saturation cause") plus L2 derivations covering per-pipeline caps, per-RPC weight (BeginRun is cheap, FinalizeRun triggers assembly), and the rejection-error contract. Until then, a misbehaving pipeline can saturate the shared SQLite connection. Risk accepted in v1 scope.
- **Host-clock validity hardening** — L2-RUN-016 (added in Increment 25d) records v1's assumption that the host clock is monotonically non-decreasing UTC, with backward-correction handling explicitly out of scope. If deployment contexts emerge where backward NTP corrections are expected (VM pause/resume, virtualized environments with imprecise clocks), promote: dual-clock reconciliation (record both `time.monotonic()` and wall-clock per event; cross-check), warn-and-continue on detected backward jumps larger than a configurable threshold, and L3 statements pinning the detection mechanism. The single `Clock` port from L2-RUN-016 is the single chokepoint to make this swap.
- **R-DASH-001 — Role-based access control** — dashboard authentication (L1-AUTH-001) is baseline only; every authenticated user can perform every dashboard action. Future option: roles (viewer, operator, admin) with per-role action gates. Requires a `user_role` column and policy checks in dashboard use cases.
- **R-DASH-002 — Subscription identifier promotion to UUID4** — v1 mints subscription IDs as `INTEGER PRIMARY KEY AUTOINCREMENT` (per L3-DASH-019, reconciled 2026-04-25 to match implementation). Per-user route scoping (L3-DASH-007) prevents cross-user access, but sequential integer IDs leak the system's subscription count to anyone who creates one. Promotion to UUID4 (server-generated `uuid.uuid4()`, stored as TEXT) defends against enumeration as a defense-in-depth measure. Requires: schema migration (new column type + backfill), `SubscriptionId` typedef change to `UUID`, repo + audit + route-validator updates, and an L3 reword back toward UUIDs. Likely paired with the mTLS / gRPC trust-boundary promotion above when the trust model widens beyond the ISOLAN deployment assumption.
- **R-DASH-003 — Audit-log substring search on actor / resource** — v1's `GET /admin/audit` route (per `L2-DASH-015`) supports exact-string matching only on `actor` and `resource`. Exact match covers the common forensic shape ("show me everything `user:5` did", "show me everything against `run:<uuid>`") and is fast against the existing `audit_log` indexes. Substring search (`actor=user:` to find every action by any user) is a useful but more expensive enhancement: it requires a SQL `LIKE` rather than `=`, and may benefit from FTS5 indexing if audit volumes grow. Future work: extend `L2-DASH-015` with a `match_mode` query parameter (default `exact`; opt-in `substring`); evaluate whether v1's index profile remains adequate or whether an FTS5-backed audit search index becomes necessary.
- **R-DASH-004 — Embedded Chart.js metrics dashboard (the visualization half of L1-DASH-004)** — v1 implements only the **scrape-endpoint half** of `L1-DASH-004`: `GET /metrics` returning the standard Prometheus exposition format per `L3-OBS-007` (landed in Increment 20d as a partial). The **embedded-visualization half** — admin route serving an HTML page that fetches `/metrics` server-side, parses the Prometheus text format in JavaScript, and renders Chart.js dashboards (run-state-transition counts, email-delivery outcomes histogram, run duration, email size, sweeper iterations) — is deferred. The deferral has two drivers: (1) the JS code needed (~100-200 lines of frontend doing text-format parsing + Chart.js wiring) is a different shape from the test patterns v1 uses, and doing it well needs a browser-based test harness (Playwright or similar) we don't currently have infrastructure for; (2) the operationally important half is the scrape endpoint — any deployment running Grafana / a Prometheus stack consumes that, and the embedded visualization becomes useful only in deployment shapes without an external Prometheus, which is different from v1's ISOLAN target. Future work, when the test-harness infrastructure is in place: author L3 children for `L2-DASH-010` (server-side fetch implementation) and `L2-DASH-011` (asset bundling) covering route path, response shape, fetch correctness; vendor `chart.min.js` at `src/message_service/interfaces/rest/static/js/chart.min.js` per `L3-DASH-017`; implement `GET /admin/metrics` as a Jinja2-rendered HTML page; gate behind `require_admin` (the same dependency the other admin routes use); add Playwright e2e tests covering the page render + the chart data flow; promote `L1-DASH-004` from Partially Implemented to Implemented. Strictly additive — no existing surface changes; the `/metrics` endpoint stays where it is.
- **R-API-001 — gRPC infrastructure hardening: per-RPC correlation interceptor + CI version-mismatch check** — two related v1 carve-outs. (Item (2) of the original triplet — pinned-tag proto dependency, originally `L3-API-003` — was closed in the v1 release-readiness pass: the proto repo cut a `v0.1.1` tag and `pyproject.toml` switched to `{ git = ..., tag = "v0.1.1" }` with the resolved SHA in `poetry.lock`; conformance test pins the shape.) Remaining items: (1) **Per-RPC structlog interceptor** (originally `L3-API-002`): v1 binds `correlation_id` only on the unexpected-error path (per `L3-ERR-017` / `L3-API-014`). The richer shape — a server-side interceptor that binds a fresh correlation id to the structlog context at every RPC entry (success and failure) and clears it in a `finally` block — is useful when the service participates in a larger distributed tracing context. Likely paired with the `R-OBS-001` distributed-tracing promotion. (3) **CI proto-version-mismatch check** (originally `L3-API-004`): a CI step asserting `message_service_proto.__version__` matches the Poetry-resolved version, gating the build on disagreement. Now that the dependency is tag-pinned the check would be meaningful (not tautological), but the failure mode it guards is small enough relative to the implementation cost to defer to v2. Future work, sequenced together: (a) CI gains a `python -c "import message_service_proto; assert message_service_proto.__version__ == '<expected>'"` step parametrized off the resolved tag; (b) the per-RPC interceptor lands alongside or after the distributed-tracing infrastructure (R-OBS-001). Both trigger off the same operational condition: the gRPC ingress crosses a stable trust boundary with external consumers depending on a versioned wire contract — same trigger as the mTLS / RBAC items.
- **R-ERR-001 — gRPC error response envelope upgrade to `google.rpc.Status` + `ErrorInfo`** — v1's gRPC error translator (`interfaces/grpc/error_mapping.py`) returns `context.abort(status, details=message, trailing_metadata=(("x-message-service-error-code", code),))` per the in-spec L3-ERR-014/015 wording (reworded from earlier draft language that called for the richer envelope). The richer shape — `google.rpc.Status` with a `google.rpc.ErrorInfo` carrying `reason=error_code` and `metadata` from the exception's `details` dict — gives clients more structured access to error metadata but is a wire-format change that breaks already-deployed pipeline clients pinned to the trailing-metadata shape. Future work, when the trust-boundary widens beyond the ISOLAN deployment assumption (paired with the mTLS / RBAC promotion noted elsewhere): switch the translator to construct `google.rpc.Status` and stuff the existing `(error_code, details)` pair into `ErrorInfo`. Strictly additive on the server side; clients reading the existing trailing-metadata key continue to work because the same key can be carried in both shapes during a phased rollout.
- **R-ERR-002 — Error-code stability lockfile + helper script** — v1 holds the error-code-stability obligation through (a) the import-time self-check (`L3-ERR-008`) verifying every exception's `error_code` exists in the proto enum, and (b) the centralized `error_code` ClassVar declarations in `domain/errors.py` that reviewers can diff at PR time. A formal `docs/error-codes.lock` manifest plus the `scripts/check-error-code-stability.py` and `scripts/update-error-codes-lock.py` helpers (per the original L3-ERR-010/011 wording, now reworded as deferred) become useful when external pipelines start pinning specific codes — until that operational pressure exists, the lockfile would be ceremony without payoff. Future work: regenerate the lockfile from the current hierarchy at PR time and gate adds/removes/renames at CI; document the manifest in a `docs/reviews/` review per the existing L3-PERS-023-style pattern.

## Feature extensions

- **Per-pipeline-type orphan policy override** — v1 applies a single global orphan disposition policy. Future work allows per-pipeline overrides of the policy set, with the global policy as fallback.
- **Hot-reload of tag vocabulary** — v1 loads the tag configuration at service start. Hot-reload removes the need for restart to add tags.
- **R-TMPL-002 — Hot-reload of templates** — the template manifest is loaded once at service start (L2-TMPL-001); changes require a restart. Future option: signal-driven reload (`SIGHUP`) that atomically swaps the manifest while in-flight runs continue to render against the old snapshot. Non-trivial: need a template-snapshot token carried through the assembly workflow so `BeginRun` and `FinalizeRun` of the same run see consistent template metadata.
- **R-MAIL-001 — Per-pipeline email subject template** — v1 pins the email Subject header to a single literal format `[{pipeline_type}] run {run_id}` (per `L2-MAIL-014`) with no operator override. Future option: extend `[pipelines.registered.*]` config entries with an optional `email_subject_template_ref` (parallel to the body-template path described in `R-TMPL-001`) so different pipelines can render different subject formats — useful when one service handles pipelines that go to different subscriber audiences with different inbox-filtering needs. Likely implementation: lift the format string into a small Jinja2 template (subject is a single line, so no fancy renderer features needed); the existing `_sanitize_filename_component` defense remains at the post-render boundary. Strictly additive — pipelines without an explicit override fall back to the L2-MAIL-014 default.
- **R-TMPL-001 — Per-pipeline email body template** — the email body template is currently a single service-wide config value (`templates.email_body_template_ref`) used for every finalized run regardless of pipeline. Future option A — per-pipeline config: extend `[pipelines.registered.*]` entries with an optional `email_body_template_ref`; when present, overrides the service-wide default. Backwards-compatible: pipelines without an explicit value fall back to the default. Small schema change; no proto change; no new port. Future option B — per-run declaration: add an optional `email_body_template_ref` field to `BeginRunRequest`. More flexible but requires a proto change, a new field on the `Run` aggregate, additional validation at `BeginRun`, and a schema migration. Consider only if per-pipeline proves insufficient. Either path is additive and will not invalidate existing behavior.
- **R-AGGR-001 — Custom email body contributions from stages** — the email body template currently receives only stage identifiers (`stage_id`, `stage_order`, `had_content`) — not any stage-supplied email body content. `AssembleAndDeliverUseCase` passes a fixed-shape context to `templates.email_body_template_ref`. Specified future behavior: L1-AGGR-001 and L2-AGGR-003 describe a richer model where each `SubmitStageReport` may carry an `email_body_contribution` with a `position` enum (`BEFORE_STAGES_SUMMARY` / `AFTER_STAGES_SUMMARY`), and the assembly process orders contributions accordingly (L3-AGGR-005). The `Stage` aggregate already has an `email_body_context_json` column, so the storage side is ready; the use case just isn't reading it yet. Future work: extend `AssembleAndDeliverUseCase._render_email_body` to read each stage's `email_body_context_json`, group by `position`, and pass the structured payload into the template. Also wire the `position` field through the proto → command → aggregate path. Entirely additive; existing email body templates keep working because the v1 context fields are preserved.
- **Subscription granularity extensions** — beyond `GLOBAL`, `PIPELINE`, `TAG`: consider per-severity, per-submitter, or boolean combinations of existing granularities if use cases emerge.
- **Alternative delivery transports** — v1 delivers via SMTP. Future options include webhooks, direct API hooks into ticketing systems, and Slack/Teams relays.
- **R-DELIVER-002 — Per-subscriber email delivery** — v1 sends one email per run with the recipient list via BCC (adapter-configurable). Future option: one email per subscriber with per-subscriber personalization tokens in the body (`{{subscriber.name}}`, `{{subscriber.unsubscribe_url}}`). Requires per-subscriber rendering and a more involved failure model (one recipient fails, does the whole run fail?). Likely paired with R-DELIVER-001.
- **Streaming gRPC RPCs** — v1 uses unary RPCs only. Two distinct future extensions:
  - Server-streaming `WatchRun` endpoint for live run-progress streaming, if pipeline-side observers ever need it.
  - **R-DELIVER-003 — Streaming `SubmitStageReport`** — server-streaming variant for very large report contributions that exceed unary message size limits (gRPC's default is 4 MiB). Most stages fit comfortably; revisit only if concrete submitters hit the limit.
- **R-OBS-002 — Real-time dashboard updates** — the dashboard polls the REST API for run state. Future option: server-sent events or WebSocket push for instant updates on state transitions. Requires an event-bus abstraction the service doesn't currently have.
- **Custom WAL for in-flight state** — dependent on the profiling item above. Would replace the SQLite-backed in-flight state with an in-memory representation plus an append-only log file.

## Operations

- **High availability and multi-node** — v1 is single-node. Multi-node introduces leader election, shared state, and coordinated orphan sweeping; substantial scope.
- **R-PERS-001 — Cross-host replication** — v1 stores all state on the host running the service. Future option: Litestream-style continuous replication of the SQLite database to a standby host for disaster recovery. Requires a deployment-layer change only; no application code changes. Orthogonal to the outbox pattern (R-DELIVER-001) and to multi-node HA above (which is leader-election rather than DR-replication).
- **R-PERS-002 — Audit log retention pruning** — *(✅ closed by Increment 30, commits `42455b4` / `70eee76` / `62b09c4` / `891ca60` / `bce7479`)*. v1 now ships an `AuditLogPrunerUseCase` driven by `AuditLogPrunerLoop` on the same `BackgroundTaskScheduler` as the orphan sweeper and the rendered-report retention pruner. Configurable via `observability.audit.cleanup_interval_hours` (default 24h) and `observability.audit.cleanup_batch_size` (default 10000); per-tick delete bounded by L3-OBS-016. Sole-deleter conformance enforced via AST/SQL scan at L3-OBS-039; anti-recursion (no audit row for the prune action) at L3-OBS-040. Entry preserved here rather than removed so the closure is traceable from this section.
- **Air-gapped installer bundle** — a single-archive offline installer for ISOLAN deployment that bundles the Poetry-locked dependency tree, NSSM on Windows, and systemd unit on Linux.
- **Backup and restore tooling** — scripts to snapshot and restore the SQLite database and rendered-reports directory as an atomic unit.
- **Audit log archival** — once retention expires (now enforced by the post-Increment-30 pruner), archive rather than delete, to satisfy long-term investigative needs.
- **Metrics dashboard templates** — ship pre-built Grafana dashboards in addition to the embedded in-service visualizations.

## Documentation

- **Architecture decision records (ADRs)** — capture the rationale for significant architectural choices as standalone records in `docs/adr/`, supplementing the Rationale field on individual requirements.
- **Operator runbook** — failure modes, diagnostic procedures, recovery steps for common incidents (SMTP relay down, SQLite corruption, runaway orphan sweeper).
- **Template author guide** — how to add a new template to the manifest, define its JSON Schema, and test it in isolation.
- **Pipeline integration guide** — example code and sequence diagrams for pipeline authors consuming the `message-service-proto` definitions.
