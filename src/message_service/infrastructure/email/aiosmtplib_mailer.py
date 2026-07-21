"""Concrete :class:`Mailer` backed by ``aiosmtplib``.

The adapter separates four concerns:

1. **MIME assembly** (:func:`_build_mime_message`) — turns
   :class:`OutboundEmail` into an :class:`email.message.EmailMessage`
   with the configured ``From:`` address, the recipients as ``Bcc:``
   (operator convention: subscribers should not see each other's
   addresses), the HTML body, and each attachment as a separate MIME
   part.
2. **Size enforcement** — ``len(message.as_bytes())`` is checked
   against ``mail.max_email_size_bytes`` after MIME encoding is
   complete and before any SMTP traffic is emitted (L2-MAIL-007,
   L2-MAIL-008, L3-MAIL-012).
3. **Retry loop with exponential backoff** (:meth:`_send_with_retries`)
   — ``min(max_interval, initial_interval * 2 ** (attempt - 1))``
   (L2-MAIL-006, L3-MAIL-009). Transient failures retry up to
   ``max_retries``; permanent failures raise immediately.
4. **SMTP session lifecycle** (:meth:`_open_and_send`) — per-send
   :class:`aiosmtplib.SMTP` instance (L3-MAIL-001; connection pooling
   deferred to ROADMAP).

Every failure mode converts to :class:`EmailDeliveryError` with
structured ``details`` so the caller (typically
:class:`AssembleAndDeliverUseCase`) can log/audit a classified reason.

Requirement references
----------------------
L1-MAIL-001, L1-MAIL-002, L1-MAIL-003 (SMTP delivery, retry, size cap)
L2-MAIL-001..008
L3-MAIL-001, L3-MAIL-003, L3-MAIL-005, L3-MAIL-006, L3-MAIL-007
L3-MAIL-009, L3-MAIL-010, L3-MAIL-012
"""

from __future__ import annotations

import asyncio
import socket
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from typing import Final

import aiosmtplib
import structlog

from message_service.application.ports.mailer import Mailer, OutboundEmail
from message_service.domain.errors import EmailDeliveryError, EmailSizeExceededError

_log = structlog.get_logger(__name__)


# -----------------------------------------------------------------------------
# Failure classification (L2-MAIL-004, L2-MAIL-005, L3-MAIL-005..007)
# -----------------------------------------------------------------------------

# Response code treated as permanent per L3-MAIL-006 (RFC 5321: service
# not available → do not retry against this run).
_PERMANENT_4XX_CODES: Final[frozenset[int]] = frozenset({421})

# Failure-type labels recorded in logs and audit details.
_FAILURE_TYPE_TRANSIENT: Final[str] = "transient"
_FAILURE_TYPE_PERMANENT: Final[str] = "permanent"


def _classify_smtp_error(exc: BaseException) -> str:
    """Return ``"transient"`` if the exception is retryable, else ``"permanent"``.

    Transient per L3-MAIL-005:
    :class:`aiosmtplib.SMTPServerDisconnected`,
    :class:`aiosmtplib.SMTPConnectTimeoutError`,
    :class:`socket.gaierror`, and
    :class:`aiosmtplib.SMTPResponseException` with code in ``[400, 500)``
    excluding 421 (L3-MAIL-006).

    Permanent per L3-MAIL-007:
    :class:`aiosmtplib.SMTPResponseException` with code in ``[500, 600)``,
    :class:`aiosmtplib.SMTPAuthenticationError`, and 421.

    Args:
        exc: Exception raised during send.

    Returns:
        ``"transient"`` or ``"permanent"``.
    """
    if isinstance(exc, aiosmtplib.SMTPAuthenticationError):
        return _FAILURE_TYPE_PERMANENT
    if isinstance(exc, aiosmtplib.SMTPResponseException):
        code = exc.code
        if code in _PERMANENT_4XX_CODES:
            return _FAILURE_TYPE_PERMANENT
        if 400 <= code < 500:
            return _FAILURE_TYPE_TRANSIENT
        if 500 <= code < 600:
            return _FAILURE_TYPE_PERMANENT
        # Unclassified code — default to permanent (safer than retrying
        # forever against an unknown server response).
        return _FAILURE_TYPE_PERMANENT
    if isinstance(
        exc,
        aiosmtplib.SMTPServerDisconnected | aiosmtplib.SMTPConnectTimeoutError,
    ):
        return _FAILURE_TYPE_TRANSIENT
    if isinstance(exc, socket.gaierror):
        return _FAILURE_TYPE_TRANSIENT
    # Anything else (TimeoutError, OSError on non-DNS paths, etc.) —
    # treat as transient. Retries are bounded by max_retries, so
    # nothing runs away.
    return _FAILURE_TYPE_TRANSIENT


# -----------------------------------------------------------------------------
# MIME assembly
# -----------------------------------------------------------------------------


