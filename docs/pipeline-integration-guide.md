# Message-Service — Pipeline Integration Guide

This guide is for ETL pipeline authors integrating with Message-Service. It covers the gRPC contract, the run lifecycle, error handling, and an end-to-end example.

For the operational side (deployment, day-2 operations, failure modes), see `docs/operator-runbook.md`. For the architectural rationale, see `docs/adr/`.

## 1. The lifecycle

A *run* is the unit of pipeline observability the service tracks. One pipeline execution → one run → one email. The pipeline issues three RPCs in order:

```
                                                       ┌──────────┐
  ┌─────────────┐  ┌──────────────────────┐ × N       │ Sweeper  │
  │  BeginRun   │→ │  SubmitStageReport   │  ─────→  │  (timeout │
  └─────────────┘  └──────────────────────┘           │  reclaim) │
                                                      └──────────┘
                                                            │
                            ┌─────────────┐                ↓
                            │ FinalizeRun │ ─────→ ORPHANED if too long
                            └─────────────┘                │
                                  │                        │
                                  ↓                        ↓
                              AGGREGATING            disposition handlers
                                  │                  (NOTIFY_ADMINS / DISCARD_SILENTLY)
                                  ↓
                                READY → SENDING → SENT
                                                   ↓
                                            FAILED if delivery breaks
```

The state machine permitted transitions are pinned by `L3-RUN-006`/`L3-RUN-007`. Notable invariants:

- `BeginRun` always lands the run in `INITIATED`.
- The first `SubmitStageReport` for a run transitions `INITIATED → AGGREGATING`. Subsequent submissions stay in `AGGREGATING`.
- `FinalizeRun` requires the run to be in `AGGREGATING`. Anything else (including `INITIATED` with zero submitted stages — see `L3-RUN-021`) returns `FAILED_PRECONDITION`.
- After `FinalizeRun`, the assembly + delivery happens in a background task (`L2-RUN-013`); the RPC returns immediately.
- Any non-terminal run that hasn't transitioned past `sweeper.run_timeout_seconds` becomes a candidate for orphan disposition. The sweeper reclaims it to `ORPHANED` and fires the configured handlers (`NOTIFY_ADMINS`, `DISCARD_SILENTLY`).

## 2. Per-RPC contract

### `BeginRun`

Mints a new run and persists its `declared_stages`. Subsequent `SubmitStageReport` calls validate against this set.

**Required fields**:

- `pipeline_type` (string): MUST be a value declared in the service's `pipelines.registered` config list. Unknown values → `INVALID_ARGUMENT` / `ERROR_CODE_UNKNOWN_PIPELINE_TYPE` with `details = {"submitted": "<value>", "allowed": ["...", "..."]}` (sorted).
- `attachment_mode` (enum): `ATTACHMENT_MODE_PER_STAGE` (one HTML attachment per stage) or `ATTACHMENT_MODE_SINGLE_AGGREGATED` (single rendered attachment combining all stages via the aggregation template).
- `declared_stages` (repeated `DeclaredStage`): the set of stage_ids the pipeline plans to submit. Each carries `stage_id`, `stage_order` (int, used for deterministic ordering in the email), and `report_template` (`TemplateRef` with `name` + `version`).

**Optional fields**:

- `tags` (repeated string): zero or more tag values from the configured tag vocabulary. Subscribers with matching tag preferences receive the email. Unknown tags → `INVALID_ARGUMENT` / `ERROR_CODE_UNKNOWN_TAG` with all invalid tags reported in `details["invalid_tags"]` (sorted, all bad ones at once — `L3-RUN-013`).
- `aggregation_template` (`TemplateRef`): required when `attachment_mode = SINGLE_AGGREGATED`; ignored when `PER_STAGE`. If `SINGLE_AGGREGATED` and `aggregation_template` is missing, the response is `INVALID_ARGUMENT` / `ERROR_CODE_MISSING_AGGREGATION_TEMPLATE` (`L3-RUN-019`).
- `subscription_predicate_tags`: alternate tag set used for subscriber matching. Defaults to `tags` when omitted.

**Template version resolution**: pass the literal sentinel `"latest"` as a `TemplateRef.version` to defer resolution to BeginRun time. The service resolves to the highest manifest entry under the same `name` per PEP 440 ordering, persists the resolved canonical version on the Run aggregate, and freezes it for the run's lifetime (`L1-TMPL-002` / `L3-TMPL-009`/`010`/`011`). Subsequent manifest updates do not retroactively re-version already-initiated runs.

