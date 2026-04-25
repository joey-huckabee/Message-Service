# Message-Service — Level 1 Requirements

## Purpose

This document establishes the Level 1 (L1) SHALL-statement requirements for the Message-Service project: a Python service that collects per-stage reports from external ETL pipelines during a run, aggregates them into composite HTML reports, and delivers them by email to subscribed users when the pipeline signals completion.

L1 requirements define **what** the service must do at the highest level of abstraction. They are the root of the requirements tree; L2 requirements decompose each L1 into architectural decisions, and L3 requirements decompose each L2 into implementation-level obligations. All three levels are traced through `docs/TRACE-MATRIX.md`.

## Scope

This document covers the v1 release of Message-Service. Items explicitly deferred to future releases are recorded in `ROADMAP.md` rather than in this document.

## Conventions

### Requirement identifier format

Each L1 requirement is assigned a stable identifier of the form `L1-<CATEGORY>-<NNN>`, where `<CATEGORY>` is the three- or four-letter category code listed in the Table of Categories below, and `<NNN>` is a zero-padded sequence number within that category. Identifiers are permanent: if a requirement is deleted, its identifier is retired and never reused.

L2 and L3 requirements derived from an L1 use `L2-<CATEGORY>-<NNN>` and `L3-<CATEGORY>-<NNN>` respectively, with parent links recorded in the trace matrix.

### SHALL language

Every requirement in this document uses the verb SHALL to express a mandatory obligation, per DO-178C and MIL-STD-498 conventions. SHOULD, MAY, and WILL are not used.

### Requirement metadata

Each requirement carries the following fields:

- **Statement** — the SHALL obligation itself
- **Rationale** — the reason the requirement exists, for the benefit of future maintainers
- **Verification Method** — how compliance is demonstrated, drawn from the DO-178 vocabulary: Test (T), Analysis (A), Inspection (I), Demonstration (D). Multiple methods may apply to one requirement.

**Status and verification artifacts** are tracked in
[`docs/TRACE-MATRIX.md`](TRACE-MATRIX.md) — regenerated from
`@pytest.mark.requirement` markers and the parent links in this file by
`scripts/build-trace-matrix.py`. Per Increment 25a, the matrix is the
single source of truth for live status; the source docs in this file,
`L2-REQ.md`, and `L3-REQ.md` carry only the spec content above.

### Verification method vocabulary

- **Test (T)**: Executable verification by running code or the system and observing outcomes against expected behavior. Implemented as pytest test functions tagged with a `@pytest.mark.requirement()` marker referencing the requirement ID.
- **Analysis (A)**: Logical or mathematical evaluation, including static analysis, model checking, and formal proof. Implemented as analysis documents in `docs/analysis/`.
- **Inspection (I)**: Visual examination of code, documents, or configuration. Implemented as review records in `docs/reviews/`.
- **Demonstration (D)**: Operational observation of the running system by a human operator. Implemented as procedure documents in `docs/procedures/`.

## Table of categories

| Code      | Title                                  | L1 Count |
|-----------|----------------------------------------|----------|
| `API`     | gRPC interface                         | 4        |
| `RUN`     | Run lifecycle                          | 5        |
| `STAGE`   | Stage lifecycle and idempotency        | 4        |
| `TMPL`    | Template governance and sandboxing     | 5        |
| `AGGR`    | Aggregation and composition            | 4        |
| `SWEEP`   | Orphan detection and disposition       | 3        |
| `SUB`     | Subscriptions and tags                 | 4        |
| `AUTH`    | Authentication                         | 2        |
| `MAIL`    | Email delivery                         | 5        |
| `DASH`    | Dashboard                              | 4        |
| `PERS`    | Persistence                            | 4        |
| `OBS`     | Observability                          | 4        |
| `ERR`     | Error handling and exception taxonomy  | 4        |
| `CFG`     | Configuration                          | 3        |
| `DEP`     | Deployment                             | 3        |
| **Total** |                                        | **58**   |

---

## L1-API: gRPC interface

### L1-API-001

