# Message-Service — Level 3 Requirements

## Purpose

This document establishes the Level 3 (L3) SHALL-statement requirements. L3 requirements decompose each L2 into **implementation-level obligations**: specific function names, configuration keys, library calls, behaviour under edge conditions, exact values and thresholds, and test coverage scope.

Every L3 traces to exactly one L2 parent. L3s are the layer at which requirements become directly testable against code.

## Conventions

L3 identifiers follow the format `L3-<CATEGORY>-<NNN>`. L3 statements use a compact two-line format to keep the document navigable at scale:

> **L3-XXX-NNN** · Parent: L2-XXX-NNN · Verification: T · Status: Draft
> *Statement.*

Verification method abbreviations (DO-178/MIL-STD vocabulary): **T** = Test, **A** = Analysis, **I** = Inspection, **D** = Demonstration. Multiple methods are comma-separated.

Verification artifacts are all `(TBD)` at this stage. They are populated in `TRACE-MATRIX.md` as implementation proceeds — the pytest marker `@pytest.mark.requirement("L3-XXX-NNN")` is the binding from test code to requirement.

## Table of categories

| Code      | L2 Parent Count | L3 Count |
|-----------|-----------------|----------|
| `API`     | 11              | 18       |
| `RUN`     | 15              | 30       |
| `STAGE`   | 9               | 18       |
| `TMPL`    | 14              | 28       |
| `AGGR`    | 10              | 20       |
| `SWEEP`   | 9               | 18       |
| `SUB`     | 10              | 20       |
| `AUTH`    | 6               | 13       |
| `MAIL`    | 13              | 26       |
| `DASH`    | 11              | 21       |
| `PERS`    | 10              | 23       |
| `OBS`     | 9               | 18       |
| `CFG`     | 8               | 16       |
| `DEP`     | 9               | 18       |
| **Total** | **144**         | **287**  |

---

## L3-API: gRPC interface

**L3-API-001** · Parent: L2-API-001 · Verification: I · Status: Draft
The gRPC server SHALL be instantiated with `maximum_concurrent_rpcs` loaded from configuration key `grpc.max_concurrent_rpcs` (default 100).

**L3-API-002** · Parent: L2-API-001 · Verification: I · Status: Draft
The gRPC server SHALL register a logging interceptor that binds `correlation_id` to the structlog context at RPC entry and clears it in a `finally` block.

**L3-API-003** · Parent: L2-API-002 · Verification: I · Status: Draft
The `pyproject.toml` dependency on `message-service-proto` SHALL pin to a specific git tag (not a branch) for production builds; the local-path variant is permitted only during development and SHALL NOT be committed to main.

**L3-API-004** · Parent: L2-API-002 · Verification: I · Status: Draft
A CI check SHALL fail the build if the installed `message_service_proto.__version__` differs from the version recorded in the Poetry lockfile.

**L3-API-005** · Parent: L2-API-003 · Verification: T · Status: Draft
A unit test SHALL enumerate the registered servicer methods and assert the set equals `{"BeginRun", "SubmitStageReport", "FinalizeRun"}`.

**L3-API-006** · Parent: L2-API-004 · Verification: I, A · Status: Draft
Every servicer method SHALL be declared as `async def` with exactly two parameters, `request` and `context`; a ruff or ast-grep rule SHALL enforce this signature.

**L3-API-007** · Parent: L2-API-005 · Verification: T · Status: Draft
An import-time test SHALL inspect the servicer class and assert that no public method accepts a streaming iterator parameter or returns an async iterator.

**L3-API-008** · Parent: L2-API-006 · Verification: T · Status: Draft
Service startup SHALL call `server.add_insecure_port(f"{host}:{port}")` and SHALL NOT call `add_secure_port()` or construct any `grpc.ServerCredentials` object.

**L3-API-009** · Parent: L2-API-007 · Verification: T · Status: Draft
`grpc.host` default SHALL be `"0.0.0.0"` and `grpc.port` default SHALL be `50051`; missing keys SHALL use defaults rather than failing startup.

**L3-API-010** · Parent: L2-API-007 · Verification: T · Status: Draft
`grpc.port` SHALL be validated as an integer in [1, 65535]; out-of-range values SHALL raise `ConfigurationError` at startup.

**L3-API-011** · Parent: L2-API-008 · Verification: T · Status: Draft
The error mapping SHALL attach `x-message-service-error-code` trailing metadata with the exception's `error_code` attribute on every error response.

**L3-API-012** · Parent: L2-API-008 · Verification: T · Status: Draft
For each concrete `ValidationError` subclass, a test SHALL trigger the error and assert the returned gRPC status is `INVALID_ARGUMENT` with the expected error_code metadata.

**L3-API-013** · Parent: L2-API-009 · Verification: T · Status: Draft
`RunNotFound` SHALL translate to gRPC `NOT_FOUND` with trailing metadata `x-message-service-error-code: ERROR_CODE_RUN_NOT_FOUND`.

**L3-API-014** · Parent: L2-API-010 · Verification: T · Status: Draft
The `INTERNAL` correlation id SHALL be a 32-character hexadecimal UUID v4 string (no hyphens), generated via `uuid.uuid4().hex`.

**L3-API-015** · Parent: L2-API-010 · Verification: T · Status: Draft
The correlation id SHALL appear both in the `x-message-service-correlation-id` trailing metadata and in the log record emitted by the translation layer, with identical values.

**L3-API-016** · Parent: L2-API-010 · Verification: T · Status: Draft
The client-facing detail message for an `INTERNAL` error SHALL be exactly `"internal error (correlation id: {id})"`; no other information SHALL appear.

**L3-API-017** · Parent: L2-API-011 · Verification: I · Status: Draft
The proto `ErrorCode` enum SHALL reserve value 0 as `ERROR_CODE_UNSPECIFIED`; no semantic code SHALL occupy value 0.

**L3-API-018** · Parent: L2-API-011 · Verification: A · Status: Draft
A static analysis SHALL verify that every concrete subclass of `MessageServiceError` has an `error_code` attribute matching a value in the proto `ErrorCode` enum.

---

## L3-RUN: Run lifecycle

**L3-RUN-001** · Parent: L2-RUN-001 · Verification: T · Status: Draft
`uuid.uuid4()` SHALL be called exactly once per `BeginRun` request, with the result assigned to the new `Run` aggregate's `run_id`.

**L3-RUN-002** · Parent: L2-RUN-002 · Verification: T · Status: Draft
The run identifier SHALL be stored as `str` (not `uuid.UUID`) in all persistence layers to avoid type round-trip and preserve lexicographic sort.

**L3-RUN-003** · Parent: L2-RUN-002 · Verification: T · Status: Draft
Functions accepting `run_id` SHALL reject strings not matching `^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$` with `MalformedRequest`.

**L3-RUN-004** · Parent: L2-RUN-003 · Verification: T · Status: Draft
The `Run` aggregate SHALL be persisted in a single transaction that also inserts the initial audit record; failure of either insert SHALL roll back both.

**L3-RUN-005** · Parent: L2-RUN-003 · Verification: T · Status: Draft
If persistence fails between UUID minting and response send, the response SHALL NOT be returned; the servicer raises `PersistenceError` which translates to `INTERNAL`.

**L3-RUN-006** · Parent: L2-RUN-004 · Verification: T, A · Status: Draft
The run state transition table SHALL be a frozen `dict[RunState, frozenset[RunState]]` at module top level, inspectable via `import` and testable in isolation.

**L3-RUN-007** · Parent: L2-RUN-004 · Verification: T · Status: Draft
A hypothesis property-based test SHALL generate all state pairs and assert only permitted transitions succeed.

**L3-RUN-008** · Parent: L2-RUN-005 · Verification: T · Status: Draft
`InvalidStateTransition.details` SHALL carry `from_state`, `to_state`, and `run_id`.

**L3-RUN-009** · Parent: L2-RUN-006 · Verification: T · Status: Draft
Transitioning a run in `SENT`, `FAILED`, or `ORPHANED` to any other state SHALL raise `InvalidStateTransition` immediately, before any repository call.

**L3-RUN-010** · Parent: L2-RUN-007 · Verification: T · Status: Draft
The pipeline registry SHALL be a `frozenset[str]` loaded at startup from `pipelines.registered`; lookups are O(1).

**L3-RUN-011** · Parent: L2-RUN-007 · Verification: T · Status: Draft
`UnknownPipelineType.details` SHALL include `submitted` and `allowed` (sorted list).

**L3-RUN-012** · Parent: L2-RUN-008 · Verification: T · Status: Draft
Tag validation SHALL be case-sensitive with no normalization; submitters must match the vocabulary exactly.

**L3-RUN-013** · Parent: L2-RUN-008 · Verification: T · Status: Draft
When multiple tags are invalid, `UnknownTag.details["invalid_tags"]` SHALL contain the full list, not just the first.

