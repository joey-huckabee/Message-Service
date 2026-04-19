"""System clock — production ``Clock`` adapter backed by ``datetime.now``.

This is the only place in the production codebase where
``datetime.now`` is permitted to appear. A conformance test greps for
``datetime.now`` outside this module and fails the build on any other
occurrence — direct calls to ``datetime.now`` from domain or application
code bypass the injected ``Clock`` and defeat the testability property
established in L2-RUN-014.

Requirement references
----------------------
L1-RUN-005, L2-RUN-014, L3-RUN-024
"""

from __future__ import annotations

from datetime import datetime, timezone

from message_service.application.ports.clock import Clock


class SystemClock(Clock):
    """Production clock backed by ``datetime.now(tz=timezone.utc)``.

    Contains no state and no configuration; instances are interchangeable.
    The production service constructs one at startup and injects it into
    every use case that needs a timestamp.
    """

    def now(self) -> datetime:
        """Return the wall-clock time in UTC.

        Returns:
            A timezone-aware ``datetime`` with ``tzinfo=timezone.utc``.
        """
        return datetime.now(tz=timezone.utc)


__all__ = ["SystemClock"]
