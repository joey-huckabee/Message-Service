# 02 — multi-stage SINGLE_AGGREGATED

Four-stage ETL pipeline with `SINGLE_AGGREGATED` attachment mode.

## What this demonstrates

How the aggregation template weaves multiple stage fragments into one HTML attachment:

1. `BeginRun` declares four stages (`extract` → `validate` → `transform` → `load`) in order.
2. `SubmitStageReport` is called four times, once per stage, each with a different report context.
3. `FinalizeRun` triggers assembly. The renderer renders each fragment from `fragment.html.j2`, then injects all four into the aggregation template — producing one combined HTML document per L2-AGGR-005.
4. The mailer attaches that one document to the email.

The body of the email lists the stages as a roster (`extract / validate / transform / load`); the actual stage content lives in the single attached file.

## Prerequisites

- `poetry install` completed at the repo root.
- TCP ports 50052 (gRPC), 8081 (dashboard), 1026 (SMTP capture) free.

## How to run

```bash
poetry run python examples/02-multi-stage-aggregated/run.py
```

Expected duration: ~8 seconds.

## Expected output

```
Scenario 02 — multi-stage SINGLE_AGGREGATED
-------------------------------------------
[..:..:..] Step 1: Reset state, write templates + tag vocabulary
[..:..:..] Step 2: Booting service (examples\02-multi-stage-aggregated\config.toml)
[service] {"event": "service_running", ...}
[..:..:..] Step 3: BeginRun (pipeline=etl-nightly, 4 stages)
   run_id = <run_id>
[..:..:..] Step 4: SubmitStageReport: extract
[..:..:..] Step 4: SubmitStageReport: validate
[..:..:..] Step 4: SubmitStageReport: transform
[..:..:..] Step 4: SubmitStageReport: load
[..:..:..] Step 5: FinalizeRun → triggers aggregation + delivery
[..:..:..] Step 6: Wait for SMTP capture to receive the email

Captured email
--------------
[..:..:..] From:        message-service@example.com
[..:..:..] Envelope to: message-service@example.com, etl-team@example.com
[..:..:..] Subject:     [etl-nightly] run <run_id>
[..:..:..] Body preview (~343 bytes):
   <html>   <body>     <h2>Run <run_id></h2>     <p>Pipeline: etl-nightly</p>
   <p>Stages reported:</p>     <ul>...
[..:..:..] Attachments: 1
   etl-nightly_<run_id>.html (~687 bytes)

Expectations
------------
[..:..:..] ✓ exactly one email captured
[..:..:..] ✓ etl-team is on the SMTP envelope
[..:..:..] ✓ subject names the pipeline
[..:..:..] ✓ body lists every declared stage
[..:..:..] ✓ body mentions stage 'extract'
[..:..:..] ✓ body mentions stage 'validate'
[..:..:..] ✓ body mentions stage 'transform'
[..:..:..] ✓ body mentions stage 'load'
[..:..:..] ✓ ONE aggregated attachment (SINGLE_AGGREGATED)
[..:..:..] ✓ aggregated attachment includes 'extract' fragment
[..:..:..] ✓ aggregated attachment includes 'validate' fragment
[..:..:..] ✓ aggregated attachment includes 'transform' fragment
[..:..:..] ✓ aggregated attachment includes 'load' fragment
[..:..:..] ✓ aggregation template wrapped fragments

Expectation summary
-------------------
[..:..:..] ✓ All 14 expectations passed.
```

## What to look for

- One — and only one — attachment is produced (compare with `03-per-stage-attachments/` which produces four).
- The attachment filename is `<pipeline>_<run_id>.html` (no per-stage suffix).
- The attachment HTML contains every stage's fragment in declared order (`extract` first, `load` last) — sorted by `(stage_order, stage_id)` per L2-AGGR-007/008.

## Cleanup

Re-running the demo wipes prior state. To clean up manually:

```bash
rm -rf examples/02-multi-stage-aggregated/.tmp examples/02-multi-stage-aggregated/templates examples/02-multi-stage-aggregated/templates.toml examples/02-multi-stage-aggregated/tags.toml
```

## Troubleshooting

- **Port collision**: 50052 / 8081 / 1026 differ from scenario 01 so two scenarios can run side-by-side, but if any of the three is already in use, edit `config.toml` and `run.py`'s `_GRPC_PORT` / `_DASHBOARD_PORT` / `_SMTP_PORT` together.
- **Attachment missing one of the stages**: check the service logs for `template_render_failed` — usually a typo in the fragment template's variable references.