def _build_mime_message(email: OutboundEmail) -> EmailMessage:
    """Assemble an :class:`EmailMessage` from an :class:`OutboundEmail`.

    Recipients go on ``Bcc:`` (subscribers should not see each other's
    addresses); ``To:`` is the sender's own address, which is the
    convention for service-originated mailing-list messages.

    Args:
        email: Domain value object; already validated (non-empty
            recipients, no newlines in subject/from_address, etc.).

    Returns:
        A fully-encoded :class:`EmailMessage` ready for
        :meth:`aiosmtplib.SMTP.send_message`. The returned message's
        ``.as_bytes()`` is the final wire form.
    """
    msg = EmailMessage(policy=SMTP_POLICY)
    msg["From"] = email.from_address
    # Addressing: To = from (per RFC 2822 "undisclosed recipients"
    # convention for BCC-only delivery); Bcc carries the actual list.
    msg["To"] = email.from_address
    msg["Bcc"] = ", ".join(sorted(email.recipients))
    msg["Subject"] = email.subject

    # HTML body. set_content sets ``Content-Type: text/html`` and
    # applies base64/quoted-printable CTE as needed.
    msg.set_content(email.body_html, subtype="html")

    # Attachments. Each becomes a separate MIME part.
    for att in email.attachments:
        maintype, _, subtype = att.content_type.partition("/")
        subtype = subtype.split(";", 1)[0].strip() or "html"
        msg.add_attachment(
            att.content,
            maintype=maintype or "application",
            subtype=subtype,
            filename=att.filename,
        )

    return msg


# -----------------------------------------------------------------------------
# Adapter
# -----------------------------------------------------------------------------


