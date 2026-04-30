# 06 — orphan detection

A run is started but never finalized; the sweeper transitions it to ORPHANED after `run_timeout_seconds`.

## What this demonstrates

Most pipelines that integrate with Message-Service are external processes the service has no control over. If one of those processes dies between `BeginRun` and `FinalizeRun`, the in-progress run would otherwise sit in `INITIATED` or `AGGREGATING` forever.

The orphan sweeper (L1-SWEEP-001) runs every `poll_interval_seconds` and transitions any non-terminal run older than `run_timeout_seconds` to `ORPHANED`. The configured `disposition_actions` are then dispatched against each orphan. v1 ships two handlers:

- `NOTIFY_ADMINS` — emits a `sweeper_admin_notification` log event at WARNING level. Operators tail logs and alert on it.
- `DISCARD_SILENTLY` — drops the run on the floor; the `ORPHANED` state transition is the entire story.

This scenario uses tight timings (5-second timeout, 1-second poll cadence) so the orphan transition happens inside the demo's wait window. Production deployments use much longer timeouts (the project default is 3600s).

## Prerequisites

- `poetry install` completed at the repo root.
- TCP ports 50056 (gRPC), 8085 (dashboard), 1030 (SMTP capture) free.

## How to run

```bash
poetry run python examples/06-orphan-detection/run.py
```

Expected duration: ~12 seconds (most of that is the deliberate wait for the sweeper).

## Expected output

```
Scenario 06 — orphan detection
------------------------------
[..:..:..] Step 1: Reset state, write templates + tag vocabulary
[..:..:..] Step 2: Booting service (examples\06-orphan-detection\config.toml)
[service] {"event": "service_running", ...}
[..:..:..] Step 3: BeginRun (this run will be ABANDONED on purpose)
   run_id = <run_id>
[..:..:..] Step 4: SubmitStageReport: extract (only one of two declared stages)
[..:..:..] ! DELIBERATELY skipping FinalizeRun (simulating a stuck pipeline)
[..:..:..] Step 5: Waiting up to 10s for the sweeper to transition the run to ORPHANED (run_timeout_seconds=5)
[service] {"count": 1, "event": "sweeper_tick_found_orphans", ...}
[service] {"orphaned_count": 1, "enqueued_actions": 2, "event": "sweeper_tick_completed", ...}
[service] {"run_id": "<run_id>", "prior_state": "ORPHANED", "event": "sweeper_admin_notification", "level": "warning", ...}
   final state observed: ORPHANED

Verification
------------
[..:..:..] Run ID: <run_id>
[..:..:..] Final state: ORPHANED
[..:..:..] SMTP messages captured: 0
   (The default disposition actions are NOTIFY_ADMINS — log-only in v1 — and DISCARD_SILENTLY. No email is delivered on orphan.)

Expectations
------------
[..:..:..] ✓ run state == ORPHANED
[..:..:..] ✓ no SMTP messages — orphan path does not deliver mail

Expectation summary
-------------------
[..:..:..] ✓ All 2 expectations passed.
```

## What to look for

- The structured log line `sweeper_admin_notification` at WARNING level. This is the operator integration point: aggregate logs and alert on this event id.
- `prior_state` in that log line is the run's state *before* the ORPHANED transition (commonly `INITIATED` or `AGGREGATING`).
- The runs table reflects the new state directly: `SELECT state FROM runs WHERE run_id = ?` returns `ORPHANED`.

## Cleanup

```bash
rm -rf examples/06-orphan-detection/.tmp examples/06-orphan-detection/templates examples/06-orphan-detection/templates.toml examples/06-orphan-detection/tags.toml
```

## Troubleshooting

- **Demo finishes in ORPHANED state but with `final_state == "AGGREGATING"`**: the sweeper poll cadence is 1s but the first tick fires at boot+1s, so the run created at boot+0.5s has only ~4.5s of "age" by the first tick. The demo waits an extra `_GRACE_SECONDS` (4) on top of `run_timeout_seconds` for exactly this reason. If you tightened those timings, raise the grace.
- **`run_timeout_seconds` ignored**: confirm the config TOML loaded — boot logs print `bootstrap_complete` after migrations apply. The sweeper's `run_timeout_seconds` shows up in the structured log via `sweeper_loop_started` events.
