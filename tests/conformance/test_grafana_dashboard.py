"""Conformance: the Grafana dashboard references only exposed metrics (L1-OBS-002).

Drift guard — every ``message_service_*`` metric name in the dashboard's panel
queries SHALL be a series the service actually exposes, derived from the metric
definitions in ``metrics.py`` (the source of truth). If a metric is renamed or
removed and the dashboard is not updated, this test fails the build rather than
letting the shipped template silently reference a metric that no longer exists.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARD = _ROOT / "deploy" / "grafana" / "message-service-dashboard.json"
_METRICS_SRC = _ROOT / "src" / "message_service" / "infrastructure" / "observability" / "metrics.py"

_DEFINITION_RE = re.compile(
    r'(?P<kind>Counter|Gauge|Histogram)\(\s*"(?P<name>message_service_[a-zA-Z0-9_]+)"'
)
_METRIC_TOKEN_RE = re.compile(r"message_service_[a-zA-Z0-9_]+")


def _exposed_series_names() -> set[str]:
    """The set of valid series names, derived from metrics.py definitions."""
    valid: set[str] = set()
    for match in _DEFINITION_RE.finditer(_METRICS_SRC.read_text(encoding="utf-8")):
        name, kind = match.group("name"), match.group("kind")
        if kind == "Histogram":
            base = name
            valid.update({base, base + "_bucket", base + "_sum", base + "_count"})
        else:  # Counter (…_total) / Gauge — the declared name is the series name.
            valid.add(name)
    return valid


def _dashboard_metric_tokens() -> set[str]:
    """Every message_service_* token referenced in the dashboard's panel exprs."""
    dashboard = json.loads(_DASHBOARD.read_text(encoding="utf-8"))
    tokens: set[str] = set()
    for panel in dashboard.get("panels", []):
        for target in panel.get("targets", []):
            tokens.update(_METRIC_TOKEN_RE.findall(target.get("expr", "")))
    return tokens


@pytest.mark.requirement("L1-OBS-002")
def test_dashboard_is_valid_json_with_panels() -> None:
    """The dashboard file is valid JSON and defines panels with queries."""
    dashboard = json.loads(_DASHBOARD.read_text(encoding="utf-8"))
    assert dashboard["panels"], "dashboard defines no panels"
    assert any(t.get("expr") for p in dashboard["panels"] for t in p.get("targets", []))


@pytest.mark.requirement("L1-OBS-002")
def test_dashboard_references_only_exposed_metrics() -> None:
    """Every metric the dashboard queries is one the service actually exposes."""
    exposed = _exposed_series_names()
    assert exposed, "no metric definitions found in metrics.py"
    referenced = _dashboard_metric_tokens()
    assert referenced, "dashboard references no message_service_* metrics"
    unknown = sorted(referenced - exposed)
    assert not unknown, (
        "Grafana dashboard references metrics the service does not expose "
        f"(renamed/removed?): {unknown}. Update deploy/grafana/"
        "message-service-dashboard.json or the metric definitions."
    )
