"""Unit tests for :class:`SweeperLoop`.

The tests use a stub :class:`SweeperUseCase`-shaped object rather than
the real one because the loop's contract is scheduling + error
absorption + metrics, not business logic. The real use case is
exercised through its own unit tests and the sweeper integration test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from prometheus_client import CollectorRegistry

from message_service.application.use_cases.sweeper import TickResult
from message_service.infrastructure.scheduler.asyncio_scheduler import (
    AsyncioBackgroundTaskScheduler,
)
from message_service.infrastructure.sweeper.loop import (
    _SWEEPER_TICK_COUNTER,
    SweeperLoop,
)

# -----------------------------------------------------------------------------
# Test double
# -----------------------------------------------------------------------------


@dataclass
class _StubUseCase:
    """Minimal stand-in for :class:`SweeperUseCase` exposing just ``tick``.

    Records the number of times ``tick`` is called; per-call result
    and optional exception are driven by the test so we can drive
    each branch of the loop.
    """

    call_count: int = 0
    next_result: TickResult | None = None
    next_exception: Exception | None = None

    async def tick(self) -> TickResult:
        self.call_count += 1
        if self.next_exception is not None:
            exc = self.next_exception
            self.next_exception = None
            raise exc
        return self.next_result or TickResult(0, 0, 0)


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
async def scheduler() -> AsyncIterator[AsyncioBackgroundTaskScheduler]:
    s = AsyncioBackgroundTaskScheduler()
    try:
        yield s
    finally:
        s.begin_shutdown()
        await s.await_all(timeout=2.0)


@pytest.fixture
def stub_use_case() -> _StubUseCase:
    return _StubUseCase()


# -----------------------------------------------------------------------------
# Start + stop
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_schedules_exactly_one_task(
    stub_use_case: _StubUseCase,
    scheduler: AsyncioBackgroundTaskScheduler,
) -> None:
    loop = SweeperLoop(
        use_case=stub_use_case,  # type: ignore[arg-type]
        scheduler=scheduler,
        poll_interval_seconds=1,
    )
    loop.start()
    assert scheduler.active_task_count == 1
    loop.stop()


@pytest.mark.asyncio
async def test_start_is_idempotent(
    stub_use_case: _StubUseCase,
    scheduler: AsyncioBackgroundTaskScheduler,
) -> None:
    loop = SweeperLoop(
        use_case=stub_use_case,  # type: ignore[arg-type]
        scheduler=scheduler,
        poll_interval_seconds=1,
    )
    loop.start()
    loop.start()  # must not raise or schedule a second task
    assert scheduler.active_task_count == 1
    loop.stop()


@pytest.mark.asyncio
async def test_stop_exits_loop_cleanly_during_sleep(
    stub_use_case: _StubUseCase,
    scheduler: AsyncioBackgroundTaskScheduler,
) -> None:
    """A long poll interval + stop() signal SHALL exit the loop in <1 second
    rather than waiting out the interval."""
    loop = SweeperLoop(
        use_case=stub_use_case,  # type: ignore[arg-type]
        scheduler=scheduler,
        poll_interval_seconds=3600,  # one hour
    )
    loop.start()
    # Let the first tick happen.
    await asyncio.sleep(0.05)
    assert stub_use_case.call_count >= 1

    # Signal stop; the loop should exit during its wait_for on stop_event.
    loop.stop()

    # Drain via the scheduler; this should return quickly.
    scheduler.begin_shutdown()
    start = asyncio.get_running_loop().time()
    await scheduler.await_all(timeout=2.0)
    elapsed = asyncio.get_running_loop().time() - start
    # If the loop waited out the poll interval, this would be ~3600s.
    assert elapsed < 1.0


# -----------------------------------------------------------------------------
# Tick invocation
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_ticks_multiple_times_with_short_interval(
    stub_use_case: _StubUseCase,
    scheduler: AsyncioBackgroundTaskScheduler,
) -> None:
    """A short poll interval SHALL produce multiple ticks over a time window."""
    loop = SweeperLoop(
        use_case=stub_use_case,  # type: ignore[arg-type]
        scheduler=scheduler,
        poll_interval_seconds=0,
    )
    loop.start()
    # 100ms gives plenty of event-loop iterations even with poll_interval=0.
    await asyncio.sleep(0.1)
    loop.stop()

    # With zero interval we should see many ticks; 3 is a very
    # conservative lower bound tolerant of scheduling jitter.
    assert stub_use_case.call_count >= 3


# -----------------------------------------------------------------------------
# Error absorption
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_exception_does_not_crash_loop(
    stub_use_case: _StubUseCase,
    scheduler: AsyncioBackgroundTaskScheduler,
) -> None:
    """An exception raised by the use case SHALL be caught; loop continues."""
    stub_use_case.next_exception = RuntimeError("boom")

    loop = SweeperLoop(
        use_case=stub_use_case,  # type: ignore[arg-type]
        scheduler=scheduler,
        poll_interval_seconds=0,
    )
    loop.start()
    await asyncio.sleep(0.1)
    loop.stop()

    # The first tick raised; subsequent ticks proceed normally.
    assert stub_use_case.call_count >= 2


# -----------------------------------------------------------------------------
# Prometheus counter outcomes
# -----------------------------------------------------------------------------


def _counter_value(outcome: str) -> float:
    """Read the labeled counter's accumulated value."""
    for metric in _SWEEPER_TICK_COUNTER.collect():
        for sample in metric.samples:
            if sample.labels.get("outcome") == outcome and sample.name.endswith("_total"):
                return sample.value
    return 0.0