**L3-RUN-014** · Parent: L2-RUN-009 · Verification: T · Status: Draft
Duplicate detection SHALL use `dict[stage_id, count]` accumulation; `DuplicateStageId.details["duplicates"]` lists every stage_id appearing more than once.

**L3-RUN-015** · Parent: L2-RUN-009 · Verification: T · Status: Draft
Empty `declared_stages` SHALL be permitted; the run finalizes with no attachments if `FinalizeRun` is called immediately.

**L3-RUN-016** · Parent: L2-RUN-010 · Verification: T · Status: Draft
Template reference validation SHALL apply to every `TemplateRef`: aggregation_template, each declared stage's report_template, and any email body template at submission time.

**L3-RUN-017** · Parent: L2-RUN-010 · Verification: T · Status: Draft
`UnknownTemplate.details` SHALL include both `name` and `version` to distinguish "unknown template" from "unknown version".

**L3-RUN-018** · Parent: L2-RUN-011 · Verification: T · Status: Draft
When `attachment_mode` is `PER_STAGE`, a submitted `aggregation_template` SHALL be silently ignored, not a validation error.

**L3-RUN-019** · Parent: L2-RUN-011 · Verification: T · Status: Draft
`MissingAggregationTemplate.details` SHALL include `attachment_mode` echoed back for clarity.

**L3-RUN-020** · Parent: L2-RUN-012 · Verification: T · Status: Draft
`FinalizeRun` against a run not in `AGGREGATING` SHALL raise `InvalidRunState` with `details={"current_state": ..., "run_id": ...}`.

**L3-RUN-021** · Parent: L2-RUN-012 · Verification: T · Status: Draft
`FinalizeRun` against a run in `INITIATED` SHALL be permitted only if `declared_stages` was empty; otherwise rejected.

**L3-RUN-022** · Parent: L2-RUN-013 · Verification: T · Status: Draft
The assembly task SHALL be created via `asyncio.create_task()` and stored in a service-lifetime set to prevent garbage collection; `FinalizeRun` returns before the task completes.

**L3-RUN-023** · Parent: L2-RUN-013 · Verification: T · Status: Draft
If the assembly task fails with an unhandled exception, the error SHALL be logged at ERROR and the run SHALL transition to `FAILED`; the original `FinalizeRun` response is unaffected.

**L3-RUN-024** · Parent: L2-RUN-014 · Verification: I, T · Status: Draft
The `Clock` port SHALL expose `now() -> datetime` returning a timezone-aware UTC `datetime`; implementations are `SystemClock` and `FakeClock`.

**L3-RUN-025** · Parent: L2-RUN-014 · Verification: T · Status: Draft
All persisted timestamps SHALL be ISO-8601 strings with literal `"Z"` suffix (not `"+00:00"`); a regex test SHALL enforce this on every written row.

**L3-RUN-026** · Parent: L2-RUN-015 · Verification: T · Status: Draft
Audit-first ordering SHALL be enforced by performing audit `INSERT` and state `UPDATE` in one transaction, with audit insert preceding state update in statement order.

**L3-RUN-027** · Parent: L2-RUN-015 · Verification: T · Status: Draft
If the audit insert fails, the state update SHALL NOT be attempted; the transaction rolls back leaving the run in its prior state.

**L3-RUN-028** · Parent: L2-RUN-004 · Verification: T · Status: Draft
Direct transitions from any non-terminal state to `FAILED` SHALL be permitted without intermediate steps, supporting abort-on-error paths.

**L3-RUN-029** · Parent: L2-RUN-004 · Verification: T · Status: Draft
The `SENDING -> FAILED` transition SHALL record a `failure_reason` enum from `{EMAIL_SIZE_EXCEEDED, PERMANENT_SMTP_FAILURE, INTERNAL_ERROR}` in the audit record.

**L3-RUN-030** · Parent: L2-RUN-002 · Verification: T · Status: Draft
`RunState` SHALL be a Python `enum.StrEnum` in `domain/state_machines/run_states.py`; comparisons use identity (`is`) in domain code.

---

## L3-STAGE: Stage lifecycle and idempotency

**L3-STAGE-001** · Parent: L2-STAGE-001 · Verification: I, T · Status: Draft
`StageState` SHALL be a Python `enum.StrEnum` in `domain/state_machines/stage_states.py`.

**L3-STAGE-002** · Parent: L2-STAGE-001 · Verification: T · Status: Draft
Stage records SHALL be stored in table `stage_state` with primary key `(run_id, stage_id)` and a foreign key on `run_id` referencing `runs`.

**L3-STAGE-003** · Parent: L2-STAGE-002 · Verification: T, A · Status: Draft
The permitted-stage-transition table SHALL live in the same module as `StageState`; a unit test SHALL assert `IN_PROGRESS` has no inbound edges in v1.

**L3-STAGE-004** · Parent: L2-STAGE-002 · Verification: T · Status: Draft
The transition function SHALL accept an optional `caller_context` string for audit; when omitted, the caller's module name SHALL be captured via stack inspection.

**L3-STAGE-005** · Parent: L2-STAGE-003 · Verification: T · Status: Draft
The `stage_state` table SHALL have a unique index on `(run_id, stage_id)`; duplicate INSERTs raise `aiosqlite.IntegrityError`, handled by the idempotent-update path.

**L3-STAGE-006** · Parent: L2-STAGE-004 · Verification: T · Status: Draft
SubmitStageReport SHALL use `INSERT ... ON CONFLICT(run_id, stage_id) DO UPDATE` in a single SQL statement for atomicity.

**L3-STAGE-007** · Parent: L2-STAGE-004 · Verification: T · Status: Draft
Prior submission content SHALL be overwritten in place, not retained; a separate audit record preserves that a retry occurred with its timestamp.

**L3-STAGE-008** · Parent: L2-STAGE-005 · Verification: T · Status: Draft
Clearing an email body contribution SHALL be implemented by passing `email_body_contribution = None`; a test SHALL verify the persisted `email_body_context` column is `NULL` afterward.

**L3-STAGE-009** · Parent: L2-STAGE-005 · Verification: T · Status: Draft
The stage record schema SHALL have separate columns for `report_context_json` and `email_body_context_json`, allowing each to be null independently.

**L3-STAGE-010** · Parent: L2-STAGE-006 · Verification: T · Status: Draft
A SubmitStageReport with empty Protobuf `Struct` context (not null, but zero fields) SHALL be accepted; the stored context is the empty JSON object `"{}"`.

**L3-STAGE-011** · Parent: L2-STAGE-006 · Verification: T · Status: Draft
A SubmitStageReport with both `report_contribution.context` and `email_body_contribution` omitted SHALL transition to `SUBMITTED` with a contribution row containing null content.

**L3-STAGE-012** · Parent: L2-STAGE-007 · Verification: T · Status: Draft
The sweeper SHALL classify stages by reading `stage_state` and checking equality with `StageState.PENDING`; no per-stage elapsed-time check is applied.

**L3-STAGE-013** · Parent: L2-STAGE-007 · Verification: T · Status: Draft
A run with any stage in `PENDING` at orphan-timeout evaluation SHALL have that stage's id included in the audit record under `pending_stages`.

**L3-STAGE-014** · Parent: L2-STAGE-008 · Verification: T · Status: Draft
Declared-stage lookup SHALL use `runs.declared_stage_ids_json` parsed once per request; the resulting set is used for membership check.

**L3-STAGE-015** · Parent: L2-STAGE-008 · Verification: T · Status: Draft
`UnknownStage.details` SHALL include `submitted_stage_id` and `declared_stage_ids` (sorted).

**L3-STAGE-016** · Parent: L2-STAGE-009 · Verification: T · Status: Draft
Run existence check SHALL precede stage membership check in code order; a test SHALL exercise "unknown run + unknown stage" and assert `RunNotFound` is raised, not `UnknownStage`.

**L3-STAGE-017** · Parent: L2-STAGE-001 · Verification: T · Status: Draft
Stage records SHALL store `last_transition_at`; the sweeper uses run's `last_transition_at`, not any stage's.

**L3-STAGE-018** · Parent: L2-STAGE-002 · Verification: A · Status: Draft
A static check SHALL confirm no production code path references `StageState.IN_PROGRESS` as a transition target; grep results limited to test fixtures and a reserved-for-future-use comment.

---

## L3-TMPL: Template governance and sandboxing

**L3-TMPL-001** · Parent: L2-TMPL-001 · Verification: T · Status: Draft
The template manifest SHALL be parsed using `tomllib` (3.11+) or `tomli` (3.10); a test SHALL verify both paths on the CI matrix.

**L3-TMPL-002** · Parent: L2-TMPL-001 · Verification: T · Status: Draft
Manifest parse failures SHALL raise `ConfigurationError` with TOML parser error location in `details`.

