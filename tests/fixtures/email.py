"""Email-related fixtures: in-process SMTP server, MIME message builders.

Planned fixtures:

* ``fake_smtp_server`` — an ``aiosmtpd`` controller started on an ephemeral
  port, capturing received messages in a list for assertion.
* ``smtp_config`` — a ``MailConfig`` pointing at ``fake_smtp_server``.
* ``mime_message_builder`` — constructs valid ``email.message.EmailMessage``
  objects for unit-level MIME assembly tests.
"""

from __future__ import annotations

# TODO(L3-MAIL-001): implement once MIME composer and SMTP sender land.
