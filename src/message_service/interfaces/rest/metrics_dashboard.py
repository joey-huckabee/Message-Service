"""Server-side rendering for the embedded metrics dashboard (L3-DASH-016/017).

Turns the Prometheus exposition (obtained from the same source ``GET /metrics``
serves) into a self-contained HTML page: the metric model is parsed server-side
(:mod:`.prometheus_parser`), serialized to JSON, and embedded alongside the
hand-authored static CSS/JS assets, which render it as inline SVG in the browser.
No external origin is referenced.
"""

from __future__ import annotations

import json
import math
from importlib import resources

from message_service.interfaces.rest.prometheus_parser import (
    MetricFamily,
    parse_exposition,
)

_REST_PACKAGE = "message_service.interfaces.rest"


def _jsonable_value(value: float) -> float | None:
    """Return a JSON-safe value (non-finite floats — inf/nan — become ``None``)."""
    return value if math.isfinite(value) else None


def families_to_jsonable(families: tuple[MetricFamily, ...]) -> list[dict[str, object]]:
    """Serialize parsed families to JSON-ready dicts for the client renderer."""
    return [
        {
            "name": family.name,
            "type": family.type,
            "help": family.help,
            "samples": [
                {"name": s.name, "labels": s.labels, "value": _jsonable_value(s.value)}
                for s in family.samples
            ],
        }
        for family in families
    ]


def _read_static_asset(name: str) -> str:
    """Read a packaged static asset (``interfaces/rest/static/<name>``)."""
    return resources.files(_REST_PACKAGE).joinpath("static", name).read_text(encoding="utf-8")


def render_metrics_dashboard(exposition_text: str) -> str:
    """Render the full dashboard HTML page from a Prometheus exposition.

    Args:
        exposition_text: The exposition text (what ``GET /metrics`` returns).

    Returns:
        A complete, self-contained HTML document embedding the parsed metric
        model as JSON plus the inlined static CSS/JS. No external references.
    """
    families = parse_exposition(exposition_text)
    payload = json.dumps(families_to_jsonable(families), separators=(",", ":"))
    # Defensive: neutralize any "</" so embedded JSON can't close the <script>.
    payload_safe = payload.replace("</", "<\\/")
    css = _read_static_asset("metrics_dashboard.css")
    js = _read_static_asset("metrics_dashboard.js")
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Message-Service — Metrics</title>\n"
        f"<style>{css}</style></head>\n"
        "<body>\n"
        '<header class="dash-head"><h1>Message-Service metrics</h1>\n'
        "<p>Live snapshot of the Prometheus metrics this instance exposes at "
        "<code>/metrics</code>.</p></header>\n"
        '<main class="panels" id="panels"></main>\n'
        f'<script type="application/json" id="metrics-data">{payload_safe}</script>\n'
        f"<script>{js}</script>\n"
        "</body></html>\n"
    )


__all__ = ["families_to_jsonable", "render_metrics_dashboard"]
