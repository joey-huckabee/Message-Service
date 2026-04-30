# 01 — hello-world

Single-stage smoke test of the Message-Service end-to-end pipeline.

## What this demonstrates

The smallest possible run-through of the gRPC API:

1. `BeginRun` declares one pipeline (`hello-world`) with one stage (`greeting`) and selects `SINGLE_AGGREGATED` attachment mode.
2. `SubmitStageReport` provides the stage's report contribution context.
3. `FinalizeRun` triggers assembly + delivery.
4. The aggregation template wraps the rendered fragment into one HTML attachment.
5. The mailer adapter sends the email to the in-process SMTP capture; the demo prints it.

## Prerequisites

- `poetry install` completed at the repo root.
- TCP ports 50051 (gRPC), 8080 (dashboard), and 1025 (SMTP capture) free.
- No real SMTP server. The capture is in-process — see the top-level [`README.md`](../README.md#no-mail-server-is-required).

## How to run

From the repo root:

```bash
poetry run python examples/01-hello-world/run.py
```

Expected duration: ~5 seconds.

## Expected output

(timestamps vary; `<run_id>` substituted for the UUID4)

```
Scenario 01 — hello-world
-------------------------
[..:..:..] Step 1: Reset state, write templates + tag vocabulary
[..:..:..] Step 2: Booting service (examples\01-hello-world\config.toml)
[service] {"event": "service_running", ...}
[..:..:..] Step 3: BeginRun (pipeline=hello-world, single stage 'greeting')
   run_id = <run_id>
[..:..:..] Step 4: SubmitStageReport for stage 'greeting'
   was_retry = False
[..:..:..] Step 5: FinalizeRun → triggers aggregation + delivery
[..:..:..] Step 6: Wait for SMTP capture to receive the email

Captured email
--------------
[..:..:..] From:        message-service@example.com
[..:..:..] Envelope to: message-service@example.com, alice@example.com
[..:..:..] Subject:     [hello-world] run <run_id>
[..:..:..] Body preview (~224 bytes of HTML):
   <html>   <body>     <h2>Run <run_id></h2>     <p>Pipeline: hello-world</p>...
[..:..:..] Attachments: 1
   hello-world_<run_id>.html (~224 bytes)

Expectations
------------
[..:..:..] ✓ exactly one email captured
[..:..:..] ✓ alice is on the SMTP envelope
[..:..:..] ✓ subject names the pipeline
[..:..:..] ✓ body references the run id
[..:..:..] ✓ one HTML attachment (SINGLE_AGGREGATED)
[..:..:..] ✓ attachment contains 'Hello, world!'

Expectation summary
-------------------
[..:..:..] ✓ All 6 expectations passed.
```

## What to look for

- The subject is `[<pipeline>] run <run_id>` (L2-MAIL-014).
- The recipient envelope contains both the configured `from_address` and `alice@example.com`. The mailer addresses the email as `To: <from_address>` with the actual recipients in `Bcc:` (RFC 2822 "undisclosed recipients" pattern).
- The HTML attachment is named `<pipeline>_<run_id>.html` and embeds the rendered fragment for stage `greeting`.

## Cleanup

The demo is idempotent: re-running it deletes `.tmp/`, the SQLite file, and any side-effects from the previous run before starting. To clean up manually:

```bash
rm -rf examples/01-hello-world/.tmp examples/01-hello-world/templates examples/01-hello-world/templates.toml examples/01-hello-world/tags.toml
```

## Troubleshooting

- **`Address already in use`**: another process is on 50051 / 8080 / 1025. Check with `Get-NetTCPConnection -State Listen -LocalPort 50051,8080,1025` (Windows) or `ss -ltn | grep -E ':(1025|8080|50051) '` (Linux/macOS).
- **`gRPC port did not bind within 15s`**: the service crashed on boot. Look at the `[service]` lines — typically a config-validation error. The exit code from the subprocess will be `2` for bad config, `1` for crash.
- **No email captured**: the run finalized but the assembler bailed (check the service's structured logs for `assemble_failed` or `mailer_send_failed`). Ensure the in-process SMTP server bound on 1025 — the demo prints `controller_started` if it did.