**L3-TMPL-003** · Parent: L2-TMPL-002 · Verification: T · Status: Draft
`source_path` and `schema_path` SHALL be resolved via `Path(manifest_path).parent / path` with `Path.resolve()` to absolute paths.

**L3-TMPL-004** · Parent: L2-TMPL-002 · Verification: T · Status: Draft
Resolved paths that escape the manifest's directory (contain `..` components after resolution) SHALL be rejected at startup with `ConfigurationError`.

**L3-TMPL-005** · Parent: L2-TMPL-003 · Verification: T · Status: Draft
Startup SHALL attempt to open each declared `source_path` and `schema_path`; `OSError` on any file aborts startup with `ConfigurationError`.

**L3-TMPL-006** · Parent: L2-TMPL-003 · Verification: T · Status: Draft
Duplicate `(name, version)` pairs SHALL be detected by set-size comparison; the first duplicate encountered is reported.

**L3-TMPL-007** · Parent: L2-TMPL-004 · Verification: T · Status: Draft
Version parsing SHALL use `packaging.version.Version`; `"latest"` is special-cased and SHALL NOT pass through `Version()`.

**L3-TMPL-008** · Parent: L2-TMPL-004 · Verification: T · Status: Draft
Versions comparing equal via `packaging.version.Version` (e.g., `1.0` vs `1.0.0`) SHALL be treated as the same; the manifest loader rejects such equivalences as duplicates.

**L3-TMPL-009** · Parent: L2-TMPL-005 · Verification: T · Status: Draft
`"latest"` resolution SHALL occur exactly once per run at `BeginRun` time; the resolved `Version` SHALL be serialized back to canonical string form before persisting.

**L3-TMPL-010** · Parent: L2-TMPL-005 · Verification: T · Status: Draft
If a template name has no manifest entries, `"latest"` resolution SHALL raise `UnknownTemplate`; otherwise it picks the highest `Version`.

**L3-TMPL-011** · Parent: L2-TMPL-006 · Verification: T · Status: Draft
The resolved version SHALL be persisted to `runs.resolved_templates_json` (a JSON map `name -> version`) at `BeginRun` time.

**L3-TMPL-012** · Parent: L2-TMPL-006 · Verification: T · Status: Draft
Audit records of rendered reports SHALL include the `resolved_templates_json` snapshot taken at run initiation; subsequent manifest updates SHALL NOT affect already-initiated runs.

**L3-TMPL-013** · Parent: L2-TMPL-007 · Verification: T, I · Status: Draft
The `SandboxedEnvironment` SHALL be constructed with `autoescape=True`, `undefined=StrictUndefined`, and `loader=FileSystemLoader` restricted to manifest source directories.

**L3-TMPL-014** · Parent: L2-TMPL-007 · Verification: T · Status: Draft
A smoke test SHALL render templates containing `{{ config }}`, `{{ self.__class__ }}`, `{% import 'os' %}` and assert each raises `jinja2.SecurityError` or `UndefinedError`.

**L3-TMPL-015** · Parent: L2-TMPL-008 · Verification: T · Status: Draft
After environment construction, the filter dictionary SHALL be cleared (`env.filters.clear()`) then repopulated only with whitelisted filters by name.

**L3-TMPL-016** · Parent: L2-TMPL-008 · Verification: T · Status: Draft
A test SHALL assert `{{ x | tojson }}`, `{{ x | reverse }}`, `{{ x | attr('__class__') }}` each raise `TemplateAssertionError` or equivalent.

**L3-TMPL-017** · Parent: L2-TMPL-009 · Verification: T · Status: Draft
The render call SHALL catch `jinja2.UndefinedError` and convert to `ContextSchemaViolation` with `details={"missing_variable": ...}` before propagation.

**L3-TMPL-018** · Parent: L2-TMPL-010 · Verification: I · Status: Draft
Schema validation SHALL use `jsonschema.Draft202012Validator`; the validator instance SHALL be constructed once per template (cached) and reused.

**L3-TMPL-019** · Parent: L2-TMPL-010 · Verification: T · Status: Draft
A test SHALL exercise a schema with `$ref` to external schemas and assert resolution is relative to the schema file location, not CWD.

**L3-TMPL-020** · Parent: L2-TMPL-011 · Verification: T · Status: Draft
`ContextSchemaViolation.details` SHALL include `schema_path` (JSON Pointer like `"/foo/bar/0"`), `instance_value`, and the schema's `message` field.

**L3-TMPL-021** · Parent: L2-TMPL-012 · Verification: T · Status: Draft
Context size measurement SHALL use `json.dumps(context, sort_keys=True, separators=(",", ":")).encode("utf-8")` length for determinism.

**L3-TMPL-022** · Parent: L2-TMPL-012 · Verification: T · Status: Draft
Size measurement SHALL occur BEFORE schema validation to avoid schema work on payloads that will be rejected for size.

**L3-TMPL-023** · Parent: L2-TMPL-013 · Verification: T · Status: Draft
Rendered output size measurement SHALL use `len(rendered.encode("utf-8"))`; the check runs after `env.render()` returns and before the rendered string is persisted.

**L3-TMPL-024** · Parent: L2-TMPL-013 · Verification: T · Status: Draft
If rendered output exceeds the limit, it SHALL be discarded without writing to disk; `RenderedSizeExceeded.details` reports measured size and limit.

**L3-TMPL-025** · Parent: L2-TMPL-014 · Verification: T · Status: Draft
`templates.max_context_bytes` default SHALL be `1_048_576` (1 MiB); `templates.max_rendered_bytes` default SHALL be `10_485_760` (10 MiB).

**L3-TMPL-026** · Parent: L2-TMPL-014 · Verification: T · Status: Draft
Both limits SHALL be validated as positive integers at startup; zero or negative values raise `ConfigurationError`.

**L3-TMPL-027** · Parent: L2-TMPL-003 · Verification: I · Status: Draft
The `SandboxedEnvironment` SHALL be constructed exactly once per service lifetime (not per request) and accessed as a DI singleton.

**L3-TMPL-028** · Parent: L2-TMPL-007 · Verification: A · Status: Draft
A security review SHALL be recorded in `docs/reviews/` after any change to the template sandbox configuration.

---

## L3-AGGR: Aggregation and composition

**L3-AGGR-001** · Parent: L2-AGGR-001 · Verification: I · Status: Draft
The `ReportContribution` proto message SHALL use `google.protobuf.Struct` for `context` to allow arbitrary JSON-like structure without per-template proto schemas.

**L3-AGGR-002** · Parent: L2-AGGR-001 · Verification: T · Status: Draft
`Struct` values SHALL be converted to Python `dict` via `google.protobuf.json_format.MessageToDict` with `preserving_proto_field_name=True`, `including_default_value_fields=False`.

**L3-AGGR-003** · Parent: L2-AGGR-002 · Verification: T · Status: Draft
Omitted `email_body_contribution` on the wire SHALL be detected via proto3 field presence check (`HasField` where supported, or `is None` after dict conversion).

**L3-AGGR-004** · Parent: L2-AGGR-003 · Verification: T · Status: Draft
A `position` value of `EMAIL_BODY_POSITION_UNSPECIFIED` SHALL default to `AFTER_STAGES_SUMMARY`; a DEBUG log SHALL note the default was applied.

**L3-AGGR-005** · Parent: L2-AGGR-003 · Verification: T · Status: Draft
Email body order SHALL be: all `BEFORE_STAGES_SUMMARY` in stage order, then the main summary block, then all `AFTER_STAGES_SUMMARY` in stage order.

**L3-AGGR-006** · Parent: L2-AGGR-004 · Verification: T · Status: Draft
The aggregation template context SHALL include `stages` (list of dicts with `stage_id`, `stage_order`, `rendered_html`), `run_id`, `run_metadata`, and `pipeline_type`.

**L3-AGGR-007** · Parent: L2-AGGR-004 · Verification: T · Status: Draft
If the aggregation template render exceeds `templates.max_rendered_bytes`, the run transitions to `FAILED` with reason `RENDERED_SIZE_EXCEEDED`, not `EMAIL_SIZE_EXCEEDED`.

**L3-AGGR-008** · Parent: L2-AGGR-005 · Verification: T · Status: Draft
"Empty report contribution" SHALL mean: `context == {}` AND rendered HTML stripped of whitespace is empty; such stages are excluded from attachment count but do not raise.

**L3-AGGR-009** · Parent: L2-AGGR-005 · Verification: T · Status: Draft
A run where all stages have empty report contributions SHALL produce zero attachments in PER_STAGE mode, yielding an email with body content only.

**L3-AGGR-010** · Parent: L2-AGGR-006 · Verification: T · Status: Draft
Filename construction SHALL sanitize `pipeline_type` and `stage_id` against regex `[^a-zA-Z0-9._-]`, replacing non-matching characters with `_`.

