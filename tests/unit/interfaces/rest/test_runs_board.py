"""Unit tests for the run-status board renderer + its static assets.

Covers L3-DASH-037 (server-side render: self-contained HTML embedding the run
projection as JSON) and L3-DASH-039 (no-external-reference conformance across
every shipped dashboard static asset).
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any

import pytest

from message_service.interfaces.rest.runs_board import render_runs_board


def _sample_summaries() -> list[dict[str, Any]]:
    return [
        {
            "run_id": "a1f3c8d2-7b40-4e11-9a2c-0f5e1d6b8e04",
            "pipeline_type": "nightly-etl",
            "state": "AGGREGATING",
            "attachment_mode": "SINGLE_AGGREGATED",
            "tags": ["finance", "priority"],
            "created_at": "2026-07-19T10:22:04Z",
            "updated_at": "2026-07-19T10:24:37Z",
        },
        {
            "run_id": "4f5e6d7c-8b9a-40c1-92d3-e4f5061728a9",
            "pipeline_type": "sales-rollup",
            "state": "SENT",
            "attachment_mode": "PER_STAGE",
            "tags": [],
            "created_at": "2026-07-18T22:00:05Z",
            "updated_at": "2026-07-18T22:06:48Z",
        },
    ]


# -----------------------------------------------------------------------------
# L3-DASH-037 — server-side render shape
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-DASH-037")
def test_render_returns_self_contained_html_document() -> None:
    """The renderer returns a complete HTML doc embedding the inlined assets."""
    html = render_runs_board(_sample_summaries())
    assert html.startswith("<!doctype html>")
    assert html.rstrip().endswith("</body></html>")
    # Inlined static assets (no external <link>/<script src>).
    assert "<style>" in html
    assert 'id="runs-data"' in html
    assert "<link" not in html
    assert "src=" not in html


@pytest.mark.requirement("L3-DASH-037")
def test_render_embeds_the_run_projection_as_parseable_json() -> None:
    """The embedded JSON round-trips to the supplied summaries."""
    summaries = _sample_summaries()
    html = render_runs_board(summaries)
    match = re.search(
        r'<script type="application/json" id="runs-data">(.*?)</script>', html, re.DOTALL
    )
    assert match is not None
    embedded = json.loads(match.group(1))
    assert embedded == summaries
    assert embedded[0]["state"] == "AGGREGATING"  # in-flight run present


@pytest.mark.requirement("L3-DASH-037")
def test_render_neutralizes_script_close_in_payload() -> None:
    """A ``</`` inside a field cannot close the embedding <script>."""
    hostile = [
        {
            "run_id": "00000000-0000-4000-8000-000000000001",
            "pipeline_type": "evil</script><script>alert(1)</script>",
            "state": "SENT",
            "attachment_mode": "SINGLE_AGGREGATED",
            "tags": [],
            "created_at": "2026-07-19T10:00:00Z",
            "updated_at": "2026-07-19T10:00:00Z",
        }
    ]
    html = render_runs_board(hostile)
    # The raw closing sequence must not appear in the embedded payload.
    data_block = html.split('id="runs-data">', 1)[1].split("</script>", 1)[0]
    assert "</script>" not in data_block
    assert "<\\/script>" in data_block


@pytest.mark.requirement("L3-DASH-037")
def test_render_handles_empty_run_list() -> None:
    """An empty board still renders a valid document with an empty array."""
    html = render_runs_board([])
    assert html.startswith("<!doctype html>")
    assert 'id="runs-data">[]</script>' in html


# -----------------------------------------------------------------------------
# L3-DASH-039 — no external reference in ANY shipped dashboard static asset
# -----------------------------------------------------------------------------

# Every shipped static asset (metrics + board + login + any future page).
_ALL_STATIC_ASSETS = (
    "metrics_dashboard.css",
    "metrics_dashboard.js",
    "runs_board.css",
    "runs_board.js",
    "login.css",
    "login.js",
)
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
_EXTERNAL_URL = re.compile(r"https?://(?!www\.w3\.org/)", re.IGNORECASE)
_PROTOCOL_RELATIVE = re.compile(r"[\"'(]//[a-z0-9]", re.IGNORECASE)


def _asset_text(name: str) -> str:
    return (
        resources.files("message_service.interfaces.rest")
        .joinpath("static", name)
        .read_text(encoding="utf-8")
    )


def test_shipped_static_dir_matches_expected_asset_list() -> None:
    """Guard: if a new static asset is added, it must be added to the scan."""
    shipped = {
        p.name
        for p in resources.files("message_service.interfaces.rest").joinpath("static").iterdir()
        if p.name.endswith((".css", ".js"))
    }
    assert shipped == set(_ALL_STATIC_ASSETS), (
        "static/ asset set changed; add new assets to the no-external-ref scan"
    )


@pytest.mark.requirement("L3-DASH-039")
@pytest.mark.parametrize("name", _ALL_STATIC_ASSETS)
def test_static_asset_has_no_external_reference(name: str) -> None:
    """Every shipped dashboard asset references no external origin (L3-DASH-039)."""
    text = _asset_text(name)
    assert not _EXTERNAL_URL.search(text), f"{name} references an external URL"
    assert not _PROTOCOL_RELATIVE.search(text), f"{name} has a protocol-relative reference"
    lowered = text.lower()
    for marker in _FORBIDDEN_MARKERS:
        assert marker not in lowered, (
            f"{name} references a forbidden CDN/library marker: {marker!r}"
        )


# -----------------------------------------------------------------------------
# L3-DASH-038 — rendering structure (Inspection; visual correctness is
# separately verified by Demonstration under L1-DASH-006)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-DASH-038")
def test_board_js_implements_required_rendering_structure() -> None:
    """The shipped board JS reads the embedded data, distinguishes in-flight from
    terminal states, and fetches per-run stage detail lazily from the detail API.
    """
    js = _asset_text("runs_board.js")
    # Reads the embedded projection rather than fetching a list from elsewhere.
    assert "runs-data" in js
    # In-flight vs terminal distinction with the full state vocabulary present.
    assert "IN_WORK" in js
    assert "TERMINAL" in js
    for state in ("INITIATED", "AGGREGATING", "READY", "SENDING", "SENT", "FAILED", "ORPHANED"):
        assert state in js, f"board JS is missing run state {state!r}"
    # Lazy stage drill-in fetches the same-origin detail endpoint.
    assert "fetch(" in js
    assert '"/runs/"' in js


@pytest.mark.requirement("L3-DASH-038")
def test_board_html_shell_provides_filter_and_summary_containers() -> None:
    """The rendered page provides the state filter controls and summary/table
    mount points the client renders into.
    """
    html = render_runs_board(_sample_summaries())
    assert 'id="summary"' in html
    assert 'id="rows"' in html
    assert 'id="count"' in html
    # In-work / All / Terminal filter controls.
    assert 'data-q="inwork"' in html
    assert 'data-q="all"' in html
    assert 'data-q="terminal"' in html