**Statement**: The service SHALL expose a gRPC server implementing the `MessageService` protocol defined in the `message-service-proto` external repository.

**Rationale**: The service's primary integration surface is pipeline-facing ingest. Locating the protocol definition in a separate repository decouples its versioning from the service implementation and allows ETL stages to consume the contract independently.

**Verification Method**: Test (T)

### L1-API-002

**Statement**: The service SHALL implement all gRPC endpoints as unary request/response RPCs; server-streaming, client-streaming, and bidirectional-streaming methods SHALL NOT be present in v1.

**Rationale**: Unary RPCs are sufficient for the known call patterns (`BeginRun`, `SubmitStageReport`, `FinalizeRun`) and minimize complexity in both service and pipeline client implementations. Streaming interfaces are recorded on the ROADMAP.

**Verification Method**: Inspection (I)

### L1-API-003

**Statement**: The service SHALL accept gRPC connections over plaintext TCP on the configured listen address and port.

**Rationale**: Initial deployment is on a trusted ISOLAN network where plaintext transport is acceptable. Mutual-TLS support is recorded on the ROADMAP.

**Verification Method**: Test (T)

### L1-API-004

**Statement**: The service SHALL return structured gRPC status codes and error messages for all client-facing validation and operational failures, and SHALL NOT expose implementation stack traces or internal exception details to clients.

**Rationale**: Structured errors enable pipeline clients to handle failures programmatically; withholding internal details prevents information leakage and enforces a stable contract boundary.

**Verification Method**: Test (T)

---

## L1-RUN: Run lifecycle

### L1-RUN-001

**Statement**: The service SHALL mint a universally unique run identifier (UUID v4) upon successful processing of a `BeginRun` request and SHALL return it to the caller in the `BeginRunResponse` message.

**Rationale**: Server-minted run identifiers prevent collisions across concurrent pipelines and guarantee uniqueness without requiring coordination between pipeline stages and the service.

**Verification Method**: Test (T)

### L1-RUN-002

**Statement**: The service SHALL maintain a run-lifecycle state machine with the states `INITIATED`, `AGGREGATING`, `READY`, `SENDING`, `SENT`, `ORPHANED`, and `FAILED`, and SHALL enforce the permitted transitions defined in the L2 derivations of this requirement.

**Rationale**: A formal state machine ensures lifecycle logic is deterministic and auditable and provides a clear basis for L2 and L3 decomposition of transition rules.

**Verification Method**: Test (T), Analysis (A)

### L1-RUN-003

**Statement**: The service SHALL validate all fields of the `BeginRun` request — including pipeline type, run tags, declared stage identifiers, template name and version references, attachment mode, and aggregation template presence where required — before transitioning the run to `INITIATED`, and SHALL reject invalid requests with specific structured error codes without creating run state.

**Rationale**: Fail-fast validation at the entry point keeps invalid runs out of persistent state and surfaces configuration errors to pipeline authors immediately rather than at finalization time.

**Verification Method**: Test (T)

### L1-RUN-004

**Statement**: The service SHALL provide a `FinalizeRun` RPC that transitions a run from `AGGREGATING` to `READY`, triggering the assembly and delivery pipeline.

**Rationale**: An explicit finalization signal from the pipeline is more reliable than inferring completion from stage submissions, particularly when some declared stages may be optional or conditionally executed.

**Verification Method**: Test (T)

### L1-RUN-005

**Statement**: The service SHALL record the UTC timestamp of every run state transition together with the triggering event, and SHALL include these records in the audit log.

**Rationale**: Timestamped transitions are essential for orphan analysis, performance profiling, and incident investigation.

**Verification Method**: Test (T)

---

## L1-STAGE: Stage lifecycle and idempotency

### L1-STAGE-001

**Statement**: The service SHALL maintain a per-stage state machine with the active v1 states `PENDING`, `SUBMITTED`, `ACCEPTED`, `RETRIED`, `TIMEOUT`, and `FAILED`, instantiated once per declared stage within each run. The identifier `IN_PROGRESS` SHALL be reserved in the state-name namespace for a future heartbeat mechanism (see ROADMAP); v1 SHALL NOT enter or persist `IN_PROGRESS` (the SQL `CHECK` constraint and the transition table both reject it), and SHALL NOT expose it as a valid value over any inbound interface.