**L3-AGGR-011** · Parent: L2-AGGR-006 · Verification: T · Status: Draft
Filenames SHALL NOT exceed 255 bytes (POSIX `NAME_MAX`); a boundary test SHALL exercise long stage_ids.

**L3-AGGR-012** · Parent: L2-AGGR-007 · Verification: T · Status: Draft
Sorting SHALL use `sorted(contributions, key=lambda c: (c.stage_order, c.stage_id))`; the sort SHALL be stable.

**L3-AGGR-013** · Parent: L2-AGGR-008 · Verification: T · Status: Draft
The `stage_id` lex tie-break SHALL use Python's default `<` operator (Unicode code-point comparison).

**L3-AGGR-014** · Parent: L2-AGGR-008 · Verification: T · Status: Draft
A test SHALL construct a run with multiple stages at `stage_order=0` and assert the attachment/body order matches stage_id-sorted order.

**L3-AGGR-015** · Parent: L2-AGGR-009 · Verification: T · Status: Draft
`MissingAggregationTemplate` check SHALL run BEFORE `UnknownTemplate`; a request with `SINGLE_AGGREGATED` and missing aggregation_template raises the former.

**L3-AGGR-016** · Parent: L2-AGGR-010 · Verification: T · Status: Draft
The aggregation template manifest entry SHALL have a distinct JSON Schema from stage-report templates (context shape differs: receives list of stages).

**L3-AGGR-017** · Parent: L2-AGGR-001 · Verification: T · Status: Draft
The proto `Struct` context SHALL be validated as JSON-object root (not array or scalar); non-object roots raise `MalformedRequest`.

**L3-AGGR-018** · Parent: L2-AGGR-002 · Verification: T · Status: Draft
The email body contribution record SHALL include `position` as a column so sort order can be reconstructed without re-parsing the email body.

**L3-AGGR-019** · Parent: L2-AGGR-004 · Verification: T · Status: Draft
The aggregation template render SHALL run AFTER all per-stage fragment renders, so per-stage failures surface before aggregation work is wasted.

**L3-AGGR-020** · Parent: L2-AGGR-005 · Verification: T · Status: Draft
PER_STAGE attachments SHALL have MIME `Content-Type: text/html; charset=utf-8` and `Content-Disposition: attachment; filename="<filename>"`.

---

## L3-SWEEP: Orphan detection and disposition

**L3-SWEEP-001** · Parent: L2-SWEEP-001 · Verification: T · Status: Draft
The sweeper task SHALL be registered in service lifespan via `contextlib.asynccontextmanager`; on exit, `task.cancel()` is awaited with `contextlib.suppress(asyncio.CancelledError)`.

**L3-SWEEP-002** · Parent: L2-SWEEP-001 · Verification: T · Status: Draft
Cancellation SHALL propagate within `shutdown_grace_period_seconds`; a test SHALL assert shutdown completes within this window even if the sweeper is mid-query.

**L3-SWEEP-003** · Parent: L2-SWEEP-002 · Verification: T · Status: Draft
The poll loop SHALL use `await asyncio.sleep(interval)` inside `while not shutdown_event.is_set()`; jitter SHALL NOT be added (deterministic scheduling preferred).

**L3-SWEEP-004** · Parent: L2-SWEEP-003 · Verification: T · Status: Draft
Prometheus counters SHALL be named `message_service_sweeper_iterations_total` with label `outcome` in `{no_orphans_found, orphans_detected, sweeper_error}`.

**L3-SWEEP-005** · Parent: L2-SWEEP-003 · Verification: T · Status: Draft
Each sweeper iteration SHALL increment the counter exactly once, even if multiple orphans are detected in the same iteration.

**L3-SWEEP-006** · Parent: L2-SWEEP-004 · Verification: T · Status: Draft
Elapsed time SHALL be `clock.now() - run.last_transition_at`, compared against `timedelta(seconds=config.sweeper.run_timeout_seconds)`.

**L3-SWEEP-007** · Parent: L2-SWEEP-005 · Verification: T · Status: Draft
The repository query SHALL be `SELECT run_id, last_transition_at FROM runs WHERE state IN ('INITIATED', 'AGGREGATING', 'READY', 'SENDING')` with an index on `state`.

**L3-SWEEP-008** · Parent: L2-SWEEP-005 · Verification: T · Status: Draft
The query SHALL fetch at most `sweeper.max_candidates_per_iteration` rows per tick (default 1000) to bound runtime; larger backlogs drain over multiple ticks.

**L3-SWEEP-009** · Parent: L2-SWEEP-006 · Verification: T · Status: Draft
Atomic transition+enqueue SHALL be `UPDATE runs SET state=? WHERE state=? AND run_id=?`; zero affected rows (race lost) SHALL cause the run to be skipped without error.

**L3-SWEEP-010** · Parent: L2-SWEEP-006 · Verification: T · Status: Draft
After atomic transition, disposition action records SHALL be inserted into `sweeper_actions`; the assembly task consumes from this table rather than being invoked directly.

**L3-SWEEP-011** · Parent: L2-SWEEP-007 · Verification: T · Status: Draft
Empty `disposition_actions` SHALL be permitted, causing orphaned runs to receive no action beyond the state transition (equivalent to `DISCARD_SILENTLY`).

**L3-SWEEP-012** · Parent: L2-SWEEP-007 · Verification: T · Status: Draft
Unknown disposition action identifiers in config SHALL raise `ConfigurationError` at startup listing the unknown name and the allowed set.

**L3-SWEEP-013** · Parent: L2-SWEEP-008 · Verification: I, T · Status: Draft
Each disposition handler SHALL be `async (run: Run, config: Config) -> None`; handlers SHALL NOT raise — failures are logged at ERROR and swallowed so one failure doesn't block others.

**L3-SWEEP-014** · Parent: L2-SWEEP-008 · Verification: T · Status: Draft
Handler registration SHALL be a `dict[str, Callable]` mapping action identifier to implementation; the dispatcher iterates the configured order.

**L3-SWEEP-015** · Parent: L2-SWEEP-009 · Verification: T · Status: Draft
The canonical documented order SHALL be `NOTIFY_ADMINS` → `NOTIFY_SUBSCRIBERS` → `SEND_PARTIAL_FLAGGED` → `DISCARD_SILENTLY`; custom orderings work but may surprise users.

**L3-SWEEP-016** · Parent: L2-SWEEP-003 · Verification: T · Status: Draft
If the sweeper's repository query fails, the counter SHALL increment `sweeper_error`, the exception SHALL be logged at ERROR, and the loop SHALL continue after the normal sleep interval.

**L3-SWEEP-017** · Parent: L2-SWEEP-002 · Verification: T · Status: Draft
A test SHALL construct a run with `last_transition_at` exactly `run_timeout_seconds` ago; the sweeper SHALL classify it as orphaned (inclusive boundary).

**L3-SWEEP-018** · Parent: L2-SWEEP-001 · Verification: T · Status: Draft
The sweeper task SHALL NOT start until after database migrations have completed and config validation has passed.

---

## L3-SUB: Subscriptions and tags

**L3-SUB-001** · Parent: L2-SUB-001 · Verification: T · Status: Draft
The `subscriptions` table SHALL have a unique index on `(user_id, granularity, target_value)` to prevent duplicates.

**L3-SUB-002** · Parent: L2-SUB-001 · Verification: T · Status: Draft
A `created_at` column SHALL be populated with `clock.now()` at insert time and SHALL be immutable thereafter.

**L3-SUB-003** · Parent: L2-SUB-002 · Verification: T · Status: Draft
`target_value` SHALL be `NULL` for `GLOBAL` subscriptions; the unique index treats NULLs distinctly only at the `(user_id, granularity)` level.

**L3-SUB-004** · Parent: L2-SUB-002 · Verification: T · Status: Draft
`target_value` validation at insert time: `PIPELINE` must match `pipelines.registered`; `TAG` must match tag vocabulary; `GLOBAL` must be null.

**L3-SUB-005** · Parent: L2-SUB-003 · Verification: T · Status: Draft
Recipient resolution SHALL use a single `SELECT DISTINCT u.email FROM users u JOIN subscriptions s ON s.user_id = u.id WHERE u.disabled = 0 AND (s.granularity = 'GLOBAL' OR (s.granularity = 'PIPELINE' AND s.target_value = ?) OR (s.granularity = 'TAG' AND s.target_value IN (<tags>)))`.

**L3-SUB-006** · Parent: L2-SUB-003 · Verification: T · Status: Draft
Tag list parameterization SHALL use SQLite's `IN` with dynamic placeholders; tests SHALL exercise runs with 0, 1, and many tags.

