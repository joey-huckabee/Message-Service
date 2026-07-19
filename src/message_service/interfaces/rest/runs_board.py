"""Server-side rendering for the embedded run-status board (L3-DASH-037/038).

Turns a list of run-summary projections (the same field set the JSON runs API
exposes, `L2-DASH-013`) into a self-contained HTML page: the summaries are
serialized to JSON and embedded alongside the hand-authored static CSS/JS
assets, which render the table, per-state summary, and state filter in the
browser. Per-run stage detail is fetched lazily by the client from the existing
``GET /runs/{run_id}`` endpoint, so it is not embedded here. No external origin
is referenced.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

_REST_PACKAGE = "message_service.interfaces.rest"


def _read_static_asset(name: str) -> str:
    """Read a packaged static asset (``interfaces/rest/static/<name>``)."""
    return resources.files(_REST_PACKAGE).joinpath("static", name).read_text(encoding="utf-8")


def render_runs_board(run_summaries: list[dict[str, Any]]) -> str:
    """Render the full run-status board HTML page from run-summary projections.

    Args:
        run_summaries: Run-summary dicts carrying the `L2-DASH-013` metadata
            field set (``run_id``, ``pipeline_type``, ``state``,
            ``attachment_mode``, ``tags``, ``created_at``, ``updated_at``),
            already ordered most-recent-first. Enum-valued fields SHALL already
            be their string values (e.g. via ``model_dump(mode="json")``).

    Returns:
        A complete, self-contained HTML document embedding the summaries as JSON
        plus the inlined static CSS/JS. No external references (the client fetches
        per-run stage detail from the same-origin ``GET /runs/{run_id}``).
    """
    payload = json.dumps(run_summaries, separators=(",", ":"))
    # Defensive: neutralize any "</" so embedded JSON can't close the <script>.
    payload_safe = payload.replace("</", "<\\/")
    css = _read_static_asset("runs_board.css")
    js = _read_static_asset("runs_board.js")
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Message-Service — Runs</title>\n"
        f"<style>{css}</style></head>\n"
        "<body>\n"
        '<div class="wrap">\n'
        '<header class="board-head"><h1>Runs</h1>\n'
        '<span class="sub">report aggregation &amp; delivery status</span></header>\n'
        '<div class="summary" id="summary"></div>\n'
        '<div class="filters">\n'
        '<div class="grp" id="quick">\n'
        '<button data-q="inwork" class="on">In&nbsp;work</button>\n'
        '<button data-q="all">All</button>\n'
        '<button data-q="terminal">Delivered&nbsp;/&nbsp;terminal</button>\n'
        "</div>\n"
        '<div class="spacer"></div>\n'
        '<span class="count" id="count"></span>\n'
        "</div>\n"
        '<div class="card"><table class="tbl">\n'
        "<thead><tr>"
        '<th style="width:150px">State</th><th>Run</th><th>Pipeline</th>'
        "<th>Tags</th><th>Created</th><th>Updated</th>"
        "</tr></thead>\n"
        '<tbody id="rows"></tbody>\n'
        "</table></div>\n"
        "</div>\n"
        f'<script type="application/json" id="runs-data">{payload_safe}</script>\n'
        f"<script>{js}</script>\n"
        "</body></html>\n"
    )


__all__ = ["render_runs_board"]