**Rationale**: A distinct state machine per stage allows fine-grained tracking of pipeline progress and supports orphan detection at stage granularity. Reserving `IN_PROGRESS` in the namespace — rather than introducing it later — preserves backward-compatible state ids when the v2 heartbeat mechanism lands. v1 enforces the reservation at three layers (`StageState` enum allows it; the transition table forbids transitions into it; the SQL `CHECK` constraint rejects persisted values), so the reservation cannot leak into runtime state by accident.

**Verification Method**: Test (T), Analysis (A)

### L1-STAGE-002

**Statement**: The service SHALL treat `SubmitStageReport` calls as idempotent with respect to the composite key `(run_id, stage_id)`: a subsequent submission with the same key SHALL supersede any prior submission, and only the most recent submission SHALL participate in the final aggregated report.

**Rationale**: Idempotency allows pipelines to retry transient submission failures safely without duplicating stage contributions in the final report.

**Verification Method**: Test (T)

### L1-STAGE-003

**Statement**: The service SHALL require every declared stage to call `SubmitStageReport` at least once to transition out of the `PENDING` state, even if the stage contributes no report content and no email body content.

**Rationale**: Explicit submission — even of empty contributions — is necessary for the orphan sweeper to distinguish intentionally silent stages from genuinely missing ones.

**Verification Method**: Test (T)

### L1-STAGE-004

**Statement**: The service SHALL reject `SubmitStageReport` calls whose `stage_id` is not declared in the corresponding run's `BeginRun` metadata, and SHALL return a structured error code indicating the mismatch.

**Rationale**: Rejection prevents stray or erroneous submissions from polluting run state and enforces the declared-stages contract.

**Verification Method**: Test (T)

---

## L1-TMPL: Template governance and sandboxing

### L1-TMPL-001

**Statement**: The service SHALL load all Jinja2 templates from a manifest file discovered at service startup and bundled with the service codebase, and SHALL reject any template reference not present in the manifest.

**Rationale**: Explicit manifest-based registration makes the set of valid templates inspectable and testable and closes off arbitrary filesystem scanning as an attack vector.

**Verification Method**: Test (T), Inspection (I)

### L1-TMPL-002

**Statement**: Every template reference submitted by a pipeline SHALL specify both a `template_name` and a `template_version`, where `template_version` is either an explicit semantic version matching a manifest entry or the literal string `"latest"`, in which case the service SHALL resolve it to the highest available semver for that template.

**Rationale**: Version-pinned templates guarantee report reproducibility; the `"latest"` escape hatch accommodates casual use while preserving the ability to pin.

**Verification Method**: Test (T)

### L1-TMPL-003

**Statement**: The service SHALL render all templates using a Jinja2 `SandboxedEnvironment` configured with `autoescape=True`, `StrictUndefined`, and an explicit whitelist of permitted filters and globals; the sandbox configuration SHALL deny filesystem, network, and module-import access.

**Rationale**: Sandboxing is the primary defense against template-based code execution. Strict undefined handling ensures missing context keys fail visibly rather than producing silently blank output.

**Verification Method**: Test (T), Inspection (I)

### L1-TMPL-004

**Statement**: Each template entry in the manifest SHALL declare a JSON Schema for its permitted context, and the service SHALL validate each submitted context against this schema before rendering.

**Rationale**: Schema validation prevents template rendering failures caused by missing or malformed fields and makes the template-to-stage contract explicit and testable.

**Verification Method**: Test (T)

### L1-TMPL-005

**Statement**: The service SHALL enforce configurable maximum byte-size limits on both the submitted context and the rendered output of every template render operation, and SHALL reject renders that exceed either limit with a structured error code.

**Rationale**: Size limits prevent a malicious or buggy stage from causing denial of service via extremely large context dictionaries or rendered output.

**Verification Method**: Test (T)

