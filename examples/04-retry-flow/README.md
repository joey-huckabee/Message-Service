# 04 — retry flow

Idempotent stage overwrite: same stage_id submitted twice; second submission wins.

## What this demonstrates

`SubmitStageReport` is idempotent on `(run_id, stage_id)` per L1-STAGE-002 / L2-STAGE-004. When the same stage is submitted a second time:

1. The first submission returns `was_retry=false` and the stage transitions to `SUBMITTED`.
2. The second submission for the *same* stage returns `was_retry=true`, transitions the stage to `RETRIED`, and overwrites the prior `report_contribution`. The first submission's data is replaced — only the second submission contributes to the final email.

The demo writes two visibly distinct payloads (`FIRST_ATTEMPT records=1000` and `SECOND_ATTEMPT records=1500_corrected`) so you can see in the captured attachment that only the second made it through.

## Prerequisites

- `poetry install` completed at the repo root.
- TCP ports 50054 (gRPC), 8083 (dashboard), 1028 (SMTP capture) free.

## How to run

```bash
poetry run python examples/04-retry-flow/run.py
```

Expected duration: ~6 seconds.

## Expected output

```
Scenario 04 — retry flow (idempotent overwrite)
-----------------------------------------------
[..:..:..] Step 1: Reset state, write templates + tag vocabulary
[..:..:..] Step 2: Booting service (examples\04-retry-flow\config.toml)
[service] {"event": "service_running", ...}
[..:..:..] Step 3: BeginRun (pipeline=etl-nightly, stages=extract,load)
   run_id = <run_id>
[..:..:..] Step 4: SubmitStageReport: extract — first attempt (initial run)
   was_retry = False  (expect False)
[..:..:..] Step 5: SubmitStageReport: extract — RESUBMIT (this is the retry)
   was_retry = True  (expect True)
[..:..:..] Step 6: SubmitStageReport: load — first attempt
[..:..:..] Step 7: FinalizeRun → triggers aggregation + delivery
[..:..:..] Step 8: Wait for SMTP capture to receive the email

Captured email
--------------
[..:..:..] Subject:     [etl-nightly] run <run_id>
[..:..:..] Attachments: 1
[..:..:..] Aggregated attachment preview (first 240 chars):
   ... <h3>extract</h3>   <pre>SECOND_ATTEMPT records=1500_corrected</pre> ...

Expectations
------------
[..:..:..] ✓ exactly one email captured
[..:..:..] ✓ first submission was_retry == False
[..:..:..] ✓ second submission was_retry == True
[..:..:..] ✓ ONE aggregated attachment
[..:..:..] ✓ aggregated attachment shows SECOND attempt's payload
[..:..:..] ✓ aggregated attachment does NOT show the first attempt
[..:..:..] ✓ aggregated attachment includes the load stage
[..:..:..] ✓ subject pins the run_id

Expectation summary
-------------------
[..:..:..] ✓ All 8 expectations passed.
```

## What to look for

- `was_retry` flips from `False` to `True` between the two submissions.
- The "FIRST_ATTEMPT" string is **absent** from the final email — the second submission replaced it cleanly.
- The retry path is also visible in the structured logs: search for `stage_retried` or `state_transition` events with `from=SUBMITTED to=RETRIED`.

## Cleanup

```bash
rm -rf examples/04-retry-flow/.tmp examples/04-retry-flow/templates examples/04-retry-flow/templates.toml examples/04-retry-flow/tags.toml
```

## Troubleshooting

- **`was_retry=False` on the second submission**: the stage transitions are tracked on `(run_id, stage_id)`. If you change the `stage_id` between submissions, the second is treated as a new stage, not a retry.
- **Both attempts visible in the attachment**: the retry path overwrites the prior context but does **not** mutate any already-rendered fragment. If you see both, something else is wrong — check the service logs for a state-machine violation.
