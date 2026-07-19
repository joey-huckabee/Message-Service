"""Unit tests for the browser login page renderer + its client asset (L3-DASH-040).

The route-level "GET /login returns HTML unauthenticated" assertion lives in
``tests/integration/rest/test_app.py``; here we cover the pure render function
and inspect the shipped login JS.
"""

from __future__ import annotations

from importlib import resources

import pytest

from message_service.interfaces.rest.login_page import render_login_page


def _login_js() -> str:
    return (
        resources.files("message_service.interfaces.rest")
        .joinpath("static", "login.js")
        .read_text(encoding="utf-8")
    )


@pytest.mark.requirement("L3-DASH-040")
def test_render_returns_self_contained_html_with_form() -> None:
    """The page is a complete HTML doc with email + password + submit, inlined."""
    html = render_login_page()
    assert html.startswith("<!doctype html>")
    assert html.rstrip().endswith("</body></html>")
    assert 'id="login-form"' in html
    assert 'name="email"' in html
    assert 'name="password"' in html
    assert 'type="submit"' in html
    # Inlined assets, no external <link>/<script src>.
    assert "<style>" in html
    assert "<link" not in html
    assert "src=" not in html


@pytest.mark.requirement("L3-DASH-040")
def test_login_js_posts_to_login_and_redirects_to_console() -> None:
    """Inspection: the shipped JS posts to /login and redirects to the console."""
    js = _login_js()
    assert '"/login"' in js
    assert 'method: "POST"' in js
    assert "/admin/console" in js
    # Submits JSON (not form-encoded) to the existing endpoint.
    assert "application/json" in js
    assert "JSON.stringify" in js