---

## L1-AGGR: Aggregation and composition

### L1-AGGR-001

**Statement**: The service SHALL accept two independent content contributions per `SubmitStageReport` call: an optional report contribution rendered into the attachment, and an optional email body contribution rendered inline into the email body. Either or both contributions MAY be empty; the call itself satisfies L1-STAGE-003's submission obligation regardless of contribution content.

**Rationale**: The two-slot model separates the detailed report (attachment) from the summary notification content (email body), allowing stages to contribute to each independently. Both slots are optional so that stages with nothing to add can still discharge L1-STAGE-003's "explicit submission" obligation — the call itself is the signal that the stage ran, distinct from the absence of a call which is what the orphan sweeper detects.

**Verification Method**: Test (T)

### L1-AGGR-002

**Statement**: The service SHALL support two attachment modes, declared per run in `BeginRun` metadata: `SINGLE_AGGREGATED`, in which all stage report contributions are composed into one attachment through the run's aggregation template, and `PER_STAGE`, in which each stage's report contribution becomes a separate attachment.

**Rationale**: Different pipelines have different presentation needs; the per-run attachment mode accommodates both without requiring separate services.

**Verification Method**: Test (T)

### L1-AGGR-003

**Statement**: The service SHALL order all stage contributions — in the aggregated attachment and in the email body alike — according to the `stage_order` field declared for each stage in `BeginRun` metadata, and SHALL NOT order contributions chronologically by submission time.

**Rationale**: Readers expect the report to reflect pipeline structure rather than execution timing; chronological ordering is a confusing artifact of parallel execution.

**Verification Method**: Test (T)

### L1-AGGR-004

**Statement**: When a run's `attachment_mode` is `SINGLE_AGGREGATED`, the service SHALL require an `aggregation_template` to be declared in `BeginRun` metadata, and SHALL reject `BeginRun` requests that omit it.

**Rationale**: A single aggregated attachment requires an explicit composition template; making this a validation error at run initiation prevents late failures at send time.

**Verification Method**: Test (T)

---

## L1-SWEEP: Orphan detection and disposition

### L1-SWEEP-001

**Statement**: The service SHALL run a background orphan sweeper task as an asyncio coroutine within the service process, polling for orphaned runs at a configurable interval.

**Rationale**: An in-process asyncio task avoids external scheduler dependencies such as APScheduler or Celery-beat and provides cross-platform compatibility without additional daemons.

**Verification Method**: Test (T), Inspection (I)

### L1-SWEEP-002

**Statement**: The service SHALL classify a run as orphaned when the elapsed time since its last state transition exceeds the globally configured run-timeout value.

**Rationale**: A time-since-last-transition criterion catches runs that never finalize and runs whose stages go silent mid-execution alike.

**Verification Method**: Test (T)

### L1-SWEEP-003

**Statement**: The service SHALL apply to every orphaned run a globally configured disposition policy consisting of any combination of the following action identifiers: `SEND_PARTIAL_FLAGGED`, `DISCARD_SILENTLY`, `NOTIFY_SUBSCRIBERS`, and `NOTIFY_ADMINS`. v1 SHALL implement only `DISCARD_SILENTLY` and `NOTIFY_ADMINS`; the other two identifiers SHALL remain reserved in the namespace, and configurations referencing them SHALL fail validation at startup with `ConfigurationError` (see L3 derivation under L2-SWEEP-007 / L2-SWEEP-008) until their handlers are implemented (see ROADMAP).

**Rationale**: Different deployment contexts require different orphan behaviors; combining actions in a set permits, for example, both notifying administrators and sending a partial report flagged as incomplete. Reserving the two deferred identifiers in the namespace — rather than introducing them later — keeps the configuration surface stable when the v2 implementations land. Failing fast at startup on an unknown handler prevents the deferred action ids from silently no-op'ing through misconfiguration.

**Verification Method**: Test (T)

---

## L1-SUB: Subscriptions and tags

### L1-SUB-001

