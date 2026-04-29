"""Inspection tests pinning v1's no-HTML-frontend posture.

The dashboard surfaces in v1 are REST/JSON only (per L1-DASH-002 /
L1-DASH-003 / L1-DASH-005); no Jinja2 HTML templates, CSS, JS, or
fonts ship with the service. The L3 statements that describe the
deferred frontend (L3-DASH-005 / L3-DASH-006 / L3-DASH-020 / L3-DASH-010)
are vacuously satisfied today — these tests pin that fact so that
adding any of those assets without also wiring the deferred-feature
contracts (R-DASH-004 + the broader HTML-frontend deferral) would
surface in code review.

When the frontend ships, these tests SHALL be replaced by the
positive-shape contracts the original L3s describe (StaticFiles
mount, Jinja2 grep CI check, font-policy compliance, populated
`<select>` rendering).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_HTML_PKG = _PROJECT_ROOT / "src" / "message_service" / "interfaces" / "rest" / "html"


@pytest.mark.requirement("L3-DASH-005")
def test_no_static_assets_directory_in_v1() -> None:
    """L3-DASH-005: v1 ships no CSS/JS/font assets; the `static/`
    subdirectory either does not exist or is empty.
    """
    static_dir = _HTML_PKG / "static"
    if not static_dir.exists():
        return  # Vacuously satisfied — no static assets.
    files = [p for p in static_dir.rglob("*") if p.is_file()]
    assert files == [], (
        f"L3-DASH-005: v1 ships no static assets but found {len(files)} files "
        f"under {static_dir}; if this is a real frontend addition, the L3-DASH-005 "
        "contract needs to be reworded back to a positive-shape requirement."
    )


@pytest.mark.requirement("L3-DASH-006")
def test_no_html_templates_with_external_http_refs_in_v1() -> None:
    """L3-DASH-006: the air-gap external-HTTP grep check is vacuously
    passing in v1 (no HTML templates exist to scan). Pins that we don't
    accidentally ship a template referencing an external CDN.
    """
    templates_dir = _HTML_PKG / "templates"
    if not templates_dir.exists():
        return
    html_files = list(templates_dir.rglob("*.html")) + list(templates_dir.rglob("*.j2"))
    for html in html_files:
        text = html.read_text(encoding="utf-8")
        for marker in ("https://", "http://"):
            # Trust that operator-controlled email templates may reference
            # external links in the rendered output; this scan only fires
            # if NEW dashboard HTML templates start appearing here.
            if marker in text and "email" not in str(html):
                pytest.fail(
                    f"L3-DASH-006: dashboard HTML template {html} references "
                    f"external host via {marker!r}; air-gapped ISOLAN deployments "
                    "cannot reach external CDNs."
                )


@pytest.mark.requirement("L3-DASH-020")
def test_no_fonts_shipped_in_v1() -> None:
    """L3-DASH-020: v1 ships no fonts; the constraint is vacuously
    satisfied. WOFF2 / WOFF / TTF / OTF files appearing under static/
    would surface here.
    """
    static_dir = _HTML_PKG / "static"
    if not static_dir.exists():
        return
    font_extensions = (".woff2", ".woff", ".ttf", ".otf", ".eot")
    fonts = [p for p in static_dir.rglob("*") if p.suffix in font_extensions]
    assert fonts == [], (
        f"L3-DASH-020: v1 ships no fonts but found {fonts}; if a frontend "
        "is being added, ensure WOFF2 is the chosen format and fonts are "
        "in the packaged static directory (not external CDN)."
    )


@pytest.mark.requirement("L3-DASH-010")
def test_subscription_creation_is_rest_only_no_html_form_in_v1() -> None:
    """L3-DASH-010: v1's subscription-creation surface is REST-only via
    POST /subscriptions; no HTML form / `<select>` exists. Tag validation
    happens at the application layer (per L3-SUB-014). When the HTML
    frontend lands, this test SHALL be replaced by a positive-shape
    test of the rendered `<select>` populated from TagVocabulary.
    """
    templates_dir = _HTML_PKG / "templates"
    if not templates_dir.exists():
        return
    # Look for a subscription form template — none should exist.
    form_templates = list(templates_dir.rglob("subscription*.html")) + list(
        templates_dir.rglob("subscriptions*.html")
    )
    assert form_templates == [], (
        f"L3-DASH-010: v1 has no subscription HTML form but found {form_templates}; "
        "if the frontend is shipping, replace this test with the positive-shape "
        "<select> rendering test the original L3-DASH-010 describes."
    )
