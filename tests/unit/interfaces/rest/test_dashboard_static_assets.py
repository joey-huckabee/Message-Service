"""Positive-shape tests for the shipped dashboard static assets.

The dashboard frontend has shipped (v0.12 onward): hand-authored CSS/JS under
``src/message_service/interfaces/rest/static/`` are read via ``importlib.resources``
and inlined into self-contained HTML pages (no ``StaticFiles`` mount, no ``/static``
URL). These tests pin the reworded L3-DASH-005 / L3-DASH-006 / L3-DASH-020 claims
against the real assets. (Air-gap / no-external-reference is separately enforced by
the ``L3-DASH-017`` / ``L3-DASH-039`` conformance scan.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REST_DIR = Path(__file__).resolve().parents[4] / "src" / "message_service" / "interfaces" / "rest"
_STATIC_DIR = _REST_DIR / "static"
_HTML_TEMPLATES_DIR = _REST_DIR / "html" / "templates"

_EXPECTED_ASSETS = {
    "login.css",
    "login.js",
    "admin_console.css",
    "admin_console.js",
    "metrics_dashboard.css",
    "metrics_dashboard.js",
    "runs_board.css",
    "runs_board.js",
    "subscriptions_console.css",
    "subscriptions_console.js",
}


@pytest.mark.requirement("L3-DASH-005")
def test_dashboard_ships_packaged_static_css_js() -> None:
    """L3-DASH-005: the hand-authored dashboard CSS/JS ship under static/."""
    assert _STATIC_DIR.is_dir()
    present = {p.name for p in _STATIC_DIR.iterdir() if p.is_file()}
    assert present >= _EXPECTED_ASSETS, f"missing dashboard assets: {_EXPECTED_ASSETS - present}"


@pytest.mark.requirement("L3-DASH-005")
def test_pages_inline_their_static_assets_no_static_mount() -> None:
    """L3-DASH-005: pages inline their CSS/JS (self-contained) — no StaticFiles mount.

    The render helpers read the packaged asset and embed it; there is no
    ``/static`` URL to fetch. Verified by rendering a page and finding the CSS
    inlined in a ``<style>`` block with no ``/static`` reference.
    """
    from message_service.interfaces.rest.subscriptions_console import (
        render_subscriptions_console,
    )

    html = render_subscriptions_console("admin@example.com", pipelines=["etl"], tags=["production"])
    css = (_STATIC_DIR / "subscriptions_console.css").read_text(encoding="utf-8")
    # A distinctive slice of the CSS appears inlined in the page.
    probe = css.strip().splitlines()[0]
    assert probe and probe in html
    assert "<style>" in html
    assert "/static/" not in html  # no mount-served reference


@pytest.mark.requirement("L3-DASH-006")
def test_no_jinja2_dashboard_html_templates() -> None:
    """L3-DASH-006: dashboard HTML is Python-rendered — no Jinja2 dashboard templates.

    The only shipped Jinja2 templates are the operator-controlled email templates;
    no dashboard-rendering ``.html``/``.j2`` templates exist under the rest package.
    """
    if not _HTML_TEMPLATES_DIR.exists():
        return  # no dashboard-template dir at all — the strongest form of the claim
    dashboard_templates = list(_HTML_TEMPLATES_DIR.rglob("*.html")) + list(
        _HTML_TEMPLATES_DIR.rglob("*.j2")
    )
    assert dashboard_templates == [], (
        f"L3-DASH-006: dashboard HTML is Python-rendered; unexpected Jinja2 "
        f"dashboard templates: {dashboard_templates}"
    )


@pytest.mark.requirement("L3-DASH-020")
def test_shipped_css_uses_system_fonts_no_font_files_or_font_face() -> None:
    """L3-DASH-020: system-font stacks only — no font files, no @font-face."""
    css_files = list(_STATIC_DIR.glob("*.css"))
    assert css_files, "expected shipped dashboard CSS"

    # No font binaries ship anywhere under static/.
    font_exts = {".woff2", ".woff", ".ttf", ".otf", ".eot"}
    fonts = [p for p in _STATIC_DIR.rglob("*") if p.suffix.lower() in font_exts]
    assert fonts == [], f"L3-DASH-020: no font files SHALL ship; found {fonts}"

    # At least one stylesheet declares a font-family (system stack), and none
    # declare an @font-face (which would pull a bundled/remote typeface).
    saw_font_family = False
    for css in css_files:
        text = css.read_text(encoding="utf-8")
        assert "@font-face" not in text, f"L3-DASH-020: unexpected @font-face in {css.name}"
        if "font-family" in text:
            saw_font_family = True
    assert saw_font_family, "L3-DASH-020: expected a system-font `font-family` stack"