**Statement**: The service SHALL support three subscription granularities: `GLOBAL` (all runs), `PIPELINE` (all runs of a specified pipeline type), and `TAG` (all runs declaring a specified tag).

**Rationale**: The three granularities cover the known subscriber-intent cases; users may create multiple subscriptions to combine them.

**Verification Method**: Test (T)

### L1-SUB-002

**Statement**: New user accounts SHALL have no active subscriptions by default; all subscriptions SHALL require explicit opt-in through the dashboard interface.

**Rationale**: Opt-in default prevents unwanted email delivery to new users and places the subscription decision in each user's hands.

**Verification Method**: Test (T)

### L1-SUB-003

**Statement**: The service SHALL load the permitted tag vocabulary from a configuration file at service startup, and SHALL reject `BeginRun` requests and subscription configurations that reference tags not present in this vocabulary.

**Rationale**: A controlled tag vocabulary prevents tag proliferation and typographical errors and aligns tag management with the ISOLAN operational model.

**Verification Method**: Test (T)

### L1-SUB-004

**Statement**: At run-completion time, the service SHALL construct the recipient list as the union of all active subscribers whose `GLOBAL`, `PIPELINE`, or `TAG` subscriptions match the run's pipeline type or declared tags, with per-user de-duplication.

**Rationale**: Union semantics match the principle of least surprise — a user receives a notification if any of their subscriptions apply — and de-duplication prevents multiple emails to users with overlapping subscriptions.

**Verification Method**: Test (T)

---

## L1-AUTH: Authentication

### L1-AUTH-001

**Statement**: The service SHALL authenticate dashboard users against a local account store, with passwords stored exclusively as salted hashes produced by a memory-hard key-derivation function (Argon2id or equivalent).

**Rationale**: Local accounts are the v1 authentication model; Argon2id provides modern resistance to offline brute-force attacks. Additional authentication backends are recorded on the ROADMAP.

**Verification Method**: Test (T), Inspection (I)

### L1-AUTH-002

**Statement**: The service SHALL issue and validate session credentials for authenticated dashboard users with a configurable idle-timeout, after which re-authentication SHALL be required.

**Rationale**: An idle-timeout limits the window of exposure for unattended sessions on shared workstations.

**Verification Method**: Test (T)

---

## L1-MAIL: Email delivery

### L1-MAIL-001

**Statement**: The service SHALL deliver composed emails via SMTP to a configured relay using the standard SMTP submission protocol.

**Rationale**: SMTP is universally supported in enterprise and ISOLAN environments and requires no external dependencies beyond the Python standard library.

**Verification Method**: Test (T)

### L1-MAIL-002

**Statement**: The service SHALL retry transient SMTP failures using exponential backoff, with the maximum retry count, the initial backoff interval, and the maximum backoff interval each independently configurable.

**Rationale**: Transient SMTP failures (relay unreachable, temporary rejection) are common and recoverable; exponential backoff avoids hammering a struggling relay.

**Verification Method**: Test (T)

### L1-MAIL-003

**Statement**: The service SHALL enforce a globally configurable maximum email size (`max_email_size_bytes`) at the assembly stage, measured as the sum of email headers, body, and all attachments, prior to handoff to SMTP.

**Rationale**: Most SMTP relays enforce their own size limits (commonly 25 MB). Enforcing at assembly ensures the service fails predictably and early rather than at the relay.

**Verification Method**: Test (T)

### L1-MAIL-004

**Statement**: When a composed email exceeds `max_email_size_bytes`, the service SHALL transition the run to `FAILED` with reason `EMAIL_SIZE_EXCEEDED`, SHALL NOT attempt SMTP delivery, SHALL persist the rendered report to the filesystem store, and SHALL notify administrators via the same channel used for orphan administrator notifications.

**Rationale**: Reusing the administrator notification channel avoids a parallel failure-alerting pathway; persisting the oversized report preserves the ability to resend, investigate, or download it through the dashboard.

**Verification Method**: Test (T)

### L1-MAIL-005

**Statement**: The service SHALL record every delivery attempt — both successful and failed — in the audit log with timestamp, recipient list, run identifier, and outcome status.

