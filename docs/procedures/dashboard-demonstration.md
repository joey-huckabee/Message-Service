# Dashboard demonstration — verification procedure

This document is the Demonstration (D) verification artifact for the browser
dashboard requirements whose correctness is *visual* and therefore cannot be
fully established by automated test alone:

- **L1-DASH-002** — subscription management UI
- **L1-DASH-003** — past-runs view, resend, report viewer
- **L1-DASH-004** — embedded metrics dashboard (hand-authored inline SVG)
- **L1-DASH-006** — run-status board (in-flight + terminal, filter, drill-in)
- **L1-DASH-007** — browser login page
- **L1-DASH-008** — administrator console (recipient roster management)
- **L1-DASH-009** — administrator-managed subscriptions
- **L2-DASH-006** — subscription-creation UI tag/pipeline selection

An operator runs the service locally, opens each page, ticks the checkpoints,
and signs the attestation block. The completed (signed) document is the
demonstration evidence. Automated tests cover the *behavior* (routes, auth,
projections, parsing); this procedure covers the *rendered appearance and
interaction* that only a human can confirm.

## Pre-conditions

- A checkout of Message-Service with `poetry install` completed.
- No real SMTP relay is required. The service can run against a local
  capture/echo SMTP (e.g. `python -m aiosmtpd -n -l localhost:8025`) or a config
  whose `mail.smtp.host`/`port` point at it; email delivery is not exercised by
  this procedure (it demonstrates the dashboard, not delivery).
- A config file with `[auth.admin]` provisioning a known admin account, so the
  login can be demonstrated. Example (`config/dev-config.toml` style):

  ```toml
  [auth.admin]
  email = "admin@example.com"
  password = "demo-password-change-me"
  ```

## Procedure + checkpoints

### Step 1 — Start the service

```bash
poetry run message-service --config config/dev-config.toml
```

**Checkpoint 1.1**: startup logs include `grpc_server_listening`,
`rest_server_listening`, and `service_running` with no ERROR records.

**Checkpoint 1.2**: the dashboard host/port from `[dashboard]` is reachable
(e.g. `http://localhost:8080`).

### Step 2 — Login page (L1-DASH-007)

Open `GET /login` in a browser.

**Checkpoint 2.1**: a centered sign-in card renders with email + password fields
and a submit button; no console errors; no network requests to any off-origin
host (verify in the browser dev-tools Network tab — every request is same-origin
or a `data:`/inline resource).

**Checkpoint 2.2**: submitting a wrong password shows the error state; submitting
the configured admin credentials lands on the authenticated dashboard.

### Step 3 — Administrator console (L1-DASH-008)

Navigate to the admin console.

**Checkpoint 3.1**: the recipient roster lists accounts with email, display name,
role (Admin/User), and status (Active/Disabled).

**Checkpoint 3.2**: create, edit (display name / role / disabled), and
reset-password actions are present and operate on the roster (create a test
recipient and observe it appear in the list).

### Step 4 — Subscriptions console (L1-DASH-002 / L1-DASH-009 / L2-DASH-006)

Open the Subscriptions tab/console.

**Checkpoint 4.1**: a recipient can be chosen; a subscription can be added at
GLOBAL / PIPELINE / TAG granularity, with the PIPELINE/TAG target chosen from a
dropdown populated with the live registered pipelines and tag vocabulary (not
free text).

**Checkpoint 4.2**: adding then removing a subscription for the chosen recipient
updates the displayed subscription list accordingly.

### Step 5 — Past-runs view + report viewer + resend (L1-DASH-003)

With at least one terminal run present (drive one via a gRPC begin→submit→finalize,
or an example scenario under `examples/`), open the past-runs list.

**Checkpoint 5.1**: past runs list paginated, most-recent-first, showing state,
pipeline type, tags, and timestamps.

**Checkpoint 5.2**: opening a run's report renders the saved email body HTML; the
per-stage fragment viewer renders a stage's fragment. (Both are admin-only.)

**Checkpoint 5.3**: triggering a resend on a SENT/FAILED run returns success and
records a `RESEND_REPORT` audit entry (visible in the audit view).

### Step 6 — Run-status board (L1-DASH-006)

Open the run-status board.

**Checkpoint 6.1**: runs are grouped with an in-flight-versus-terminal
distinction; a state filter narrows the view; expanding a run row reveals its
stage list.

### Step 7 — Metrics dashboard (L1-DASH-004)

Open the embedded metrics dashboard.

**Checkpoint 7.1**: the metric families render as hand-authored inline SVG charts
(counters, histograms) — NOT via any third-party charting library. Confirm in the
Network tab that no external script/style/font/CDN is fetched (same-origin only).

**Checkpoint 7.2**: the numbers shown match the raw `GET /metrics` exposition
(spot-check one counter value).

## Operator attestation

I confirm that I performed the procedure above on the environment described and
that every checkpoint passed as written.

- Operator name: ____________________________
- Environment (OS / browser / version): ____________________________
- Message-Service version (`git describe` or tag): ____________________________
- Date: ____________________________
- Signature: ____________________________

Any checkpoint that did NOT pass SHALL be recorded here with the observed
behavior, and the demonstration is not complete until re-run to a full pass:

- Deviations: ____________________________
