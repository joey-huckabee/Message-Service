"""Unit tests for the admin console renderer + its client asset (L3-DASH-041).

The route-level auth-gate assertions live in
``tests/integration/rest/test_admin_users.py``; here we cover the pure render
function (including email escaping) and inspect the shipped console JS.
"""

from __future__ import annotations

from importlib import resources

import pytest

from message_service.interfaces.rest.admin_console import render_admin_console


def _console_js() -> str:
    return (
        resources.files("message_service.interfaces.rest")
        .joinpath("static", "admin_console.js")
        .read_text(encoding="utf-8")
    )


@pytest.mark.requirement("L3-DASH-041")
def test_render_returns_self_contained_console() -> None:
    """A complete HTML doc with the roster table mount + inlined assets."""
    html = render_admin_console("admin@example.com")
    assert html.startswith("<!doctype html>")
    assert html.rstrip().endswith("</body></html>")
    assert 'id="rows"' in html  # roster table body
    assert 'id="new-btn"' in html  # create action
    assert "admin@example.com" in html  # signed-in email in the top bar
    assert "<style>" in html
    assert "<link" not in html
    assert "src=" not in html


@pytest.mark.requirement("L3-DASH-041")
def test_render_escapes_the_admin_email() -> None:
    """The embedded admin email is HTML-escaped (defense in depth)."""
    html = render_admin_console("a<script>@x")
    assert "a<script>@x" not in html
    assert "a&lt;script&gt;@x" in html


@pytest.mark.requirement("L3-DASH-041")
def test_console_js_wires_the_admin_apis_with_csrf() -> None:
    """Inspection: the JS reads the roster, sends CSRF, drives the write routes."""
    js = _console_js()
    # Reads the roster.
    assert '"/admin/users"' in js
    # Sends the CSRF header on state-changing calls.
    assert "X-CSRF-Token" in js
    assert "msp_csrf" in js
    # References the three write routes (create / update / reset-password).
    assert '"POST"' in js
    assert '"PATCH"' in js
    assert "/password" in js
    # Redirects to the login page on a 401.
    assert "/login" in js
    assert "401" in js
