# Message-Service — Level 2 Requirements

## Purpose

This document establishes the Level 2 (L2) SHALL-statement requirements for the Message-Service project. L2 requirements are architectural derivations of the L1 requirements documented in `L1-REQ.md`: they specify *how* each L1 obligation is structurally satisfied, without yet prescribing implementation details (those belong to L3).

Every L2 requirement traces to exactly one L1 parent. L3 requirements will derive from these L2s.

## Conventions

L2 identifiers follow the format `L2-<CATEGORY>-<NNN>`. Each L2 declares its parent L1 explicitly. Metadata fields (Statement, Rationale, Verification Method) carry the same semantics as in `L1-REQ.md`.

L2s are organized by category, with L1 parents shown as subsection headers to make the trace visible at reading time. Full forward trace tables appear in `TRACE-MATRIX.md`.

**Status and verification artifacts** are tracked in
[`docs/TRACE-MATRIX.md`](TRACE-MATRIX.md), regenerated from
`@pytest.mark.requirement` markers and parent links by
`scripts/build-trace-matrix.py`. Per Increment 25a, the matrix is the
single source of truth for live status.

## Table of categories

| Code      | Title                                  | L2 Count |
|-----------|----------------------------------------|----------|
| `API`     | gRPC interface                         | 11       |
| `RUN`     | Run lifecycle                          | 16       |
| `STAGE`   | Stage lifecycle and idempotency        | 9        |
| `TMPL`    | Template governance and sandboxing     | 14       |
| `AGGR`    | Aggregation and composition            | 10       |
| `SWEEP`   | Orphan detection and disposition       | 10       |
| `SUB`     | Subscriptions and tags                 | 10       |
| `AUTH`    | Authentication                         | 6        |
| `MAIL`    | Email delivery                         | 13       |
| `DASH`    | Dashboard                              | 14       |
| `PERS`    | Persistence                            | 13       |
| `OBS`     | Observability                          | 18       |
| `ERR`     | Error handling and exception taxonomy  | 10       |
| `CFG`     | Configuration                          | 8        |
| `DEP`     | Deployment                             | 9        |
| `CICD`    | Continuous integration and delivery    | 15       |
| **Total** |                                        | **186**  |

---

## L2-API: gRPC interface

### Derivations of L1-API-001 (expose MessageService protocol)

#### L2-API-001

**Parent**: L1-API-001
**Statement**: The gRPC server SHALL be instantiated as a `grpc.aio.Server` to support asynchronous request handling consistent with the service's asyncio model.
**Rationale**: Asyncio alignment with FastAPI and the orphan sweeper keeps the event loop single and avoids thread-pool coordination.
**Verification Method**: Inspection (I)

#### L2-API-002

**Parent**: L1-API-001
**Statement**: The `message-service-proto` package SHALL be declared as a Poetry dependency referencing the external repository, with pinned version resolution at build time.
**Rationale**: Pinned external references make builds reproducible for air-gapped deployment.
**Verification Method**: Inspection (I)

#### L2-API-003

**Parent**: L1-API-001
**Statement**: The service SHALL register exactly three unary methods on the servicer: `BeginRun`, `SubmitStageReport`, and `FinalizeRun`.
**Rationale**: Explicit enumeration prevents accidental exposure of methods not reviewed against the API surface policy.
**Verification Method**: Inspection (I), Test (T)

### Derivations of L1-API-002 (unary only)

#### L2-API-004

**Parent**: L1-API-002
**Statement**: Servicer methods SHALL be implemented with the `async def` signature corresponding to gRPC unary-unary RPCs.
**Rationale**: The async unary-unary pattern is the v1 contract shape; streaming signatures are a ROADMAP item.
**Verification Method**: Inspection (I)

#### L2-API-005

**Parent**: L1-API-002
**Statement**: The servicer SHALL NOT inherit or expose any streaming RPC stubs generated from the proto file, even if the proto file defines them in a future version.
**Rationale**: Defense-in-depth against accidental exposure through upstream proto changes.
**Verification Method**: Inspection (I)

### Derivations of L1-API-003 (plaintext TCP)

#### L2-API-006

**Parent**: L1-API-003
**Statement**: The gRPC server SHALL be started using `add_insecure_port()` with no channel credentials configured.
**Rationale**: Explicit insecure-port binding is the v1 transport posture; a future mTLS path will use `add_secure_port()` with credentials loaded from config.
**Verification Method**: Inspection (I), Test (T)

#### L2-API-007

**Parent**: L1-API-003
**Statement**: The gRPC listen address and port SHALL be read from configuration keys `grpc.host` and `grpc.port` at service startup.
**Rationale**: Explicit config keys keep network configuration inspectable and changeable without code changes.
**Verification Method**: Test (T)

### Derivations of L1-API-004 (structured error codes)

#### L2-API-008

**Parent**: L1-API-004
**Statement**: Validation failures SHALL be mapped to gRPC status code `INVALID_ARGUMENT` with a structured detail message containing a machine-readable error code and a human-readable description.
**Rationale**: Distinguishing validation errors from other failures allows clients to handle recoverable input errors differently from server-side failures.
**Verification Method**: Test (T)

#### L2-API-009

**Parent**: L1-API-004
**Statement**: References to unknown runs SHALL be mapped to gRPC status code `NOT_FOUND` with a structured detail message.
**Rationale**: `NOT_FOUND` is the semantically correct status for missing resources and enables standard client-side retry decisions.
**Verification Method**: Test (T)

#### L2-API-010

**Parent**: L1-API-004
**Statement**: Unexpected internal errors SHALL be mapped to gRPC status code `INTERNAL` with a sanitized detail message containing a correlation identifier, and SHALL NOT include the originating exception type or stack trace.
**Rationale**: Correlation identifiers allow operators to find the full internal log record without exposing implementation details to clients.
**Verification Method**: Test (T), Inspection (I)

#### L2-API-011

**Parent**: L1-API-004
**Statement**: The set of machine-readable error codes SHALL be defined as an enumeration in the proto file and maintained as a single source of truth for both client and server.
**Rationale**: Co-locating error codes with the proto definition prevents client/server drift.
**Verification Method**: Inspection (I)

---

## L2-RUN: Run lifecycle

### Derivations of L1-RUN-001 (UUID v4 run_id minting)

#### L2-RUN-001

**Parent**: L1-RUN-001
**Statement**: Run identifiers SHALL be generated using the Python standard library `uuid.uuid4()` function.
**Rationale**: The stdlib implementation is cryptographically random and does not require additional dependencies.
**Verification Method**: Inspection (I)

#### L2-RUN-002

**Parent**: L1-RUN-001
**Statement**: Run identifiers SHALL be serialized as the canonical 36-character hyphenated hexadecimal form (8-4-4-4-12) in all protocol messages and persistent storage.
**Rationale**: A single representation avoids format-conversion bugs and makes log-searching consistent.
**Verification Method**: Test (T)

#### L2-RUN-003

**Parent**: L1-RUN-001
**Statement**: The minted `run_id` SHALL be persisted to the run repository before the `BeginRunResponse` message is returned to the client.
**Rationale**: Persisting before response guarantees that a client receiving a success response can subsequently reference the run.
**Verification Method**: Test (T)

### Derivations of L1-RUN-002 (run state machine)

#### L2-RUN-004

**Parent**: L1-RUN-002
**Statement**: The run state machine's permitted transitions SHALL be defined as an explicit transition table in the domain layer, with the following transitions permitted and no others: `INITIATED → AGGREGATING`, `AGGREGATING → READY`, `READY → SENDING`, `SENDING → SENT`, `SENDING → FAILED`, and any non-terminal state to `ORPHANED` or `FAILED`.
**Rationale**: An explicit transition table is inspectable, testable, and forms the basis for L3 derivation.
**Verification Method**: Test (T), Analysis (A)

#### L2-RUN-005

**Parent**: L1-RUN-002
**Statement**: Any attempted transition not present in the transition table SHALL raise an `InvalidStateTransition` domain exception.
**Rationale**: Domain exceptions for illegal transitions prevent silent state corruption.
**Verification Method**: Test (T)

#### L2-RUN-006

**Parent**: L1-RUN-002
**Statement**: The terminal states `SENT`, `FAILED`, and `ORPHANED` SHALL reject all outgoing transitions by raising `InvalidStateTransition`.
**Rationale**: Terminal states represent the end of a run's lifecycle; further transitions would indicate a logic error.
**Verification Method**: Test (T)

### Derivations of L1-RUN-003 (validate BeginRun fields)

#### L2-RUN-007

**Parent**: L1-RUN-003
**Statement**: The `pipeline_type` field SHALL be validated against a configured pipeline registry loaded at service startup, with unknown values rejected by error code `UNKNOWN_PIPELINE_TYPE`.
**Rationale**: A registry-based approach allows operators to control which pipelines the service accepts from without code changes.
**Verification Method**: Test (T)

#### L2-RUN-008

**Parent**: L1-RUN-003
**Statement**: Each tag in the `run_tags` array SHALL be validated against the configured tag vocabulary, with unknown tags rejected by error code `UNKNOWN_TAG`.
**Rationale**: Tag validation at the entry point keeps invalid tags out of the subscription-matching system.
**Verification Method**: Test (T)

#### L2-RUN-009

**Parent**: L1-RUN-003
**Statement**: The `declared_stages` array SHALL be validated for uniqueness of `stage_id` values within the request, with duplicates rejected by error code `DUPLICATE_STAGE_ID`.
**Rationale**: Duplicate stage identifiers would make the per-stage state machine semantics ambiguous.
**Verification Method**: Test (T)

#### L2-RUN-010

**Parent**: L1-RUN-003
**Statement**: Every `template_name` and `template_version` referenced in the request (including stage templates, aggregation template, and email body template) SHALL be validated against the template manifest at initiation time, with unknown references rejected by error code `UNKNOWN_TEMPLATE`.
**Rationale**: Up-front template validation prevents late failures during assembly after stages have already submitted content.
**Verification Method**: Test (T)

#### L2-RUN-011

**Parent**: L1-RUN-003
**Statement**: When `attachment_mode` is `SINGLE_AGGREGATED`, the request SHALL be validated for presence of a non-empty `aggregation_template` field, with omissions rejected by error code `MISSING_AGGREGATION_TEMPLATE`.
**Rationale**: See L1-AGGR-004 — fail early rather than at send time.
**Verification Method**: Test (T)

### Derivations of L1-RUN-004 (FinalizeRun RPC)

#### L2-RUN-012

**Parent**: L1-RUN-004
**Statement**: `FinalizeRun` SHALL reject calls against runs not currently in the `AGGREGATING` state with gRPC status code `FAILED_PRECONDITION` and a structured error code identifying the current state.
**Rationale**: Strict precondition enforcement prevents double-finalization races and out-of-order lifecycle transitions.
**Verification Method**: Test (T)

#### L2-RUN-013

**Parent**: L1-RUN-004
**Statement**: `FinalizeRun` SHALL enqueue the assembly and delivery workflow as an asyncio task and return to the client without blocking on the workflow's completion.
**Rationale**: Non-blocking finalization keeps the RPC latency bounded and allows the pipeline to terminate promptly.
**Verification Method**: Test (T)

### Derivations of L1-RUN-005 (record state transitions)

#### L2-RUN-014

**Parent**: L1-RUN-005
**Statement**: State transition timestamps SHALL be UTC and acquired from an injected clock abstraction (rather than a direct `datetime.now()` call) to support deterministic testing.
**Rationale**: Clock injection is a standard pattern for time-sensitive testability without giving up precision in production.
**Verification Method**: Inspection (I), Test (T)

#### L2-RUN-015

**Parent**: L1-RUN-005
**Statement**: State transition records SHALL be written through the audit repository before the new state is persisted to the run repository; a failure to write the audit record SHALL abort the transition.
**Rationale**: Audit-first ordering guarantees that every state visible in the run repository has a corresponding audit record.
**Verification Method**: Test (T)

#### L2-RUN-016