**L3-SUB-007** · Parent: L2-SUB-004 · Verification: T · Status: Draft
User creation SHALL be in a transaction inserting exactly one `users` row and zero `subscriptions` rows.

**L3-SUB-008** · Parent: L2-SUB-005 · Verification: T · Status: Draft
Admin users SHALL use the same user-creation code path; the only differentiator is the `is_admin` boolean column.

**L3-SUB-009** · Parent: L2-SUB-006 · Verification: T · Status: Draft
Tag vocabulary TOML SHALL use `[[tag]]` arrays of tables with required `name` and optional `description`; unknown TOML keys SHALL be rejected at load time.

**L3-SUB-010** · Parent: L2-SUB-006 · Verification: T · Status: Draft
Tag names SHALL match `^[a-z][a-z0-9_-]{0,63}$`; non-conforming names raise `ConfigurationError` at startup with the offending name in details.

**L3-SUB-011** · Parent: L2-SUB-007 · Verification: T · Status: Draft
The loaded vocabulary SHALL be `frozenset[str]` for O(1) membership; descriptions are a separate `dict[str, str]` for UI use.

**L3-SUB-012** · Parent: L2-SUB-007 · Verification: T · Status: Draft
Hot-reload of the vocabulary is out of scope for v1; `SIGHUP` SHALL be ignored; the service documents that changes require restart.

**L3-SUB-013** · Parent: L2-SUB-008 · Verification: T · Status: Draft
`BeginRun` validation SHALL iterate all tags and collect invalid ones into a single error rather than rejecting on the first.

**L3-SUB-014** · Parent: L2-SUB-008 · Verification: T · Status: Draft
Subscription creation SHALL validate the tag before insert; the dashboard surfaces validation errors inline on the form.

**L3-SUB-015** · Parent: L2-SUB-009 · Verification: T · Status: Draft
The recipient set SHALL be cast to a sorted `list[str]` before logging to ensure deterministic log output.

**L3-SUB-016** · Parent: L2-SUB-009 · Verification: T · Status: Draft
A run with zero matching subscribers SHALL NOT cause an SMTP send; the run transitions to `SENT` with `recipient_count=0` in the audit log.

**L3-SUB-017** · Parent: L2-SUB-010 · Verification: T · Status: Draft
The `users.disabled` column SHALL be `BOOLEAN NOT NULL DEFAULT 0`; the recipient query predicate is `disabled = 0`.

**L3-SUB-018** · Parent: L2-SUB-010 · Verification: T · Status: Draft
Disabling a user SHALL NOT delete their subscriptions; re-enabling restores delivery without re-opt-in.

**L3-SUB-019** · Parent: L2-SUB-001 · Verification: T · Status: Draft
The `granularity` column SHALL be stored as a string literal matching `SubscriptionGranularity` enum values; a CHECK constraint enforces the value set.

**L3-SUB-020** · Parent: L2-SUB-003 · Verification: T · Status: Draft
Recipient resolution SHALL be wrapped in a read-only transaction to ensure a consistent snapshot across `users` and `subscriptions`.

---

## L3-AUTH: Authentication

**L3-AUTH-001** · Parent: L2-AUTH-001 · Verification: T · Status: Draft
Password hashing SHALL use `argon2.PasswordHasher(...)` with config-loaded parameters; the hasher instance is a service-scoped singleton.

**L3-AUTH-002** · Parent: L2-AUTH-002 · Verification: T · Status: Draft
Argon2 defaults SHALL be `memory_cost=65536` (64 MiB), `time_cost=3`, `parallelism=4`, `hash_len=32`, `salt_len=16`.

**L3-AUTH-003** · Parent: L2-AUTH-002 · Verification: T · Status: Draft
A benchmark test SHALL assert `hash()` completes in 50–500 ms on CI hardware; parameters SHALL be reviewed if outside this band.

**L3-AUTH-004** · Parent: L2-AUTH-003 · Verification: T · Status: Draft
A `Password` value object SHALL wrap plaintext, override `__repr__` and `__str__` to return `"<Password>"`, and use `secrets.compare_digest` for comparison.

**L3-AUTH-005** · Parent: L2-AUTH-003 · Verification: T · Status: Draft
The logging redaction list SHALL include `password`, `passwd`, `password_hash`, `pwd`; a test submits a log call with each and asserts the emitted JSON contains `<redacted>`.

**L3-AUTH-006** · Parent: L2-AUTH-004 · Verification: T · Status: Draft
Session tokens SHALL be generated via `secrets.token_urlsafe(32)` (256 bits of entropy, exceeding the 128-bit minimum).

**L3-AUTH-007** · Parent: L2-AUTH-004 · Verification: T · Status: Draft
The `sessions` table SHALL have `token_hash` (SHA-256 of the token), `user_id`, `created_at`, `last_activity_at`; plaintext tokens SHALL NOT be stored.

**L3-AUTH-008** · Parent: L2-AUTH-005 · Verification: T · Status: Draft
Session cookie name SHALL be `msp_session`; attributes via `response.set_cookie(httponly=True, samesite="lax", secure=config.dashboard.https_only)`.

**L3-AUTH-009** · Parent: L2-AUTH-005 · Verification: T · Status: Draft
The `Secure` attribute SHALL be set based on config `dashboard.https_only` (default `true`); dev environments may override to `false`.

**L3-AUTH-010** · Parent: L2-AUTH-006 · Verification: T · Status: Draft
The idle-timeout check SHALL update `last_activity_at` on every successful authenticated request, not just at session creation.

**L3-AUTH-011** · Parent: L2-AUTH-006 · Verification: T · Status: Draft
Expired sessions SHALL be deleted in the same request that rejects them; a periodic cleanup task sweeps abandoned sessions.

**L3-AUTH-012** · Parent: L2-AUTH-006 · Verification: T · Status: Draft
The HTTP 401 response SHALL include `WWW-Authenticate: Session realm="Message-Service"`.

**L3-AUTH-013** · Parent: L2-AUTH-001 · Verification: T · Status: Draft
Password verification SHALL use `PasswordHasher.verify(hash, plaintext)`; `VerifyMismatchError` SHALL be caught and translated to a generic "invalid credentials" response without distinguishing unknown-user from wrong-password.

---

## L3-MAIL: Email delivery

**L3-MAIL-001** · Parent: L2-MAIL-001 · Verification: T · Status: Draft
The `aiosmtplib.SMTP` client SHALL be instantiated per send (not reused); connection pooling is on the ROADMAP pending profiling.

**L3-MAIL-002** · Parent: L2-MAIL-002 · Verification: T · Status: Draft
`mail.smtp.host` is a string; `port` is an int [1, 65535]; `username` and `password` are optional strings (password supports `${env:...}`).

**L3-MAIL-003** · Parent: L2-MAIL-002 · Verification: T · Status: Draft
`use_starttls=true` SHALL cause `STARTTLS` before auth; `use_starttls=false` issues plaintext auth and logs WARNING at startup.

**L3-MAIL-004** · Parent: L2-MAIL-003 · Verification: T · Status: Draft
`mail.from_address` SHALL be validated at startup using `email.utils.parseaddr` with a non-empty address portion check.

**L3-MAIL-005** · Parent: L2-MAIL-004 · Verification: T · Status: Draft
Transient classification catches `SMTPServerDisconnected`, `SMTPConnectTimeoutError`, `socket.gaierror`, and `SMTPResponseException` with `code in range(400, 500)`.

**L3-MAIL-006** · Parent: L2-MAIL-004 · Verification: T · Status: Draft
Response code `421` SHALL be excluded from transient classification and treated as permanent for the current run (RFC 5321: service not available).

**L3-MAIL-007** · Parent: L2-MAIL-005 · Verification: T · Status: Draft
Permanent failures: `SMTPResponseException` with `code in range(500, 600)` or `SMTPAuthenticationError`.

**L3-MAIL-008** · Parent: L2-MAIL-005 · Verification: T · Status: Draft
Permanent failure SHALL transition the run to `FAILED` with `failure_reason="PERMANENT_SMTP_FAILURE"` and log at ERROR with SMTP code and message.

**L3-MAIL-009** · Parent: L2-MAIL-006 · Verification: T · Status: Draft
Backoff SHALL be `min(max_interval, initial_interval * (2 ** (attempt - 1)))` with `attempt` starting at 1.

**L3-MAIL-010** · Parent: L2-MAIL-006 · Verification: T · Status: Draft
Retry loop SHALL log each attempt at WARNING with `attempt`, `max_retries`, `backoff_seconds`, `failure_type`.

**L3-MAIL-011** · Parent: L2-MAIL-006 · Verification: T · Status: Draft
Defaults SHALL be `max_retries=5`, `initial_interval_seconds=2`, `max_interval_seconds=300`.

**L3-MAIL-012** · Parent: L2-MAIL-007 · Verification: T · Status: Draft
Size measurement SHALL use `len(message.as_bytes())` on the `email.message.EmailMessage` after encoding completes.