**Rationale**: Delivery audit records support operational troubleshooting and satisfy the audit-log scope agreed for v1.

**Verification Method**: Test (T)

---

## L1-DASH: Dashboard

### L1-DASH-001

**Statement**: The service SHALL expose a web dashboard implemented with FastAPI, accessible over HTTP on a configurable listen address and port.

**Rationale**: FastAPI integrates naturally with the service's asyncio model and provides a mature ecosystem for HTML and REST interfaces.

**Verification Method**: Test (T)

### L1-DASH-002

**Statement**: The dashboard SHALL allow authenticated users to create, view, modify, and delete their own subscriptions at each of the three supported granularities (`GLOBAL`, `PIPELINE`, `TAG`).

**Rationale**: Self-service subscription management removes operational burden from administrators and gives users control over their own notification stream.

**Verification Method**: Test (T), Demonstration (D)

### L1-DASH-003

**Statement**: The dashboard SHALL allow authenticated administrators to view past rendered reports, trigger manual resends to the current active subscriber list, and inspect the template registry contents in a read-only view.

**Rationale**: Resend and past-report access are known operational needs; template inspection supports troubleshooting without requiring filesystem access.

**Verification Method**: Test (T), Demonstration (D)

### L1-DASH-004

**Statement**: The dashboard SHALL present Prometheus service metrics as embedded visualizations, in addition to exposing them at the standard `/metrics` endpoint for external scraping.

**Rationale**: Embedded visualizations give operators immediate visibility without requiring a separate Grafana deployment, while the scrape endpoint preserves integration with standard monitoring stacks.

**Verification Method**: Test (T), Demonstration (D)

---

## L1-PERS: Persistence

### L1-PERS-001

**Statement**: The service SHALL store users, subscriptions, audit log entries, template registry metadata, and in-flight run state in a single SQLite database located at a configurable filesystem path.

**Rationale**: SQLite provides ACID guarantees, built-in write-ahead logging, cross-platform compatibility, and zero operational overhead — appropriate for the single-node deployment model. Co-locating in-flight run state defers the custom-WAL decision to the ROADMAP.

**Verification Method**: Inspection (I), Test (T)

### L1-PERS-002

**Statement**: The service SHALL store rendered HTML reports and Jinja2 template source files on the local filesystem at configurable paths, with one file per rendered report named by its `run_id`.

**Rationale**: Filesystem storage is appropriate for large variable-size artifacts and avoids bloating the SQLite database; per-`run_id` naming supports direct access from the dashboard and resend flow.

**Verification Method**: Inspection (I), Test (T)

### L1-PERS-003

**Statement**: The service SHALL access all persistence stores through repository-pattern abstractions; domain and application layer code SHALL NOT contain direct database queries or filesystem calls.

**Rationale**: The repository pattern enforces Fowler-style separation of concerns and preserves the ability to swap persistence backends without touching domain logic.

**Verification Method**: Inspection (I)

### L1-PERS-004

**Statement**: Rendered HTML reports persisted under L1-PERS-002 SHALL be retained on disk for at least `persistence.filesystem.report_retention_days` (default 90), after which a background pruner task SHALL evict reports whose `run_id` corresponds to a run whose terminal-state transition is older than the retention window. The pruner SHALL audit each evicted report (one record per file) so the deletion is traceable to operator intent rather than appearing as silent data loss.

**Rationale**: Without a retention policy, the rendered-reports directory grows unbounded — one orphan, per-stage attachment per run, every run, forever. Operators reviewing past runs through the dashboard need recent reports available; long-term storage of every rendered HTML is rarely the operational requirement and is not what the audit log is for. The retention key gives operations the ability to set the window; the pruner-with-audit pattern mirrors the audit-log retention pattern from L1-OBS-003 so the operational mental model is consistent across both retention concerns.

**Verification Method**: Test (T), Inspection (I)

---

## L1-OBS: Observability

### L1-OBS-001

**Statement**: The service SHALL emit all log records in structured JSON format to standard output, with each record containing at a minimum a timestamp, severity level, logger name, message, and relevant contextual identifiers such as `run_id` and `stage_id` where applicable.

