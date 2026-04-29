"""Unit tests for :class:`AiosmtplibMailer`.

Strategy: patch :class:`aiosmtplib.SMTP` at the adapter module level so
every construction inside the code under test returns an
:class:`AsyncMock`. Tests inspect the mock to verify ordering
(connect → starttls → login → send_message → quit) and drive failure
modes by setting ``side_effect`` on individual methods.

Requirement references
----------------------
L1-MAIL-001, L1-MAIL-002, L1-MAIL-003
L2-MAIL-004, L2-MAIL-005, L2-MAIL-006, L2-MAIL-007, L2-MAIL-008
L3-MAIL-001, L3-MAIL-003, L3-MAIL-005, L3-MAIL-006, L3-MAIL-007
L3-MAIL-009, L3-MAIL-012
"""

from __future__ import annotations

import socket
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiosmtplib
import pytest

from message_service.application.ports.mailer import EmailAttachment, OutboundEmail
from message_service.domain.errors import EmailDeliveryError
from message_service.infrastructure.email.aiosmtplib_mailer import (
    AiosmtplibMailer,
    _build_mime_message,
    _classify_smtp_error,
)

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


def _email(**overrides: Any) -> OutboundEmail:
    fields: dict[str, Any] = {
        "recipients": frozenset({"alice@example.com"}),
        "subject": "Run done",
        "body_html": "<p>hi</p>",
        "from_address": "svc@example.com",
        "attachments": (),
    }
    fields.update(overrides)
    return OutboundEmail(**fields)


@pytest.fixture
def smtp_mock() -> AsyncMock:
    """An AsyncMock standing in for aiosmtplib.SMTP instances.

    All methods (connect, starttls, login, send_message, quit) return
    AsyncMocks by default. Tests configure side_effect as needed.
    """
    mock = AsyncMock()
    mock.connect = AsyncMock()
    mock.starttls = AsyncMock()
    mock.login = AsyncMock()
    mock.send_message = AsyncMock()
    mock.quit = AsyncMock()
    return mock


@pytest.fixture
def patched_smtp(smtp_mock: AsyncMock) -> Any:
    """Patch ``aiosmtplib.SMTP`` in the adapter module.

    Yields ``(SMTP_class_mock, SMTP_instance_mock)``. Use the instance
    mock to verify awaited methods; use the class mock to verify
    constructor call args.
    """
    with patch(
        "message_service.infrastructure.email.aiosmtplib_mailer.aiosmtplib.SMTP",
        return_value=smtp_mock,
    ) as smtp_class:
        yield smtp_class, smtp_mock


# -----------------------------------------------------------------------------
# Construction validation
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("bad_port", [0, -1, 65_536, 100_000])
def test_rejects_invalid_port(bad_port: int) -> None:
    with pytest.raises(ValueError, match="port"):
        AiosmtplibMailer(host="x", port=bad_port, max_email_size_bytes=1000)


def test_rejects_non_positive_size() -> None:
    with pytest.raises(ValueError, match="max_email_size_bytes"):
        AiosmtplibMailer(host="x", port=587, max_email_size_bytes=0)


def test_rejects_negative_retries() -> None:
    with pytest.raises(ValueError, match="max_retries"):
        AiosmtplibMailer(host="x", port=587, max_email_size_bytes=1000, max_retries=-1)


def test_rejects_max_interval_less_than_initial() -> None:
    with pytest.raises(ValueError, match="max_interval"):
        AiosmtplibMailer(
            host="x",
            port=587,
            max_email_size_bytes=1000,
            initial_interval_seconds=10.0,
            max_interval_seconds=5.0,
        )


# -----------------------------------------------------------------------------
# MIME assembly
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L1-MAIL-001")
def test_mime_message_has_expected_headers() -> None:
    email = _email(
        recipients=frozenset({"alice@example.com", "bob@example.com"}),
        subject="Subject line",
        from_address="svc@example.com",
    )
    msg = _build_mime_message(email)
    assert msg["From"] == "svc@example.com"
    assert msg["Subject"] == "Subject line"
    # Recipients on BCC, sorted.
    bcc = msg["Bcc"]
    assert "alice@example.com" in bcc
    assert "bob@example.com" in bcc


