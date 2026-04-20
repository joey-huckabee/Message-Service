"""Port: outbound email delivery.

The port accepts a fully-assembled :class:`OutboundEmail` value object
and delivers it to the SMTP relay (or stages it to a retry queue on
transient failure). Attachments are raw bytes; assembly and rendering
happen in use-case code before this port is called.

The port hides SMTP details: TLS negotiation, connection reuse, retry
backoff, and credentials all live in the adapter. Use cases see only
"deliver this payload or raise".

Requirement references
----------------------
L1-MAIL-001, L1-MAIL-002, L1-MAIL-003
L2-MAIL-002, L2-MAIL-003, L2-MAIL-005, L2-MAIL-006
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmailAttachment:
    """A single MIME attachment for an outbound email.

    Attributes:
        filename: Displayed filename. MUST NOT contain path separators
            or control characters (adapter enforces).
        content_type: Full MIME type including any parameters (e.g.,
            ``"text/html; charset=utf-8"``).
        content: Raw attachment bytes. Base64 encoding is applied by
            the adapter during MIME assembly; callers pass the
            unencoded payload.
    """

    filename: str
    content_type: str
    content: bytes

    def __post_init__(self) -> None:
        """Validate non-empty filename and content_type.

        Raises:
            ValueError: If ``filename`` or ``content_type`` is empty.
        """
        if not self.filename:
            raise ValueError("EmailAttachment.filename must be non-empty")
        if not self.content_type:
            raise ValueError("EmailAttachment.content_type must be non-empty")


@dataclass(frozen=True, slots=True)
class OutboundEmail:
    """A fully-assembled email payload ready for SMTP submission.

    Attributes:
        recipients: One or more ``To:`` addresses. Typically the
            result of
            :meth:`~message_service.application.ports.subscription_repository.SubscriptionRepository.list_recipients_for_run`.
            Delivered as ``BCC:`` when the adapter is configured for
            privacy; the port does not dictate header choice.
        subject: Email subject line. MUST NOT contain newline
            characters (adapter enforces against header injection).
        body_html: Rendered HTML body. The plain-text alternative is
            the adapter's responsibility, typically via html2text.
        attachments: Zero or more MIME attachments. For
            :attr:`~message_service.domain.run.AttachmentMode.PER_STAGE`
            runs, one per ACCEPTED stage.
        from_address: The ``From:`` address. Typically the validated
            :attr:`mail.from_address` from config.
    """

    recipients: frozenset[str]
    subject: str
    body_html: str
    from_address: str
    attachments: Sequence[EmailAttachment] = ()

    def __post_init__(self) -> None:
        """Validate presence of recipients and absence of CR/LF in headers.

        Raises:
            ValueError: If ``recipients`` is empty, if any header field
                contains a newline (header injection defense), or if
                ``from_address`` is empty.
        """
        if not self.recipients:
            raise ValueError("OutboundEmail.recipients must be non-empty")
        if not self.from_address:
            raise ValueError("OutboundEmail.from_address must be non-empty")
        if "\n" in self.subject or "\r" in self.subject:
            raise ValueError("OutboundEmail.subject must not contain newline characters")
        if "\n" in self.from_address or "\r" in self.from_address:
            raise ValueError("OutboundEmail.from_address must not contain newline characters")


class Mailer(ABC):
    """Abstract outbound email port.

    Implementations MUST:

    * Apply exponential backoff with the configured parameters
      (L2-MAIL-006) on transient SMTP failures (4xx responses, network
      timeouts). After ``max_retries`` exhausted, raise
      :class:`~message_service.domain.errors.EmailDeliveryError`.
    * Classify 5xx SMTP responses as non-retryable and raise
      :class:`EmailDeliveryError` immediately.
    * Enforce :attr:`mail.max_email_size_bytes` (L1-MAIL-003) before
      handing off to the SMTP client; oversize payloads raise
      :class:`EmailDeliveryError` with ``details['size_bytes']``.
    """

    @abstractmethod
    async def send(self, email: OutboundEmail) -> None:
        """Deliver an email via SMTP.

        Args:
            email: The assembled payload.

        Raises:
            EmailDeliveryError: Delivery failed after retries, or the
                payload exceeded the configured size limit, or the SMTP
                relay returned a permanent error.
        """


__all__ = ["EmailAttachment", "Mailer", "OutboundEmail"]
