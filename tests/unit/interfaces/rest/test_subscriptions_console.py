"""Unit tests for the admin subscriptions-console renderer + client asset (L3-DASH-046).

Route-level auth-gate assertions live in
``tests/integration/rest/test_admin_users.py``; here we cover the pure render
function (vocabulary embedding + email escaping) and inspect the shipped JS.
"""

from __future__ import annotations

import json
import re
from importlib import resources

import pytest

from message_service.interfaces.rest.subscriptions_console import render_subscriptions_console


def _console_js() -> str:
    return (
        resources.files("message_service.interfaces.rest")
        .joinpath("static", "subscriptions_console.js")
        .read_text(encoding="utf-8")
    )


@pytest.mark.requirement("L3-DASH-046")
def test_render_embeds_vocabulary_and_is_self_contained() -> None:
    """The page embeds the pipelines/tags vocabulary and inlines its assets."""
    html = render_subscriptions_console(
        "admin@example.com", pipelines=["etl-nightly"], tags=["finance", "ops"]
    )
    assert html.startswith("<!doctype html>")
    assert "admin@example.com" in html
    assert "<style>" in html
    assert "<link" not in html
    assert "src=" not in html
    match = re.search(
        r'<script type="application/json" id="vocab-data">(.*?)</script>', html, re.DOTALL
    )
    assert match is not None
    vocab = json.loads(match.group(1))
    assert vocab == {"pipelines": ["etl-nightly"], "tags": ["finance", "ops"]}
    # Cross-links back to the recipients console.
    assert 'href="/admin/console"' in html


@pytest.mark.requirement("L3-DASH-046")
def test_render_escapes_admin_email() -> None:
    html = render_subscriptions_console("a<script>@x", pipelines=[], tags=[])
    assert "a<script>@x" not in html
    assert "a&lt;script&gt;@x" in html


@pytest.mark.requirement("L3-DASH-046")
def test_render_neutralizes_script_close_in_vocab() -> None:
    """A hostile vocab value cannot close the embedding <script>."""
    html = render_subscriptions_console("a@x", pipelines=["</script><b>"], tags=[])
    data_block = html.split('id="vocab-data">', 1)[1].split("</script>", 1)[0]
    assert "</script>" not in data_block
    assert "<\\/script>" in data_block


@pytest.mark.requirement("L3-DASH-046")
def test_console_js_wires_the_subscription_apis_with_csrf() -> None:
    """Inspection: the JS reads recipients, sends CSRF, drives the sub APIs."""
    js = _console_js()
    assert '"/admin/users"' in js  # recipient list
    assert "/subscriptions" in js  # per-recipient subs paths
    assert "X-CSRF-Token" in js
    assert "msp_csrf" in js
    assert '"POST"' in js
    assert '"DELETE"' in js
    assert "/login" in js
    assert "vocab-data" in js