class AiosmtplibMailer(Mailer):
    """SMTP delivery using the ``aiosmtplib`` client.

    Attributes set at construction:

    * ``host``, ``port`` — SMTP relay connection target.
    * ``username``, ``password`` — authentication (empty strings
      disable auth).
    * ``use_starttls`` — when ``True``, issue STARTTLS before auth.
      When ``False``, logs a startup WARNING (L3-MAIL-003).
    * ``max_email_size_bytes`` — size ceiling (L1-MAIL-003).
    * ``max_retries``, ``initial_interval_seconds``,
      ``max_interval_seconds`` — exponential backoff parameters
      (L2-MAIL-006).
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str = "",
        password: str = "",
        use_starttls: bool = True,
        max_email_size_bytes: int,
        max_retries: int = 5,
        initial_interval_seconds: float = 2.0,
        max_interval_seconds: float = 300.0,
        timeout_seconds: float = 30.0,
    ) -> None:
        """Construct the adapter.

        Args:
            host: SMTP relay hostname.
            port: SMTP relay port (1..65535).
            username: SMTP auth username; empty string disables auth.
            password: SMTP auth password.
            use_starttls: If ``True``, issue STARTTLS. Setting this to
                ``False`` in the presence of credentials logs a WARNING
                (L3-MAIL-003).
            max_email_size_bytes: Byte ceiling for MIME-encoded
                message. Messages exceeding this raise
                :class:`EmailDeliveryError` *before* any SMTP traffic
                (L2-MAIL-008).
            max_retries: Maximum retry attempts for transient failures.
                ``0`` disables retries; the send is attempted once.
            initial_interval_seconds: First-attempt backoff delay
                (applied before attempt 2).
            max_interval_seconds: Ceiling for the backoff delay.
            timeout_seconds: Per-connection SMTP timeout (L3-RUN-034),
                applied to connect and every subsequent command. Bounds
                how long a single attempt can hang against an
                unresponsive relay so a run cannot sit in ``SENDING``
                indefinitely and race the orphan sweeper. A hung connect
                surfaces as :class:`aiosmtplib.SMTPConnectTimeoutError`
                (classified transient, so it retries).

        Raises:
            ValueError: Any numeric parameter out of range.
        """
        if port < 1 or port > 65_535:
            raise ValueError(f"port must be in [1, 65535]; got {port}")
        if max_email_size_bytes < 1:
            raise ValueError("max_email_size_bytes must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if initial_interval_seconds <= 0:
            raise ValueError("initial_interval_seconds must be positive")
        if max_interval_seconds < initial_interval_seconds:
            raise ValueError("max_interval_seconds must be >= initial_interval_seconds")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_starttls = use_starttls
        self._max_email_size_bytes = max_email_size_bytes
        self._max_retries = max_retries
        self._initial_interval_seconds = initial_interval_seconds
        self._max_interval_seconds = max_interval_seconds
        self._timeout_seconds = timeout_seconds

        # L3-MAIL-003: warn on plaintext auth at construction-time. The
        # adapter is built once at service start; this is the startup
        # log event.
        if not use_starttls and username:
            _log.warning(
                "mail.smtp.use_starttls=False with credentials present — "
                "plaintext auth over the wire",
                host=host,
                port=port,
            )

    # -- Port contract ---------------------------------------------------

    async def send(self, email: OutboundEmail) -> None:  # noqa: D102
        # MIME encode first — then size-check, then send. This order
        # satisfies L2-MAIL-008 (no bytes transmitted if oversize) and
        # L3-MAIL-012 (measurement uses message.as_bytes()).
        message = _build_mime_message(email)
        raw = message.as_bytes()
        size_bytes = len(raw)

        if size_bytes > self._max_email_size_bytes:
            # L3-MAIL-014 details schema; EmailSizeExceededError is a
            # subclass of EmailDeliveryError so existing generic
            # ``except EmailDeliveryError`` handlers continue to work,
            # while the L3-MAIL-030 admin-notification path catches
            # this subtype first.
            raise EmailSizeExceededError(
                f"encoded email size {size_bytes} bytes exceeds limit "
                f"{self._max_email_size_bytes} bytes",
                details={
                    "failure_reason": "EMAIL_SIZE_EXCEEDED",
                    "measured_bytes": size_bytes,
                    "limit_bytes": self._max_email_size_bytes,
                    "recipient_count": len(email.recipients),
                },
            )

        await self._send_with_retries(message, email)

    # -- Retry loop ------------------------------------------------------

    async def _send_with_retries(self, message: EmailMessage, email: OutboundEmail) -> None:
        """Attempt delivery up to ``max_retries + 1`` times.

        First attempt is number 1. Backoff is applied *before* each
        retry (i.e., between attempts), not before the first attempt.

        Args:
            message: The encoded MIME message.
            email: Original domain value (for error ``details``).

        Raises:
            EmailDeliveryError: Send failed definitively (permanent
                error, or retries exhausted).
        """
        attempt = 1
        while True:
            try:
                await self._open_and_send(message)
                return
            except (
                aiosmtplib.SMTPException,
                socket.gaierror,
                TimeoutError,
                OSError,
            ) as exc:
                failure_type = _classify_smtp_error(exc)

                if failure_type == _FAILURE_TYPE_PERMANENT:
                    raise EmailDeliveryError(
                        f"permanent SMTP failure: {type(exc).__name__}: {exc}",
                        details={
                            "failure_reason": "PERMANENT_SMTP_FAILURE",
                            "exception_class": type(exc).__name__,
                            "message": str(exc),
                            "smtp_code": getattr(exc, "code", None),
                            "recipient_count": len(email.recipients),
                            "attempt": attempt,
                        },
                    ) from exc

                if attempt > self._max_retries:
                    raise EmailDeliveryError(
                        f"transient SMTP failure; retries exhausted after "
                        f"{attempt} attempts: {type(exc).__name__}: {exc}",
                        details={
                            "failure_reason": "RETRIES_EXHAUSTED",
                            "exception_class": type(exc).__name__,
                            "message": str(exc),
                            "smtp_code": getattr(exc, "code", None),
                            "recipient_count": len(email.recipients),
                            "attempts": attempt,
                        },
                    ) from exc

                # Compute backoff: L3-MAIL-009.
                backoff = min(
                    self._max_interval_seconds,
                    self._initial_interval_seconds * (2 ** (attempt - 1)),
                )
                # L3-MAIL-010: WARNING log per attempt with structured fields.
                _log.warning(
                    "smtp_transient_failure_retrying",
                    attempt=attempt,
                    max_retries=self._max_retries,
                    backoff_seconds=backoff,
                    failure_type=failure_type,
                    exception_class=type(exc).__name__,
                    message=str(exc),
                )
                await asyncio.sleep(backoff)
                attempt += 1
                # loop continues

    # -- SMTP session ----------------------------------------------------

    async def _open_and_send(self, message: EmailMessage) -> None:
        """Open a fresh SMTP session, send, disconnect.

        Per L3-MAIL-001: a new :class:`aiosmtplib.SMTP` instance per
        send. Connection pooling is on the ROADMAP.

        Args:
            message: The encoded MIME message.

        Raises:
            aiosmtplib.SMTPException: Any SMTP-level error.
            socket.gaierror: DNS resolution failure.
            TimeoutError: Connection timeout.
            OSError: Other network-layer errors.
        """
        client = aiosmtplib.SMTP(
            hostname=self._host,
            port=self._port,
            start_tls=False,  # we issue STARTTLS manually after connect
            timeout=self._timeout_seconds,  # L3-RUN-034: bound each attempt
        )
        try:
            await client.connect()
            if self._use_starttls:
                await client.starttls()
            if self._username:
                await client.login(self._username, self._password)
            await client.send_message(message)
        finally:
            try:
                await client.quit()
            except (aiosmtplib.SMTPException, OSError):
                # A failed quit after a successful send is not an
                # error we want to propagate; the message is on its
                # way. Log and move on.
                _log.debug("smtp_quit_after_send_failed")


__all__ = ["AiosmtplibMailer"]