**L3-MAIL-013** · Parent: L2-MAIL-008 · Verification: T · Status: Draft
A test SHALL intercept the SMTP client at `send_message` and assert `as_bytes()` was called before any SMTP traffic was emitted.

**L3-MAIL-014** · Parent: L2-MAIL-009 · Verification: T · Status: Draft
The size-exceeded audit schema: `{timestamp, run_id, failure_reason: "EMAIL_SIZE_EXCEEDED", measured_bytes: int, limit_bytes: int, recipient_count: int}`.

**L3-MAIL-015** · Parent: L2-MAIL-010 · Verification: T · Status: Draft
The admin notification template SHALL live at `src/message_service/templates/email/admin_notification.j2` and accept only `run_id`, `failure_reason`, `timestamp` — nothing from the failing run's content.

**L3-MAIL-016** · Parent: L2-MAIL-010 · Verification: T · Status: Draft
A test SHALL attempt to inject a template expression into `run_id` and assert the admin template's `autoescape` renders it literally.

**L3-MAIL-017** · Parent: L2-MAIL-011 · Verification: T · Status: Draft
Oversized reports SHALL be written via the same atomic-rename path as successful reports; the dashboard resend endpoint SHALL locate and resend them.

**L3-MAIL-018** · Parent: L2-MAIL-012 · Verification: T · Status: Draft
Delivery audit schema columns: `timestamp`, `run_id`, `outcome`, `recipient_count`, `recipient_addresses` (JSON array), `failure_reason` (nullable), `smtp_response_code` (nullable int).

**L3-MAIL-019** · Parent: L2-MAIL-013 · Verification: T · Status: Draft
Audit insert and state transition SHALL be in a single `BEGIN IMMEDIATE` transaction; any failure rolls back.

**L3-MAIL-020** · Parent: L2-MAIL-001 · Verification: T · Status: Draft
SMTP client `timeout` SHALL be loaded from `mail.smtp.timeout_seconds` (default 30); timeouts count as transient failures.

**L3-MAIL-021** · Parent: L2-MAIL-007 · Verification: T · Status: Draft
Size check SHALL include `Content-Transfer-Encoding: base64` overhead (~4/3 of raw bytes); measurement is on the post-encoding message.

**L3-MAIL-022** · Parent: L2-MAIL-002 · Verification: T · Status: Draft
Missing SMTP `password` for a configured `username` SHALL raise `ConfigurationError` at startup, not at first send.

**L3-MAIL-023** · Parent: L2-MAIL-005 · Verification: T · Status: Draft
SMTP `550` on one recipient SHALL NOT permanently fail the whole send if others accepted; the per-recipient failure is recorded in the audit log.

**L3-MAIL-024** · Parent: L2-MAIL-011 · Verification: T · Status: Draft
The persisted oversized-report path SHALL be identical to a successful report's path, simplifying resend logic.

**L3-MAIL-025** · Parent: L2-MAIL-012 · Verification: I · Status: Draft
`recipient_addresses` in audit logs MAY be redacted per site policy; default is to store the full list; a configuration option is on the ROADMAP.

**L3-MAIL-026** · Parent: L2-MAIL-013 · Verification: T · Status: Draft
If audit insert fails AFTER a successful SMTP send, the run SHALL NOT be rolled back at the SMTP layer (email already delivered); the audit failure logs at CRITICAL and the run remains in `SENDING` for operator investigation.

---

## L3-DASH: Dashboard

**L3-DASH-001** · Parent: L2-DASH-001 · Verification: T · Status: Draft
The `create_app(config: Config) -> FastAPI` factory SHALL attach routers via `include_router`; no module-level `app` global SHALL exist.

**L3-DASH-002** · Parent: L2-DASH-001 · Verification: T · Status: Draft
The factory SHALL register startup and shutdown handlers via `@app.on_event(...)` or the lifespan context manager.

**L3-DASH-003** · Parent: L2-DASH-002 · Verification: T · Status: Draft
`dashboard.host` default `"0.0.0.0"`; `dashboard.port` default `8080`.

**L3-DASH-004** · Parent: L2-DASH-002 · Verification: T · Status: Draft
Startup SHALL raise `ConfigurationError` if `dashboard.port == grpc.port`.

**L3-DASH-005** · Parent: L2-DASH-003 · Verification: I · Status: Draft
Static assets live in `src/message_service/interfaces/rest/html/static/`, mounted via FastAPI `StaticFiles` at `/static`.

**L3-DASH-006** · Parent: L2-DASH-003 · Verification: A · Status: Draft
A grep-based CI check SHALL fail if any HTML template references `https://` or `http://` to external hosts (allowlist: none).

**L3-DASH-007** · Parent: L2-DASH-004 · Verification: T · Status: Draft
Subscription CRUD SHALL filter all SQL by session `user_id`; a test attempts PATCH on another user's subscription and asserts HTTP 403.

**L3-DASH-008** · Parent: L2-DASH-004 · Verification: T · Status: Draft
Subscription routes: `GET /subscriptions`, `POST /subscriptions`, `DELETE /subscriptions/{id}`.

**L3-DASH-009** · Parent: L2-DASH-005 · Verification: T · Status: Draft
The subscription POST body Pydantic model SHALL define only `granularity` and `target_value`; extras are rejected via `model_config = ConfigDict(extra='forbid')`.

**L3-DASH-010** · Parent: L2-DASH-006 · Verification: T, D · Status: Draft
The tag-selection UI SHALL render a `<select>` populated from the vocabulary at page render; `value` attributes match the exact validated strings.

**L3-DASH-011** · Parent: L2-DASH-007 · Verification: T · Status: Draft
The `require_admin` FastAPI dependency SHALL verify `is_admin` on every request; non-admin encountering admin route returns HTTP 403.

**L3-DASH-012** · Parent: L2-DASH-008 · Verification: T · Status: Draft
Resend SHALL call the same `RecipientResolver` used originally; a test verifies a new subscription added between send and resend receives the resent email.

**L3-DASH-013** · Parent: L2-DASH-008 · Verification: T · Status: Draft
Resend SHALL create a new audit record (not overwrite original) with `outcome=RESEND`.

**L3-DASH-014** · Parent: L2-DASH-009 · Verification: T · Status: Draft
Template inspection routes accept only GET; POST/PATCH/DELETE against `/templates/*` return HTTP 405.

**L3-DASH-015** · Parent: L2-DASH-009 · Verification: T · Status: Draft
Template listing SHALL expose `name`, `version`, repo-relative `schema_path` and `source_path`, and schema contents; it SHALL NOT expose rendered contents of any past report.

**L3-DASH-016** · Parent: L2-DASH-010 · Verification: T · Status: Draft
The metrics dashboard page SHALL fetch `/metrics` via same-origin `fetch()`; CORS SHALL NOT be relaxed for this path.

**L3-DASH-017** · Parent: L2-DASH-011 · Verification: I · Status: Draft
Chart.js SHALL be bundled at a pinned version in `static/js/chart.min.js` with its license notice.

**L3-DASH-018** · Parent: L2-DASH-001 · Verification: T · Status: Draft
CSRF protection SHALL apply to POST/PATCH/DELETE via double-submit cookie or equivalent; a test verifies a POST without the CSRF token returns HTTP 403.

**L3-DASH-019** · Parent: L2-DASH-002 · Verification: T · Status: Draft
Subscription IDs SHALL be UUIDs; route validators reject non-UUIDs with HTTP 422.

**L3-DASH-020** · Parent: L2-DASH-003 · Verification: I · Status: Draft
Fonts SHALL be system fonts (via `font-family` stack only) or WOFF2 files shipped in the static directory.

**L3-DASH-021** · Parent: L2-DASH-007 · Verification: T · Status: Draft
The admin gate SHALL re-check `is_admin` on every request (not cache in the session) so role changes take effect immediately.

---

## L3-PERS: Persistence

**L3-PERS-001** · Parent: L2-PERS-001 · Verification: T · Status: Draft
Startup SHALL `mkdir(parents=True, exist_ok=True)` on the SQLite path's parent directory if missing.

**L3-PERS-002** · Parent: L2-PERS-002 · Verification: T · Status: Draft
Startup pragma sequence SHALL be: `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=5000`; each is verified via `PRAGMA` read-back.

**L3-PERS-003** · Parent: L2-PERS-002 · Verification: T · Status: Draft
If `PRAGMA journal_mode=WAL` returns anything other than `"wal"` (e.g., on a network filesystem), startup SHALL log WARNING but proceed.

**L3-PERS-004** · Parent: L2-PERS-003 · Verification: T · Status: Draft
Migrations SHALL be named `NNN_description.sql` with three-digit zero-padded prefix; gaps in the sequence SHALL fail startup.

