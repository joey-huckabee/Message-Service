"""Server-side rendering for the admin subscriptions console (L3-DASH-046).

Returns a self-contained HTML page for managing a chosen recipient's notification
subscriptions. The registered pipelines and tag vocabulary are embedded as JSON
so the client's ``PIPELINE`` / ``TAG`` target is chosen from a dropdown of valid
values; the dynamic data (recipient list, a recipient's subscriptions) is fetched
from the admin APIs. No external origin is referenced.
"""

from __future__ import annotations

import json
from importlib import resources

_REST_PACKAGE = "message_service.interfaces.rest"


def _read_static_asset(name: str) -> str:
    """Read a packaged static asset (``interfaces/rest/static/<name>``)."""
    return resources.files(_REST_PACKAGE).joinpath("static", name).read_text(encoding="utf-8")


def _escape(text: str) -> str:
    """Minimal HTML-text escape for embedded content (defense in depth)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_subscriptions_console(
    admin_email: str,
    *,
    pipelines: list[str],
    tags: list[str],
) -> str:
    """Render the admin subscriptions-console HTML page.

    Args:
        admin_email: The signed-in administrator's email (escaped before embed).
        pipelines: Registered pipeline-type names for the PIPELINE dropdown.
        tags: The tag vocabulary for the TAG dropdown.

    Returns:
        A complete, self-contained HTML document embedding the vocabulary as JSON
        plus the inlined static CSS/JS. No external references.
    """
    css = _read_static_asset("subscriptions_console.css")
    js = _read_static_asset("subscriptions_console.js")
    who = _escape(admin_email)
    payload = json.dumps({"pipelines": pipelines, "tags": tags}, separators=(",", ":"))
    payload_safe = payload.replace("</", "<\\/")
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Message-Service — Subscriptions</title>\n"
        f"<style>{css}</style></head>\n"
        "<body>\n"
        '<div class="topbar"><div class="inner">\n'
        '<span class="brand"><span class="dot"></span>Message-Service</span>\n'
        '<span class="spacer"></span>\n'
        f'<span class="who">signed in as <b>{who}</b></span>\n'
        '<button class="signout" id="signout">Sign out</button>\n'
        "</div></div>\n"
        '<div class="wrap">\n'
        '<div class="tabs">\n'
        '<a class="tab" href="/admin/console">Recipients</a>\n'
        '<span class="tab active">Subscriptions</span>\n'
        "</div>\n"
        "<h1>Notification subscriptions</h1>\n"
        '<div class="sub">Choose a recipient, then manage which finalized runs '
        "they are emailed about.</div>\n"
        '<div id="msg" class="msg hidden" role="alert"></div>\n'
        '<div class="pickrow">\n'
        '<div class="grow"><label class="lbl" for="who">Recipient</label>\n'
        '<select id="recipient"></select></div>\n'
        "</div>\n"
        '<div class="card"><h2>Add a subscription</h2>\n'
        '<div class="add">\n'
        '<div class="field"><label class="lbl" for="gran">Type</label>\n'
        '<select id="gran"><option value="GLOBAL">Global — every run</option>'
        '<option value="PIPELINE">Pipeline</option>'
        '<option value="TAG">Tag</option></select></div>\n'
        '<div class="field grow" id="target-wrap"><label class="lbl" id="target-lbl" '
        'for="target">Target</label><select id="target"></select></div>\n'
        '<button class="btn primary" id="add-btn">Add subscription</button>\n'
        "</div></div>\n"
        '<div class="card"><h2 id="subs-title">Subscriptions</h2>\n'
        '<table><thead><tr><th style="width:130px">Type</th><th>Target</th>'
        '<th class="row-actions">Actions</th></tr></thead>\n'
        '<tbody id="rows"></tbody></table></div>\n'
        "</div>\n"
        f'<script type="application/json" id="vocab-data">{payload_safe}</script>\n'
        f"<script>{js}</script>\n"
        "</body></html>\n"
    )


__all__ = ["render_subscriptions_console"]
