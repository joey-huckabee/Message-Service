"""Server-side rendering for the admin notification console (L3-DASH-041).

Returns a self-contained HTML console for managing notification recipients. The
page's hand-authored client code fetches the roster from ``GET /admin/users`` and
drives create / update / disable / reset-password through the existing admin
account routes, echoing the CSRF cookie as a header; on a ``401`` it redirects to
the login page. No external origin is referenced.
"""

from __future__ import annotations

from importlib import resources

_REST_PACKAGE = "message_service.interfaces.rest"


def _read_static_asset(name: str) -> str:
    """Read a packaged static asset (``interfaces/rest/static/<name>``)."""
    return resources.files(_REST_PACKAGE).joinpath("static", name).read_text(encoding="utf-8")


def _escape(text: str) -> str:
    """Minimal HTML-text escape for embedded content (defense in depth)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_admin_console(admin_email: str) -> str:
    """Render the admin recipient-console HTML page.

    Args:
        admin_email: The signed-in administrator's email, shown in the top bar.
            Escaped before embedding.

    Returns:
        A complete, self-contained HTML document with the inlined static CSS/JS.
        No external references; the client fetches the roster from the same-origin
        ``GET /admin/users``.
    """
    css = _read_static_asset("admin_console.css")
    js = _read_static_asset("admin_console.js")
    who = _escape(admin_email)
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Message-Service — Notification recipients</title>\n"
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
        '<span class="tab active">Recipients</span>\n'
        '<a class="tab" href="/admin/subscriptions">Subscriptions</a>\n'
        "</div>\n"
        '<div class="head-row">\n'
        "<div><h1>Notification recipients</h1>\n"
        '<div class="sub">Accounts that can receive emailed reports. '
        "Manage the roster here.</div></div>\n"
        '<span class="spacer"></span>\n'
        '<input class="search" id="search" placeholder="Filter by email or name…">\n'
        '<button class="btn primary" id="new-btn">+ New recipient</button>\n'
        "</div>\n"
        '<div id="msg" class="msg hidden" role="alert"></div>\n'
        '<div class="panel hidden" id="panel"></div>\n'
        '<div class="card"><table>\n'
        "<thead><tr><th>Email</th><th>Name</th><th>Role</th><th>Status</th>"
        '<th>Created</th><th style="text-align:right">Actions</th></tr></thead>\n'
        '<tbody id="rows"></tbody>\n'
        "</table></div>\n"
        "</div>\n"
        f"<script>{js}</script>\n"
        "</body></html>\n"
    )


__all__ = ["render_admin_console"]
