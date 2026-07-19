# 07 — manual resend via dashboard

Complete a run, then re-deliver it via the dashboard's `POST /runs/{run_id}/resend` route.

## What this demonstrates

The operator-facing recovery path described by L1-DASH-003 / L3-DASH-027/028. The full flow:

1. A user is seeded with an Argon2 password hash and a GLOBAL subscription so they receive every run.
2. A pipeline runs end-to-end (`BeginRun` → `SubmitStageReport` → `FinalizeRun`); the user receives the first email.
3. The demo logs into the dashboard via `POST /login`, capturing the session cookie (`msp_session`) and the CSRF cookie (`msp_csrf`).
4. It then calls `POST /runs/{run_id}/resend` with both cookies plus the `X-CSRF-Token` header (CSRF double-submit per L3-DASH-018).
5. The user receives a second email — the resend.

Both emails use the same standard subject format `[<pipeline>] run <run_id>` — as of v0.6.0 the resend shares the first-delivery subject construction (`AssembleAndDeliverUseCase.build_subject`, L3-MAIL-034), so it also honors any per-pipeline `subject_templates` override. The resend is distinguished not by its subject but by its `RESEND_REPORT` audit action (see below).

## Prerequisites

- `poetry install` completed at the repo root.
- TCP ports 50057 (gRPC), 8086 (dashboard), 1031 (SMTP capture) free.

## How to run

```bash
poetry run python examples/07-manual-resend/run.py
```

Expected duration: ~10 seconds.

## Expected output

```
Scenario 07 — manual resend via dashboard
-----------------------------------------
[..:..:..] Step 1: Reset state, write templates + tag vocabulary
[..:..:..] Step 2: Booting service (examples\07-manual-resend\config.toml)
[service] {"event": "service_running", ...}
[..:..:..] Step 3: BeginRun (pipeline=etl-nightly, single stage)
   run_id = <run_id>
[..:..:..] Step 4: SubmitStageReport: extract
[..:..:..] Step 5: FinalizeRun → triggers FIRST delivery
[..:..:..] Step 6: Wait for FIRST email (FinalizeRun delivery)
[..:..:..] Step 7: POST /login (acquire session + CSRF cookies)
[service] {"event": "login_success", "user_id": 1, ...}
   login OK; csrf cookie present (length 43)
[..:..:..] Step 8: POST /runs/<short>…/resend (CSRF-protected)
[service] {"event": "resend_completed", "run_id": "<run_id>", "recipient_count": 1, ...}
   HTTP 202 {"status":"ok"}
[..:..:..] Step 9: Wait for SECOND email (resend delivery)

Captured deliveries
-------------------
[..:..:..] messages captured: 2
     #1: subject='[etl-nightly] run <run_id>'
     #2: subject='[etl-nightly] run <run_id>'

Expectations
------------
[..:..:..] ✓ resend route returned HTTP 202
[..:..:..] ✓ two emails captured (original + resend)
[..:..:..] ✓ first delivery's subject pins the run_id
[..:..:..] ✓ resend's subject pins the same run_id
[..:..:..] ✓ both deliveries went to the same recipient set

Expectation summary
-------------------
[..:..:..] ✓ All 5 expectations passed.
```

## What to look for

- Login emits `login_success` at INFO level; the resend emits `resend_completed` with `outcome=SUCCESS` and the `recipient_count`.
- The two captured emails share the same `run_id`, `Subject` header, and recipient set — the resend reproduces the original delivery. It is distinguished in the audit log by its `RESEND_REPORT` action, not by the subject.
- The resend re-resolves recipients at the moment it fires (per L2-DASH-008 / L3-DASH-012). If you'd added or removed subscriptions between the original send and the resend, the resend would honor the *current* set, not the historical one.

## Cleanup

```bash
rm -rf examples/07-manual-resend/.tmp examples/07-manual-resend/templates examples/07-manual-resend/templates.toml examples/07-manual-resend/tags.toml
```

## Troubleshooting

- **HTTP 401 on POST /runs/{run_id}/resend**: the session cookie didn't make it through. `httpx.AsyncClient` carries cookies between requests automatically; if you split the login + resend across two clients, the cookie won't transfer. Both calls in this demo go through the same `AsyncClient` instance.
- **HTTP 403 with body `CSRF token missing or invalid`**: the `X-CSRF-Token` header is missing or doesn't match the `msp_csrf` cookie. The demo reads the cookie value from the client cookie jar after `/login` and echoes it in the header.
- **HTTP 409**: the run is not in SENT or FAILED state — the resend precondition (L3-DASH-028) rejected it. Make sure the first delivery completed before triggering the resend (the demo's `wait_for(count=1)` ensures this).