**Response**: `run_id` (canonical UUID4 string), `initiated_at` (server-side timestamp, ISO-Z).

### `SubmitStageReport`

Records one stage's output. Idempotent on retry: a second call for the same `(run_id, stage_id)` overwrites the prior content and emits a separate audit row (`L3-STAGE-006`/`007`).

**Required fields**:

- `run_id` (string): the value returned by `BeginRun`. Unknown → `NOT_FOUND` / `ERROR_CODE_RUN_NOT_FOUND`. Malformed (not a canonical UUID4 string) → `INVALID_ARGUMENT` / `ERROR_CODE_MALFORMED_REQUEST`.
- `stage_id` (string): MUST be in the run's `declared_stages` set from `BeginRun`. Unknown → `INVALID_ARGUMENT` / `ERROR_CODE_UNKNOWN_STAGE` with `details = {"stage_id": "<value>", "declared_stages": [...]}`.
- `report_contribution` (`ReportContribution` sub-message): contains `template` (`TemplateRef`) and `context` (`google.protobuf.Struct`). The Struct converts to a Python `dict` server-side via `MessageToDict` (`L3-AGGR-002`).

**Optional fields**:

- `email_body_contribution`: per-stage email body contribution (currently stored but not consumed by the email body template — see `R-AGGR-001` deferral). Absent → no contribution; passed through to the audit log per-stage state.
- `was_retry` (bool): pipeline-supplied hint that this is a retry (e.g., after a transient failure mid-pipeline). Influences the audit row's `details.was_retry` field but does NOT change the state machine — the server detects retries automatically by checking the existing stage state. The hint exists primarily for log-correlation purposes.

**Response**: `stage_state` (the new state, typically `SUBMITTED` or `RETRIED`), `was_retry` (server-determined; reflects whether the stage already had a submission).

### `FinalizeRun`

Closes the run and triggers the assembly + delivery background task.

**Required fields**:

- `run_id` (string).

**Response**: `state` (the new state, typically `READY`), `finalized_at` (server-side timestamp).

**Important**: `FinalizeRun` returns BEFORE delivery completes. The actual email send happens in `BackgroundTaskScheduler` (`L3-RUN-022`). To observe delivery success, poll the run via the dashboard `GET /runs/{run_id}` or scrape `message_service_email_delivery_outcomes_total{outcome}`.

## 3. Tag vocabulary

The service ships a TOML tag vocabulary file (`tags.vocabulary_path`). Format:

```toml
[[tag]]
name = "production"
description = "Customer-impacting runs"

[[tag]]
name = "nightly"
description = "Off-hours batch runs"
```

Tag names match the regex `^[a-z][a-z0-9_-]{0,63}$` (`L3-SUB-010`). Operators add tags by editing the file and restarting the service (hot-reload is `R-TMPL-002`-deferred). Pipelines must use only configured tag values; unknown tags fail `BeginRun` with all-at-once reporting per `L3-RUN-013`.

## 4. Template references

Templates are operator-managed and shipped via `templates.manifest_path` (a TOML manifest), with each entry pointing at a Jinja2 source file (and optionally a JSON Schema for context validation). Pipeline `TemplateRef` values are `(name, version)` pairs that the service resolves against this manifest.

**Three template kinds**:

- `REPORT_FRAGMENT` — per-stage report body. The pipeline supplies the `context` dict per `SubmitStageReport`. Consumed in both attachment modes.
- `AGGREGATION` — combines per-stage rendered fragments into a single attachment. Consumed only in `SINGLE_AGGREGATED` mode. Receives `{stages, run_id, run_metadata, pipeline_type}` per `L3-AGGR-006`.
- `EMAIL_BODY` — the email body itself. Service-wide single template (per `R-TMPL-001` deferral on per-pipeline override). Receives a fixed-shape context (run metadata + per-stage identifiers; per-stage email body contributions are `R-AGGR-001`-deferred).

**Version semantics**:

- `version` is operator-defined. The manifest comparison is exact-string equality, but `latest` resolution uses `packaging.version.Version` (PEP 440) for ordering.
- Pre-release versions (`1.0.0rc1`) order below the corresponding release per PEP 440.
- Submitting an unknown `(name, version)` → `INVALID_ARGUMENT` / `ERROR_CODE_UNKNOWN_TEMPLATE` with both `name` and `version` echoed in `details`.

