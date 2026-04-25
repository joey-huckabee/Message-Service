# Message-Service — ROADMAP

This document has two parts:

1. **Upcoming v1 increments** — the planned next steps within v1 scope. Order is the current best guess; subject to team re-prioritization.
2. **Deferred from v1** — items explicitly carved out of v1 during requirements elicitation, retained here for the rationale and the trigger that would prompt reconsideration. Items in this section are **not** requirements; promotion to a future release moves them into `docs/L1-REQ.md` with a fresh requirement identifier.

---

## Part 1 — Upcoming v1 increments

Last full increment merged: **13b — SweeperLoop + bootstrap wiring** (commit `7ad1f9a`). The list below is keyed off the still-Draft rows in `docs/TRACE-MATRIX.md` and the empty source/test directories under `src/message_service/interfaces/rest/`, `tests/e2e/`, and `docs/adr/`.

Re-order freely. Each item names the requirement category it closes so trace-matrix impact is visible.

### Increment 14a — Default sweeper config aligned with implemented handlers  *(team-flagged, top priority — small)*

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

### Increment 14b — Sweeper exactly-once: atomic transition + outbox table  *(team-flagged, top priority)*

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

### Increment 14c — Sweeper conformance fixes  *(team-flagged, medium — three small items)*

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

### Increment 14d — Stuck-claim recovery for the sweeper outbox  *(follow-up from 14b.3)*

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

### Increment 14e — Wire `max_candidates_per_iteration` + L2-SWEEP-005 tests  *(team-flagged High; follow-up from 14b)*

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

### Increment 14f — Sweeper boundary alignment: L1↔L3↔SQL all inclusive  *(team-flagged High)*

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

### Increment 14g — Trace-matrix rollup correctness  *(team-flagged Medium)*

**Problem**

`docs/TRACE-MATRIX.md:164-165` shows L1-SWEEP-001 and L1-SWEEP-002 marked **Implemented** while three of their L2 children (L2-SWEEP-001, L2-SWEEP-002, L2-SWEEP-005) are **Draft**. The L1 status is computed independently of child status, so an L1 can claim Implemented despite gaps below it. That makes the matrix unreliable as a release-readiness signal — an Implemented L1 should mean every child is at least Implemented, otherwise the rollup misleads operators and reviewers.

**Work**

1. In `scripts/build-trace-matrix.py`, change the L1 rollup so an L1 is Implemented only if every L2 child is Implemented (or higher). Otherwise it's Draft. Same rule applied to the eventual Verified state once that's wired.
2. Apply the same propagation rule top-to-bottom on regen: L2 → L3 children.
3. Add a status legend update in `TRACE-MATRIX.md`'s preamble explaining the rollup rule so operators reading the matrix understand "Implemented at L1 means every child has at least one verification artifact."
4. Add a unit test under `tests/conformance/` (or under `scripts/`-adjacent tests if any exist) that builds a synthetic L1/L2/L3 graph with a Draft leaf and asserts the L1 root rolls up as Draft, not Implemented.

**Trace impact**: matrix becomes trustworthy. L1-SWEEP-001 / L1-SWEEP-002 / L1-SWEEP-003 will likely flip to Draft until 14e + 14f + a future increment cover the L2-SWEEP-001 / L2-SWEEP-002 children that don't yet have artifacts. That's the *correct* state — the matrix should make the gap visible, not hide it.

**Sequencing**: best to land 14g *after* 14e and 14f so the post-rollup state isn't a confusing flood of regressions in one PR.

### Increment 14h — Implement the unit-test I/O guard  *(team-flagged Medium)*

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

### Increment 25a — Status model overhaul

Combines my "status drift" finding with the team's "Partially Implemented" recommendation.

**Problem**

- All 57 L1, 157 L2, 315 L3 statements still carry `Status: Draft` in the source docs while `TRACE-MATRIX.md` promotes many to `Implemented`. Two sources of truth, drifting apart on every commit.
- The trace matrix's Implemented status is too loose: it fires when *any* test marker exists for the row, including for an L1 whose L2 children are all Draft. (Same root issue as Increment 14g but framed at the model level.)
- No `Partially Implemented` state exists, so a parent with some-but-not-all children done has nowhere accurate to land.

**Work**

