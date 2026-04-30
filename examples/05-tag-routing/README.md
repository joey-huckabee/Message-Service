# 05 — tag-based recipient routing

Two subscribers, different tag preferences. The run's tag set decides who gets the email.

## What this demonstrates

The recipient resolver (`SubscriptionRepository.list_recipients_for_run`) joins subscriptions to runs via three OR'd predicates per L3-SUB-005:

1. `granularity = 'GLOBAL'` — every run matches.
2. `granularity = 'PIPELINE' AND target_value = <pipeline>` — runs of one specific pipeline.
3. `granularity = 'TAG' AND target_value IN (<run.tags>)` — runs carrying any of the user's subscribed tags.

This scenario seeds two users with TAG-granularity subscriptions:

- `prod-watcher@example.com` → TAG `production`
- `staging-watcher@example.com` → TAG `staging`

Then it runs a pipeline tagged only `production`. Only the production subscriber matches the predicate; the staging subscriber is filtered out before the email is composed.

## Prerequisites

- `poetry install` completed at the repo root.
- TCP ports 50055 (gRPC), 8084 (dashboard), 1029 (SMTP capture) free.

## How to run

```bash
poetry run python examples/05-tag-routing/run.py
```

Expected duration: ~6 seconds.

## Expected output

```
Scenario 05 — tag-based recipient routing
-----------------------------------------
[..:..:..] Step 1: Reset state, write templates + tag vocabulary
[..:..:..] Step 2: Booting service (examples\05-tag-routing\config.toml)
[service] {"event": "service_running", ...}
   seeding subscribers: prod-watcher@example.com (TAG=production)
                       staging-watcher@example.com (TAG=staging)
[..:..:..] Step 3: BeginRun (run_tags=['production'] — should match prod-watcher only)
   run_id = <run_id>
[..:..:..] Step 4: SubmitStageReport: extract
[..:..:..] Step 5: FinalizeRun → triggers delivery
[..:..:..] Step 6: Wait for SMTP capture to receive the email

Captured email
--------------
[..:..:..] From:        message-service@example.com
[..:..:..] Envelope to: message-service@example.com, prod-watcher@example.com
[..:..:..] Subject:     [etl-nightly] run <run_id>

Expectations
------------
[..:..:..] ✓ exactly one email captured
[..:..:..] ✓ production subscriber WAS routed (tag matched)
[..:..:..] ✓ staging subscriber was NOT routed (tag did not match)
[..:..:..] ✓ subject pins the run_id

Expectation summary
-------------------
[..:..:..] ✓ All 4 expectations passed.
```

## What to look for

- The captured envelope contains exactly two RCPT entries: the configured `from_address` (the BCC-self pattern from L1-MAIL-006) and `prod-watcher@example.com`.
- `staging-watcher@example.com` is not on the envelope at all. The recipient resolver filtered them out before the message was composed; the SMTP layer never saw their address.

## Variations to try

Edit `run.py` to flip the run's tag set:

- `run_tags=["staging"]` — only `staging-watcher` should receive.
- `run_tags=["production", "staging"]` — both should receive (one email, two RCPT entries).
- `run_tags=["unknown-tag"]` — neither receives. With zero recipients the run is still marked SENT but no SMTP delivery happens (per L3-MAIL-031). The expectation `length(messages, 1)` would fail; the demo is designed for the matching case.

## Cleanup

```bash
rm -rf examples/05-tag-routing/.tmp examples/05-tag-routing/templates examples/05-tag-routing/templates.toml examples/05-tag-routing/tags.toml
```

## Troubleshooting

- **Both subscribers receive**: check that the seeded subscriptions used `granularity='TAG'` and not `'GLOBAL'`. A GLOBAL subscription matches every run regardless of tag.
- **Neither subscriber receives**: the seeded `target_value` may not match the configured tag vocabulary. The demo writes `tags.toml` with both `production` and `staging` so the BeginRun call accepts the tag — if you change the run_tags, also update `_setup()` to widen the vocabulary.