**Context validation**: if a template's manifest entry declares a `context_schema_path`, the renderer validates the supplied context against the JSON Schema (Draft 2020-12) at render time and rejects violations with `INVALID_ARGUMENT` / `ERROR_CODE_CONTEXT_SCHEMA_VIOLATION`. Templates without `context_schema_path` skip validation (`L3-TMPL-030`). The error's `details` carries `json_pointer` (RFC 6901 path to the offending element), `validator` (the failing JSON Schema keyword like `"type"` or `"required"`), `instance_value` (the rejected value), and `message`.

## 5. Error codes

Every error response carries `x-message-service-error-code` in the trailing metadata (`L3-API-011`). Use this code for programmatic handling rather than parsing the human-readable message.

| gRPC status | Error code | Trigger | Pipeline action |
|---|---|---|---|
| `INVALID_ARGUMENT` | `ERROR_CODE_UNKNOWN_PIPELINE_TYPE` | `BeginRun` with unregistered `pipeline_type` | Fix client config; do NOT retry |
| `INVALID_ARGUMENT` | `ERROR_CODE_UNKNOWN_TAG` | `BeginRun` with tags not in vocabulary | Fix tag list; do NOT retry |
| `INVALID_ARGUMENT` | `ERROR_CODE_DUPLICATE_STAGE_ID` | `BeginRun` `declared_stages` has duplicates | Fix client logic; do NOT retry |
| `INVALID_ARGUMENT` | `ERROR_CODE_UNKNOWN_TEMPLATE` | `(name, version)` not in manifest | Fix template ref; do NOT retry |
| `INVALID_ARGUMENT` | `ERROR_CODE_MISSING_AGGREGATION_TEMPLATE` | `SINGLE_AGGREGATED` + missing aggregation template | Fix request; do NOT retry |
| `INVALID_ARGUMENT` | `ERROR_CODE_UNKNOWN_STAGE` | `SubmitStageReport.stage_id` not declared | Fix client logic; do NOT retry |
| `INVALID_ARGUMENT` | `ERROR_CODE_CONTEXT_SCHEMA_VIOLATION` | Stage context fails JSON Schema | Inspect `details.json_pointer`; fix data; do NOT retry the same payload |
| `INVALID_ARGUMENT` | `ERROR_CODE_CONTEXT_SIZE_EXCEEDED` | Context > `templates.max_context_bytes` (default 1 MiB) | Slim context; do NOT retry |
| `INVALID_ARGUMENT` | `ERROR_CODE_MALFORMED_REQUEST` | Malformed `run_id` (not a UUID4 string) | Fix client; do NOT retry |
| `NOT_FOUND` | `ERROR_CODE_RUN_NOT_FOUND` | `run_id` doesn't exist | Pipeline forgot the BeginRun response or sent stale id |
| `FAILED_PRECONDITION` | `ERROR_CODE_INVALID_RUN_STATE` | E.g., `FinalizeRun` on a non-AGGREGATING run | Inspect `details.run_state`; do NOT retry |
| `INTERNAL` | `ERROR_CODE_INTERNAL` | Unhandled server-side error | Carries `x-message-service-correlation-id`; report the correlation id to operators; safe to retry once after delay |

The `details` dict's content is also redacted before flowing to logs (`L3-OBS-005`/`006`); if a pipeline includes sensitive values in submitted context, those fields are stripped from the error response's logged form (but the response itself is unredacted — your client sees the original).

## 6. Idempotency and retry

