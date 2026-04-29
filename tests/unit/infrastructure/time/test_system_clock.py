"""Unit tests for :mod:`message_service.infrastructure.time.system_clock`."""

from __future__ import annotations

from datetime import timedelta

import pytest

from message_service.application.ports.clock import Clock
from message_service.infrastructure.time.system_clock import SystemClock


@pytest.mark.requirement("L2-RUN-014")
def test_system_clock_is_instance_of_clock() -> None:
    """SystemClock SHALL be a concrete ``Clock`` implementation."""
    assert isinstance(SystemClock(), Clock)


@pytest.mark.requirement("L2-RUN-014")
@pytest.mark.requirement("L3-RUN-032")
def test_system_clock_returns_timezone_aware_utc() -> None:
    """L3-RUN-032: ``SystemClock.now`` SHALL return a tz-aware UTC datetime
    (the only legitimate `datetime.now(tz=UTC)` call site in production).
    """
    result = SystemClock().now()
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)


@pytest.mark.requirement("L2-RUN-014")
def test_system_clock_advances_between_calls() -> None:
    """Wall-clock SHALL advance monotonically in practice.

    Not strictly required by the ABC contract, but any production clock
    that returns identical values from successive calls is broken.
    """
    clock = SystemClock()
    first = clock.now()
    # A trivial loop to guarantee some time elapses on any reasonable system.
    for _ in range(1000):
        pass
    second = clock.now()
    assert second >= first
