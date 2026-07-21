# UI previews

Self-contained, browser-openable **design mockups** of the service's dashboard
pages. Each file is a single HTML document with all CSS and JavaScript inlined
and **no external/third-party dependencies** — open it directly in a browser
(double-click, or `file://…`); no server, authentication, or email backend is
required.

These are **design references for the team**, not the shipped pages. They render
representative sample data so reviewers can agree on layout, states, and styling
before (or alongside) the real implementation. The field names, run/stage states,
and enum values match the actual domain model, so a preview maps directly onto
the corresponding API projection.

| File | Shows | Backed by |
|------|-------|-----------|
| `metrics-dashboard-preview.html` | The embedded metrics dashboard — counters as labeled bars, histograms as count/sum/avg plus bucket bars. | Shipped in v0.12.0 as `GET /admin/metrics` (`interfaces/rest/metrics_dashboard.py`, `L1-DASH-004`). This preview was generated from `render_metrics_dashboard` over representative exposition data. |
| `runs-board-preview.html` | The run-status board — per-state summary with an "In work" total, an In-work / All / Terminal filter, a runs table with pulsing badges for active states, and click-to-expand stage detail. | Design mockup for the v0.14.0 board over the existing `GET /runs` and `GET /runs/{run_id}` APIs (`interfaces/rest/routes/runs.py`). |
| `login-preview.html` | The browser login page — a centered sign-in card (email + password) with an error state, for the configurable local admin account. | Design mockup for the (shipped) v0.15.0 `GET /login` page; submits to the existing JSON `POST /login`. |
| `admin-console-preview.html` | The admin notification console — recipient roster (email, name, Admin/User role, Active/Disabled status) with create / edit / disable / reset-password actions, plus a placeholder Subscriptions tab. | Design mockup for the (shipped) v0.15.0 `GET /admin/console` (recipient management) and v0.16.0 (subscription management). |
| `subscriptions-tab-preview.html` | The admin console's Subscriptions tab — pick a recipient, then add (Global / Pipeline / Tag, with the target chosen from the live vocabulary) or remove their notification subscriptions. | Design mockup for the (shipped) v0.16.0 `GET /admin/subscriptions` + admin-on-behalf subscription API. |

## Keeping these honest

A preview is a snapshot of intended design, not a tested artifact — it can drift
from the real page over time. When a page's shipped implementation changes
materially, refresh (or retire) its preview here so the mockup keeps matching
what the service actually renders. The shipped pages, not these files, are the
source of truth; the automated tests cover the shipped pages.