| Scenario | Idempotent? | Notes |
|---|---|---|
| Two `BeginRun` calls with the same payload | NO — produces two separate runs | Save the response's `run_id`; don't replay BeginRun |
| `SubmitStageReport` retry for the same `(run_id, stage_id)` | YES — second submission overwrites first | The stage transitions `SUBMITTED → RETRIED`; both submissions are audited |
| Retrying a failed `BeginRun` (e.g., transient transport error) | OK | If the original RPC may have committed before the connection dropped, the retry creates a duplicate run; check via the dashboard before retrying |
| Retrying `FinalizeRun` after success | NO — second call returns `FAILED_PRECONDITION` (run already past `AGGREGATING`) | Treat as success |
| Retrying `FinalizeRun` after transport error | OK | Use the same run_id; the second call either succeeds (first didn't commit) or returns `FAILED_PRECONDITION` (first did) |

**Retry guidance**: pipelines SHOULD retry on `INTERNAL` (the server reports a correlation id; operators may need it for debugging), `UNAVAILABLE` (server may be cycling — backoff), and `DEADLINE_EXCEEDED`. Pipelines SHOULD NOT retry on the `INVALID_ARGUMENT` / `NOT_FOUND` / `FAILED_PRECONDITION` codes — these are client errors that won't fix themselves on retry.

## 7. Rate considerations

v1 deliberately omits per-pipeline rate limiting (`L1-API-005`-deferred per the trusted-ISOLAN deployment context). The single-shared SQLite connection (per ADR-001) effectively serializes concurrent gRPC writers — sustained high write rates from one pipeline can elevate latency for other pipelines on the same service instance. If multiple pipelines share an instance, stagger their schedules.

The default `grpc.max_concurrent_rpcs = 100` (per `L3-API-001`) caps in-flight RPCs at the server level; clients exceeding the cap experience gRPC `RESOURCE_EXHAUSTED`. Operators tune this per workload.

## 8. End-to-end example

Python pseudo-code using the generated stubs (real client code would handle channel lifecycle and error translation more carefully):

```python
import grpc
from google.protobuf.struct_pb2 import Struct
from message_service_proto.v1 import message_service_pb2 as pb
from message_service_proto.v1 import message_service_pb2_grpc as pb_grpc


def run_etl_pipeline(channel: grpc.Channel) -> None:
    stub = pb_grpc.MessageServiceStub(channel)

    # 1. Begin the run.
    begin_resp = stub.BeginRun(
        pb.BeginRunRequest(
            pipeline_type="etl-nightly",
            attachment_mode=pb.ATTACHMENT_MODE_SINGLE_AGGREGATED,
            tags=["production", "etl"],
            aggregation_template=pb.TemplateRef(
                name="nightly_summary",
                version="latest",
            ),
            declared_stages=[
                pb.DeclaredStage(
                    stage_id="extract",
                    stage_order=0,
                    report_template=pb.TemplateRef(name="extract_rpt", version="1.0"),
                ),
                pb.DeclaredStage(
                    stage_id="transform",
                    stage_order=1,
                    report_template=pb.TemplateRef(name="transform_rpt", version="1.0"),
                ),
                pb.DeclaredStage(
                    stage_id="load",
                    stage_order=2,
                    report_template=pb.TemplateRef(name="load_rpt", version="1.0"),
                ),
            ],
        )
    )
    run_id = begin_resp.run_id

    # 2. Submit each stage as it completes.
    for stage_id, payload in [
        ("extract", {"rows_extracted": 12_345, "source": "sales_dw"}),
        ("transform", {"rows_input": 12_345, "rows_output": 12_300, "rejects": 45}),
        ("load", {"rows_loaded": 12_300, "target": "reporting_dw", "duration_s": 23.4}),
    ]:
        ctx = Struct()
        ctx.update(payload)
        stub.SubmitStageReport(
            pb.SubmitStageReportRequest(
                run_id=run_id,
                stage_id=stage_id,
                report_contribution=pb.ReportContribution(
                    template=pb.TemplateRef(name=f"{stage_id}_rpt", version="1.0"),
                    context=ctx,
                ),
            )
        )

    # 3. Close the run.
    stub.FinalizeRun(pb.FinalizeRunRequest(run_id=run_id))
    # The email sends in the background; FinalizeRun returns immediately.


def main() -> None:
    with grpc.insecure_channel("message-service-host:50051") as channel:
        run_etl_pipeline(channel)


if __name__ == "__main__":
    main()
```

For pipelines that want to observe delivery, scrape `message_service_email_delivery_outcomes_total{outcome="SUCCESS|FAILURE"}` or fetch `GET /runs/{run_id}` from the dashboard after a delay (the delivery typically completes within a few seconds of `FinalizeRun` for normal-sized emails).

## 9. Where to look for what

| Question | Where the answer lives |
|---|---|
| Exact wire shape of an RPC | `message_service_proto/v1/message_service.proto` (the canonical proto definition) |
| What does requirement L3-FOO-NNN say? | `docs/L3-REQ.md` |
| What's the operational model? | `docs/operator-runbook.md` |
| Why does the service look this way? | `docs/adr/` |
| What's deferred to v2? | `docs/ROADMAP.md` |
| Where do I see this requirement tested? | `docs/TRACE-MATRIX.md` |
