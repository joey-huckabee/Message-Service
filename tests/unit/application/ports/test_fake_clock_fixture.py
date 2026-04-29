"""Tests for the FakeClock fixture.

Fixtures need tests too. A bug in FakeClock silently corrupts every
downstream test that depends on it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from message_service.application.ports.clock import Clock, iso_z
from tests.fixtures.clocks import FakeClock

# -----------------------------------------------------------------------------
# FakeClock satisfies the Clock contract
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-024")
@pytest.mark.requirement("L3-RUN-033")
def test_fake_clock_is_a_clock() -> None:
    """L3-RUN-024 / L3-RUN-033: FakeClock SHALL implement the Clock port,
    serving as the substitution mechanism the chokepoint enables.
    """
    assert isinstance(FakeClock(), Clock)


@pytest.mark.requirement("L3-RUN-024")
@pytest.mark.requirement("L3-RUN-033")
def test_fake_clock_now_returns_tz_aware_utc() -> None:
    result = FakeClock().now()
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)


# -----------------------------------------------------------------------------
# Determinism: now() is stable until advanced
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-024")
def test_repeated_now_calls_return_identical_values() -> None:
    """Two successive ``now()`` calls on a FakeClock SHALL return the same value."""
    clock = FakeClock()
    first = clock.now()
    second = clock.now()
    assert first == second


@pytest.mark.requirement("L3-RUN-024")
def test_default_epoch_is_2026_01_01_utc() -> None:
    """Unconfigured FakeClock starts at the documented default epoch."""
    assert FakeClock().now() == datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


# -----------------------------------------------------------------------------
# set() and advance() behaviour
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-024")
def test_set_jumps_to_absolute_time(fake_clock: FakeClock) -> None:
    target = datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC)
    fake_clock.set(target)
    assert fake_clock.now() == target


@pytest.mark.requirement("L3-RUN-024")
def test_advance_adds_positive_delta(fake_clock: FakeClock) -> None:
    before = fake_clock.now()
    fake_clock.advance(timedelta(seconds=30))
    assert (fake_clock.now() - before).total_seconds() == 30.0


@pytest.mark.requirement("L3-RUN-024")
def test_advance_accepts_negative_delta(fake_clock: FakeClock) -> None:
    """Negative deltas SHALL be permitted for clock-skew scenarios."""
    before = fake_clock.now()
    fake_clock.advance(timedelta(hours=-1))
    assert (fake_clock.now() - before) == timedelta(hours=-1)


@pytest.mark.requirement("L3-RUN-024")
def test_advance_by_zero_is_a_noop(fake_clock: FakeClock) -> None:
    before = fake_clock.now()
    fake_clock.advance(timedelta(0))
    assert fake_clock.now() == before


# -----------------------------------------------------------------------------
# Timezone normalization
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-024")
def test_set_normalizes_non_utc_to_utc(fake_clock: FakeClock) -> None:
    """A non-UTC tz-aware time SHALL be converted to UTC internally."""
    est = timezone(timedelta(hours=-5))
    fake_clock.set(datetime(2026, 4, 19, 7, 0, 0, tzinfo=est))
    assert fake_clock.now() == datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)


@pytest.mark.requirement("L3-RUN-024")
def test_init_normalizes_non_utc_to_utc() -> None:
    est = timezone(timedelta(hours=-5))
    clock = FakeClock(datetime(2026, 4, 19, 7, 0, 0, tzinfo=est))
    assert clock.now() == datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)


# -----------------------------------------------------------------------------
# Naive-datetime rejection
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-024")
def test_init_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FakeClock(datetime(2026, 4, 19, 12, 0, 0))


@pytest.mark.requirement("L3-RUN-024")
def test_set_rejects_naive_datetime(fake_clock: FakeClock) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        fake_clock.set(datetime(2026, 4, 19, 12, 0, 0))


# -----------------------------------------------------------------------------
# fake_clock_at_epoch fixture
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-024")
def test_fake_clock_at_epoch_starts_at_unix_epoch(
    fake_clock_at_epoch: FakeClock,
) -> None:
    assert fake_clock_at_epoch.now() == datetime(1970, 1, 1, 0, 0, 0, tzinfo=UTC)


# -----------------------------------------------------------------------------
# Integration: FakeClock feeds directly into iso_z without drama
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-024")
@pytest.mark.requirement("L3-RUN-025")
def test_fake_clock_output_flows_through_iso_z(fake_clock: FakeClock) -> None:
    """Composability smoke test: FakeClock -> iso_z produces a valid timestamp string."""
    fake_clock.set(datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC))
    formatted = iso_z(fake_clock.now())
    assert formatted == "2026-04-19T12:00:00Z"
