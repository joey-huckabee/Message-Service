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
| `RUN`     | Run lifecycle                          | 15       |
| `STAGE`   | Stage lifecycle and idempotency        | 9        |
| `TMPL`    | Template governance and sandboxing     | 14       |
| `AGGR`    | Aggregation and composition            | 10       |
| `SWEEP`   | Orphan detection and disposition       | 9        |
| `SUB`     | Subscriptions and tags                 | 10       |
| `AUTH`    | Authentication                         | 6        |
| `MAIL`    | Email delivery                         | 13       |
| `DASH`    | Dashboard                              | 11       |
| `PERS`    | Persistence                            | 10       |
| `OBS`     | Observability                          | 12       |
| `ERR`     | Error handling and exception taxonomy  | 10       |
| `CFG`     | Configuration                          | 8        |
| `DEP`     | Deployment                             | 9        |
| **Total** |                                        | **166**  |

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
**Statement**: The orphan sweeper SHALL classify any stage in state `PENDING` at orphan-timeout evaluation as missing, and the run containing such a stage SHALL be treated according to the orphan disposition policy.
**Rationale**: The sweeper's definition of "missing" is the contrapositive of L1-STAGE-003's explicit-submission obligation.
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
**Statement**: Validation of the `aggregation_template` field in `BeginRun` SHALL occur at request initiation, rejecting omissions with error code `MISSING_AGGREGATION_TEMPLATE`.
**Rationale**: See L2-RUN-011; this duplicates the statement here to anchor it under the AGGR category for trace clarity.
**Verification Method**: Test (T)

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
**Statement**: The FastAPI application SHALL be constructed via a factory function `create_app(config)` rather than a module-level singleton, to support test isolation and repeated construction in unit tests.
**Rationale**: Factory construction is the FastAPI-recommended pattern for testable applications.
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
**Statement**: The service SHALL maintain a connection pool sized to accommodate concurrent gRPC servicer calls and FastAPI request handlers, with pool size controlled by configuration key `persistence.connection_pool_size`.
**Rationale**: Explicit pool sizing prevents connection exhaustion under concurrent load and gives operators a tuning knob.
**Verification Method**: Inspection (I)

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
**Verification Method**: Inspection (I)

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

## Document change history

| Date       | Author | Change            |
|------------|--------|-------------------|
| 2026-04-18 | Joey   | Initial L2 draft  |