**Rationale**: JSON-structured logs to stdout support the 12-factor log-aggregation model and enable machine-parseable analysis across the ISOLAN deployment.

**Verification Method**: Inspection (I), Test (T)

### L1-OBS-002

**Statement**: The service SHALL expose Prometheus-format metrics at a standard `/metrics` endpoint, covering at minimum: run-lifecycle state transition counts, stage submission counts, email delivery outcome counts, email size percentiles, and orphan sweep outcome counts.

**Rationale**: These metrics cover the primary operational concerns — throughput, success rate, resource pressure, and failure modes. Additional metrics may be added as needs emerge.

**Verification Method**: Test (T), Inspection (I)

### L1-OBS-003

**Statement**: The service SHALL maintain an append-only audit log covering every governance-relevant action category, including (non-exhaustive): pipeline-initiated lifecycle events (`BEGIN_RUN`, `SUBMIT_STAGE_REPORT`, `FINALIZE_RUN`), service-driven state transitions (`RUN_STATE_TRANSITION`, `STAGE_STATE_TRANSITION`), the orphan sweeper (`SWEEP_ORPHAN`), email delivery outcomes (`SEND_REPORT` with success/failure), subscription changes (`SUBSCRIBE`, `UNSUBSCRIBE`), user-account management (`CREATE_USER`, `UPDATE_USER`), and authentication events (`LOGIN`, `LOGIN_FAILED`, `LOGOUT`). Each record SHALL carry timestamp, action, actor, resource, outcome, and structured details. Records SHALL be retained for a globally configurable duration. The exhaustive set of recorded action identifiers is the `AuditAction` enum in `src/message_service/domain/aggregates/audit_event.py`.

**Rationale**: An audit log limited to email delivery would miss the lifecycle and authentication events that incident investigation routinely needs. Widening to the action set the implementation already records (without further code change) makes the spec match reality and gives operations a single tail-able audit stream covering every governance-relevant action. Per-category L2 derivations document the field shapes; the retention key (`observability.audit.retention_days`) gives operations the ability to meet site-specific retention requirements without code changes.

**Verification Method**: Test (T)

### L1-OBS-004

