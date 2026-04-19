"""Logging fixtures for test isolation and assertion.

Planned fixtures:

* ``null_logger`` — a structlog logger that discards all records.
* ``capture_logs`` — captures structlog records into a list for assertion.
  Based on ``structlog.testing.capture_logs``.
* ``assert_no_sensitive_leaks`` — session-scoped autouse fixture that
  scans captured logs for sensitive field names and fails the test if any
  slipped through redaction.
"""

from __future__ import annotations

# TODO(L3-OBS-005, L3-OBS-006): wire up once logging_setup exports a test hook.
