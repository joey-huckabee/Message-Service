"""Email-related fixtures: in-process SMTP capture.

The :func:`smtp_capture` fixture starts an :mod:`aiosmtpd` controller
on an ephemeral port and yields a handle whose ``messages`` list
captures every successfully-received SMTP envelope. e2e tests assert
on this list to confirm the service actually emitted SMTP traffic
end-to-end (not just that the ``Mailer.send`` port was called).

The controller runs in its own thread with its own asyncio loop —
that's the standard ``aiosmtpd.controller.Controller`` shape — so the
``messages`` list is appended-to from the SMTP thread and read from
the test thread. The append-only Python list is safe for that
pattern under CPython's GIL; tests that need stricter ordering
guarantees can call :meth:`SmtpCapture.wait_for` to busy-wait a
short timeout.

Why aiosmtpd rather than a TCP-listener stub: aiosmtpd is the
canonical reference SMTP server in the asyncio ecosystem (same
maintainer as aiosmtplib), handles the SMTP grammar correctly out
of the box (EHLO / MAIL FROM / RCPT TO / DATA / QUIT), and gives us
a real ``Envelope`` we can introspect rather than a hand-rolled
parser of raw bytes. The dev-dependency cost is small (~50 LoC
package) and the test fidelity gain is large.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterator
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import Message
from typing import Any

import pytest
from aiosmtpd.controller import Controller


def _grab_ephemeral_port(host: str) -> int:
    """Bind, read, and release an OS-assigned port.

    Workaround for ``aiosmtpd.Controller.start()`` failing on Windows
    when ``port=0`` is passed: the controller's post-start "trigger"
    handshake tries to ``connect((host, self.port))`` and ``self.port``
    has not yet been updated to the actually-bound port. Pre-binding
    an ephemeral port and passing it to the controller sidesteps the
    ordering bug. The race window between ``close()`` here and
    ``bind()`` inside the controller is negligible on a quiet test
    machine.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


@dataclass
class _CapturedMessage:
    """One successfully-received SMTP envelope."""

    mail_from: str
    rcpt_tos: list[str]
    raw_content: bytes

    def parsed(self) -> Message:
        """Parse the raw bytes as a MIME message for assertion convenience."""
        return message_from_bytes(self.raw_content)

    @property
    def subject(self) -> str:
        """Convenience: Subject header from the parsed message."""
        return str(self.parsed().get("Subject", ""))

    @property
    def body_html(self) -> str:
        """Convenience: HTML body part as a UTF-8 string.

        The mailer composes a multipart message with an HTML body
        and attachments; this returns the first ``text/html`` part's
        payload. Empty string if no HTML part is present.
        """
        for part in self.parsed().walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    return payload.decode("utf-8", errors="replace")
        return ""


@dataclass
class SmtpCapture:
    """Handle returned by the :func:`smtp_capture` fixture."""

    host: str
    port: int
    messages: list[_CapturedMessage] = field(default_factory=list)

    async def wait_for(self, count: int, *, timeout_seconds: float = 5.0) -> None:
        """Async-poll until ``len(messages) >= count`` or timeout.

        SMTP delivery is async — the e2e flow returns from
        FinalizeRun before AssembleAndDeliver has run on the
        scheduler — so tests need a brief wait window before
        asserting on captured messages. The poll uses
        ``asyncio.sleep`` (NOT ``time.sleep``) so the event loop can
        run other tasks during the wait — most importantly, the
        scheduled AssembleAndDeliver task that produces the SMTP
        traffic the wait is for. 5 seconds is generous for a
        local-loopback aiosmtpd; tests that hit the timeout almost
        always indicate a real bug rather than a slow machine.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            if len(self.messages) >= count:
                return
            await asyncio.sleep(0.05)
        raise AssertionError(
            f"timed out waiting for {count} captured SMTP message(s); got {len(self.messages)}"
        )


class _CaptureHandler:
    """aiosmtpd handler that appends every received envelope to a list."""

    def __init__(self, messages: list[_CapturedMessage]) -> None:
        self._messages = messages

    async def handle_DATA(  # noqa: N802 — name dictated by aiosmtpd's handler protocol
        self,
        server: Any,
        session: Any,
        envelope: Any,
    ) -> str:
        del server, session
        self._messages.append(
            _CapturedMessage(
                mail_from=envelope.mail_from,
                rcpt_tos=list(envelope.rcpt_tos),
                raw_content=bytes(envelope.content),
            )
        )
        return "250 Message accepted for delivery"


@pytest.fixture
def smtp_capture() -> Iterator[SmtpCapture]:
    """Start aiosmtpd on an ephemeral local port; capture every message.

    Yields:
        :class:`SmtpCapture` with bound ``host`` / ``port`` and a
        ``messages`` list. Tests configure the service's
        ``mail.smtp.host``/``mail.smtp.port`` to these values and
        assert on the list after triggering delivery.
    """
    host = "127.0.0.1"
    port = _grab_ephemeral_port(host)
    capture = SmtpCapture(host=host, port=port)
    handler = _CaptureHandler(capture.messages)
    controller = Controller(handler, hostname=host, port=port)
    controller.start()
    try:
        yield capture
    finally:
        controller.stop()


__all__ = ["SmtpCapture", "smtp_capture"]
