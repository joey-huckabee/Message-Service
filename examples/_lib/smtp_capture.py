"""In-process SMTP capture for demonstration scenarios.

This is the SMTP MOCK the examples use. There is NO real mail server
required — no Docker, no MailHog, no internet, no SMTP relay account.
``aiosmtpd`` is a pure-Python SMTP server that runs in the same
process as the demo, captures every message that the
Message-Service mailer adapter delivers, and exposes the captured
mail as plain Python objects for the demo to inspect and print.

Why aiosmtpd: it speaks real SMTP (the production mailer adapter
talks to it the same way it would talk to a real relay), it's a
test-only Python dependency the project already ships, and it
binds to a loopback port — so two scenarios can run on the same
host without conflicting.

Usage:

    from examples._lib.smtp_capture import SmtpCapture

    with SmtpCapture(port=1025) as capture:
        # ... start the message-service pointed at 127.0.0.1:1025 ...
        # ... drive the gRPC RPCs ...
        capture.wait_for(count=1, timeout=10.0)
        for msg in capture.messages:
            print(f"To: {msg.rcpt_tos}; subject: {msg.subject}")
"""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import Message
from types import TracebackType
from typing import Any

from aiosmtpd.controller import Controller


@dataclass
class CapturedMessage:
    """One message delivered to the in-process SMTP server."""

    mail_from: str
    rcpt_tos: list[str]
    raw_content: bytes

    @property
    def parsed(self) -> Message:
        """The raw bytes parsed via :mod:`email`."""
        return message_from_bytes(self.raw_content)

    @property
    def subject(self) -> str:
        return self.parsed.get("Subject", "")

    @property
    def from_header(self) -> str:
        return self.parsed.get("From", "")

    def body_text(self) -> str:
        """Best-effort plain-text body extraction.

        v1 sends HTML bodies; this returns the HTML payload as a
        string so the demos can preview it.
        """
        msg = self.parsed
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct in ("text/html", "text/plain"):
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        return payload.decode("utf-8", errors="replace")
            return ""
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            return payload.decode("utf-8", errors="replace")
        return str(payload or "")

    def attachment_filenames(self) -> list[str]:
        """Filenames of every MIME attachment, in order."""
        return [part.get_filename() or "(unnamed)" for part in self.parsed.iter_attachments()]


@dataclass
class SmtpCapture:
    """Context manager that runs aiosmtpd on a chosen port."""

    host: str = "127.0.0.1"
    port: int = 1025
    messages: list[CapturedMessage] = field(default_factory=list)
    _controller: Controller | None = None

    def __enter__(self) -> SmtpCapture:
        # If port=0 the caller wants ephemeral; resolve and stash.
        if self.port == 0:
            self.port = _grab_ephemeral_port(self.host)
        handler = _CaptureHandler(self.messages)
        self._controller = Controller(handler, hostname=self.host, port=self.port)
        self._controller.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._controller is not None:
            self._controller.stop()
            self._controller = None

    async def wait_for(self, *, count: int, timeout: float = 10.0) -> None:
        """Wait until at least ``count`` messages are captured.

        Polling-based; raises ``TimeoutError`` if the deadline elapses.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if len(self.messages) >= count:
                return
            await asyncio.sleep(0.1)
        raise TimeoutError(
            f"timed out waiting for {count} SMTP message(s); got {len(self.messages)}"
        )


class _CaptureHandler:
    """aiosmtpd handler that appends every received envelope to a list."""

    def __init__(self, messages: list[CapturedMessage]) -> None:
        self._messages = messages

    async def handle_DATA(  # noqa: N802 — name dictated by aiosmtpd
        self,
        server: Any,
        session: Any,
        envelope: Any,
    ) -> str:
        del server, session
        self._messages.append(
            CapturedMessage(
                mail_from=envelope.mail_from,
                rcpt_tos=list(envelope.rcpt_tos),
                raw_content=bytes(envelope.content),
            )
        )
        return "250 Message accepted for delivery"


def _grab_ephemeral_port(host: str) -> int:
    """Resolve a free TCP port on ``host``.

    Used when ``SmtpCapture(port=0)`` to avoid collision when scenarios
    are run in parallel.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])
