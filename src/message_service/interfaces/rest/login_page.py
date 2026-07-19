"""Server-side rendering for the browser login page (L3-DASH-040).

Returns a self-contained HTML sign-in page for the local administrator account.
The page's hand-authored client code posts the entered credentials to the
existing JSON ``POST /login`` endpoint (unchanged) and, on success, redirects to
the admin console. No external origin is referenced; the CSS/JS ship as packaged
static assets and are inlined into the page.
"""

from __future__ import annotations

from importlib import resources

_REST_PACKAGE = "message_service.interfaces.rest"


def _read_static_asset(name: str) -> str:
    """Read a packaged static asset (``interfaces/rest/static/<name>``)."""
    return resources.files(_REST_PACKAGE).joinpath("static", name).read_text(encoding="utf-8")


def render_login_page() -> str:
    """Render the full login-page HTML document.

    Returns:
        A complete, self-contained HTML document with the inlined static CSS/JS.
        No external references; the form submits to the same-origin ``POST
        /login`` and redirects to ``/admin/console`` on success.
    """
    css = _read_static_asset("login.css")
    js = _read_static_asset("login.js")
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Message-Service — Sign in</title>\n"
        f"<style>{css}</style></head>\n"
        "<body>\n"
        '<form class="card" id="login-form" autocomplete="on">\n'
        '<div class="brand"><span class="dot"></span>'
        '<span class="name">Message-Service</span></div>\n'
        "<h1>Sign in</h1>\n"
        '<div class="sub">Administrator access to the notification console.</div>\n'
        '<div id="err" class="error hidden" role="alert">'
        "Invalid credentials. Please try again.</div>\n"
        '<label for="email">Email</label>\n'
        '<input id="email" name="email" type="email" autocomplete="username" required '
        'placeholder="admin@example.com">\n'
        '<label for="pw">Password</label>\n'
        '<input id="pw" name="password" type="password" autocomplete="current-password" required '
        'placeholder="password">\n'
        '<button class="primary" type="submit" id="submit">Sign in</button>\n'
        '<div class="foot">Local administrator account · session expires after inactivity</div>\n'
        "</form>\n"
        f"<script>{js}</script>\n"
        "</body></html>\n"
    )


__all__ = ["render_login_page"]