**L3-PERS-005** · Parent: L2-PERS-003 · Verification: T · Status: Draft
Applied migrations tracked in `_migrations(version INT PRIMARY KEY, name TEXT, applied_at TEXT)`; re-running is a no-op.

**L3-PERS-006** · Parent: L2-PERS-004 · Verification: T · Status: Draft
The connection pool SHALL be backed by an `asyncio.Queue`; exhaustion blocks with `persistence.connection_acquire_timeout_seconds` (default 5s) before raising `PersistenceError`.

**L3-PERS-007** · Parent: L2-PERS-004 · Verification: T · Status: Draft
Default `persistence.connection_pool_size` SHALL be 16; exhaustion events SHALL increment a Prometheus counter.

**L3-PERS-008** · Parent: L2-PERS-005 · Verification: T · Status: Draft
Atomic writes SHALL write to `<final>.tmp.<uuid>`, `fsync`, then `os.rename` to `<final>`; concurrent writers SHALL each use distinct `.tmp` suffixes.

**L3-PERS-009** · Parent: L2-PERS-005 · Verification: T · Status: Draft
A test SHALL interrupt the write between `.tmp` creation and rename, then restart the service, and assert the final file was NOT created (leaving room for retry).

**L3-PERS-010** · Parent: L2-PERS-006 · Verification: T · Status: Draft
Missing report directory at startup SHALL be created via `Path.mkdir(parents=True, exist_ok=True)`; failure to create SHALL raise `ConfigurationError`.

**L3-PERS-011** · Parent: L2-PERS-006 · Verification: T · Status: Draft
If the report directory exists but is not writable, startup SHALL raise `ConfigurationError` after an explicit write-test.

**L3-PERS-012** · Parent: L2-PERS-007 · Verification: A, I · Status: Draft
A ruff rule (`PTH`) SHALL enforce `pathlib.Path` usage; a CI check SHALL fail the build on any `os.path.join` or string `/` concatenation in source.

**L3-PERS-013** · Parent: L2-PERS-008 · Verification: I · Status: Draft
Abstract port classes SHALL use `abc.ABC` with `@abstractmethod`; every method SHALL have full type hints including return type.

**L3-PERS-014** · Parent: L2-PERS-008 · Verification: T · Status: Draft
A test SHALL instantiate each abstract port via `MagicMock(spec=<PortClass>)` and assert all expected methods are present.

**L3-PERS-015** · Parent: L2-PERS-009 · Verification: A · Status: Draft
A dependency-direction analysis SHALL confirm that `infrastructure/persistence/sqlite/` imports nothing from `infrastructure/persistence/filesystem/` and vice versa.

**L3-PERS-016** · Parent: L2-PERS-010 · Verification: A · Status: Draft
A static check SHALL assert that `domain/` and `application/` (excluding `application/ports/` implementations) have no imports from `infrastructure/` or `interfaces/`.

**L3-PERS-017** · Parent: L2-PERS-010 · Verification: A · Status: Draft
The dependency-direction check SHALL run in CI; violations SHALL fail the build.

**L3-PERS-018** · Parent: L2-PERS-001 · Verification: T · Status: Draft
The SQLite database file SHALL have permissions 0600 on Linux (the service account owns it); Windows uses the equivalent NTFS ACL.

**L3-PERS-019** · Parent: L2-PERS-002 · Verification: T · Status: Draft
Service shutdown SHALL issue `PRAGMA wal_checkpoint(TRUNCATE)` before closing the last connection to compact the WAL.

**L3-PERS-020** · Parent: L2-PERS-003 · Verification: T · Status: Draft
Migrations SHALL be applied one per transaction; a migration failure rolls back only that migration, leaving prior migrations applied.

**L3-PERS-021** · Parent: L2-PERS-004 · Verification: T · Status: Draft
Connection acquisition SHALL log DEBUG with the current pool depth; a test SHALL verify the debug line fires when the pool is near exhaustion.

**L3-PERS-022** · Parent: L2-PERS-005 · Verification: T · Status: Draft
Report filenames SHALL include the run_id as the only variable component; no timestamp or sequence number SHALL be appended.

**L3-PERS-023** · Parent: L2-PERS-007 · Verification: I · Status: Draft
Any new filesystem access point SHALL be added to the approved list in `docs/reviews/filesystem-access-points.md` with a pathlib-based implementation reference.

---

## L3-OBS: Observability

**L3-OBS-001** · Parent: L2-OBS-001 · Verification: I · Status: Draft
structlog SHALL be configured in `src/message_service/observability/logging_setup.py` via `configure_logging()`; the function SHALL be called exactly once at startup.

**L3-OBS-002** · Parent: L2-OBS-001 · Verification: T · Status: Draft
The JSON renderer SHALL emit records with at minimum `timestamp`, `level`, `logger`, `event`, plus any structured fields supplied by the call site.

**L3-OBS-003** · Parent: L2-OBS-002 · Verification: T · Status: Draft
Each inbound gRPC method SHALL call `bind_request_context(correlation_id=..., run_id=... if known)` at entry and `clear_request_context()` in a `finally` block.

**L3-OBS-004** · Parent: L2-OBS-002 · Verification: T · Status: Draft
Each FastAPI route SHALL apply the same pattern via a middleware or dependency; a test SHALL emit a log from a route handler and assert the record carries the request correlation_id.

**L3-OBS-005** · Parent: L2-OBS-003 · Verification: T · Status: Draft
The sensitive-field list SHALL include at minimum: `password`, `passwd`, `password_hash`, `pwd`, `secret`, `smtp_password`, `session_token`, `cookie`, `authorization`, `email_body`, `rendered_output`, `template_context`.

**L3-OBS-006** · Parent: L2-OBS-003 · Verification: T · Status: Draft
Redaction SHALL be case-insensitive on the key name; a test SHALL submit `PASSWORD`, `Password`, `password` as keys and assert all three are redacted.

**L3-OBS-007** · Parent: L2-OBS-004 · Verification: I · Status: Draft
The Prometheus client SHALL be `prometheus_client`; the `/metrics` endpoint is a FastAPI route returning `prometheus_client.generate_latest()` with content type `text/plain; version=0.0.4; charset=utf-8`.

**L3-OBS-008** · Parent: L2-OBS-005 · Verification: A · Status: Draft
A static check SHALL assert every metric declared in the codebase has a name beginning with `message_service_`.

**L3-OBS-009** · Parent: L2-OBS-006 · Verification: I · Status: Draft
Required metrics: `message_service_run_state_transitions_total{target_state}`, `message_service_stage_state_transitions_total{target_state}`, `message_service_email_delivery_outcomes_total{outcome}`, `message_service_email_size_bytes` (histogram), `message_service_run_duration_seconds` (histogram), `message_service_sweeper_iterations_total{outcome}`.

**L3-OBS-010** · Parent: L2-OBS-006 · Verification: T · Status: Draft
Histogram buckets for `email_size_bytes` SHALL be `[1_000, 10_000, 100_000, 1_000_000, 10_000_000, 25_000_000, 50_000_000]` to cover typical and limit-boundary sizes.

**L3-OBS-011** · Parent: L2-OBS-006 · Verification: T · Status: Draft
Histogram buckets for `run_duration_seconds` SHALL be `[1, 5, 15, 60, 300, 900, 1800, 3600]` covering sub-minute to hour-scale runs.

**L3-OBS-012** · Parent: L2-OBS-007 · Verification: T · Status: Draft
The `audit_log` table schema: `(audit_id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, event_type TEXT NOT NULL, run_id TEXT, details_json TEXT)` with an index on `(event_type, timestamp)` for retention queries.

**L3-OBS-013** · Parent: L2-OBS-007 · Verification: T · Status: Draft
The `details_json` column SHALL hold a JSON-serialized dict; non-JSON-serializable fields raise `PersistenceError` at insert time.

**L3-OBS-014** · Parent: L2-OBS-008 · Verification: T · Status: Draft
The retention cleanup task SHALL run every 24 hours (configurable via `observability.audit.cleanup_interval_hours`, default 24).

**L3-OBS-015** · Parent: L2-OBS-008 · Verification: T · Status: Draft
Cleanup SHALL use `DELETE FROM audit_log WHERE timestamp < ?` with a cutoff computed from `retention_days`; a test verifies boundary behaviour at exactly `retention_days` ago.

**L3-OBS-016** · Parent: L2-OBS-008 · Verification: T · Status: Draft
Cleanup SHALL execute in batches of `observability.audit.cleanup_batch_size` (default 10000) to avoid long-running deletes blocking other writers.

**L3-OBS-017** · Parent: L2-OBS-009 · Verification: I · Status: Draft
The cleanup task SHALL use the same `asyncio.create_task` + cancellation pattern as the orphan sweeper — documented once, shared via a helper.