**Parent**: L1-RUN-005
**Statement**: All timestamps recorded by the service — run/stage transition `updated_at`, audit-log `timestamp`, sweeper cutoff arithmetic, retention thresholds — SHALL be drawn from a single injected `Clock` port (`application/ports/clock.py`). The service SHALL assume the host clock is monotonically non-decreasing UTC under normal operation; behavior under backward host-clock corrections (NTP step, VM pause, manual `date` change) is unspecified for v1. The `Clock` port encapsulates all reads of the host clock, so swapping in a synthetic clock for tests is the only mechanism through which timestamp behavior may legitimately differ from production.
**Rationale**: Centralizing every clock read behind one port makes the assumption explicit, makes deterministic testing possible (`FakeClock`), and gives a single chokepoint to revisit if the assumption needs to be relaxed (e.g., monotonic-only timestamps via `time.monotonic()` for SLA windows). v1 explicitly deems backward-correction handling out of scope to avoid the complexity of dual-clock reconciliation; ROADMAP captures the eventual hardening if the trusted-host assumption is later relaxed.
**Verification Method**: Test (T), Inspection (I)

---

## L2-STAGE: Stage lifecycle and idempotency

### Derivations of L1-STAGE-001 (stage state machine)

#### L2-STAGE-001

**Parent**: L1-STAGE-001
**Statement**: Stage state records SHALL be persisted one per (run_id, stage_id) tuple, with the state value constrained to the enumerated set `PENDING`, `IN_PROGRESS`, `SUBMITTED`, `ACCEPTED`, `RETRIED`, `TIMEOUT`, `FAILED`.
**Rationale**: Per-stage records support fine-grained orphan detection and audit.
**Verification Method**: Inspection (I), Test (T)

#### L2-STAGE-002

**Parent**: L1-STAGE-001
**Statement**: Permitted stage transitions in v1 SHALL be exactly: `PENDING → SUBMITTED`, `SUBMITTED → RETRIED`, `RETRIED → RETRIED`, any of `{SUBMITTED, RETRIED}` to `ACCEPTED`, any non-terminal state to `TIMEOUT`, and any non-terminal state to `FAILED`. The `IN_PROGRESS` state SHALL be reserved for a future stage-heartbeat mechanism and SHALL NOT be entered by any transition in v1.
**Rationale**: Explicitly reserving IN_PROGRESS preserves forward compatibility with the ROADMAP heartbeat RPC without using the state prematurely.
**Verification Method**: Test (T), Analysis (A)

### Derivations of L1-STAGE-002 (idempotency)

#### L2-STAGE-003

**Parent**: L1-STAGE-002
**Statement**: Stage contribution content SHALL be stored keyed on the composite `(run_id, stage_id)` primary key, with at most one record per key.
**Rationale**: A single-record-per-key model makes overwrite semantics trivial and matches the idempotency requirement directly.
**Verification Method**: Inspection (I)

#### L2-STAGE-004

**Parent**: L1-STAGE-002
**Statement**: A second or subsequent `SubmitStageReport` call for the same `(run_id, stage_id)` SHALL atomically replace the prior contribution record and transition the stage to `RETRIED`, with the prior content discarded.
**Rationale**: Atomic replacement eliminates the possibility of a partial update being visible to the assembly stage.
**Verification Method**: Test (T)

#### L2-STAGE-005

**Parent**: L1-STAGE-002
**Statement**: Idempotent overwrite SHALL apply to both the report contribution and the optional email body contribution slots independently; an omitted email body contribution in a subsequent submission SHALL clear any previously recorded email body contribution.
**Rationale**: Treating the two slots independently preserves pipeline authors' ability to remove contributions by resubmitting without them.
**Verification Method**: Test (T)

### Derivations of L1-STAGE-003 (explicit submission required)

#### L2-STAGE-006

**Parent**: L1-STAGE-003
**Statement**: `SubmitStageReport` calls with empty report contribution and no email body contribution SHALL be accepted as valid submissions that transition the stage out of `PENDING`.
**Rationale**: Allowing empty submissions lets pipelines signal "this stage ran and deliberately produced nothing" — distinct from "this stage never ran."
**Verification Method**: Test (T)

#### L2-STAGE-007