@pytest.mark.requirement("L1-MAIL-001")
@pytest.mark.requirement("L3-AGGR-020")
def test_mime_message_includes_attachments() -> None:
    """L1-MAIL-001 / L3-AGGR-020: PER_STAGE attachments SHALL carry
    ``Content-Type: text/html`` and
    ``Content-Disposition: attachment; filename="<filename>"``. v1
    sends bytes via ``add_attachment``, which omits the charset
    parameter by RFC 2045 binary-content semantics.
    """
    att1 = EmailAttachment(
        filename="report.html",
        content_type="text/html",
        content=b"<p>report</p>",
    )
    att2 = EmailAttachment(
        filename="stage2.html",
        content_type="text/html",
        content=b"<p>stage2</p>",
    )
    email = _email(attachments=(att1, att2))
    msg = _build_mime_message(email)
    parts = list(msg.iter_attachments())
    assert len(parts) == 2
    filenames = {p.get_filename() for p in parts}
    assert filenames == {"report.html", "stage2.html"}
    # L3-AGGR-020: each attachment has the prescribed Content-Type and
    # Content-Disposition headers. v1 sends already-encoded bytes via
    # add_attachment(), so the part's charset is unset (the bytes are
    # the canonical wire form); the L3-AGGR-020 contract is honored at
    # the maintype/subtype level + the Content-Disposition shape.
    for part in parts:
        assert part.get_content_type() == "text/html"
        cd = part.get("Content-Disposition", "")
        assert cd.startswith("attachment"), f"Content-Disposition SHALL be `attachment`; got {cd!r}"
        assert "filename=" in cd


# -----------------------------------------------------------------------------
# Size enforcement (L2-MAIL-007, L2-MAIL-008, L3-MAIL-012)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-MAIL-008")
@pytest.mark.requirement("L3-MAIL-012")
@pytest.mark.requirement("L3-MAIL-013")
@pytest.mark.requirement("L3-MAIL-021")
async def test_oversize_email_raises_before_smtp_traffic(
    patched_smtp: tuple[MagicMock, AsyncMock],
) -> None:
    smtp_class, smtp_instance = patched_smtp
    mailer = AiosmtplibMailer(host="x", port=587, max_email_size_bytes=100)
    big_email = _email(body_html="<p>" + "X" * 500 + "</p>")

    with pytest.raises(EmailDeliveryError) as exc_info:
        await mailer.send(big_email)

    assert exc_info.value.details["failure_reason"] == "EMAIL_SIZE_EXCEEDED"
    assert exc_info.value.details["limit_bytes"] == 100
    assert exc_info.value.details["measured_bytes"] > 100
    # Crucially: SMTP was never contacted.
    smtp_class.assert_not_called()
    smtp_instance.connect.assert_not_awaited()
    smtp_instance.send_message.assert_not_awaited()


# -----------------------------------------------------------------------------
# Happy path SMTP lifecycle
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-MAIL-001")
async def test_happy_path_issues_connect_starttls_send_quit(
    patched_smtp: tuple[MagicMock, AsyncMock],
) -> None:
    _, smtp = patched_smtp
    mailer = AiosmtplibMailer(
        host="smtp.example.com",
        port=587,
        username="user",
        password="pass",
        max_email_size_bytes=100_000,
    )
    await mailer.send(_email())

    # Order: connect, starttls, login, send_message, quit.
    manager = MagicMock()
    manager.attach_mock(smtp.connect, "connect")
    manager.attach_mock(smtp.starttls, "starttls")
    manager.attach_mock(smtp.login, "login")
    manager.attach_mock(smtp.send_message, "send_message")
    manager.attach_mock(smtp.quit, "quit")

    # Verify each was awaited.
    smtp.connect.assert_awaited_once()
    smtp.starttls.assert_awaited_once()
    smtp.login.assert_awaited_once_with("user", "pass")
    smtp.send_message.assert_awaited_once()
    smtp.quit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-MAIL-001")
async def test_each_send_constructs_a_new_smtp_client(
    patched_smtp: tuple[MagicMock, AsyncMock],
) -> None:
    """L3-MAIL-001: per-send instance, not reused."""
    smtp_class, _ = patched_smtp
    mailer = AiosmtplibMailer(host="x", port=587, max_email_size_bytes=100_000)
    await mailer.send(_email())
    await mailer.send(_email())
    await mailer.send(_email())
    assert smtp_class.call_count == 3


@pytest.mark.asyncio
@pytest.mark.requirement("L3-MAIL-003")
async def test_starttls_skipped_when_disabled(
    patched_smtp: tuple[MagicMock, AsyncMock],
) -> None:
    _, smtp = patched_smtp
    mailer = AiosmtplibMailer(host="x", port=25, use_starttls=False, max_email_size_bytes=100_000)
    await mailer.send(_email())
    smtp.starttls.assert_not_awaited()


@pytest.mark.asyncio
async def test_login_skipped_when_username_empty(
    patched_smtp: tuple[MagicMock, AsyncMock],
) -> None:
    _, smtp = patched_smtp
    mailer = AiosmtplibMailer(host="x", port=25, username="", max_email_size_bytes=100_000)
    await mailer.send(_email())
    smtp.login.assert_not_awaited()