**Statement**: The service SHALL emit log records at appropriate severity levels drawn from the Python `logging` standard taxonomy (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`), with consistent level-assignment rules applied across all components.

**Rationale**: Consistent level assignment is what makes log filtering in production useful; without a documented convention, operators face a mix of overly-verbose and overly-quiet components.

**Verification Method**: Inspection (I), Test (T)

---

## L1-ERR: Error handling and exception taxonomy

### L1-ERR-001

**Statement**: The service SHALL define a hierarchical exception taxonomy rooted at a single base class `MessageServiceError`, with distinct subclasses for domain errors, validation errors, infrastructure errors, and configuration errors.

**Rationale**: A rooted hierarchy allows exception handlers to catch broad categories (`except InfrastructureError`) or specific cases (`except SmtpTransientError`), and makes the full set of expected error conditions enumerable and documentable.

**Verification Method**: Inspection (I), Test (T)

### L1-ERR-002

**Statement**: The service SHALL attach a stable machine-readable error code to every exception instance, drawn from a single enumerated set shared between the exception classes and the proto-defined error codes.

**Rationale**: A shared error code set keeps the exception-to-gRPC-status mapping mechanical and prevents drift between internal and external error identifiers.

**Verification Method**: Inspection (I), Test (T)

### L1-ERR-003

**Statement**: Every exception raised within the domain or application layer SHALL be caught at an inbound interface boundary (gRPC servicer, FastAPI route, CLI entry point, or background task), translated to the appropriate transport-level error response, and logged at a severity level appropriate to the exception category.

**Rationale**: Catching exceptions exclusively at interface boundaries keeps error-handling logic out of domain code and ensures uniform response translation across all callers.

**Verification Method**: Test (T), Inspection (I)

### L1-ERR-004

**Statement**: Exceptions SHALL NOT be silently swallowed; every caught exception SHALL either be logged, re-raised, or translated to a transport error with an associated log record.

**Rationale**: Silent swallowing is the single most common source of "it doesn't work and I can't tell why" operational issues; explicit handling of every caught exception is the remedy.

**Verification Method**: Inspection (I), Analysis (A)

---

## L1-CFG: Configuration

### L1-CFG-001

**Statement**: The service SHALL load all runtime configuration from a single TOML configuration file whose path is specified via a command-line argument or environment variable at startup, in conformance with the 12-factor app principle of separating configuration from code.

**Rationale**: Single-file TOML configuration is readable, version-controllable, and aligns with 12-factor separation of configuration from code. TOML is preferred over YAML for its stricter parsing semantics.

**Verification Method**: Inspection (I), Test (T)

### L1-CFG-002

**Statement**: The service SHALL validate all configuration values at startup against a schema, and SHALL refuse to start with a structured error if any required value is missing, malformed, or out of range.

**Rationale**: Startup-time validation prevents configuration errors from causing unpredictable runtime behavior and surfaces problems immediately to operators.

**Verification Method**: Test (T)

### L1-CFG-003

**Statement**: The configuration schema SHALL include at minimum the following settings, grouped by area:

- **Network**: gRPC listen address and port; FastAPI listen address and port.
- **Persistence**: SQLite database path; SQLite connection-pool size; rendered-report directory path; rendered-report retention duration (`persistence.filesystem.report_retention_days`, see L1-PERS-004); rendered-report pruner cadence and per-iteration cap.
- **Templates**: template manifest path; email-body template reference (name + version); maximum context byte size; maximum rendered byte size.
- **Tags and pipelines**: tag vocabulary path; registered pipeline-type list.
- **Sweeper**: global run timeout; orphan sweeper poll interval; orphan disposition policy set; per-tick maximum candidates (`sweeper.max_candidates_per_iteration`, see L3-SWEEP-008).
- **Mail**: SMTP relay address, port, credentials, and STARTTLS toggle; from-address; maximum email size in bytes; administrator recipient list; SMTP retry knobs (max attempts, initial interval, max interval).
- **Auth and dashboard**: session idle timeout.
- **Observability**: audit log retention duration; log level.

**Rationale**: Explicit enumeration of required settings ensures that no critical behavior is driven by hidden defaults, and gives the operations team a checklist for deployment configuration. Grouping by area (rather than the original flat list) keeps the checklist scannable as the configuration surface grows. The list is the **floor** — the schema may add fields beyond these, but every field below SHALL be present in the schema and have a default or be operator-required.

**Verification Method**: Inspection (I)

---

## L1-DEP: Deployment

### L1-DEP-001

**Statement**: The service SHALL run without modification on both Linux (x86_64) and Windows (x86_64) host operating systems, and SHALL NOT contain platform-specific code paths in domain or application layers.

**Rationale**: Dual-platform support is a stated requirement driven by the user's deployment environments; keeping platform-specific code confined to infrastructure layers preserves portability.

**Verification Method**: Test (T), Demonstration (D)

### L1-DEP-002

**Statement**: The service SHALL provide a systemd unit file for Linux deployment and a documented installation procedure for Windows Service deployment via NSSM, with both mechanisms supporting the standard lifecycle operations of start, stop, restart, and status.

**Rationale**: Both mechanisms are industry-standard for their respective platforms; NSSM avoids introducing pywin32 as a runtime dependency in the service codebase.

**Verification Method**: Demonstration (D), Inspection (I)

### L1-DEP-003

**Statement**: The service SHALL be packaged and distributable as a single installable Poetry project, with all runtime and development dependencies pinned to specific versions in the Poetry lockfile.

**Rationale**: Poetry and pinned dependencies produce reproducible builds, which are essential for air-gapped ISOLAN deployments where offline installation is the norm.

**Verification Method**: Inspection (I), Test (T)

---

## Document change history

| Date       | Author | Change            |
|------------|--------|-------------------|
| 2026-04-18 | Joey   | Initial L1 draft  |
