# 08 — error recovery

Three deliberate gRPC misuse cases to demonstrate the structured-error envelope.

## What this demonstrates

Every domain rejection in Message-Service surfaces as a gRPC status code paired with a trailer:

```
x-message-service-error-code: ERROR_CODE_<NAME>
```

Clients integrate against `error_code` (mechanical match) — not against the human-readable `details` string (informational, may change). The error code values come from the `ErrorCode` enum in `message_service.proto`.

The three exercised cases:

| Case | Cause | gRPC status | Error code |
|------|-------|-------------|-----------|
| 1 | `pipeline_type` not in `[pipelines].registered` | `INVALID_ARGUMENT` | `ERROR_CODE_UNKNOWN_PIPELINE_TYPE` |
| 2 | A `run_tag` not in the configured vocabulary | `INVALID_ARGUMENT` | `ERROR_CODE_UNKNOWN_TAG` |
| 3 | Two `declared_stages` share a `stage_id` | `INVALID_ARGUMENT` | `ERROR_CODE_DUPLICATE_STAGE_ID` |

No successful run is produced. The SMTP capture is asserted empty — every BeginRun is rejected before any delivery path runs.

## Prerequisites

- `poetry install` completed at the repo root.
- TCP ports 50058 (gRPC), 8087 (dashboard), 1032 (SMTP capture) free.

## How to run

```bash
poetry run python examples/08-error-recovery/run.py
```

Expected duration: ~5 seconds.

## Expected output

```
Scenario 08 — error recovery
----------------------------
[..:..:..] Step 1: Reset state, write templates + tag vocabulary
[..:..:..] Step 2: Booting service (examples\08-error-recovery\config.toml)
[service] {"event": "service_running", ...}
[..:..:..] Step 3: Case 1: BeginRun with pipeline_type='not-registered'
   caught: gRPC INVALID_ARGUMENT / ERROR_CODE_UNKNOWN_PIPELINE_TYPE
[..:..:..] Step 4: Case 2: BeginRun with run_tags=['gibberish'] (not in vocabulary)
   caught: gRPC INVALID_ARGUMENT / ERROR_CODE_UNKNOWN_TAG
[..:..:..] Step 5: Case 3: BeginRun with two declared_stages sharing stage_id='extract'
   caught: gRPC INVALID_ARGUMENT / ERROR_CODE_DUPLICATE_STAGE_ID

Captured errors
---------------
[..:..:..] unknown pipeline_type:
     gRPC code:  INVALID_ARGUMENT
     error code: ERROR_CODE_UNKNOWN_PIPELINE_TYPE
     detail:     pipeline_type not registered: 'not-registered'
[..:..:..] unknown tag:
     gRPC code:  INVALID_ARGUMENT
     error code: ERROR_CODE_UNKNOWN_TAG
     detail:     unknown tag(s): ['gibberish']
[..:..:..] duplicate stage_id:
     gRPC code:  INVALID_ARGUMENT
     error code: ERROR_CODE_DUPLICATE_STAGE_ID
     detail:     duplicate stage_id(s) in declared_stages: ['extract']

Expectations
------------
[..:..:..] ✓ three error cases observed
[..:..:..] ✓ no SMTP messages — every BeginRun rejected before delivery
[..:..:..] ✓ case 1 captured
[..:..:..] ✓ case 1 gRPC code is INVALID_ARGUMENT
[..:..:..] ✓ case 1 error code is ERROR_CODE_UNKNOWN_PIPELINE_TYPE
[..:..:..] ✓ case 2 captured
[..:..:..] ✓ case 2 gRPC code is INVALID_ARGUMENT
[..:..:..] ✓ case 2 error code is ERROR_CODE_UNKNOWN_TAG
[..:..:..] ✓ case 3 captured
[..:..:..] ✓ case 3 gRPC code is INVALID_ARGUMENT
[..:..:..] ✓ case 3 error code is ERROR_CODE_DUPLICATE_STAGE_ID

Expectation summary
-------------------
[..:..:..] ✓ All 11 expectations passed.
```

## What to look for

- The service logs each rejection at INFO level with `event=request_rejected` and the same `error_code` that landed in the gRPC trailer. That's how operators correlate a client-visible error with a server-side log entry.
- The `detail` strings are informational and may evolve; callers should not parse them. Match against `error_code` values from the enum instead.

## Other error codes worth knowing about

These are specified in `message_service.proto` but not exercised by this scenario:

| Error code | Cause |
|-----------|-------|
| `ERROR_CODE_UNKNOWN_TEMPLATE` | A `TemplateRef` references a name not in the manifest |
| `ERROR_CODE_MISSING_AGGREGATION_TEMPLATE` | `SINGLE_AGGREGATED` mode with no `aggregation_template` |
| `ERROR_CODE_RUN_NOT_FOUND` | `SubmitStageReport` / `FinalizeRun` for a `run_id` that doesn't exist (gRPC `NOT_FOUND`) |
| `ERROR_CODE_INVALID_RUN_STATE` | `SubmitStageReport` / `FinalizeRun` against a run whose state forbids it (gRPC `FAILED_PRECONDITION`) |
| `ERROR_CODE_CONTEXT_SIZE_EXCEEDED` | A `ReportContribution.context` exceeds `[templates].max_context_bytes` |
| `ERROR_CODE_RENDERED_SIZE_EXCEEDED` | The rendered HTML exceeds `[templates].max_rendered_bytes` |

Each maps to a specific gRPC status code; the partition is documented in the `ErrorCode` enum's comments.

## Cleanup

```bash
rm -rf examples/08-error-recovery/.tmp examples/08-error-recovery/templates examples/08-error-recovery/templates.toml examples/08-error-recovery/tags.toml
```
