"""Time-related test fixtures: ``FakeClock`` and ``fake_clock`` pytest fixture.

Every time-dependent test SHOULD inject ``fake_clock`` rather than relying
on wall-clock time. This keeps tests deterministic and fast.

Usage::

    def test_audit_timestamp_matches_clock(fake_clock: FakeClock) -> None:
        fake_clock.set(datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC))
        audit.record_event(...)
        assert audit.last_event.timestamp == "2026-04-19T12:00:00Z"

    def test_elapsed_time_math(fake_clock: FakeClock) -> None:
        t0 = fake_clock.now()
        fake_clock.advance(timedelta(seconds=30))
        assert (fake_clock.now() - t0).total_seconds() == 30.0

Requirement references
----------------------
L3-RUN-024 (Clock port ABC has a FakeClock implementation for tests)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from message_service.application.ports.clock import Clock


class FakeClock(Clock):
    """Deterministic clock implementation for tests.

    The internal time starts at a fixed epoch (2026-01-01T00:00:00Z by
    default) and advances only when ``advance()`` or ``set()`` is called.
    The clock NEVER advances spontaneously — two successive ``now()``
    calls return identical values.

    Attributes:
        default_epoch: The time the clock is initialized to. Tests that
            need a specific starting time should call ``set()`` in their
            arrange step rather than relying on this default.
    """

    default_epoch: datetime = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    def __init__(self, initial: datetime | None = None) -> None:
        """Initialize the fake clock.

        Args:
            initial: Starting time. Defaults to ``default_epoch``. Must
                be timezone-aware.

        Raises:
            ValueError: If ``initial`` is naive (no tzinfo).
        """
        start = initial if initial is not None else self.default_epoch
        if start.tzinfo is None:
            raise ValueError("FakeClock requires a timezone-aware initial datetime")
        self._current: datetime = start.astimezone(UTC)

    def now(self) -> datetime:
        """Return the current fake time without advancing it."""
        return self._current

    def set(self, when: datetime) -> None:
        """Jump the clock to an absolute time.

        Args:
            when: The new current time. Must be timezone-aware.

        Raises:
            ValueError: If ``when`` is naive.
        """
        if when.tzinfo is None:
            raise ValueError("FakeClock.set() requires a timezone-aware datetime")
        self._current = when.astimezone(UTC)

    def advance(self, delta: timedelta) -> None:
        """Advance the clock by a positive or negative delta.

        Tests that need to simulate "an hour passes" use this method::

            fake_clock.advance(timedelta(hours=1))

        Args:
            delta: The amount of time to add to the current fake time.
                Negative deltas are permitted (rare, but useful for
                testing clock-skew scenarios).
        """
        self._current = self._current + delta


@pytest.fixture
def fake_clock() -> FakeClock:
    """Fresh ``FakeClock`` per test function.

    Function-scoped because tests routinely mutate time; a shared fixture
    would cause test-order dependencies.
    """
    return FakeClock()


@pytest.fixture
def fake_clock_at_epoch() -> FakeClock:
    """``FakeClock`` initialized at the Unix epoch (1970-01-01T00:00:00Z).

    Useful for tests asserting on specific timestamp values rather than
    on elapsed-time math.
    """
    return FakeClock(datetime(1970, 1, 1, 0, 0, 0, tzinfo=UTC))
