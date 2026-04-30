# 03 — PER_STAGE attachments

Same four-stage pipeline as 02 but with `attachment_mode = PER_STAGE`.

## What this demonstrates

How `PER_STAGE` produces one MIME attachment per stage instead of a single aggregated document:

1. `BeginRun` selects `ATTACHMENT_MODE_PER_STAGE`. The aggregation template is intentionally omitted — supplying one alongside `PER_STAGE` is a `MISSING_AGGREGATION_TEMPLATE` violation in the opposite direction (per L2-RUN-011 / L2-AGGR-009).
2. `SubmitStageReport` is called four times.
3. `FinalizeRun` triggers per-stage assembly. The renderer renders each stage's fragment template; the mailer wraps each rendered fragment as its own attachment.
4. The captured email has four attachments, each named `<pipeline>_<run_id>_<stage_id>.html`.

Compare with `02-multi-stage-aggregated/` (one combined attachment).

## Prerequisites

- `poetry install` completed at the repo root.
- TCP ports 50053 (gRPC), 8082 (dashboard), 1027 (SMTP capture) free.

## How to run

```bash
poetry run python examples/03-per-stage-attachments/run.py
```

Expected duration: ~8 seconds.

## Expected output

```
Scenario 03 — PER_STAGE attachments
-----------------------------------
[..:..:..] Step 1: Reset state, write templates + tag vocabulary
[..:..:..] Step 2: Booting service (examples\03-per-stage-attachments\config.toml)
[service] {"event": "service_running", ...}
[..:..:..] Step 3: BeginRun (PER_STAGE, no aggregation template)
   run_id = <run_id>
[..:..:..] Step 4: SubmitStageReport: extract
[..:..:..] Step 4: SubmitStageReport: validate
[..:..:..] Step 4: SubmitStageReport: transform
[..:..:..] Step 4: SubmitStageReport: load
[..:..:..] Step 5: FinalizeRun → triggers per-stage assembly + delivery
[..:..:..] Step 6: Wait for SMTP capture to receive the email

Captured email
--------------
[..:..:..] From:        message-service@example.com
[..:..:..] Envelope to: message-service@example.com, etl-team@example.com
[..:..:..] Subject:     [etl-nightly] run <run_id>
[..:..:..] Attachments: 4
   etl-nightly_<run_id>_extract.html (~76 bytes)
   etl-nightly_<run_id>_validate.html (~88 bytes)
   etl-nightly_<run_id>_transform.html (~75 bytes)
   etl-nightly_<run_id>_load.html (~73 bytes)

Expectations
------------
[..:..:..] ✓ exactly one email captured
[..:..:..] ✓ etl-team is on the SMTP envelope
[..:..:..] ✓ FOUR attachments (one per stage)
[..:..:..] ✓ attachment for stage 'extract' present
[..:..:..] ✓ attachment for stage 'validate' present
[..:..:..] ✓ attachment for stage 'transform' present
[..:..:..] ✓ attachment for stage 'load' present
[..:..:..] ✓ 'extract' attachment carries its own payload
[..:..:..] ✓ 'validate' attachment carries its own payload
[..:..:..] ✓ 'transform' attachment carries its own payload
[..:..:..] ✓ 'load' attachment carries its own payload
[..:..:..] ✓ filenames embed the run_id

Expectation summary
-------------------
[..:..:..] ✓ All 12 expectations passed.
```

## What to look for

- Four attachments, each with a `_<stage_id>.html` suffix.
- Each file contains only that stage's fragment — they do not share an aggregation wrapper.
- The body still lists every stage (so a recipient can tell which attachments to expect even before opening any).

## Cleanup

```bash
rm -rf examples/03-per-stage-attachments/.tmp examples/03-per-stage-attachments/templates examples/03-per-stage-attachments/templates.toml examples/03-per-stage-attachments/tags.toml
```

## Troubleshooting

- **Fewer attachments than expected**: a stage that produces an empty rendered fragment is dropped per L3-AGGR-009. Check that every fragment template's variables resolve to non-empty content.
- **`MISSING_AGGREGATION_TEMPLATE`**: you accidentally supplied an `aggregation_template` alongside `PER_STAGE`. Remove it; the service ignores it for PER_STAGE but earlier validation may flag a mismatch in mixed-mode setups.