@pytest.mark.asyncio
@pytest.mark.requirement("L2-SWEEP-003")
async def test_no_orphans_tick_increments_correct_label(
    stub_use_case: _StubUseCase,
    scheduler: AsyncioBackgroundTaskScheduler,
) -> None:
    before = _counter_value("no_orphans_found")

    stub_use_case.next_result = TickResult(0, 0, 0)
    loop = SweeperLoop(
        use_case=stub_use_case,  # type: ignore[arg-type]
        scheduler=scheduler,
        poll_interval_seconds=3600,
    )
    loop.start()
    await asyncio.sleep(0.05)
    loop.stop()

    after = _counter_value("no_orphans_found")
    assert after > before


@pytest.mark.asyncio
@pytest.mark.requirement("L2-SWEEP-003")
async def test_orphans_detected_tick_increments_correct_label(
    stub_use_case: _StubUseCase,
    scheduler: AsyncioBackgroundTaskScheduler,
) -> None:
    before = _counter_value("orphans_detected")

    stub_use_case.next_result = TickResult(3, 6, 0)
    loop = SweeperLoop(
        use_case=stub_use_case,  # type: ignore[arg-type]
        scheduler=scheduler,
        poll_interval_seconds=3600,
    )
    loop.start()
    await asyncio.sleep(0.05)
    loop.stop()

    after = _counter_value("orphans_detected")
    assert after > before


@pytest.mark.asyncio
@pytest.mark.requirement("L2-SWEEP-003")
async def test_sweeper_error_tick_increments_correct_label(
    stub_use_case: _StubUseCase,
    scheduler: AsyncioBackgroundTaskScheduler,
) -> None:
    before = _counter_value("sweeper_error")

    stub_use_case.next_exception = RuntimeError("database down")
    loop = SweeperLoop(
        use_case=stub_use_case,  # type: ignore[arg-type]
        scheduler=scheduler,
        poll_interval_seconds=3600,
    )
    loop.start()
    await asyncio.sleep(0.05)
    loop.stop()

    after = _counter_value("sweeper_error")
    assert after > before


# -----------------------------------------------------------------------------
# Prometheus registry hygiene
# -----------------------------------------------------------------------------


def test_counter_exists_in_default_registry() -> None:
    """The module-level Counter SHALL be registered on the default registry
    so a metrics endpoint can scrape it without extra wiring."""
    from prometheus_client import Counter

    # A duplicate-name registration against the default registry
    # raises ValueError; that proves the counter is already present.
    with pytest.raises(ValueError, match="Duplicated"):
        Counter(
            "message_service_sweeper_ticks_total",
            "duplicate",
            labelnames=["outcome"],
        )


def test_counter_registry_parameterizable_for_tests() -> None:
    """Isolated per-test registry usage remains possible for future tests."""
    from prometheus_client import Counter

    reg = CollectorRegistry()
    # This SHALL succeed: a per-test registry has no conflict with
    # the module-level default-registry counter.
    Counter(
        "isolated_counter",
        "test",
        labelnames=["outcome"],
        registry=reg,
    )