**L3-OBS-018** · Parent: L2-OBS-001 · Verification: T · Status: Draft
Structured log records SHALL NOT exceed 8 KiB per line (a conservative limit for downstream log aggregators); oversized records SHALL have variable fields truncated with a `_truncated: true` marker.

---

## L3-CFG: Configuration

**L3-CFG-001** · Parent: L2-CFG-001 · Verification: T · Status: Draft
The CLI entry point SHALL accept `--config PATH` as a required argument unless `MSG_SERVICE_CONFIG` is set; absence of both SHALL exit with a usage message.

**L3-CFG-002** · Parent: L2-CFG-001 · Verification: T · Status: Draft
CLI argument parsing SHALL use Typer; the resolved path SHALL be logged at INFO during startup under event `config_loaded`.

**L3-CFG-003** · Parent: L2-CFG-002 · Verification: T · Status: Draft
When both `--config` and `MSG_SERVICE_CONFIG` are provided, the CLI flag SHALL win; a test asserts this precedence with both set to different paths.

**L3-CFG-004** · Parent: L2-CFG-003 · Verification: I, T · Status: Draft
TOML parsing SHALL import `tomllib` on Python 3.11+ and `tomli` on 3.10 via `try: import tomllib except ImportError: import tomli as tomllib` in `config/loader.py`.

**L3-CFG-005** · Parent: L2-CFG-004 · Verification: I · Status: Draft
The top-level Pydantic model SHALL be named `Config` in `config/schema.py`, composed of nested models per TOML section (`GrpcConfig`, `DashboardConfig`, `PersistenceConfig`, `MailConfig`, etc.).

**L3-CFG-006** · Parent: L2-CFG-004 · Verification: T · Status: Draft
Every Pydantic model SHALL use `model_config = ConfigDict(extra='forbid')` to catch typos in configuration keys.

**L3-CFG-007** · Parent: L2-CFG-005 · Verification: T · Status: Draft
Validation failures SHALL be caught from `ValidationError`, formatted as a numbered list with JSON Pointer paths, and written to stderr before `sys.exit(1)`.

**L3-CFG-008** · Parent: L2-CFG-005 · Verification: T · Status: Draft
The stderr output SHALL follow the format `  [N] <json_pointer>: <message>` per failure; a test parses the output to ensure it is machine-grep-friendly.

**L3-CFG-009** · Parent: L2-CFG-006 · Verification: A · Status: Draft
A static check SHALL assert no module at import time performs I/O or network operations; all such work SHALL occur in explicit startup functions called after config validation.

**L3-CFG-010** · Parent: L2-CFG-007 · Verification: T · Status: Draft
Path resolution SHALL use `(Path(config_file).parent / value).resolve(strict=False)` when `value` is relative; absolute paths pass through unchanged.

**L3-CFG-011** · Parent: L2-CFG-007 · Verification: T · Status: Draft
Path resolution SHALL be applied uniformly to: `persistence.sqlite_path`, `persistence.filesystem.report_directory`, `templates.manifest_path`, `tags.vocabulary_path`.

**L3-CFG-012** · Parent: L2-CFG-008 · Verification: T · Status: Draft
The `${env:VAR_NAME}` substitution SHALL be applied by a Pydantic `field_validator` scanning string values for the pattern and resolving via `os.environ.get(var_name)`.

**L3-CFG-013** · Parent: L2-CFG-008 · Verification: T · Status: Draft
Missing environment variable during substitution SHALL raise `ConfigurationError` naming the missing variable; a test verifies the error path.

**L3-CFG-014** · Parent: L2-CFG-008 · Verification: T · Status: Draft
Env-var substitution SHALL apply only to string fields declared as substitutable via a `SubstitutableStr` type alias; other fields are treated literally.

**L3-CFG-015** · Parent: L2-CFG-003 · Verification: T · Status: Draft
Config file SHALL be readable (via `Path.is_file()` and open-for-read test) at startup; errors surface as `ConfigurationError` before parsing is attempted.

**L3-CFG-016** · Parent: L2-CFG-002 · Verification: T · Status: Draft
The Pydantic model SHALL be frozen (`model_config = ConfigDict(frozen=True)`); attempts to mutate config at runtime raise `ValidationError`.

---

## L3-DEP: Deployment

**L3-DEP-001** · Parent: L2-DEP-001 · Verification: I · Status: Draft
The GitHub Actions matrix SHALL include at minimum `ubuntu-latest` and `windows-latest` runners executing the full pytest suite.

**L3-DEP-002** · Parent: L2-DEP-001 · Verification: T · Status: Draft
On Windows runners, the test suite SHALL skip any test explicitly marked `@pytest.mark.skipif(sys.platform == 'win32')` and document the reason per-skip.

**L3-DEP-003** · Parent: L2-DEP-002 · Verification: A, I · Status: Draft
The ruff rule `PTH` SHALL be enabled; a CI check SHALL fail if `os.path.join` or string `/` path concatenation appears in any source file.

**L3-DEP-004** · Parent: L2-DEP-002 · Verification: A · Status: Draft
A grep-based CI check SHALL fail on literal `\` or `/` path separators in any `str`-typed filesystem constant outside of URL contexts.

**L3-DEP-005** · Parent: L2-DEP-003 · Verification: A, I · Status: Draft
A ruff rule or grep-based check SHALL fail the build on any import of `os.fork`, `signal.SIGCHLD`, `signal.SIGUSR1`, `signal.SIGUSR2` outside `infrastructure/` modules.

**L3-DEP-006** · Parent: L2-DEP-004 · Verification: I · Status: Draft
The systemd unit file SHALL include `Type=exec`, `Restart=on-failure`, `RestartSec=5s`, `TimeoutStopSec=30s`, `KillSignal=SIGTERM`.

**L3-DEP-007** · Parent: L2-DEP-004 · Verification: I · Status: Draft
The systemd unit SHALL include sandboxing directives: `NoNewPrivileges=true`, `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`, with explicit `ReadWritePaths=` for the data and log directories.

**L3-DEP-008** · Parent: L2-DEP-005 · Verification: I, D · Status: Draft
The NSSM procedure documentation SHALL include the specific commands for: install, set display name and description, configure stdout/stderr redirection, set graceful shutdown method (`AppStopMethodConsole 30000`), and set service account.

**L3-DEP-009** · Parent: L2-DEP-005 · Verification: D · Status: Draft
A demonstration SHALL walk through a clean Windows install from unpack to running service, producing a procedure verification artifact in `docs/procedures/`.

**L3-DEP-010** · Parent: L2-DEP-006 · Verification: T · Status: Draft
The service SHALL install `signal.SIGTERM` (Linux) and `signal.SIGINT`/`SIGBREAK` (Windows) handlers via `asyncio.get_event_loop().add_signal_handler` on Linux and `signal.signal` on Windows.

**L3-DEP-011** · Parent: L2-DEP-006 · Verification: T · Status: Draft
Shutdown SHALL set an `asyncio.Event` that all long-running tasks observe; new RPCs are rejected with `UNAVAILABLE` once the event is set.

**L3-DEP-012** · Parent: L2-DEP-006 · Verification: T, D · Status: Draft
In-flight gRPC calls SHALL have `service.shutdown_grace_period_seconds` (default 30) to complete before being force-cancelled; a test SHALL exercise this with a synthetic long-running RPC.

**L3-DEP-013** · Parent: L2-DEP-007 · Verification: I · Status: Draft
`pyproject.toml` SHALL declare `python = ">=3.10,<4.0"` in `[tool.poetry.dependencies]`.

**L3-DEP-014** · Parent: L2-DEP-008 · Verification: I · Status: Draft
`poetry.lock` SHALL be committed; a pre-commit hook SHALL fail if `pyproject.toml` is modified without a corresponding lockfile update.

**L3-DEP-015** · Parent: L2-DEP-009 · Verification: I · Status: Draft
The console script entry SHALL be declared as `message-service = "message_service.interfaces.cli.main:main"` in `[tool.poetry.scripts]`.

**L3-DEP-016** · Parent: L2-DEP-009 · Verification: T · Status: Draft
A smoke test SHALL run `poetry run message-service --help` in CI and assert exit code 0 with help text containing "config".

**L3-DEP-017** · Parent: L2-DEP-001 · Verification: T · Status: Draft
Cross-platform line-ending tests SHALL verify that templates and configs load identically on LF and CRLF line endings.

**L3-DEP-018** · Parent: L2-DEP-003 · Verification: A · Status: Draft
A static check SHALL assert that no `domain/` or `application/` module imports from `multiprocessing`, `subprocess`, `os.fork`, or POSIX-only signal modules.

---

## Document change history

| Date       | Author | Change                                           |
|------------|--------|--------------------------------------------------|
| 2026-04-18 | Joey   | Initial L3 draft; 287 statements across 14 cats. |
