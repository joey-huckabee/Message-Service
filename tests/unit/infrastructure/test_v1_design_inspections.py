"""Inspection tests for v1 design decisions documented in L3-REQ.md.

Each test pins an L3 invariant that is structural (not behavioral) — a
file exists, an attribute has a known shape, etc. These tests catch
silent drift between spec and code rather than running the code.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


# -----------------------------------------------------------------------------
# L3-TMPL-004: manifest_loader does not validate `..` path escaping
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-TMPL-004")
def test_manifest_loader_does_not_reject_dotdot_paths() -> None:
    """L3-TMPL-004: v1 deliberately does NOT reject paths containing `..`
    after resolution. The deployment trust model assumes operator-authored
    manifests on a trusted host. Verifying via inspection: the manifest
    loader source SHALL NOT contain `..` path-rejection logic.
    """
    loader_path = (
        _PROJECT_ROOT
        / "src"
        / "message_service"
        / "infrastructure"
        / "templating"
        / "manifest_loader.py"
    )
    text = loader_path.read_text(encoding="utf-8")
    # No `..` rejection — the loader resolves but does not validate.
    # Any future code adding the check would surface here in review.
    assert ".." not in text or "is_relative_to" not in text, (
        "manifest_loader appears to have gained path-escape rejection logic; "
        "if intentional, update L3-TMPL-004 to match"
    )


# -----------------------------------------------------------------------------
# L3-TMPL-028: docs/reviews directory exists for security-review records
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-TMPL-028")
def test_docs_reviews_directory_exists() -> None:
    """L3-TMPL-028: ``docs/reviews/`` SHALL exist as the destination for
    security-review records produced after sandbox-configuration changes.
    The directory is empty in v1 (no sandbox-configuration changes have
    occurred since initial authoring), which is the correct state.
    """
    reviews_dir = _PROJECT_ROOT / "docs" / "reviews"
    assert reviews_dir.is_dir(), (
        "docs/reviews/ SHALL exist as the destination for security reviews (L3-TMPL-028)"
    )


# -----------------------------------------------------------------------------
# L3-MAIL-023: per-recipient 550 — BCC delivery shape acknowledgment
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-MAIL-023")
def test_outbound_email_uses_bcc_for_recipients() -> None:
    """L3-MAIL-023: v1 sends as a single SMTP message with the recipient
    list on Bcc:; partial-recipient failures fail the whole send.
    Inspection: the MIME assembly puts recipients on Bcc:, and the
    mailer constructs a single message rather than iterating per recipient.
    """
    mailer_path = (
        _PROJECT_ROOT
        / "src"
        / "message_service"
        / "infrastructure"
        / "email"
        / "aiosmtplib_mailer.py"
    )
    text = mailer_path.read_text(encoding="utf-8")
    # Recipients are placed on Bcc: (case-sensitive header name in the
    # source). v1 does not iterate per recipient.
    assert '"Bcc"' in text or "'Bcc'" in text, (
        "Mailer SHALL set Bcc: header for the recipient list (L3-MAIL-023)"
    )
    # No per-recipient retry loop — the send_message call is once per email.
    # Inspect the AiosmtplibMailer.send method body.
    from message_service.infrastructure.email.aiosmtplib_mailer import (
        AiosmtplibMailer,
    )

    src = inspect.getsource(AiosmtplibMailer.send)
    assert src.count("send_message") <= 2, (
        "send() appears to iterate send_message per recipient; v1 sends one "
        "message via Bcc per L3-MAIL-023"
    )
