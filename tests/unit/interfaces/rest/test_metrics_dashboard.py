"""Tests for the embedded metrics dashboard rendering + asset self-containment.

L3-DASH-016 (server-side parse + embed) and L3-DASH-017 (no external reference in
the shipped static assets).
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any

import pytest

from message_service.interfaces.rest.metrics_dashboard import (
    families_to_jsonable,
    render_metrics_dashboard,
)
from message_service.interfaces.rest.prometheus_parser import parse_exposition

_SAMPLE = (
    "# HELP svc_transitions_total Transitions.\n"
    "# TYPE svc_transitions_total counter\n"
    'svc_transitions_total{target_state="SENT"} 3.0\n'
    "# TYPE svc_size_bytes histogram\n"
    'svc_size_bytes_bucket{le="+Inf"} 5.0\n'
    "svc_size_bytes_sum 2048.0\n"
    "svc_size_bytes_count 5.0\n"
)


def _embedded_model(html: str) -> Any:
    match = re.search(
        r'<script type="application/json" id="metrics-data">(.*?)</script>', html, re.S
    )
    assert match is not None
    return json.loads(match.group(1).replace("<\\/", "</"))


# -----------------------------------------------------------------------------
# L3-DASH-016 — server-side parse + embed
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-DASH-016")
def test_render_embeds_the_parsed_model_and_inlines_assets() -> None:
    """The page embeds the parsed metric model as JSON and inlines the CSS/JS."""
    html = render_metrics_dashboard(_SAMPLE)
    model = _embedded_model(html)
    names = {f["name"] for f in model}
    assert {"svc_transitions_total", "svc_size_bytes"} <= names
    # Assets are inlined (self-contained page), not linked.
    assert "<style>" in html and "--panel" in html  # CSS inlined
    assert "createElementNS" in html  # JS inlined
    assert 'id="panels"' in html  # render target present


@pytest.mark.requirement("L3-DASH-016")
def test_families_to_jsonable_nulls_non_finite_values() -> None:
    """Non-finite values (inf/nan) serialize as null (valid JSON)."""
    families = parse_exposition("# TYPE g gauge\ng +Inf\n")
    payload = families_to_jsonable(families)
    assert payload[0]["samples"][0]["value"] is None  # type: ignore[index]
    json.dumps(payload)  # must not raise


# -----------------------------------------------------------------------------
# L3-DASH-017 — no external reference in the shipped static assets
# -----------------------------------------------------------------------------

_ASSET_NAMES = ("metrics_dashboard.css", "metrics_dashboard.js")
# Known third-party CDN hostnames and charting-library API signatures — specific
# enough not to false-positive on hand-authored prose (the two URL checks below
# already catch any external-origin fetch; these guard against a vendored lib).
_FORBIDDEN_MARKERS = (
    "cdnjs",
    "unpkg.com",
    "jsdelivr",
    "ajax.googleapis",
    "chart.min.js",
    "d3.min.js",
    "plotly.min.js",
    "new chart(",
    "d3.select",
)
# http(s) URLs that are NOT the standard W3C XML/SVG namespace identifiers.
_EXTERNAL_URL = re.compile(r"https?://(?!www\.w3\.org/)", re.IGNORECASE)
# A protocol-relative resource reference like src="//host" or url(//host).
_PROTOCOL_RELATIVE = re.compile(r'["\'(]//[a-z0-9]', re.IGNORECASE)


def _asset_text(name: str) -> str:
    return (
        resources.files("message_service.interfaces.rest")
        .joinpath("static", name)
        .read_text(encoding="utf-8")
    )


@pytest.mark.requirement("L3-DASH-017")
@pytest.mark.parametrize("name", _ASSET_NAMES)
def test_static_asset_has_no_external_reference(name: str) -> None:
    """Each shipped dashboard asset references no external origin."""
    text = _asset_text(name)
    assert not _EXTERNAL_URL.search(text), f"{name} references an external URL"
    assert not _PROTOCOL_RELATIVE.search(text), f"{name} has a protocol-relative reference"
    lowered = text.lower()
    for marker in _FORBIDDEN_MARKERS:
        assert marker not in lowered, (
            f"{name} references a forbidden CDN/library marker: {marker!r}"
        )


@pytest.mark.requirement("L3-DASH-017")
def test_only_w3c_namespace_urls_appear() -> None:
    """The only absolute URLs in the JS are the standard W3C namespace identifiers."""
    js = _asset_text("metrics_dashboard.js")
    urls = re.findall(r"https?://[^\s\"')]+", js)
    assert urls, "expected the SVG namespace URL to be present"
    assert all(u.startswith("http://www.w3.org/") for u in urls)
