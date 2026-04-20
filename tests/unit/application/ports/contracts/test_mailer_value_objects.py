"""Unit tests for :class:`OutboundEmail` and :class:`EmailAttachment`."""

from __future__ import annotations

import pytest

from message_service.application.ports.mailer import EmailAttachment, OutboundEmail

# -----------------------------------------------------------------------------
# EmailAttachment
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-MAIL-005")
def test_email_attachment_constructs_with_valid_values() -> None:
    att = EmailAttachment(
        filename="report.html",
        content_type="text/html; charset=utf-8",
        content=b"<html><body>hi</body></html>",
    )
    assert att.filename == "report.html"


@pytest.mark.requirement("L2-MAIL-005")
def test_email_attachment_is_frozen() -> None:
    att = EmailAttachment(filename="x.html", content_type="text/html", content=b"x")
    with pytest.raises((AttributeError, TypeError)):
        att.filename = "y.html"  # type: ignore[misc]


@pytest.mark.requirement("L2-MAIL-005")
@pytest.mark.parametrize(
    ("filename", "content_type"),
    [("", "text/html"), ("x.html", ""), ("", "")],
)
def test_email_attachment_rejects_empty_fields(filename: str, content_type: str) -> None:
    with pytest.raises(ValueError):
        EmailAttachment(filename=filename, content_type=content_type, content=b"x")


# -----------------------------------------------------------------------------
# OutboundEmail
# -----------------------------------------------------------------------------


def _email(**overrides: object) -> OutboundEmail:
    fields: dict[str, object] = {
        "recipients": frozenset({"alice@example.com"}),
        "subject": "Report ready",
        "body_html": "<p>Your run completed.</p>",
        "from_address": "svc@example.com",
    }
    fields.update(overrides)
    return OutboundEmail(**fields)  # type: ignore[arg-type]


@pytest.mark.requirement("L1-MAIL-001")
def test_outbound_email_constructs_with_valid_values() -> None:
    email = _email()
    assert email.subject == "Report ready"
    assert email.attachments == ()


@pytest.mark.requirement("L1-MAIL-001")
def test_outbound_email_rejects_empty_recipients() -> None:
    with pytest.raises(ValueError, match="recipients"):
        _email(recipients=frozenset())


@pytest.mark.requirement("L2-MAIL-003")
def test_outbound_email_rejects_empty_from_address() -> None:
    with pytest.raises(ValueError, match="from_address"):
        _email(from_address="")


# Header-injection defense
@pytest.mark.requirement("L2-MAIL-003")
@pytest.mark.parametrize(
    "bad_subject",
    [
        "Subject\nBcc: attacker@example.com",
        "Subject\rX-Evil: yes",
        "Subject\r\nX-Evil: yes",
    ],
)
def test_outbound_email_rejects_newlines_in_subject(bad_subject: str) -> None:
    with pytest.raises(ValueError, match="subject"):
        _email(subject=bad_subject)


@pytest.mark.requirement("L2-MAIL-003")
@pytest.mark.parametrize(
    "bad_from",
    [
        "svc@example.com\nBcc: attacker@example.com",
        "svc@example.com\rX-Evil: yes",
    ],
)
def test_outbound_email_rejects_newlines_in_from_address(bad_from: str) -> None:
    with pytest.raises(ValueError, match="from_address"):
        _email(from_address=bad_from)


@pytest.mark.requirement("L1-MAIL-001")
def test_outbound_email_multi_recipient() -> None:
    email = _email(recipients=frozenset({"a@example.com", "b@example.com"}))
    assert len(email.recipients) == 2


@pytest.mark.requirement("L2-MAIL-005")
def test_outbound_email_with_attachments() -> None:
    att1 = EmailAttachment(filename="r1.html", content_type="text/html", content=b"<p>1</p>")
    att2 = EmailAttachment(filename="r2.html", content_type="text/html", content=b"<p>2</p>")
    email = _email(attachments=(att1, att2))
    assert len(email.attachments) == 2