1. Add a fourth status value — **`Partially Implemented`** — to the status legend in `TRACE-MATRIX.md` and to the conventions section of L1-REQ.md.
2. Rollup rule (also Increment 14g, but consolidated here): a parent is `Implemented` only if *every* child is `Implemented`. If any child is `Draft`, the parent is `Partially Implemented`. If every child is `Draft`, the parent is `Draft`. Same rule applied L1↔L2↔L3.
3. `scripts/build-trace-matrix.py` becomes the single source of truth for status: it reads `@pytest.mark.requirement` markers, computes leaf-level status, propagates upward, and writes the matrix. Source-doc `Status:` lines either get auto-synced (script also rewrites them) or get dropped from L1/L2/L3 entirely with a note pointing readers at the matrix.
4. Drop `Verification Artifact: (TBD)` lines from L1/L2/L3 — the matrix has the real artifacts. Or auto-sync the same way.
5. Conformance test that the rollup propagation works (covers Increment 14g's test 4 — fold them).

**Sequencing**: largely supersedes Increment 14g; merge them. Recommend doing 25a *before* 14e/14f land so their trace impact is computed under the correct rollup.

### Increment 25b — L1 contradictions and reality drift

Five L1 fixes uncovered in the review.

1. **L1-AGGR-001 vs L1-STAGE-003 contradiction.** AGGR-001 says report contribution is "required" per `SubmitStageReport`; STAGE-003 says a stage may submit no report and no email body content; L2-STAGE-006 confirms STAGE-003. Reword AGGR-001's "required" → "optional" or "two content slots, both of which may be empty," consistent with STAGE-003.
2. **L1-OBS-003 audit scope is too narrow.** It limits the audit log to "successful email deliveries… and failed delivery attempts." The implemented `AuditAction` enum has 14 categories (`BEGIN_RUN`, `SUBMIT_STAGE_REPORT`, `FINALIZE_RUN`, `RUN_STATE_TRANSITION`, `STAGE_STATE_TRANSITION`, `SWEEP_ORPHAN`, `SUBSCRIBE`, `UNSUBSCRIBE`, `CREATE_USER`, `UPDATE_USER`, `LOGIN`, `LOGIN_FAILED`, `LOGOUT`, `SEND_REPORT`). Widen L1-OBS-003 to cover the actual scope; add L2 derivations under it for run-lifecycle, stage-lifecycle, sweeper, subscription, and auth audit categories.
3. **L1-STAGE-001 IN_PROGRESS handling.** L1 lists `IN_PROGRESS` as a regular state; the SQL `CHECK` constraint rejects it; the transition table forbids it; code comments mark it "reserved for v2." Mark `IN_PROGRESS` as explicitly reserved in L1-STAGE-001's SHALL statement and Rationale, so the L1 reads accurately.
4. **L1-SWEEP-003 deferred actions.** L1-SWEEP-003 lists all four disposition actions (`SEND_PARTIAL_FLAGGED`, `DISCARD_SILENTLY`, `NOTIFY_SUBSCRIBERS`, `NOTIFY_ADMINS`) as if all worked. After Increment 14a, only `DISCARD_SILENTLY` and `NOTIFY_ADMINS` are registered; configs that reference the others raise `ConfigurationError` at startup. Two sub-options:
   - **(a)** Annotate L1-SWEEP-003: "v1 implements `DISCARD_SILENTLY` and `NOTIFY_ADMINS`; the other two action ids remain valid in the type but raise `ConfigurationError` at startup until implemented (see ROADMAP)."
   - **(b)** Remove `SEND_PARTIAL_FLAGGED` and `NOTIFY_SUBSCRIBERS` from L1-SWEEP-003 entirely and move them to ROADMAP Part 2.
   - **Plus an explicit L3** (per team request): "Known disposition action identifiers in `DispositionAction` whose handlers are not registered SHALL raise `ConfigurationError` at startup with `details.unregistered_actions` listing the offenders." This pins the runtime behavior 14a delivers.

   Recommendation: **(a) + the new L3.** Keeps the type stable and makes the implementation contract explicit.
5. **L2-STAGE-007 wording vs. implementation** (also team-flagged). L2-STAGE-007 says the sweeper "SHALL classify any stage in state PENDING at orphan-timeout evaluation as missing." The current sweeper only looks at run state, not stage state — the L2 promises a code path that doesn't exist. Two sub-options:
   - **(a) Reword** L2-STAGE-007 to match emergent behavior: "Any run containing PENDING stages at orphan-timeout SHALL be treated according to L1-SWEEP-002's run-level orphan rule." Then no new code; just docs.
   - **(b) Implement** stage-level orphan classification: add L3 statements under L2-SWEEP for "the sweeper SHALL record the list of PENDING stage_ids in the SWEEP_ORPHAN audit details," extend `SqliteRunRepository` to surface them in `list_expired`, and extend the audit details payload accordingly.

   Recommendation: **(b)** if the operator value of "which stages were missing when this orphaned" is high; **(a)** if it isn't. The team's framing leans (b) ("if pending stages are part of orphan classification, add explicit L3 requirements under SWEEP for querying/recording pending stage IDs").

### Increment 25c — Audit-log L2 reference fix in code

Tiny but important. `src/message_service/application/ports/audit_log.py`'s docstring cites `L2-OBS-002, L2-OBS-005` as the audit-contract requirements. Both are mis-references: L2-OBS-002 is about contextvars-based logging-context propagation, L2-OBS-005 is about Prometheus metric naming. Find the right L2 derivations of L1-OBS-003 (post-25b widening, those will exist) and update the docstring's `Requirement references` block. Also audit other ports/use cases for similar drift via grep.

### Increment 25d — Net-new requirements: graceful shutdown, clock validity, report retention

Three areas where the spine has gaps that the implementation either silently assumes or unbounded-grows on:

1. **Graceful shutdown semantics.** Code has `shutdown_grace_period_seconds`, `__main__.py` honors SIGTERM, gRPC server uses bounded `stop(grace=...)`. No L1 or L2 anchors this. Add **L1-DEP-004**: "The service SHALL drain in-flight gRPC RPCs within `service.shutdown_grace_period_seconds` of receiving SIGTERM/SIGINT, after which remaining work is cancelled." Add L2 derivations for signal handling, scheduler `await_all`, and forced cancellation as fallback.
2. **Clock validity.** Every timestamp trusts the host clock; sweeper thresholds, SLA windows, and audit ordering all depend on it. Add **L2-RUN-016** under L1-RUN-005: "Timestamps SHALL be drawn from a monotonically-increasing UTC clock; the service does not detect or compensate for backward host-clock corrections, and behavior under such corrections is unspecified." Or add an explicit L1-OBS-005 if it wants more visibility.
3. **Rendered-report retention.** L1-OBS-003 has retention for the audit log; rendered HTML reports on disk grow forever. Add **L1-PERS-004** (or L1-OBS-005): "Rendered reports SHALL be retained on disk for at least `persistence.filesystem.report_retention_days` (default value TBD by ops); a background pruner SHALL evict reports older than the retention window." Then a future increment implements the pruner.

### Increment 25e — Smaller spec cleanup

Catch-all for the lower-impact items — useful to bundle so they don't get lost.

1. **Rate-limiting decision.** No L1 covers per-pipeline concurrency caps or in-flight RPC limits. Either author L1-API-005 ("the service SHALL bound concurrent in-flight RPCs by a configurable limit; excess SHALL be rejected with `RESOURCE_EXHAUSTED`") or note in ROADMAP Part 2 that v1 deliberately doesn't rate-limit.
2. **L1-MAIL-002 retry formula.** L1 mandates exponential backoff with three knobs but doesn't pin the formula. Add an L3 under L2-MAIL-002 documenting "interval *= 2 each retry, capped at `max_interval_seconds`."
3. **L2-AGGR-009 duplication note.** L2-AGGR-009's Rationale already says "this duplicates the statement here to anchor it under the AGGR category." Convert from a re-statement of L2-RUN-011 to an explicit "see L2-RUN-011" cross-reference so readers don't have to spot the dupe.
4. **Merge "L3-OBS (extension)"** section into L3-OBS proper. Remove the workaround note ("they are grouped separately… for clarity").
5. **L1-CFG-003 enumeration completeness** (folds the team's #C and my #4 beyond what 14e adds). Add to the L1-CFG-003 minimum config keys: `email_body_template_ref`, `pipelines.registered`, `mail.admin_recipients`, `templates.max_context_bytes`/`max_rendered_bytes`, `smtp.use_starttls`, `persistence.connection_pool_size`, `service.shutdown_grace_period_seconds`. (`max_candidates_per_iteration` lands via 14e.)

---

## Cluster 26 — CI/CD requirements + workflows

The team flagged "Full requirements for CICD" as missing, which is true — there's no L1-CICD category, no L2/L3 derivations, and `.github/workflows/` is empty. This cluster authors the requirements then implements them.

### Increment 26a — Author L1-CICD requirements category

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

### Increment 26b — CI/CD workflow implementation

Cash in the L1-CICD requirements as `.github/workflows/ci.yaml`. Matrix (`ubuntu-latest`, `windows-latest`) × (Python `3.12`, `3.13`). Per-job: `poetry install`, `poetry run pre-commit run --all-files`, `poetry run pytest`, `poetry run python scripts/build-trace-matrix.py --check` (new flag — exit non-zero if regenerated matrix differs from committed). Upload `coverage.xml` and `.coverage_html/` as artifacts. Schedule a nightly run on `main` to catch flakes that pass per-PR.

### Increment 26c — Traceability rollup CI gate

Implements **L1-CICD-004** specifically. `scripts/build-trace-matrix.py` gains a `--check` mode that re-derives the matrix in memory, compares against the committed `docs/TRACE-MATRIX.md`, and exits non-zero if they differ OR if any row violates the parent-status-bounded-by-children rule from 25a. Wired into the CI workflow from 26b.

### Increment 26d — Cross-platform pytest hygiene audit

Implements **L1-CICD-001 / L1-CICD-005** specifically. Audit `pyproject.toml`'s `filterwarnings` (currently has `"error"` plus a Google-deprecation ignore) for completeness. Verify `.gitignore` includes `.pytest_tmp/` (likely already does — confirm). Run the suite on Windows with `-W error::ResourceWarning -W error::DeprecationWarning` and fix anything that surfaces. The recent Windows-event-loop work (`tests/conftest.py::_NoImplicitEventLoopPolicy`) suggests this surface is already partly clean, but a deliberate pass is worthwhile.

---

### Increment 15 — Prometheus metrics adapter

Closes **L1-OBS-002, L1-OBS-003** (currently Draft).

- Add `infrastructure/observability/metrics.py` with the counters/histograms named in L2-OBS-004…009 (run-state transitions, stage-submit latency, email size, sweeper rounds).
- Inject through a thin port so domain/application stay framework-free.
- Lifts `error_mapping.py` and `logging_setup.py` out of the 0%-covered gap noted in this file's Part 2.

### Increment 16 — Local-account auth adapter

Closes **L1-AUTH-001, L1-AUTH-002** (Draft). `rest/auth/` is currently empty.

- `argon2-cffi` password hasher (already a dependency).
- `User` aggregate, `UserRepository` port, SQLite adapter + new migration.
- Session-cookie + CSRF middleware.
- Local accounts only — LDAP/OIDC stay in Part 2.

### Increment 17 — FastAPI app factory + bootstrap wiring

`rest/routes/` is empty; `__main__.py` only spins up the gRPC server.

- `interfaces/rest/app.py` builds the FastAPI instance from `Service`.
- `__main__.py` runs uvicorn alongside `grpc.aio` under one shutdown event.
- No domain routes yet — chassis + login flow only.

### Increment 18 — Subscription management routes

Closes **L1-DASH-001, L1-SUB-002** (Draft).

- CRUD over `SqliteSubscriptionRepository` for the existing GLOBAL/PIPELINE/TAG granularity.
- Jinja screens under `rest/html/templates/`.

### Increment 19 — Past-runs list, report viewer, resend

Closes **L1-DASH-002, L1-DASH-003** (Draft).

- Paginated runs page.
- Stage-by-stage report viewer reading from the filesystem report store.
- Resend action that re-queues rendered reports through the `Mailer` port.
- Fills in `tests/e2e/resend/`.

### Increment 20 — Admin surfaces

Closes **L1-DASH-004** (Draft).

- User management, audit-log viewer, template inspection — all read-mostly over existing adapters.
- Gate behind an `is_admin` flag on `User`.

### Increment 21 — E2E happy-path + orphan-path harness

`tests/e2e/{happy_path,admin,orphan_path,resend}/` currently contain only `__init__.py`.

- Stand up the `running_service` fixture sketched in `tests/README.md` (real `grpc.aio` + httpx + tmp SQLite + `aiosmtpd`).
- BeginRun → submissions → FinalizeRun → email path.
- Sweeper-fires-and-disposes path.
- Moves a wave of L2 rows from "Implemented" to "Verified".

### Increment 22 — Error-mapping + servicer tests, exception-detail coverage

Closes **L1-ERR-001..004** (all Draft).

- Unit tests for `interfaces/grpc/error_mapping.py` (translation table, trailing-metadata population).
- `details=` assertions across the use-case raise sites.

### Increment 23 — Deployment polish

Closes **L1-DEP-001, L1-DEP-003** (Draft). The `deploy/` placeholders need to be finished.

- systemd unit env-var passthrough.
- NSSM Windows install script.
- Graceful-shutdown verification artifact tied to existing `__main__.py` signal handling.
- A minimal `.github/workflows/ci.yaml` — the directory exists but is empty.

### Increment 24 — Documentation deliverables (release-gating)

- Promote `tests/README.md` into the formal **Test strategy document** listed in Part 2.
- First two ADRs into `docs/adr/`: SQLite-for-in-flight-state, hexagonal boundary enforcement.
- **Operator runbook** + **Pipeline integration guide** drafts.
- All four are explicit Part 2 items; tagging v1 should retire them.

### Cross-cutting tradeoffs

**Cluster 14 (sweeper correctness)**

- 14a/14b/14c.1/14c.2 have already landed (commits `04a88dc`, `460d127`, `7c33c87`, `3b48d38`, `5456f2e`, `9b28e2b`, `3fd0673`); 14c.3 became unnecessary once 14b.3's dispatcher fetches the post-transition aggregate.
- 14d (stuck-claim recovery) is benign for v1 idempotent handlers but should land before tagging v1.
- 14e (`max_candidates_per_iteration`) is a real availability risk under backlog. 14f (boundary alignment across L1↔L3↔SQL) is small.
- 14g (trace-matrix rollup) is largely **superseded by 25a** — fold them.
- 14h (I/O guard) is independent and can slot anywhere; the test-relocation half is the larger piece.

**Cluster 25 (requirements spec cleanup)**

- Should land **before** Cluster 15+ feature work. Every new feature increment otherwise compounds the spec drift identified by both the team review and the requirements doc audit.
- 25a (status model + Partially Implemented) is the highest-leverage single edit — it makes the matrix trustworthy and fixes the rollup logic in one pass. Fold 14g into it.
- 25b–25e are mostly docs-only; can land in any order. The L1-SWEEP-003 deferred-actions split (25b.4) and the new L3 for known-but-unregistered → ConfigurationError directly pin behavior 14a already delivers — quick win.

**Cluster 26 (CI/CD)**

- 26a (author L1-CICD requirements) blocks 26b/26c/26d. The L1 statements need spec review before workflows are written against them.
- 26c (traceability gate) depends on 25a's rollup rule being settled. Sequence 25a → 26a → 26c.
- 26b (workflow implementation) can run in parallel with 25b–25e since it touches different files entirely.
- 26d (ResourceWarnings hygiene) is dependent on 26b being in place to actually run the matrix; the audit can begin sooner against local Windows runs.

**Cluster 15+ (features)**

- Increments 16–20 form one feature stream (dashboard). Interleaving with 15 / 22 keeps category coverage balanced.
- Increment 15 (metrics) can move earlier if you want OBS rows green before introducing new untested surface.
- Increment 21 (E2E) can shift earlier — running it after Increment 17 instead of 20 forces the FastAPI chassis to stay testable as routes accrete.

**Recommended overall sequencing (refreshed)**

1. **25a** (status model) — single highest-leverage cleanup; makes the rest of the matrix work meaningful.
2. **14f** (boundary alignment) — smallest sweeper fix, pins L1↔L3↔SQL.
3. **14e** (`max_candidates_per_iteration`) — closes the second High and feeds new entries into 25b's L1-CFG-003 expansion.
4. **25b–25e** in parallel where possible — most are docs-only.
5. **26a** — author L1-CICD now that the status model is settled.
6. **26b/26c/26d** in parallel — implement CI workflows + gates + hygiene.
7. **14d, 14h** — sweeper polish that can slot in any time after 14b landed.
8. **15** (Prometheus metrics) — first feature increment, with the spec spine no longer drifting.
9. **16–24** as previously sequenced.

---

## Part 2 — Deferred from v1

## Testing and verification

- **Test strategy document** — a top-level document covering unit test conventions, integration test harness for gRPC and FastAPI, end-to-end run-simulation fixtures, orphan-path test harness, and SMTP sandbox configuration. (Partially superseded by `tests/README.md`; still to be promoted to a formal top-level doc.)
- ~~**pytest marker auto-extraction tool**~~ — **Done.** `scripts/build-trace-matrix.py` now scans `@pytest.mark.requirement` markers and auto-populates `docs/TRACE-MATRIX.md`.
- **Coverage ratchet** — the pytest gate is currently set at **60%** in `pyproject.toml` to allow progress while `src/message_service/interfaces/grpc/error_mapping.py` and `src/message_service/observability/logging_setup.py` still lack tests (they are 0% covered). Ratchet the gate to **75%** once those two modules have unit tests, and to **85%** once the SQLite adapter has integration coverage.
- **Coverage enforcement** — CI gate requiring every approved L1 requirement to have at least one linked verification artifact before release. (The `--cov-fail-under` gate enforces aggregate coverage; requirement-level coverage tracking is the separate item.)

## Performance and profiling

- **In-flight run state backing profiling** — v1 co-locates in-flight run state in SQLite, relying on SQLite's built-in WAL journal for durability. If profiling later shows SQLite write latency is a bottleneck on the gRPC ingest hot path, evaluate an in-memory store with a custom write-ahead log. The repository-pattern abstraction (L1-PERS-003) makes this swap possible without touching domain code.
- **Email size distribution analysis** — once the Prometheus email-size histogram has collected production data, analyze for patterns that would justify per-pipeline-type size limits or automatic compression strategies.

## Security hardening

- **Mutual TLS on gRPC** — v1 uses plaintext TCP on the trusted ISOLAN network. Promote when gRPC ingest crosses trust boundaries or when compliance requirements demand transport encryption.
- **Additional authentication backends** — LDAP/AD and OIDC. Current scope is local accounts only. LDAP integration is the likely first addition, consistent with broader ISOLAN architecture patterns.
- **Secrets handling review** — SMTP credentials and any future API keys currently live in the TOML configuration file. Consider integration with Vault CE if secret rotation becomes operationally significant.

## Feature extensions

- **Per-pipeline-type orphan policy override** — v1 applies a single global orphan disposition policy. Future work allows per-pipeline overrides of the policy set, with the global policy as fallback.
- **Hot-reload of tag vocabulary** — v1 loads the tag configuration at service start. Hot-reload removes the need for restart to add tags.
- **Hot-reload of templates** — v1 ships templates with the code and loads them at service start. Hot-reload would allow operators to roll out new template versions without restart.
- **Subscription granularity extensions** — beyond `GLOBAL`, `PIPELINE`, `TAG`: consider per-severity, per-submitter, or boolean combinations of existing granularities if use cases emerge.
- **Alternative delivery transports** — v1 delivers via SMTP. Future options include webhooks, direct API hooks into ticketing systems, and Slack/Teams relays.
- **Streaming gRPC RPCs** — v1 uses unary RPCs only. If live run-progress streaming becomes a pipeline-side need, add a server-streaming `WatchRun` endpoint.
- **Custom WAL for in-flight state** — dependent on the profiling item above. Would replace the SQLite-backed in-flight state with an in-memory representation plus an append-only log file.

## Operations

- **High availability and multi-node** — v1 is single-node. Multi-node introduces leader election, shared state, and coordinated orphan sweeping; substantial scope.
- **Air-gapped installer bundle** — a single-archive offline installer for ISOLAN deployment that bundles the Poetry-locked dependency tree, NSSM on Windows, and systemd unit on Linux.
- **Backup and restore tooling** — scripts to snapshot and restore the SQLite database and rendered-reports directory as an atomic unit.
- **Audit log archival** — once retention expires, archive rather than delete, to satisfy long-term investigative needs.
- **Metrics dashboard templates** — ship pre-built Grafana dashboards in addition to the embedded in-service visualizations.

## Documentation

- **Architecture decision records (ADRs)** — capture the rationale for significant architectural choices as standalone records in `docs/adr/`, supplementing the Rationale field on individual requirements.
- **Operator runbook** — failure modes, diagnostic procedures, recovery steps for common incidents (SMTP relay down, SQLite corruption, runaway orphan sweeper).
- **Template author guide** — how to add a new template to the manifest, define its JSON Schema, and test it in isolation.
- **Pipeline integration guide** — example code and sequence diagrams for pipeline authors consuming the `message-service-proto` definitions.