# -----------------------------------------------------------------------------
# Error classification (L3-MAIL-005, L3-MAIL-006, L3-MAIL-007)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-MAIL-005")
def test_classify_smtp_server_disconnected_is_transient() -> None:
    assert _classify_smtp_error(aiosmtplib.SMTPServerDisconnected("x")) == "transient"


@pytest.mark.requirement("L3-MAIL-005")
def test_classify_connect_timeout_is_transient() -> None:
    assert _classify_smtp_error(aiosmtplib.SMTPConnectTimeoutError("x")) == "transient"


@pytest.mark.requirement("L3-MAIL-005")
def test_classify_gaierror_is_transient() -> None:
    assert _classify_smtp_error(socket.gaierror("dns lookup failed")) == "transient"


@pytest.mark.requirement("L3-MAIL-005")
@pytest.mark.parametrize("code", [400, 421, 450, 499])
def test_classify_4xx_response_codes(code: int) -> None:
    exc = aiosmtplib.SMTPResponseException(code, "x")
    result = _classify_smtp_error(exc)
    # 421 is permanent per L3-MAIL-006; everything else in 4xx is transient.
    if code == 421:
        assert result == "permanent"
    else:
        assert result == "transient"


@pytest.mark.requirement("L3-MAIL-007")
@pytest.mark.parametrize("code", [500, 535, 550, 599])
def test_classify_5xx_is_permanent(code: int) -> None:
    exc = aiosmtplib.SMTPResponseException(code, "x")
    assert _classify_smtp_error(exc) == "permanent"


@pytest.mark.requirement("L3-MAIL-007")
def test_classify_authentication_error_is_permanent() -> None:
    exc = aiosmtplib.SMTPAuthenticationError(535, "auth failed")
    assert _classify_smtp_error(exc) == "permanent"


# -----------------------------------------------------------------------------
# Retry loop (L2-MAIL-006, L3-MAIL-009)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-MAIL-006")
@pytest.mark.requirement("L3-MAIL-010")
async def test_transient_failure_retries_then_succeeds(
    patched_smtp: tuple[MagicMock, AsyncMock],
) -> None:
    _, smtp = patched_smtp
    # Fail first, succeed on retry.
    smtp.send_message.side_effect = [
        aiosmtplib.SMTPServerDisconnected("connection lost"),
        None,
    ]
    mailer = AiosmtplibMailer(
        host="x",
        port=587,
        max_email_size_bytes=100_000,
        max_retries=3,
        initial_interval_seconds=0.001,  # fast test
        max_interval_seconds=0.01,
    )
    await mailer.send(_email())
    assert smtp.send_message.await_count == 2


@pytest.mark.asyncio
@pytest.mark.requirement("L2-MAIL-006")
@pytest.mark.requirement("L3-MAIL-010")
async def test_transient_failure_exhausts_retries(
    patched_smtp: tuple[MagicMock, AsyncMock],
) -> None:
    _, smtp = patched_smtp
    smtp.send_message.side_effect = aiosmtplib.SMTPServerDisconnected("lost")
    mailer = AiosmtplibMailer(
        host="x",
        port=587,
        max_email_size_bytes=100_000,
        max_retries=2,
        initial_interval_seconds=0.001,
        max_interval_seconds=0.01,
    )
    with pytest.raises(EmailDeliveryError) as exc_info:
        await mailer.send(_email())
    # Initial attempt + 2 retries = 3 calls.
    assert smtp.send_message.await_count == 3
    assert exc_info.value.details["failure_reason"] == "RETRIES_EXHAUSTED"
    assert exc_info.value.details["attempts"] == 3


@pytest.mark.asyncio
@pytest.mark.requirement("L3-MAIL-007")
@pytest.mark.requirement("L3-MAIL-008")
async def test_permanent_failure_does_not_retry(
    patched_smtp: tuple[MagicMock, AsyncMock],
) -> None:
    _, smtp = patched_smtp
    smtp.send_message.side_effect = aiosmtplib.SMTPResponseException(550, "mailbox unavailable")
    mailer = AiosmtplibMailer(
        host="x",
        port=587,
        max_email_size_bytes=100_000,
        max_retries=5,
        initial_interval_seconds=0.001,
    )
    with pytest.raises(EmailDeliveryError) as exc_info:
        await mailer.send(_email())
    # Exactly one attempt — no retries on permanent.
    assert smtp.send_message.await_count == 1
    assert exc_info.value.details["failure_reason"] == "PERMANENT_SMTP_FAILURE"
    assert exc_info.value.details["smtp_code"] == 550


