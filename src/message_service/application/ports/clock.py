"""Clock port.

Abstraction over the current time, injected into any domain or application
code that needs a timestamp. Direct calls to ``datetime.now`` are
forbidden outside the ``SystemClock`` adapter module — a conformance
check enforces this at CI time.

Design
------
* Single method :meth:`Clock.now` returning a timezone-aware UTC
  ``datetime`` (L3-RUN-024).
* A companion :func:`iso_z` free function produces the canonical string
  form with literal ``"Z"`` suffix used for persistence (L3-RUN-025).
* Production implementation lives at
  :mod:`message_service.infrastructure.time.system_clock` (hexagonal
  split — ports here, adapters in infrastructure).
* Test implementation lives at ``tests/fixtures/clocks.py``.

Requirement references
----------------------
L1-RUN-005, L2-RUN-014, L3-RUN-024, L3-RUN-025
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone


class Clock(ABC):
    """Abstract clock port.

    Implementations MUST return a timezone-aware ``datetime`` in UTC.
    Naive datetimes are forbidden — callers depend on the ``tzinfo``
    attribute being set.
    """

    @abstractmethod
    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        raise NotImplementedError


# ISO-8601 with literal "Z" suffix. L3-RUN-025 requires persisted
# timestamps to match this exact form — not ``+00:00``, which is what
# ``datetime.isoformat()`` produces by default for UTC times.
#
# Example output: ``2026-04-19T18:30:15.123456Z``

_ISO_Z_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


def iso_z(value: datetime) -> str:
    """Format a timezone-aware datetime as ISO-8601 with literal ``"Z"`` suffix.

    Args:
        value: A timezone-aware ``datetime``. Naive datetimes raise
            ``ValueError``.

    Returns:
        An ISO-8601 string in the form ``YYYY-MM-DDTHH:MM:SS[.ffffff]Z``.

    Raises:
        ValueError: If ``value`` has no ``tzinfo`` attribute.
    """
    if value.tzinfo is None:
        raise ValueError("iso_z requires a timezone-aware datetime; got naive " f"value {value!r}")
    # Convert to UTC regardless of the incoming tz, then format.
    as_utc = value.astimezone(timezone.utc)
    # isoformat() yields e.g. "2026-04-19T18:30:15.123456+00:00"; strip
    # the "+00:00" and append "Z" to match L3-RUN-025 exactly.
    return as_utc.isoformat().removesuffix("+00:00") + "Z"


def is_iso_z(candidate: str) -> bool:
    """Return True iff ``candidate`` matches the L3-RUN-025 timestamp form.

    Used by persistence-layer tests to assert every persisted timestamp
    is in the canonical form.
    """
    return bool(_ISO_Z_PATTERN.match(candidate))


__all__ = [
    "Clock",
    "iso_z",
    "is_iso_z",
]