**Parent**: L1-STAGE-003
**Statement**: A run that orphans (per L1-SWEEP-002's run-level timeout) and that has one or more stages still in state `PENDING` SHALL have those PENDING stage ids recorded in the `SWEEP_ORPHAN` audit record (see L3-STAGE-013), so operators investigating the orphan can identify which stages were the cause. The run itself is treated according to the orphan disposition policy regardless of whether any stages were PENDING; the per-stage capture is for forensics, not for separate per-stage classification.
**Rationale**: The sweeper's definition of "missing" is the contrapositive of L1-STAGE-003's explicit-submission obligation. Capturing pending stage ids inside the audit record (rather than implementing a separate stage-level orphan code path) keeps the orphan detection model simple — there is exactly one orphan-detection signal (run-level transition timeout), with stage-level detail surfaced as audit metadata.
**Verification Method**: Test (T)

### Derivations of L1-STAGE-004 (reject unknown stage_id)

#### L2-STAGE-008

**Parent**: L1-STAGE-004
**Statement**: A `SubmitStageReport` call whose `stage_id` does not appear in the run's declared stages SHALL be rejected with gRPC status `INVALID_ARGUMENT` and error code `UNKNOWN_STAGE`.
**Rationale**: Declared-stage validation at submission time prevents unauthorized extension of a run's scope mid-flight.
**Verification Method**: Test (T)

#### L2-STAGE-009

**Parent**: L1-STAGE-004
**Statement**: A `SubmitStageReport` call whose `run_id` does not correspond to a known run SHALL be rejected with gRPC status `NOT_FOUND` and error code `RUN_NOT_FOUND`, taking precedence over stage-ID validation.
**Rationale**: `NOT_FOUND` is the correct status for a missing parent resource; stage-ID validation is meaningless without a valid run.
**Verification Method**: Test (T)

---

## L2-TMPL: Template governance and sandboxing

### Derivations of L1-TMPL-001 (manifest-based discovery)

#### L2-TMPL-001

**Parent**: L1-TMPL-001
**Statement**: The template manifest SHALL be a TOML file whose path is provided via the configuration key `templates.manifest_path`.
**Rationale**: TOML parses strictly and matches the project-wide configuration format.
**Verification Method**: Inspection (I)

#### L2-TMPL-002

**Parent**: L1-TMPL-001
**Statement**: Each manifest entry SHALL declare the fields `name`, `version`, `source_path`, and `schema_path`, with all paths resolved relative to the manifest file's directory.
**Rationale**: Relative path resolution keeps the manifest self-contained and portable across deployment environments.
**Verification Method**: Test (T)

#### L2-TMPL-003

**Parent**: L1-TMPL-001
**Statement**: The service SHALL load and validate all manifest entries at startup, and SHALL fail to start if any entry is malformed, refers to a missing source or schema file, or declares a duplicate `(name, version)` pair.
**Rationale**: Startup-time validation surfaces template configuration errors before any pipeline attempts to use the service.
**Verification Method**: Test (T)

### Derivations of L1-TMPL-002 (name + version required)

#### L2-TMPL-004

**Parent**: L1-TMPL-002
**Statement**: Template version strings SHALL be parsed and compared using `packaging.version.Version` from the `packaging` library.
**Rationale**: The `packaging` library implements the canonical Python semver semantics.
**Verification Method**: Inspection (I), Test (T)

#### L2-TMPL-005

**Parent**: L1-TMPL-002
**Statement**: Resolution of the `"latest"` version sentinel SHALL occur at `BeginRun` initiation time, not at render time, and SHALL select the highest semver among all manifest entries sharing the requested template name.
**Rationale**: Early resolution guarantees all stages in a run use the same resolved version, even if the manifest is updated mid-run.
**Verification Method**: Test (T)

#### L2-TMPL-006

**Parent**: L1-TMPL-002
**Statement**: The resolved template version SHALL be recorded with the run state and included in the audit log for each rendered report.
**Rationale**: Version traceability is essential for reproducing past reports and diagnosing template-related issues.
**Verification Method**: Test (T)

### Derivations of L1-TMPL-003 (SandboxedEnvironment)

#### L2-TMPL-007

**Parent**: L1-TMPL-003
**Statement**: The `SandboxedEnvironment` SHALL be instantiated with an explicit `globals` mapping containing only the whitelisted entries; the default Jinja2 globals SHALL NOT be extended.
**Rationale**: Starting from an empty globals map reduces the attack surface to what is explicitly permitted.
**Verification Method**: Inspection (I), Test (T)

#### L2-TMPL-008

**Parent**: L1-TMPL-003
**Statement**: The filter whitelist SHALL permit only the safe built-in filters `escape`, `safe`, `length`, `default`, `upper`, `lower`, `title`, `trim`, `replace`, `join`, and `format`; all other built-in and custom filters SHALL be removed from the environment.
**Rationale**: An explicit permit-list is more auditable than a block-list of dangerous filters.
**Verification Method**: Inspection (I), Test (T)

#### L2-TMPL-009

**Parent**: L1-TMPL-003
**Statement**: The `undefined` parameter SHALL be set to `jinja2.StrictUndefined`, causing any reference to an unbound variable to raise `jinja2.UndefinedError` during rendering.
**Rationale**: Strict undefined handling prevents silent production of blank output due to missing context keys.
**Verification Method**: Test (T)

### Derivations of L1-TMPL-004 (JSON Schema validation)

#### L2-TMPL-010

**Parent**: L1-TMPL-004
**Statement**: JSON Schema validation SHALL use the `jsonschema` Python library with Draft 2020-12 semantics or later.
**Rationale**: Draft 2020-12 is the current standard and supports the full feature set likely to be needed by template authors.
**Verification Method**: Inspection (I)

#### L2-TMPL-011

**Parent**: L1-TMPL-004
**Statement**: Schema validation failures SHALL be returned as gRPC status `INVALID_ARGUMENT` with error code `CONTEXT_SCHEMA_VIOLATION` and a detail message containing the JSON Pointer path to the failing element.
**Rationale**: JSON Pointer paths allow template authors to diagnose schema violations without guessing.
**Verification Method**: Test (T)

### Derivations of L1-TMPL-005 (size limits)

#### L2-TMPL-012

**Parent**: L1-TMPL-005
**Statement**: Submitted context size SHALL be measured as the byte length of the canonical JSON serialization of the context dictionary.
**Rationale**: A canonical serialization ensures size measurements are deterministic across clients and server.
**Verification Method**: Test (T)

#### L2-TMPL-013

**Parent**: L1-TMPL-005
**Statement**: Rendered output size SHALL be measured as the UTF-8 byte length of the rendered string after template evaluation completes.
**Rationale**: UTF-8 byte length matches how the rendered output will be transmitted in SMTP and stored on disk.
**Verification Method**: Test (T)

#### L2-TMPL-014

**Parent**: L1-TMPL-005
**Statement**: The context size limit and the rendered output size limit SHALL be controlled by independent configuration keys `templates.max_context_bytes` and `templates.max_rendered_bytes`.
**Rationale**: Independent control allows operators to tune each limit without side effects on the other.
**Verification Method**: Inspection (I)

#### L2-TMPL-015

**Parent**: L1-TMPL-001
**Statement**: The email body template rendered for a finalized run SHALL default to the service-wide `templates.email_body_template_ref`. An operator MAY override it per pipeline via the optional `pipelines.email_body_template_overrides` configuration mapping (`pipeline_type` → a `(name, version)` template reference): when the run's `pipeline_type` has an entry, assembly SHALL render the email body from the override reference, and otherwise from the service-wide default. Each override reference SHALL name a `(name, version)` pair present in the template manifest — validated at startup per L1-TMPL-001, failing service start when a referenced template is absent — and each key SHALL be a member of `pipelines.registered`. Override references use the same explicit-version model as the service-wide default (the `"latest"` sentinel is resolved only for request-supplied refs at `BeginRun` initiation per L2-TMPL-007, not for the service-configured body template).
**Rationale**: Different pipelines often want visually distinct notification emails; a per-pipeline override keeps that operator-configurable without a proto change or a per-run declaration. Reusing the manifest-reference model preserves L1-TMPL-001's guarantee that every rendered template is a manifest-registered one, and startup validation surfaces a misconfigured override before any run is finalized. Additive: pipelines without an override are byte-identical to the prior single-template behavior.
**Verification Method**: Test (T), Inspection (I)

---

## L2-AGGR: Aggregation and composition

### Derivations of L1-AGGR-001 (two-slot submission)

#### L2-AGGR-001

**Parent**: L1-AGGR-001
**Statement**: The report contribution slot SHALL be represented in the proto as a required sub-message containing `template_name`, `template_version`, and `context`, where `context` is a structured map type.
**Rationale**: Required sub-message semantics allow protobuf-level validation of the report slot.
**Verification Method**: Inspection (I)

#### L2-AGGR-002

**Parent**: L1-AGGR-001
**Statement**: The email body contribution slot SHALL be represented in the proto as an optional sub-message, with its absence interpreted as "this stage contributes nothing to the email body."
**Rationale**: Optional semantics support stages that render a full report but intentionally do not appear in the email body.
**Verification Method**: Inspection (I)

#### L2-AGGR-003

**Parent**: L1-AGGR-001
**Statement**: The email body contribution sub-message SHALL include a `position` enumeration field with permitted values `BEFORE_STAGES_SUMMARY` and `AFTER_STAGES_SUMMARY`, controlling placement of the contribution relative to the main aggregated summary block in the email body.
**Rationale**: Position control gives pipeline authors the ability to place contextual information either above or below the main summary.
**Verification Method**: Test (T)

### Derivations of L1-AGGR-002 (attachment modes)

#### L2-AGGR-004

**Parent**: L1-AGGR-002
**Statement**: Under `SINGLE_AGGREGATED` mode, the assembly process SHALL render the aggregation template once with a context containing the list of rendered stage contributions in `stage_order` order, producing a single attachment file.
**Rationale**: A single render pass over the aggregation template gives the template author full control over the composite document structure.
**Verification Method**: Test (T)

#### L2-AGGR-005

**Parent**: L1-AGGR-002
**Statement**: Under `PER_STAGE` mode, the assembly process SHALL produce one attachment file per stage that submitted a non-empty report contribution; stages with empty report contributions SHALL NOT produce attachments.
**Rationale**: Avoiding empty per-stage attachments keeps the email's attachment count aligned with the actually-present content.
**Verification Method**: Test (T)

#### L2-AGGR-006

**Parent**: L1-AGGR-002
**Statement**: Attachment filenames SHALL follow the conventions `{pipeline_type}_{run_id}.html` for `SINGLE_AGGREGATED` mode and `{pipeline_type}_{run_id}_{stage_id}.html` for `PER_STAGE` mode.
**Rationale**: Deterministic filenames make attachments identifiable on the recipient's side and in the filesystem store.
**Verification Method**: Test (T)

### Derivations of L1-AGGR-003 (stage_order ordering)

#### L2-AGGR-007

**Parent**: L1-AGGR-003
**Statement**: The assembly process SHALL sort stage contributions by the `stage_order` integer declared in `BeginRun` metadata, in ascending order.
**Rationale**: Ascending order matches conventional pipeline-first-to-last reading order.
**Verification Method**: Test (T)

#### L2-AGGR-008

**Parent**: L1-AGGR-003
**Statement**: When two or more stages share the same `stage_order` value, the assembly process SHALL break the tie by sorting on `stage_id` in lexicographic ascending order.
**Rationale**: A deterministic tie-breaker ensures that ordering is reproducible across runs and across re-renders.
**Verification Method**: Test (T)

### Derivations of L1-AGGR-004 (aggregation_template required for SINGLE_AGGREGATED)

#### L2-AGGR-009

**Parent**: L1-AGGR-004
**Statement**: See **L2-RUN-011** — the same `aggregation_template` validation is anchored there under L1-RUN-003 (BeginRun validation). This identifier exists to make the obligation visible inside the AGGR category for trace-matrix readers; verification artifacts SHALL be linked via `@pytest.mark.requirement("L2-RUN-011")` only (do not double-tag).
**Rationale**: A pure cross-reference rather than a re-stated SHALL avoids two copies of the same obligation drifting apart over time. The trace matrix surfaces the dual-category visibility through both L2-AGGR-009's row (which reads "see L2-RUN-011") and the artifacts attached to L2-RUN-011 itself.
**Verification Method**: Inspection (I)

#### L2-AGGR-010

**Parent**: L1-AGGR-004
**Statement**: The declared `aggregation_template` SHALL itself be validated against the template manifest at `BeginRun` time, independently of the stage templates.
**Rationale**: Validating the aggregation template early prevents late failures at assembly time when all stages have already submitted.
**Verification Method**: Test (T)

---

## L2-SWEEP: Orphan detection and disposition

### Derivations of L1-SWEEP-001 (asyncio background task)

#### L2-SWEEP-001

**Parent**: L1-SWEEP-001
**Statement**: The sweeper task SHALL be created during service startup via `asyncio.create_task()` and stored in the service application context, and SHALL be cancelled cleanly during shutdown.
**Rationale**: Explicit task tracking ensures the sweeper is bound to the service lifecycle and does not leak across reloads.
**Verification Method**: Test (T)

#### L2-SWEEP-002

**Parent**: L1-SWEEP-001
**Statement**: The sweeper loop SHALL implement its polling interval using `asyncio.sleep()` and SHALL NOT take dependencies on APScheduler, Celery, or other external scheduling libraries.
**Rationale**: Explicitly excluding external schedulers matches the user-stated constraint and keeps the dependency footprint minimal.
**Verification Method**: Inspection (I)

#### L2-SWEEP-003

**Parent**: L1-SWEEP-001
**Statement**: Each sweeper iteration SHALL increment a Prometheus counter metric labeled by outcome (`no_orphans_found`, `orphans_detected`, `sweeper_error`).
**Rationale**: Operational visibility into sweeper behavior is essential for diagnosing runs-not-being-delivered complaints.
**Verification Method**: Test (T)

#### L2-SWEEP-010

**Parent**: L1-SWEEP-001
**Statement**: The sweeper's per-tick scan SHALL be bounded to at most `sweeper.max_candidates_per_iteration` orphan candidates, ensuring a backlog cannot monopolize the shared SQLite connection or starve other request handlers. Backlogs larger than the cap drain over multiple ticks at the configured `poll_interval_seconds` cadence.
**Rationale**: Without a per-tick cap, a large backlog (post-incident recovery against thousands of stuck runs, recovery after extended downtime) would hold the connection through thousands of per-run UoWs in a single tick, blocking the gRPC ingest hot path. Bounded work keeps each tick's cost predictable; the bound is configurable so operators can trade detection latency against per-tick load. The L3 derivation (L3-SWEEP-008) pins the SQL-level enforcement.
**Verification Method**: Test (T)

### Derivations of L1-SWEEP-002 (timeout classification)

#### L2-SWEEP-004

**Parent**: L1-SWEEP-002
**Statement**: The sweeper SHALL compute elapsed time as the difference between the current clock reading and the `last_transition_at` timestamp stored on the run record.
**Rationale**: Using the most-recent transition captures runs that stalled mid-execution as well as those that never started progressing.
**Verification Method**: Test (T)

#### L2-SWEEP-005

**Parent**: L1-SWEEP-002
**Statement**: The sweeper SHALL query the run repository for runs in any non-terminal state (`INITIATED`, `AGGREGATING`, `READY`, `SENDING`) and evaluate each against the timeout threshold.
**Rationale**: Runs in terminal states by definition cannot become orphans and are excluded from the scan for efficiency.
**Verification Method**: Test (T)

#### L2-SWEEP-006

**Parent**: L1-SWEEP-002
**Statement**: A run identified as orphaned SHALL be transitioned to the `ORPHANED` state and have its configured disposition actions enqueued in a single atomic operation, preventing duplicate dispositions in the event of sweeper retry.
**Rationale**: Atomic transition-plus-enqueue is the simplest pattern that guarantees exactly-once disposition.
**Verification Method**: Test (T)

### Derivations of L1-SWEEP-003 (disposition policy)

#### L2-SWEEP-007

**Parent**: L1-SWEEP-003
**Statement**: The disposition policy SHALL be configured as an array of action identifiers in the key `sweeper.disposition_actions`, with permitted identifiers `SEND_PARTIAL_FLAGGED`, `DISCARD_SILENTLY`, `NOTIFY_SUBSCRIBERS`, and `NOTIFY_ADMINS`.
**Rationale**: Array-of-identifiers is a clean TOML construct that supports the required "any combination" semantics.
**Verification Method**: Inspection (I)

#### L2-SWEEP-008

**Parent**: L1-SWEEP-003
**Statement**: Each disposition action SHALL be implemented as an independent async handler registered with the sweeper, and the sweeper SHALL dispatch to every handler whose identifier appears in the configured policy.
**Rationale**: Independent handlers keep the action implementations decoupled and individually testable.
**Verification Method**: Inspection (I), Test (T)

#### L2-SWEEP-009

**Parent**: L1-SWEEP-003
**Statement**: Disposition handlers SHALL be invoked in a deterministic order matching the order of appearance in the configuration array.
**Rationale**: Deterministic ordering makes the observable behavior reproducible across environments and test runs.
**Verification Method**: Test (T)

---

## L2-SUB: Subscriptions and tags

### Derivations of L1-SUB-001 (three granularities)

#### L2-SUB-001

**Parent**: L1-SUB-001
**Statement**: Subscription records SHALL have the relational schema `(subscription_id, user_id, granularity, target_value, created_at)`, where `granularity` is one of `GLOBAL`, `PIPELINE`, `TAG` and `target_value` is a scoping string whose interpretation depends on granularity.
**Rationale**: A single record shape for all granularities simplifies storage, queries, and management UI.
**Verification Method**: Inspection (I)

#### L2-SUB-002

**Parent**: L1-SUB-001
**Statement**: For `GLOBAL` subscriptions `target_value` SHALL be null; for `PIPELINE` it SHALL hold the pipeline type name; for `TAG` it SHALL hold a tag name.
**Rationale**: Explicit semantics for `target_value` by granularity prevent ambiguity in recipient resolution.
**Verification Method**: Test (T)

#### L2-SUB-003

**Parent**: L1-SUB-001
**Statement**: Recipient resolution for a run SHALL execute via a single SQL query that joins the subscription table with the user table filtering on granularity and target_value matches against the run's pipeline type and declared tags.
**Rationale**: Single-query resolution bounds the resolution latency and supports atomic consistency.
**Verification Method**: Inspection (I), Test (T)

### Derivations of L1-SUB-002 (opt-in default)

#### L2-SUB-004

**Parent**: L1-SUB-002
**Statement**: User-creation operations (both self-service registration if enabled and admin-initiated) SHALL NOT insert any subscription records as a side effect.
**Rationale**: Side-effect-free creation is the simplest mechanism to enforce opt-in default.
**Verification Method**: Test (T)

#### L2-SUB-005

**Parent**: L1-SUB-002
**Statement**: Users created with administrator privilege SHALL be subject to the same opt-in default as non-administrator users.
**Rationale**: Admins are not automatically interested in every run; subscription choice is independent of role.
**Verification Method**: Test (T)

### Derivations of L1-SUB-003 (tag vocabulary)

#### L2-SUB-006

**Parent**: L1-SUB-003
**Statement**: The tag vocabulary file SHALL be a TOML file at the path specified by configuration key `tags.vocabulary_path`, containing one entry per tag with an optional `description` string for dashboard display.
**Rationale**: TOML format matches the manifest and main configuration files; descriptions improve the subscription UI without requiring code changes.
**Verification Method**: Inspection (I)

#### L2-SUB-007

**Parent**: L1-SUB-003
**Statement**: The tag vocabulary SHALL be loaded into an in-memory set at service startup and held read-only for the service lifetime.
**Rationale**: In-memory caching avoids per-validation I/O; hot-reload is a ROADMAP item.
**Verification Method**: Test (T)

#### L2-SUB-008

**Parent**: L1-SUB-003
**Statement**: Tag validation SHALL be applied both to `BeginRun` requests (checking `run_tags`) and to subscription creation requests (checking the `target_value` when `granularity=TAG`).
**Rationale**: Two-sided validation prevents both producers and consumers from binding to invalid tags.
**Verification Method**: Test (T)

### Derivations of L1-SUB-004 (union with dedup)

#### L2-SUB-009

**Parent**: L1-SUB-004
**Statement**: Recipient resolution SHALL construct the recipient collection as a Python `set` keyed on user email address, guaranteeing no duplicate recipients.
**Rationale**: Set-based construction is the standard idiom for de-duplication and eliminates the need for an explicit deduplication pass.
**Verification Method**: Test (T)

#### L2-SUB-010

**Parent**: L1-SUB-004
**Statement**: Recipient resolution SHALL exclude users whose account is marked disabled.
**Rationale**: Disabled accounts should not receive email; this enforces the disable semantics uniformly.
**Verification Method**: Test (T)

---

## L2-AUTH: Authentication

### Derivations of L1-AUTH-001 (Argon2id)

#### L2-AUTH-001

**Parent**: L1-AUTH-001
**Statement**: Password hashing SHALL use the `argon2-cffi` library configured for the Argon2id variant.
**Rationale**: `argon2-cffi` is the reference Python binding for the Argon2 PHC winner; Argon2id is the variant recommended for password hashing by OWASP.
**Verification Method**: Inspection (I)

#### L2-AUTH-002

**Parent**: L1-AUTH-001
**Statement**: Argon2id parameters (`memory_cost`, `time_cost`, `parallelism`) SHALL be exposed as configuration keys with defaults chosen to exceed OWASP-recommended minimums for the target deployment hardware.
**Rationale**: Parameter tunability lets operators increase cost on future hardware without code changes.
**Verification Method**: Inspection (I)

#### L2-AUTH-003

**Parent**: L1-AUTH-001
**Statement**: Passwords in plaintext form SHALL NOT appear in any log record, audit entry, or database field; the password-handling code path SHALL use a dedicated secret type that suppresses its value from the default `repr()`.
**Rationale**: Defense-in-depth against accidental disclosure via logging.
**Verification Method**: Inspection (I), Test (T)

### Derivations of L1-AUTH-002 (session management)

#### L2-AUTH-004

**Parent**: L1-AUTH-002
**Statement**: Session records SHALL be persisted server-side in the SQLite database, keyed on a cryptographically random session token of at least 128 bits of entropy.
**Rationale**: Server-side sessions permit immediate revocation; 128 bits of entropy is the minimum for session tokens under current guidance.
**Verification Method**: Inspection (I), Test (T)

#### L2-AUTH-005

**Parent**: L1-AUTH-002
**Statement**: The session cookie SHALL be set with the `HttpOnly` and `SameSite=Lax` attributes, and (when the dashboard is accessed over HTTPS) the `Secure` attribute.
**Rationale**: These attributes mitigate XSS theft, CSRF, and cleartext transmission respectively.
**Verification Method**: Inspection (I), Test (T)

#### L2-AUTH-006

**Parent**: L1-AUTH-002
**Statement**: On every authenticated request, the service SHALL check that the session's `last_activity_at` is within the configured idle-timeout window; expired sessions SHALL be invalidated and the request rejected with HTTP 401.
**Rationale**: Per-request checking ensures timeout is enforced consistently regardless of client behavior.
**Verification Method**: Test (T)

### Derivations of L1-AUTH-003 (admin user management)

#### L2-AUTH-007

**Parent**: L1-AUTH-003
**Statement**: Administrator user-management routes SHALL be exposed under the `/admin/users` prefix: `POST /admin/users` to create a new account, `PATCH /admin/users/{user_id}` to update an existing account's `display_name`, `is_admin`, or `disabled` fields (every field optional in the request body — only supplied fields SHALL be mutated), and `POST /admin/users/{user_id}/password` to set a new password. The account `email` SHALL NOT be mutable through these routes — email changes would conflate user-identity continuity with audit-log references; v1 treats email as immutable. All three routes SHALL be gated by the same `require_admin` dependency described under L2-DASH-007.
**Rationale**: Three narrowly scoped endpoints are easier to validate, audit, and grant separate operational guardrails than a single `POST /admin/users/{id}` that overloads multiple concerns. Treating email as immutable avoids a v1-out-of-scope question (does an email change count as a delete + re-create for audit purposes?) while leaving the door open for a later `POST /admin/users/{id}/email` route if operational need emerges.
**Verification Method**: Test (T)

#### L2-AUTH-008

**Parent**: L1-AUTH-003
**Statement**: Every admin-set password value SHALL flow through the shared `PasswordHasher` singleton (the `Argon2PasswordHasher` constructed at bootstrap, per L1-AUTH-001 / L2-AUTH-001 / L2-AUTH-002); only the resulting hash SHALL be persisted in the `users.password_hash` column. The plaintext password SHALL NOT appear in any HTTP response body, audit-log `details` field, structured log record, or response error message — neither on the success path nor on validation/error paths.
**Rationale**: A single hashing chokepoint guarantees admin-set and self-set passwords obey identical Argon2id discipline; tunable parameters (memory_cost / time_cost / parallelism) take effect uniformly. The plaintext-suppression obligation extends L2-AUTH-003's defense-in-depth to the admin surface.
**Verification Method**: Test (T)

#### L2-AUTH-009

**Parent**: L1-AUTH-003
**Statement**: Every successful admin user-management action SHALL emit an audit record per the existing L3-OBS-035 format: `CREATE_USER` on a successful create, `UPDATE_USER` on a successful PATCH or password reset (with the password reset's `mutated_fields` carrying `'password_hash'`; the hash value itself SHALL NOT appear in `details` per L3-OBS-036). To prevent accidental loss of administrative access, an administrator SHALL NOT (a) remove their own `is_admin` flag via PATCH or (b) set `disabled=True` on their own account; both attempts SHALL return HTTP 409 with a generic detail string and SHALL NOT emit an audit record (no successful action occurred). The rejected attempt SHALL be logged at `WARNING` severity with the attempting `admin_id` and the targeted `target_user_id` (which equal each other in the self-deadmin/self-disable case).
**Rationale**: Self-protection is an operational safety belt: a single admin who accidentally PATCHes themselves to non-admin or disabled would lock the system out of administrative recovery without operators having to issue raw SQL. The 409-without-audit rule matches the project's existing convention that audit records reflect successful actions, while still leaving an operator-visible WARNING log for forensics.
**Verification Method**: Test (T)

---

## L2-MAIL: Email delivery

### Derivations of L1-MAIL-001 (SMTP delivery)

#### L2-MAIL-001

**Parent**: L1-MAIL-001
**Statement**: SMTP delivery SHALL use the `aiosmtplib` library to maintain asyncio compatibility with the rest of the service.
**Rationale**: Synchronous SMTP would block the event loop and degrade throughput of concurrent requests.
**Verification Method**: Inspection (I)

#### L2-MAIL-002

**Parent**: L1-MAIL-001
**Statement**: SMTP connection parameters (`host`, `port`, `username`, `password`, `use_starttls`) SHALL be loaded from the configuration section `mail.smtp` at service startup.
**Rationale**: Standard configuration grouping simplifies operator review.
**Verification Method**: Inspection (I)

#### L2-MAIL-003

**Parent**: L1-MAIL-001
**Statement**: The sender address used in the `From:` header SHALL be loaded from configuration key `mail.from_address` and SHALL be validated at startup as a syntactically valid email address.
**Rationale**: A single, validated sender address prevents malformed `From:` values from reaching the relay.
**Verification Method**: Test (T)

#### L2-MAIL-014

**Parent**: L1-MAIL-001
**Statement**: The outbound email `Subject:` header SHALL default to the literal format `[{pipeline_type}] run {run_id}`, where `pipeline_type` is sanitized using the same regex as `L3-AGGR-010` (`[^a-zA-Z0-9._-]` replaced with `_`) and `run_id` is the canonical UUID4 string emitted by `domain.ids.new_run_id`. An operator MAY override this default per pipeline via the optional `pipelines.subject_templates` configuration mapping (`pipeline_type` → template string): when the run's `pipeline_type` has an entry, the subject SHALL be rendered from that template — which may reference only the `{pipeline_type}` and `{run_id}` placeholders, with `pipeline_type` sanitized by the same `L3-AGGR-010` helper before substitution — and when it has no entry the default format above SHALL be used unchanged. This construction SHALL apply to every outbound pipeline-report email — both the first delivery and a manual resend (L1-DASH-003); the resend path SHALL NOT substitute a different subject format or bypass the per-pipeline override.
**Rationale**: Subjects need a deterministic format so inbox filtering and rule-based mail routing work reliably; the bracketed `pipeline_type` leads so recipients can filter or sort by pipeline at a glance, and the `run_id` trails for support correspondence. Sanitizing `pipeline_type` at construction time provides defense-in-depth against header-injection-style payloads (CR/LF or other control characters reaching the SMTP layer); the existing `OutboundEmail.__post_init__` newline-rejection assertion (raises `ValueError`) remains as a second line of defense at the boundary. Mirrors the same naming discipline `L2-AGGR-006` applies to attachment filenames.
**Verification Method**: Test (T)

### Derivations of L1-MAIL-002 (exponential backoff)

#### L2-MAIL-004

**Parent**: L1-MAIL-002
**Statement**: Transient SMTP failures SHALL be identified as: connection refused, connection timeout, DNS resolution failure, and server responses with status codes in the 4xx range (except 421, which SHALL be treated as permanent for the current run).
**Rationale**: This categorization matches RFC 5321 retry semantics and avoids retrying unrecoverable conditions.
**Verification Method**: Test (T)

#### L2-MAIL-005

**Parent**: L1-MAIL-002
**Statement**: Permanent SMTP failures SHALL be identified as: server responses with status codes in the 5xx range, authentication failures (535), and status code 421.
**Rationale**: Permanent failures SHALL NOT trigger retries and SHALL transition the run to `FAILED`.
**Verification Method**: Test (T)

#### L2-MAIL-006

**Parent**: L1-MAIL-002
**Statement**: Exponential backoff SHALL compute the delay before retry attempt `n` as `min(max_interval, initial_interval * 2^(n-1))`, with the three quantities `max_retries`, `initial_interval`, and `max_interval` controlled by independent configuration keys.
**Rationale**: Independent control of all three parameters allows tuning across deployment contexts.
**Verification Method**: Test (T)

### Derivations of L1-MAIL-003 (size enforcement at assembly)

#### L2-MAIL-007

**Parent**: L1-MAIL-003
**Statement**: The email size check SHALL measure the byte length of the fully-encoded MIME message, including all headers, body parts, and base64-encoded attachments in their final form.
**Rationale**: Measuring the encoded message matches what the SMTP relay will see and avoids discrepancies from encoding inflation.
**Verification Method**: Test (T)

#### L2-MAIL-008

**Parent**: L1-MAIL-003
**Statement**: The size check SHALL occur after MIME encoding is complete and before the message is handed to the SMTP sender; no bytes SHALL be transmitted to the relay prior to passing the check.
**Rationale**: Pre-transmission checking avoids wasting relay resources on messages that cannot be accepted.
**Verification Method**: Test (T)

### Derivations of L1-MAIL-004 (size exceeded handling)

#### L2-MAIL-009

**Parent**: L1-MAIL-004
**Statement**: The `FAILED` run state produced by a size-limit breach SHALL include the structured reason enum value `EMAIL_SIZE_EXCEEDED` together with the measured size and the configured limit in the audit record.
**Rationale**: Preserving both measured and configured values in the audit record supports diagnostic work.
**Verification Method**: Test (T)

#### L2-MAIL-010

**Parent**: L1-MAIL-004
**Statement**: Administrator notification emails for size-exceeded events SHALL use a fixed, built-in template that does not incorporate any user-supplied content from the failing run.
**Rationale**: Eliminating user content from the admin notification template closes off a template-injection pathway.
**Verification Method**: Inspection (I)

#### L2-MAIL-011

**Parent**: L1-MAIL-004
**Statement**: The oversized rendered report SHALL be persisted to the filesystem store under the standard `run_id`-based filename, making it accessible through the dashboard resend interface.
**Rationale**: Preserving the report supports manual resend after the recipient list or size limit is adjusted.
**Verification Method**: Test (T)

### Derivations of L1-MAIL-005 (delivery audit)

#### L2-MAIL-012

**Parent**: L1-MAIL-005
**Statement**: Delivery audit records SHALL include the fields `timestamp`, `run_id`, `recipient_count`, `recipient_addresses`, `outcome` (success | failed), and `failure_reason` (null on success).
**Rationale**: The specified fields satisfy both operational troubleshooting and the narrow v1 audit scope.
**Verification Method**: Inspection (I)

#### L2-MAIL-013

**Parent**: L1-MAIL-005
**Statement**: Delivery audit records SHALL be written synchronously before the run transitions to `SENT` or `FAILED`; a failure to write the audit record SHALL prevent the state transition.
**Rationale**: Audit-first ordering guarantees that every terminal run has a corresponding delivery audit record.
**Verification Method**: Test (T)

---

## L2-DASH: Dashboard

### Derivations of L1-DASH-001 (FastAPI dashboard)

#### L2-DASH-001

**Parent**: L1-DASH-001
**Statement**: The FastAPI application SHALL be constructed via a factory function `create_app(service)` (taking the composed `Service` from the bootstrap composition root) rather than a module-level singleton, to support test isolation and repeated construction in unit tests.
**Rationale**: Factory construction is the FastAPI-recommended pattern for testable applications. The factory takes the composed `Service` rather than just the `Config` so it can reach the constructed use cases (login, logout, etc.) without re-executing the composition root.
**Verification Method**: Inspection (I)

#### L2-DASH-002

**Parent**: L1-DASH-001
**Statement**: The dashboard SHALL listen on configuration keys `dashboard.host` and `dashboard.port`, which SHALL be separate from `grpc.host` and `grpc.port`.
**Rationale**: Separate listeners allow operators to firewall the dashboard and gRPC interfaces independently.
**Verification Method**: Inspection (I)

#### L2-DASH-003

**Parent**: L1-DASH-001
**Statement**: Static assets (CSS, JavaScript, fonts) SHALL be served from a location packaged with the service and SHALL NOT reference external CDNs or network-hosted resources.
**Rationale**: Air-gapped ISOLAN deployments cannot reach external CDNs.
**Verification Method**: Inspection (I)

### Derivations of L1-DASH-002 (self-service subscriptions)

#### L2-DASH-004

**Parent**: L1-DASH-002
**Statement**: Subscription CRUD routes SHALL scope all operations to subscriptions whose `user_id` matches the authenticated session user; attempts to access other users' subscriptions SHALL return HTTP 403.
**Rationale**: Per-user scoping at the route level prevents horizontal privilege escalation.
**Verification Method**: Test (T)

#### L2-DASH-005

**Parent**: L1-DASH-002
**Statement**: The subscription creation endpoint SHALL NOT accept a `user_id` parameter in the request body; the user identity SHALL be taken exclusively from the session context.
**Rationale**: Removing `user_id` from the request body eliminates a class of parameter-tampering vulnerabilities.
**Verification Method**: Inspection (I), Test (T)

#### L2-DASH-006

**Parent**: L1-DASH-002
**Statement**: Subscription-creation UI forms SHALL populate the tag-selection control from the configured tag vocabulary loaded at service startup, and SHALL NOT permit free-text tag entry.
**Rationale**: Constrained tag entry enforces the vocabulary at the UI layer in addition to the validation layer.
**Verification Method**: Demonstration (D), Inspection (I)

### Derivations of L1-DASH-003 (admin features)

#### L2-DASH-007

**Parent**: L1-DASH-003
**Statement**: Administrator-only routes SHALL be gated by a boolean `is_admin` flag on the user record, enforced via a FastAPI dependency applied to the relevant route group.
**Rationale**: Dependency-based gating is the FastAPI-idiomatic approach to route-level authorization.
**Verification Method**: Test (T)

#### L2-DASH-008

**Parent**: L1-DASH-003
**Statement**: The manual resend action SHALL construct the recipient list at the time of the resend request by querying the current active subscriptions, not by replaying the recipient list from the original send event.
**Rationale**: Resend-time recipient resolution matches the user-stated semantic: resend goes to the current subscriber list.
**Verification Method**: Test (T)

#### L2-DASH-009

**Parent**: L1-DASH-003
**Statement**: The template registry inspection interface SHALL expose only read operations (list, view); no write, update, or delete endpoints for template metadata SHALL exist in the dashboard.
**Rationale**: Templates are git-managed; write operations through the dashboard would bypass the source-of-truth.
**Verification Method**: Inspection (I)

#### L2-DASH-012

**Parent**: L1-DASH-003
**Statement**: The dashboard's past-runs listing endpoint SHALL support offset+limit pagination, default to terminal-state runs (`SENT`, `FAILED`, `ORPHANED`), and SHALL order results by run-creation time most-recent-first.
**Rationale**: Operators need a paginated view of completed runs for incident review and resend operations; defaulting to terminal states keeps the "history" view distinct from in-flight runs, which have different operational semantics. Most-recent-first ordering matches the typical investigative pattern (start from the latest, scroll back).
**Verification Method**: Test (T)

#### L2-DASH-013

**Parent**: L1-DASH-003
**Statement**: The run-detail view SHALL expose the run's metadata (`run_id`, `pipeline_type`, `state`, `created_at`, `updated_at`, attachment mode, tags) and an ordered list of declared stages with each stage's submission state and timestamps; the large per-stage `report_context_json` and `email_body_context_json` payloads SHALL NOT appear inline in this response.
**Rationale**: A single run-detail page is the natural place for operators to investigate a specific run; exposing both the run-level state and the per-stage state lets them diagnose stalls without separate queries. Excluding the large JSON payloads from the inline response keeps the page lightweight; viewers fetch them on demand via the report-viewer routes (L2-DASH-014).
**Verification Method**: Test (T)

#### L2-DASH-014

**Parent**: L1-DASH-003
**Statement**: The report viewer SHALL expose two read-only HTML routes — one returning the assembled email body of a finalized run, and one returning each per-stage rendered fragment — backed by the filesystem report store (see `L3-PERS-024`..`L3-PERS-026`).
**Rationale**: Splitting body and fragments matches the assembly model (per-stage fragments composed into a body) and lets operators view individual stage outputs even when a run failed before full assembly. Reading from the filesystem store rather than re-rendering ensures the viewer shows the bytes that were actually delivered.
**Verification Method**: Test (T)

### Derivations of L1-DASH-004 (embedded metrics)

#### L2-DASH-010

**Parent**: L1-DASH-004
**Statement**: The dashboard metrics page SHALL retrieve metric values by performing a server-side HTTP GET against the service's own `/metrics` endpoint.
**Rationale**: Using the same endpoint that external scrapers use guarantees consistency between internal and external metric views.
**Verification Method**: Test (T)

#### L2-DASH-011

**Parent**: L1-DASH-004
**Statement**: Metric visualizations SHALL be rendered using a charting library that ships as a static asset without external CDN dependencies (e.g., Chart.js served from packaged static assets).
**Rationale**: Consistent with L2-DASH-003 — air-gapped deployments require offline-capable visualization.
**Verification Method**: Inspection (I)

### Derivations of L1-DASH-005 (admin audit-log viewer)

#### L2-DASH-015

**Parent**: L1-DASH-005
**Statement**: `GET /admin/audit` SHALL be the admin audit-log read endpoint, gated by the same `require_admin` dependency described under L2-DASH-007. Query parameters SHALL include: `limit` (int, inclusive range `[1, 200]`, default 50); `offset` (int, `>= 0`, default 0); `action` (optional, repeated query parameter; each value validated against the `AuditAction` enum and ANY-matched — multiple `action=X&action=Y` values OR together); `actor` (optional exact string match against `audit_log.actor`); `resource` (optional exact string match against `audit_log.resource`); `from` (optional ISO-Z timestamp lower bound, inclusive); `to` (optional ISO-Z timestamp upper bound, inclusive). Substring search on `actor` / `resource` is deliberately deferred from v1 (see ROADMAP `R-DASH-003`). Results SHALL be ordered `audit_id DESC` (most recent first; stable across same-`timestamp` ties — see L3-DASH-034).
**Rationale**: Offset-plus-limit pagination matches the past-runs endpoint (L2-DASH-012) so dashboard clients reuse the same paging UX. Multi-value `action` filters cover the typical investigative shape ("show me everything that's a state transition: any of `RUN_STATE_TRANSITION` or `STAGE_STATE_TRANSITION`"). Exact-match `actor` / `resource` is fast against the existing `audit_log` indexes and covers the common case ("everything `user:5` did", "everything against `run:<uuid>`"); substring is a future-flex item rather than v1 scope. Inclusive bounds on both `from` and `to` simplify the operator mental model — half-open ranges are an old SQL convention that is no longer worth the cognitive overhead.
**Verification Method**: Test (T)

#### L2-DASH-016

**Parent**: L1-DASH-005
**Statement**: Each response item SHALL be a JSON object carrying exactly: `audit_id` (int — the `audit_log` table primary key, exposed so clients can deep-link to a specific record), `timestamp` (ISO-Z string), `action` (`AuditAction` enum value as a string), `actor` (string), `resource` (string), `outcome` (`AuditOutcome` enum value as a string), and `details` (parsed JSON object — clients SHALL NOT receive a stringified-JSON form). Response models SHALL use `extra="forbid"`; future field additions are an explicit response-shape change rather than a silent extension. The route SHALL NOT add new redaction logic — it is a faithful projection of the table. Redaction is single-source-of-truth at write time per `L3-OBS-036`; introducing a viewer-side redaction pass would create a second, divergence-prone surface and would mask write-side regressions.
**Rationale**: Exposing `audit_id` lets dashboard clients construct stable references to specific audit records (e.g., a "permalink to this entry" link); the integer is opaque and carries no entropy that wasn't already public via offset-pagination. Parsed-JSON `details` (rather than a stringified blob) is what every consumer wants and matches the way the use-case layer constructs the field upstream. The "no double redaction" rationale is the operational lesson from L3-OBS-036: keep the obligation in one place and let any drift surface as a write-side test failure rather than a silent viewer mask.
**Verification Method**: Test (T)

---

## L2-PERS: Persistence

### Derivations of L1-PERS-001 (SQLite shared database)

#### L2-PERS-001

**Parent**: L1-PERS-001
**Statement**: The service SHALL maintain exactly one SQLite database file per deployment, at the path specified by configuration key `persistence.sqlite_path`.
**Rationale**: A single file simplifies backup, restore, and operator mental model.
**Verification Method**: Inspection (I)

#### L2-PERS-002

**Parent**: L1-PERS-001
**Statement**: The SQLite connection SHALL be configured at startup with `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, and `PRAGMA foreign_keys=ON`.
**Rationale**: WAL journal mode gives durability without blocking readers; `synchronous=NORMAL` is the recommended safe-and-fast balance for WAL; foreign keys are off by default in SQLite and must be explicitly enabled.
**Verification Method**: Inspection (I), Test (T)

#### L2-PERS-003

**Parent**: L1-PERS-001
**Statement**: Schema migrations SHALL be applied at service startup via a versioned migration script set stored in the `infrastructure/persistence/sqlite/migrations/` directory, with each migration numbered and applied in order.
**Rationale**: Versioned migrations support schema evolution across releases without operator intervention.
**Verification Method**: Test (T)

#### L2-PERS-004

**Parent**: L1-PERS-001
**Statement**: The service SHALL hold a single shared `aiosqlite.Connection` across all UnitOfWork instances and SHALL serialize concurrent UoW openings via an `asyncio.Lock` held across the BEGIN→COMMIT (or BEGIN→ROLLBACK) span, so that at most one transaction is active on the connection at any time.
**Rationale**: SQLite enforces at most one writer per database file regardless of how many connections are open; the codebase's UoWs are write-heavy (audit-log writes are bundled with most reads), so the pool's main potential benefit (read parallelism in WAL mode) is small in practice. A single shared connection plus an explicit `asyncio.Lock` makes the serialization point obvious in code, gives a predictable failure mode under contention (latency, not `SQLITE_BUSY` retries), and avoids pool-sizing / acquire-timeout / exhaustion-handling complexity that this single-node workload does not justify. The pool architecture, including its rationale, configuration knob, and diagram fragment, is preserved verbatim in `docs/archive/connection-pool-architecture.md` along with the re-evaluation triggers that would justify revisiting it.
**Verification Method**: Test (T), Inspection (I)

### Derivations of L1-PERS-002 (filesystem storage)

#### L2-PERS-005

**Parent**: L1-PERS-002
**Statement**: Rendered report files SHALL be written atomically by writing to a temporary file with suffix `.tmp` in the same directory, then renaming to the final path using `os.rename()` (which is atomic on both Linux and Windows for same-directory renames).
**Rationale**: Atomic rename prevents partial files from being visible to the dashboard or resend flow.
**Verification Method**: Test (T)

#### L2-PERS-006

**Parent**: L1-PERS-002
**Statement**: The rendered-report directory SHALL be created (including parent directories) at service startup if it does not exist, with the service failing to start if directory creation fails.
**Rationale**: Fail-fast on missing storage paths prevents late errors when the first run attempts to persist.
**Verification Method**: Test (T)

#### L2-PERS-007

**Parent**: L1-PERS-002
**Statement**: All filesystem path manipulation in the service SHALL use `pathlib.Path`; string-based path concatenation with `/` or `os.path.join()` SHALL NOT be used.
**Rationale**: `pathlib.Path` is platform-aware and eliminates separator confusion on Windows.
**Verification Method**: Inspection (I)

### Derivations of L1-PERS-003 (repository pattern)

#### L2-PERS-008

**Parent**: L1-PERS-003
**Statement**: Abstract repository interfaces SHALL be defined as Python `abc.ABC` classes in the `application/ports/` package, with concrete methods annotated with full type hints.
**Rationale**: ABC-based interfaces give explicit contract enforcement and integrate with static type checkers.
**Verification Method**: Inspection (I)

#### L2-PERS-009

**Parent**: L1-PERS-003
**Statement**: Concrete SQLite repository implementations SHALL reside in `infrastructure/persistence/sqlite/`, and filesystem repository implementations in `infrastructure/persistence/filesystem/`, with no cross-imports between the two.
**Rationale**: Directory-level separation enforces the architectural boundary.
**Verification Method**: Inspection (I)

#### L2-PERS-010

**Parent**: L1-PERS-003
**Statement**: Domain layer modules (`domain/`) and application layer modules (`application/`) SHALL NOT import any symbol from `infrastructure/`; imports SHALL flow outward only (interfaces → application → domain), enforced via a static-analysis rule in CI.
**Rationale**: Strict inward-flow enforces the dependency rule of hexagonal architecture.
**Verification Method**: Inspection (I), Analysis (A)

### Derivations of L1-PERS-004 (rendered-report retention)

#### L2-PERS-011

**Parent**: L1-PERS-004
**Statement**: The configuration schema SHALL expose `persistence.filesystem.report_retention_days: int` (default 90) constrained to `>= 1`, controlling how long rendered reports are retained on disk before the pruner evicts them. The retention key SHALL be loaded at startup; mid-run changes require a service restart.
**Rationale**: Mirrors the existing `observability.audit.retention_days` pattern so operations works with one mental model. Defaulting to 90 covers a typical post-incident investigation window without committing to indefinite growth. The startup-load constraint matches `observability.log_level` and avoids hot-reload complexity that v1 doesn't carry.
**Verification Method**: Test (T)

#### L2-PERS-012

**Parent**: L1-PERS-004
**Statement**: The pruner task SHALL run as an asyncio coroutine on the same `BackgroundTaskScheduler` infrastructure as the orphan sweeper (L2-SWEEP-001), polling at a configurable cadence (`persistence.filesystem.prune_interval_seconds`, default 86400 — daily). The pruner SHALL bound per-tick work via `persistence.filesystem.max_prunes_per_iteration` (default 1000) so a large backlog drains over multiple iterations rather than monopolizing the connection.
**Rationale**: Re-using the sweeper's scheduling model keeps the runtime model uniform — there's exactly one background-task pattern. Daily cadence balances disk-pressure responsiveness against scheduling overhead. The per-iteration bound mirrors L3-SWEEP-008's `max_candidates_per_iteration` rationale and prevents a cleanup tick from starving request handlers on the shared SQLite connection.
**Verification Method**: Test (T)

#### L2-PERS-013

**Parent**: L1-PERS-004
**Statement**: Each successful eviction SHALL be recorded in the audit log with `action="PRUNE_REPORT"`, `actor="system:report_pruner"`, `resource="report:<run_id>"`, `outcome=SUCCESS`, and `details` containing `file_path`, `file_size_bytes`, and the source run's `terminal_state` and `terminal_state_at`. Eviction failures (file missing, permission denied, etc.) SHALL be logged at WARNING and recorded with `outcome=FAILURE` plus `failure_reason`; the pruner SHALL continue with the next file rather than abort the iteration.
**Rationale**: Audit-per-eviction makes deletion traceable to operator policy rather than appearing as silent data loss when the dashboard's "show me run X" link 404s. Continuing past per-file failures matches the L3-SWEEP-013 "swallowed-with-log" pattern that the disposition dispatcher uses — a single bad file SHOULD NOT block the rest of the cleanup batch.
**Verification Method**: Test (T)

---

## L2-OBS: Observability

### Derivations of L1-OBS-001 (JSON logging)

#### L2-OBS-001

**Parent**: L1-OBS-001
**Statement**: JSON log emission SHALL use the `structlog` library configured with a JSON renderer processor.
**Rationale**: `structlog` provides the contextvars integration required for request-scoped context propagation.
**Verification Method**: Inspection (I)

#### L2-OBS-002

**Parent**: L1-OBS-001
**Statement**: Contextual identifiers (`run_id`, `stage_id`, `user_id`) SHALL be bound to the current logging context via `contextvars` at the boundary of each inbound request (gRPC or REST handler), and SHALL propagate automatically to all log records emitted during the request's lifetime.
**Rationale**: Contextvars-based propagation removes the need to thread identifiers manually through every function call.
**Verification Method**: Test (T), Inspection (I)

#### L2-OBS-003

**Parent**: L1-OBS-001
**Statement**: Log records SHALL NOT include passwords, session tokens, full email message bodies, or template context values; such fields SHALL be redacted or omitted by processors in the logging pipeline.
**Rationale**: Redaction in the pipeline is less error-prone than relying on every call site to omit sensitive fields.
**Verification Method**: Inspection (I), Test (T)

### Derivations of L1-OBS-002 (Prometheus metrics)

#### L2-OBS-004

**Parent**: L1-OBS-002
**Statement**: Prometheus metrics SHALL be implemented using the `prometheus-client` library and exposed via FastAPI at the route path `/metrics`.
**Rationale**: `prometheus-client` is the reference Python client; co-locating the endpoint with FastAPI avoids a second HTTP server.
**Verification Method**: Inspection (I)

#### L2-OBS-005

**Parent**: L1-OBS-002
**Statement**: All metric names SHALL be prefixed with `message_service_` and SHALL follow the Prometheus naming conventions (lowercase with underscores, unit suffix where applicable).
**Rationale**: Consistent prefixing enables straightforward filtering in downstream monitoring stacks.
**Verification Method**: Inspection (I)

#### L2-OBS-006

**Parent**: L1-OBS-002
**Statement**: The metric set SHALL include at minimum: counters for run state transitions (by target state), counters for stage state transitions (by target state), counters for email delivery outcomes (by outcome), a histogram of email message sizes in bytes, a histogram of run end-to-end duration, and counters for orphan sweep outcomes.
**Rationale**: The enumerated set covers the primary operational concerns of throughput, success rate, resource pressure, and failure modes.
**Verification Method**: Inspection (I), Test (T)

### Derivations of L1-OBS-003 (audit log with retention)

#### L2-OBS-007

**Parent**: L1-OBS-003
**Statement**: Audit records SHALL be stored in a single `audit_log` table with the schema `(audit_id, timestamp, event_type, run_id, details_json)`, supporting queries filtered by `event_type` and date range.
**Rationale**: A unified table simplifies querying and retention enforcement across event types.
**Verification Method**: Test (T), Inspection (I)

#### L2-OBS-008

**Parent**: L1-OBS-003
**Statement**: Audit log retention SHALL be enforced by a daily cleanup task that deletes records whose `timestamp` is older than the configured retention duration.
**Rationale**: Daily cadence balances storage pressure against query performance impact from large deletes.
**Verification Method**: Test (T)

#### L2-OBS-009

**Parent**: L1-OBS-003
**Statement**: The retention-enforcement cleanup task SHALL run as an asyncio coroutine using the same scheduling approach as the orphan sweeper, and SHALL NOT introduce additional external scheduler dependencies.
**Rationale**: Consistency of approach across background tasks simplifies the runtime model.
**Verification Method**: Inspection (I)

#### L2-OBS-013

**Parent**: L1-OBS-003
**Statement**: Pipeline-initiated lifecycle audit records (`BEGIN_RUN`, `SUBMIT_STAGE_REPORT`, `FINALIZE_RUN`) SHALL set `actor` to `pipeline:<pipeline_type>`, set `resource` to `run:<run_id>` for run-scoped events and `stage:<run_id>:<stage_id>` for stage-scoped events, and capture in `details` at minimum the request-shape fields needed to reconstruct the call (declared stages, tags, attachment mode for `BEGIN_RUN`; stage id, was_retry flag for `SUBMIT_STAGE_REPORT`; etc.).
**Rationale**: Pipeline events are external traffic — operators investigating an incident need to see what the pipeline sent, not just that something happened.
**Verification Method**: Test (T)

#### L2-OBS-014

**Parent**: L1-OBS-003
**Statement**: Service-driven state-transition audit records (`RUN_STATE_TRANSITION`, `STAGE_STATE_TRANSITION`) SHALL set `actor` to the use case that triggered the transition (e.g., `system:finalize_run`, `system:assemble_and_deliver`, `system:sweeper`), and SHALL include `prior_state`, `new_state`, and the `last_transition_at` timestamp in `details`.
**Rationale**: Capturing both states plus the trigger lets operators reconstruct the lifecycle without consulting source code.
**Verification Method**: Test (T)

#### L2-OBS-015

**Parent**: L1-OBS-003
**Statement**: Sweeper audit records (`SWEEP_ORPHAN`) SHALL set `actor` to `system:sweeper`, set `resource` to `run:<run_id>`, and include `prior_state`, `new_state`, `last_transition_at`, and the configured `enqueued_actions` list in `details`.
**Rationale**: The sweeper is unattended infrastructure — every orphan transition must be auditable to the configured policy that produced it, including the disposition actions that were enqueued.
**Verification Method**: Test (T)

#### L2-OBS-016

**Parent**: L1-OBS-003
**Statement**: Subscription audit records (`SUBSCRIBE`, `UNSUBSCRIBE`) SHALL set `actor` to `user:<user_id>` (the user mutating their own subscriptions) and `resource` to `subscription:<subscription_id>`, with `details` capturing `granularity` and `target_value`.
**Rationale**: Subscription changes are user-initiated and audit-relevant for both compliance ("who opted in to what?") and incident investigation ("did this user subscribe before or after the bad run?").
**Verification Method**: Test (T)

#### L2-OBS-017

**Parent**: L1-OBS-003
**Statement**: Authentication and user-management audit records (`LOGIN`, `LOGIN_FAILED`, `LOGOUT`, `CREATE_USER`, `UPDATE_USER`) SHALL distinguish actor identity by event type: `LOGIN` / `LOGOUT` set `actor` to `user:<user_id>`; `LOGIN_FAILED` sets `actor` to `username:<attempted_email>` (no user_id, since authentication was rejected); `CREATE_USER` and `UPDATE_USER` set `actor` to the administrator's `user:<admin_id>`. `outcome` SHALL be `SUCCESS` for `LOGIN`/`LOGOUT`/`CREATE_USER`/`UPDATE_USER` and `FAILURE` for `LOGIN_FAILED`. Passwords and password hashes SHALL NOT appear in `details`.
**Rationale**: Auth events are central to security incident investigation. Distinguishing successful and failed authentication actor formats lets analysts query both ("who tried to log in as `alice@example.com`?" vs. "what did `user:42` do?"). The redaction obligation prevents the audit log from becoming a credential exfiltration vector.
**Verification Method**: Test (T), Inspection (I)

#### L2-OBS-018

**Parent**: L1-OBS-003
**Statement**: System-initiated delivery and orphan-handling outcome audit records (`SEND_REPORT`, `DISPATCHER_ACTION_ABANDONED`) SHALL set `actor` to the emitting use case (`system:assemble_and_deliver`, `system:sweeper_action_dispatcher` respectively), and SHALL pin per-action format obligations (recipient enumeration for delivery; failure reason + attempt count for abandonment) at the L3 level (see `L3-OBS-037`, `L3-OBS-038`). The manual-resend audit (`RESEND_REPORT`) is outside this L2 because its format is pinned alongside the resend behavior contract at `L2-DASH-008` / `L3-DASH-013`; cross-reference is informational only.
**Rationale**: The `L2-OBS-013..017` cluster (authored in 25f) covered pipeline-initiated lifecycle, service-driven state transitions, sweeper orphan classification, subscription mutations, and auth + user-management. Two `AuditAction` values predated that audit (`SEND_REPORT` and `DISPATCHER_ACTION_ABANDONED`) and were not anchored under any L2 — their formats were implementation-decided rather than spec-pinned. This L2 closes that gap so every emitted `AuditAction` has at least one L2-OBS or L2-DASH parent governing its format.
**Verification Method**: Test (T)

### Derivations of L1-OBS-004 (log severity levels)

#### L2-OBS-010

**Parent**: L1-OBS-004
**Statement**: Log severity assignment SHALL follow these rules: `DEBUG` for high-volume per-request detail, `INFO` for lifecycle events (service start/stop, run state transitions, configuration load), `WARNING` for recoverable degraded conditions (retries, transient failures, orphan detection), `ERROR` for operation-failing conditions that do not terminate the service, and `CRITICAL` for conditions that require service termination or immediate administrator attention (audit-write failure, database corruption, configuration hot-reload failure).
**Rationale**: Explicit level-assignment rules prevent the common anti-pattern where every component picks its own conventions.
**Verification Method**: Inspection (I), Test (T)

#### L2-OBS-011

**Parent**: L1-OBS-004
**Statement**: The effective minimum log level emitted to stdout SHALL be controlled by the configuration key `observability.log_level`, with default value `INFO`.
**Rationale**: Level control via configuration lets operators enable `DEBUG` for incident investigation without a code change.
**Verification Method**: Test (T)

#### L2-OBS-012

**Parent**: L1-OBS-004
**Statement**: Every log record emitted at `ERROR` or `CRITICAL` severity SHALL include the exception's `error_code` field (if the record is associated with a caught exception) to support downstream alerting rules that filter by code.
**Rationale**: Downstream tooling (SIEMs, alerting rules) benefits from structured error codes far more than from message-text matching.
**Verification Method**: Test (T)

---

## L2-ERR: Error handling and exception taxonomy

### Derivations of L1-ERR-001 (exception hierarchy)

#### L2-ERR-001

**Parent**: L1-ERR-001
**Statement**: The base exception class `MessageServiceError` SHALL be defined in `src/message_service/domain/errors.py` and SHALL declare abstract attributes `error_code: str` and `http_status: int`, along with a `log_level: int` defaulting to `logging.ERROR`.
**Rationale**: Locating the base in the domain layer (rather than infrastructure) makes the exception hierarchy part of the domain contract; the declared attributes enable mechanical translation at interface boundaries.
**Verification Method**: Inspection (I), Test (T)

#### L2-ERR-002

**Parent**: L1-ERR-001
**Statement**: The exception hierarchy SHALL include at minimum these direct subclasses of `MessageServiceError`: `DomainError` (invariant violations), `ValidationError` (input validation failures), `InfrastructureError` (adapter-layer failures), and `ConfigurationError` (startup-time failures).
**Rationale**: These four categories cover the distinct handling strategies needed: domain errors typically map to gRPC `FAILED_PRECONDITION`, validation errors to `INVALID_ARGUMENT`, infrastructure errors to `INTERNAL` or `UNAVAILABLE` depending on cause, and configuration errors cause process exit.
**Verification Method**: Inspection (I)

#### L2-ERR-003

**Parent**: L1-ERR-001
**Statement**: Each leaf exception class SHALL declare a class-level `error_code` string constant matching the corresponding enum value in the proto `ErrorCode` definition, with no duplication across the hierarchy.
**Rationale**: Class-level constants are inspectable at import time and are what allows the shared-enumeration property (L1-ERR-002) to be enforced by a simple test.
**Verification Method**: Test (T)

### Derivations of L1-ERR-002 (shared error codes)

#### L2-ERR-004

**Parent**: L1-ERR-002
**Statement**: The canonical list of error codes SHALL live in a single proto enum (`message_service.v1.ErrorCode`) and SHALL be imported into the Python exception hierarchy at module load time, with a startup self-check verifying that every exception class's `error_code` maps to a defined proto enum value.
**Rationale**: Startup self-check catches drift between the two definitions immediately, before any request is served.
**Verification Method**: Test (T)

#### L2-ERR-005

**Parent**: L1-ERR-002
**Statement**: Error codes SHALL be `UPPER_SNAKE_CASE` strings, stable across service versions; once an error code appears in a released version it SHALL NOT be renamed or repurposed.
**Rationale**: Error-code stability is a client-compatibility concern, since pipelines may program against specific codes.
**Verification Method**: Inspection (I)

### Derivations of L1-ERR-003 (boundary handling)

#### L2-ERR-006

**Parent**: L1-ERR-003
**Statement**: Each inbound interface (gRPC servicer, FastAPI route, CLI entry point, asyncio background task) SHALL wrap its handler invocations in a top-level exception-translation layer responsible for logging, mapping to transport error, and (for background tasks) deciding whether to continue or terminate.
**Rationale**: A single translation layer per interface prevents the handling logic from being scattered across individual handlers.
**Verification Method**: Inspection (I), Test (T)

#### L2-ERR-007

**Parent**: L1-ERR-003
**Statement**: The gRPC exception translator SHALL map `MessageServiceError` subclasses to gRPC status codes via each exception's declared `error_code`, log the exception at its declared `log_level`, and return a structured error to the client containing only the `error_code` and a short human-readable message — no stack trace, no internal exception class name.
**Rationale**: Client-facing responses SHALL carry only stable, sanitized information; diagnostic depth lives in the server-side log record accessed via the correlation identifier.
**Verification Method**: Test (T)

#### L2-ERR-008

**Parent**: L1-ERR-003
**Statement**: Unhandled (non-`MessageServiceError`) exceptions caught at interface boundaries SHALL be logged at `CRITICAL` severity with the full traceback, mapped to gRPC `INTERNAL` or HTTP 500, and returned with a generic message referencing a correlation identifier.
**Rationale**: Unhandled exceptions are bugs; they deserve the highest log severity and a correlation identifier for post-incident investigation.
**Verification Method**: Test (T)

### Derivations of L1-ERR-004 (no silent swallowing)

#### L2-ERR-009

**Parent**: L1-ERR-004
**Statement**: Bare `except:` clauses and broad `except Exception:` clauses without re-raise, log, or translation SHALL be flagged as lint errors by the CI pipeline.
**Rationale**: Lint-level enforcement catches regressions at merge time without requiring code review vigilance.
**Verification Method**: Inspection (I), Analysis (A)

#### L2-ERR-010

**Parent**: L1-ERR-004
**Statement**: Exception-handling code in the codebase SHALL NOT catch `BaseException` or its non-`Exception` children (`SystemExit`, `KeyboardInterrupt`, `GeneratorExit`); these SHALL propagate to enable normal shutdown and cancellation semantics.
**Rationale**: Catching `BaseException` is the classic bug that breaks Ctrl-C and systemd shutdown.
**Verification Method**: Inspection (I)

---

## L2-CFG: Configuration

### Derivations of L1-CFG-001 (TOML config at startup)

#### L2-CFG-001

**Parent**: L1-CFG-001
**Statement**: The configuration file path SHALL be accepted via the `--config` command-line argument and via the `MSG_SERVICE_CONFIG` environment variable.
**Rationale**: Both options are conventional; supporting both gives operators flexibility in deployment scripting.
**Verification Method**: Test (T)

#### L2-CFG-002

**Parent**: L1-CFG-001
**Statement**: When both `--config` and `MSG_SERVICE_CONFIG` are provided, `--config` SHALL take precedence.
**Rationale**: Explicit command-line arguments conventionally override environment variables.
**Verification Method**: Test (T)

#### L2-CFG-003

**Parent**: L1-CFG-001
**Statement**: TOML parsing SHALL use the `tomllib` standard library module.
**Rationale**: `tomllib` is available in the standard library on Python 3.11+, and the project's minimum Python version is 3.12 (see L2-DEP-008); no third-party TOML shim is required.
**Verification Method**: Inspection (I)

### Derivations of L1-CFG-002 (schema validation at startup)

#### L2-CFG-004

**Parent**: L1-CFG-002
**Statement**: The configuration schema SHALL be defined as a Pydantic v2 model tree with field-level type annotations, default values, and validators as appropriate.
**Rationale**: Pydantic v2 combines declarative schema with high-performance validation.
**Verification Method**: Inspection (I)

#### L2-CFG-005

**Parent**: L1-CFG-002
**Statement**: Configuration validation failures SHALL produce a structured error message on standard error listing all invalid fields with their paths and failure reasons, and SHALL exit the process with a nonzero exit code.
**Rationale**: Listing all failures at once avoids the whack-a-mole cycle of fixing one field at a time.
**Verification Method**: Test (T)

#### L2-CFG-006

**Parent**: L1-CFG-002
**Statement**: No service component (gRPC server, FastAPI app, sweeper, SMTP sender) SHALL be instantiated before the configuration has been successfully parsed and validated.
**Rationale**: Ordering ensures that no partial initialization state is visible if configuration is invalid.
**Verification Method**: Inspection (I)

### Derivations of L1-CFG-003 (minimum config settings)

#### L2-CFG-007

**Parent**: L1-CFG-003
**Statement**: All filesystem paths declared in the configuration SHALL be resolved relative to the configuration file's directory if expressed as relative paths; absolute paths SHALL be used as-is.
**Rationale**: Relative-to-config-file resolution makes configuration portable across deployment environments without rewriting.
**Verification Method**: Test (T)

#### L2-CFG-008

**Parent**: L1-CFG-003
**Statement**: Secret values (specifically SMTP password) MAY be specified by reference to an environment variable using the form `${env:VAR_NAME}`, resolved at configuration load time.
**Rationale**: Env-var indirection keeps plaintext secrets out of the configuration file while preserving the 12-factor model.
**Verification Method**: Test (T)

---

## L2-DEP: Deployment

### Derivations of L1-DEP-001 (Linux + Windows compatibility)

#### L2-DEP-001

**Parent**: L1-DEP-001
**Statement**: The continuous integration matrix SHALL include both a Linux (Ubuntu LTS) and a Windows Server runner, running the full test suite on each.
**Rationale**: Matrix CI catches platform-specific regressions at merge time rather than in deployment.
**Verification Method**: Inspection (I)

#### L2-DEP-002

**Parent**: L1-DEP-001
**Statement**: All file system paths in the codebase SHALL use `pathlib.Path`; usage of `os.path.join()` and hardcoded path separators SHALL be flagged by a lint rule in CI.
**Rationale**: Lint-level enforcement prevents regression on path handling.
**Verification Method**: Inspection (I), Analysis (A)

#### L2-DEP-003

**Parent**: L1-DEP-001
**Statement**: The codebase SHALL NOT invoke `os.fork()`, `signal.SIGCHLD`, or other POSIX-only primitives in domain, application, or interface layers; infrastructure modules that require such primitives SHALL be gated behind platform detection.
**Rationale**: Confining platform-specific code to infrastructure preserves portability of the core service logic.
**Verification Method**: Inspection (I)

### Derivations of L1-DEP-002 (systemd + NSSM)

#### L2-DEP-004

**Parent**: L1-DEP-002
**Statement**: A systemd unit file SHALL be provided at `deploy/linux/message-service.service` with standard sections for [Unit], [Service] (including `ExecStart`, `Restart=on-failure`, and `User` directives), and [Install].
**Rationale**: A ready-made unit file reduces deployment friction and ensures consistent service behavior across Linux installations.
**Verification Method**: Inspection (I), Demonstration (D)

#### L2-DEP-005

**Parent**: L1-DEP-002
**Statement**: A Windows installation procedure using NSSM SHALL be documented in `deploy/windows/README.md`, covering service registration, configuration of startup parameters, and log file placement.
**Rationale**: NSSM is the user-selected Windows service mechanism; documented procedure avoids per-deployment improvisation.
**Verification Method**: Inspection (I), Demonstration (D)

#### L2-DEP-006

**Parent**: L1-DEP-002
**Statement**: The service SHALL handle graceful shutdown on both SIGTERM (Linux) and CTRL_BREAK_EVENT (Windows), completing in-flight gRPC calls within a configurable shutdown grace period before forcing termination.
**Rationale**: Cross-platform shutdown handling is the mechanism through which systemd and NSSM achieve orderly stops.
**Verification Method**: Test (T), Demonstration (D)

### Derivations of L1-DEP-003 (Poetry packaging)

#### L2-DEP-007

**Parent**: L1-DEP-003
**Statement**: The `pyproject.toml` file SHALL declare `python = ">=3.12,<4.0"` in `[tool.poetry.dependencies]`, matching the minimum version documented in L1-DEP-003 and leaving room for future compatibility.
**Rationale**: Explicit version constraint prevents accidental use of versions outside the tested range. Python 3.12 is the current stable release with support through October 2028; older versions are EOL or near-EOL by the project's target deployment window.
**Verification Method**: Inspection (I)

#### L2-DEP-008

**Parent**: L1-DEP-003
**Statement**: The `poetry.lock` file SHALL be committed to the repository and SHALL be used unchanged by all deployment builds.
**Rationale**: A committed lockfile is the mechanism through which reproducible builds are achieved.
**Verification Method**: Inspection (I)

#### L2-DEP-009

**Parent**: L1-DEP-003
**Statement**: The Poetry configuration SHALL declare a console script entry point `message-service` invoking the `message_service.interfaces.cli:main` function, providing the canonical CLI entry point for both systemd and NSSM launches.
**Rationale**: A single entry point ensures uniform startup across platforms.
**Verification Method**: Inspection (I), Test (T)

---

## L2-CICD: Continuous integration and delivery

### Derivations of L1-CICD-001 (cross-platform pytest matrix)

#### L2-CICD-001

**Parent**: L1-CICD-001
**Statement**: The CI workflow SHALL declare a job matrix with the cartesian product of `os` ∈ {`ubuntu-latest`, `windows-latest`} and `python-version` ∈ {`3.12`, `3.13`}; every cell SHALL execute `poetry install` followed by `poetry run pytest` and SHALL be required-to-pass for the workflow to be considered green.
**Rationale**: Two OSes × two Python versions catches platform-specific and version-specific regressions before merge. Required-to-pass on every cell prevents one cell from being silently skipped or marked allowed-failure.
**Verification Method**: Inspection (I)

#### L2-CICD-002

**Parent**: L1-CICD-001
**Statement**: The pytest invocation SHALL run with `filterwarnings = ["error", ...]` (already set in `pyproject.toml::tool.pytest.ini_options`); any `ResourceWarning`, `DeprecationWarning` not in the explicit ignore list, or other escalated warning SHALL cause the test run to fail.
**Rationale**: Warning escalation is the contract that catches resource leaks (unclosed sockets/file handles/event loops) at unit-test time rather than under production load. The explicit ignore list is small and reviewable; growth requires deliberate intent.
**Verification Method**: Inspection (I), Test (T)

#### L2-CICD-003

**Parent**: L1-CICD-001
**Statement**: The CI workflow SHALL trigger on every `push` to `main` and every `pull_request` (open + synchronize). A nightly scheduled run on `main` SHALL also execute the full matrix, surfacing flakes that don't reproduce per-PR.
**Rationale**: Per-PR triggers gate merges; the scheduled run catches non-deterministic failures (asyncio races, clock-sensitive tests) that pass per-merge but fail under the natural load of a 24-hour rerun cadence.
**Verification Method**: Inspection (I)

### Derivations of L1-CICD-002 (pre-commit gate)

#### L2-CICD-004

**Parent**: L1-CICD-002
**Statement**: The CI workflow SHALL execute `poetry run pre-commit run --all-files` as a required job; failure of any hook SHALL fail the workflow.
**Rationale**: Running `--all-files` (rather than only changed files) catches drift from prior PRs that bypassed local pre-commit. Required-to-pass status keeps the gate authoritative.
**Verification Method**: Inspection (I)

#### L2-CICD-005

**Parent**: L1-CICD-002
**Statement**: Pre-commit hook versions in `.pre-commit-config.yaml` SHALL be pinned to specific revisions (not branch references like `main`); CI SHALL execute against the same pinned revisions developers use locally.
**Rationale**: Hook drift between local and CI is the most common source of "passes locally, fails on CI" friction. Pinned revisions across both environments makes the gate deterministic.
**Verification Method**: Inspection (I)

### Derivations of L1-CICD-003 (coverage gate)

#### L2-CICD-006

**Parent**: L1-CICD-003
**Statement**: The pytest configuration SHALL set `--cov-fail-under` to the current coverage floor in `pyproject.toml::tool.pytest.ini_options::addopts`; CI SHALL fail the workflow if coverage drops below this floor. The floor SHALL be ratcheted upward (never downward) as test gaps close.
**Rationale**: A monotonically non-decreasing floor prevents per-PR coverage erosion. Ratcheting downward to "fix" a drop hides the regression rather than reverting the change that caused it.
**Verification Method**: Inspection (I)

#### L2-CICD-007

**Parent**: L1-CICD-003
**Statement**: Coverage reports (HTML at `.coverage_html/` and XML at `.coverage.xml`) SHALL be uploaded as workflow artifacts on every CI run, downloadable from the GitHub Actions UI for at least 30 days.
**Rationale**: Artifacts let reviewers inspect line-by-line coverage of a PR without checking out the branch. The 30-day retention covers typical PR review timelines plus post-merge investigation windows.
**Verification Method**: Inspection (I), Demonstration (D)

### Derivations of L1-CICD-004 (traceability gate)

#### L2-CICD-008

**Parent**: L1-CICD-004
**Statement**: `scripts/build-trace-matrix.py` SHALL accept a `--check` flag that re-derives the matrix in memory, compares it byte-for-byte against the committed `docs/TRACE-MATRIX.md`, and exits non-zero on any difference. CI SHALL invoke the script with `--check` as a required job.
**Rationale**: Byte-comparison is the simplest and most precise check — any committed matrix that the script can't reproduce is by definition stale. Required-to-pass means contributors can't merge without regenerating after marker changes.
**Verification Method**: Test (T)

#### L2-CICD-009

**Parent**: L1-CICD-004
**Statement**: The `--check` mode SHALL also fail (with a distinct exit code or error message) if any rollup row is internally inconsistent under the propagation rule from Increment 25a — for example, a parent labeled `Implemented` while any child is `Draft`. The failure message SHALL list the offending parent ids and the children that violate the rule.
**Rationale**: Defense in depth against a drift mode where the committed matrix matches what the script regenerates but both encode an inconsistent state. Listing offenders makes the failure actionable rather than just a "fix it" notice.
**Verification Method**: Test (T)

### Derivations of L1-CICD-005 (test-temp isolation)

#### L2-CICD-010

**Parent**: L1-CICD-005
**Statement**: `pyproject.toml::tool.pytest.ini_options::addopts` SHALL include `--basetemp=.pytest_tmp` so every pytest run roots its temporary files in a workspace-local directory rather than the OS temp directory.
**Rationale**: Workspace-local rooting keeps inspection trivial and surfaces Windows path-quoting issues during development rather than CI.
**Verification Method**: Inspection (I)

#### L2-CICD-011

**Parent**: L1-CICD-005
**Statement**: `.gitignore` SHALL include `.pytest_tmp/` (and the existing `.pytest_cache/` entry SHALL be retained). A conformance test SHALL fail if the ignore is missing.
**Rationale**: Without the ignore, a single forgotten cleanup adds tens or hundreds of test-artifact files to the next commit. The conformance test catches accidental removal.
**Verification Method**: Test (T), Inspection (I)

### Derivations of L1-CICD-006 (reproducibility)

#### L2-CICD-012

**Parent**: L1-CICD-006
**Statement**: `poetry.lock` SHALL be tracked in version control alongside `pyproject.toml`; the existing pre-commit `check-added-large-files` hook SHALL not exempt the lockfile from its size budget.
**Rationale**: Tracking the lockfile is the precondition for reproducibility; the size-check note is a guard against the lockfile being LFS-staged or otherwise treated specially.
**Verification Method**: Inspection (I)

#### L2-CICD-013

**Parent**: L1-CICD-006
**Statement**: The CI workflow SHALL execute `poetry lock --check` (or equivalent reproducibility check, e.g., `poetry install --dry-run --sync` followed by hash comparison) as a required job; failure SHALL block merge.
**Rationale**: Running the check on CI catches the case where a contributor edited `pyproject.toml` without regenerating the lockfile.
**Verification Method**: Test (T)

### Derivations of L1-CICD-007 (build provenance)

#### L2-CICD-014

**Parent**: L1-CICD-007
**Statement**: Each workflow run's logs SHALL include, at the top of the test job, the commit SHA (`${{ github.sha }}`), the runner OS (`${{ runner.os }}`), the Python version, the workflow trigger event, and the run's UTC start timestamp.
**Rationale**: These five fields are the minimum needed to reproduce the run from scratch and to correlate a green/red signal to a specific (commit, environment) tuple.
**Verification Method**: Inspection (I)

#### L2-CICD-015

**Parent**: L1-CICD-007
**Statement**: Each workflow run SHALL upload as artifacts: the regenerated `docs/TRACE-MATRIX.md`, `.coverage.xml`, the `.coverage_html/` directory, and any pytest junit-xml report. Retention SHALL be at least 30 days; the workflow YAML SHALL set `retention-days` explicitly rather than relying on the GitHub Actions default.
**Rationale**: Explicit retention prevents a future GitHub default change from silently expiring artifacts faster than expected. The four artifacts together are sufficient for an auditor reviewing a release tag months after the fact.
**Verification Method**: Inspection (I), Demonstration (D)

---

## Document change history

| Date       | Author | Change            |
|------------|--------|-------------------|
| 2026-04-18 | Joey   | Initial L2 draft  |
| 2026-07-18 | Joey   | R-TMPL-001: added L2-TMPL-015 under L1-TMPL-001 (optional per-pipeline `pipelines.email_body_template_overrides`); reworded L2-MAIL-014 earlier for R-MAIL-001. |
| 2026-07-18 | Joey   | L2-MAIL-014 conformance: reworded to state the subject construction applies to manual resend too (no separate resend format / no override bypass). No new L2. |