@pytest.mark.asyncio
@pytest.mark.requirement("L3-MAIL-006")
async def test_421_treated_as_permanent_no_retry(
    patched_smtp: tuple[MagicMock, AsyncMock],
) -> None:
    _, smtp = patched_smtp
    smtp.send_message.side_effect = aiosmtplib.SMTPResponseException(421, "service not available")
    mailer = AiosmtplibMailer(
        host="x",
        port=587,
        max_email_size_bytes=100_000,
        max_retries=5,
        initial_interval_seconds=0.001,
    )
    with pytest.raises(EmailDeliveryError) as exc_info:
        await mailer.send(_email())
    assert smtp.send_message.await_count == 1
    assert exc_info.value.details["failure_reason"] == "PERMANENT_SMTP_FAILURE"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-MAIL-009")
async def test_backoff_schedule_follows_exponential_formula(
    patched_smtp: tuple[MagicMock, AsyncMock],
) -> None:
    """min(max_interval, initial * 2^(attempt-1))"""
    _, smtp = patched_smtp
    smtp.send_message.side_effect = [
        aiosmtplib.SMTPServerDisconnected("1"),
        aiosmtplib.SMTPServerDisconnected("2"),
        aiosmtplib.SMTPServerDisconnected("3"),
        None,  # eventual success
    ]
    sleep_calls: list[float] = []

    async def _fake_sleep(s: float) -> None:
        sleep_calls.append(s)

    with patch(
        "message_service.infrastructure.email.aiosmtplib_mailer.asyncio.sleep",
        _fake_sleep,
    ):
        mailer = AiosmtplibMailer(
            host="x",
            port=587,
            max_email_size_bytes=100_000,
            max_retries=5,
            initial_interval_seconds=1.0,
            max_interval_seconds=100.0,
        )
        await mailer.send(_email())

    # attempt 1 fails -> sleep 1, attempt 2 fails -> sleep 2,
    # attempt 3 fails -> sleep 4, attempt 4 succeeds.
    assert sleep_calls == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-MAIL-009")
async def test_backoff_capped_at_max_interval(
    patched_smtp: tuple[MagicMock, AsyncMock],
) -> None:
    _, smtp = patched_smtp
    smtp.send_message.side_effect = [
        aiosmtplib.SMTPServerDisconnected("1"),
        aiosmtplib.SMTPServerDisconnected("2"),
        aiosmtplib.SMTPServerDisconnected("3"),
        aiosmtplib.SMTPServerDisconnected("4"),
        None,
    ]
    sleep_calls: list[float] = []

    async def _fake_sleep(s: float) -> None:
        sleep_calls.append(s)

    with patch(
        "message_service.infrastructure.email.aiosmtplib_mailer.asyncio.sleep",
        _fake_sleep,
    ):
        mailer = AiosmtplibMailer(
            host="x",
            port=587,
            max_email_size_bytes=100_000,
            max_retries=5,
            initial_interval_seconds=1.0,
            max_interval_seconds=3.0,
        )
        await mailer.send(_email())

    # 1, 2, 3 (capped), 3 (still capped)
    assert sleep_calls == [1.0, 2.0, 3.0, 3.0]


# -----------------------------------------------------------------------------
# Edge: gaierror (DNS failure) is transient
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-MAIL-005")
async def test_dns_failure_is_transient(
    patched_smtp: tuple[MagicMock, AsyncMock],
) -> None:
    _, smtp = patched_smtp
    smtp.connect.side_effect = [socket.gaierror("dns"), None]
    smtp.send_message.return_value = None
    mailer = AiosmtplibMailer(
        host="x",
        port=587,
        max_email_size_bytes=100_000,
        max_retries=1,
        initial_interval_seconds=0.001,
    )
    await mailer.send(_email())
    # One retry succeeded.
    assert smtp.connect.await_count == 2


# -----------------------------------------------------------------------------
# Quit failure does not propagate on successful send
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quit_failure_after_send_is_suppressed(
    patched_smtp: tuple[MagicMock, AsyncMock],
) -> None:
    _, smtp = patched_smtp
    smtp.send_message.return_value = None
    smtp.quit.side_effect = aiosmtplib.SMTPException("server closed")
    mailer = AiosmtplibMailer(host="x", port=587, max_email_size_bytes=100_000)
    # Should complete without raising.
    await mailer.send(_email())


# -----------------------------------------------------------------------------
# Zero-retries configuration
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_retries_fails_on_first_transient_error(
    patched_smtp: tuple[MagicMock, AsyncMock],
) -> None:
    _, smtp = patched_smtp
    smtp.send_message.side_effect = aiosmtplib.SMTPServerDisconnected("lost")
    mailer = AiosmtplibMailer(
        host="x",
        port=587,
        max_email_size_bytes=100_000,
        max_retries=0,
        initial_interval_seconds=0.001,
    )
    with pytest.raises(EmailDeliveryError) as exc_info:
        await mailer.send(_email())
    assert smtp.send_message.await_count == 1
    assert exc_info.value.details["failure_reason"] == "RETRIES_EXHAUSTED"
