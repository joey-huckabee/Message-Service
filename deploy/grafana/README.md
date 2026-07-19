# Grafana dashboard for Message-Service

`message-service-dashboard.json` is a pre-built Grafana dashboard for the
Prometheus metrics Message-Service exposes at `/metrics` (`L1-OBS-002`). It is
the external-monitoring counterpart to the built-in admin dashboard at
`GET /admin/metrics` — use whichever fits your operational setup (or both).

## Panels

- Run- and stage-state transition rates (by target state)
- Email delivery outcomes (by outcome)
- Average email size and run duration
- Email size and run duration p95 (from the histogram buckets)

## Import

1. In Grafana: **Dashboards → New → Import**.
2. Upload `message-service-dashboard.json` (or paste its contents).
3. When prompted, select the **Prometheus** data source that scrapes the
   service's `/metrics` endpoint.

The dashboard references only metrics the service actually exports; a
conformance test (`tests/conformance/test_grafana_dashboard.py`) fails the build
if a panel query ever references a metric name the service does not expose, so
the template cannot silently drift out of sync with the code.

## Offline / air-gapped import

The JSON is self-contained and has no external dependencies, so it imports on an
offline Grafana instance exactly as it does on a connected one.
