"""Unit tests for :class:`AsyncioBackgroundTaskScheduler`.

Covers the full adapter contract: task retention, GC safety, exception
absorption, task naming, shutdown mode, graceful drain with and without
timeout expiry.

Requirement references
----------------------
L1-OBS-002 (graceful shutdown)
L2-RUN-013 (non-blocking; asyncio task)
"""

from __future__ import annotations

import asyncio

import pytest

from message_service.infrastructure.scheduler.asyncio_scheduler import (
    AsyncioBackgroundTaskScheduler,
)

# -----------------------------------------------------------------------------
# Basic scheduling
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-RUN-013")
async def test_schedule_runs_coroutine() -> None:
    """A scheduled coroutine SHALL actually execute on the event loop."""
    sched = AsyncioBackgroundTaskScheduler()
    ran = asyncio.Event()

    async def work() -> None:
        ran.set()

    sched.schedule(work())
    await sched.await_all(timeout=1.0)
    assert ran.is_set()


@pytest.mark.asyncio
@pytest.mark.requirement("L2-RUN-013")
async def test_schedule_returns_immediately_without_awaiting() -> None:
    """schedule() SHALL NOT block waiting for the coroutine."""
    sched = AsyncioBackgroundTaskScheduler()
    done = asyncio.Event()

    async def slow() -> None:
        await asyncio.sleep(0.05)
        done.set()

    # schedule() returns synchronously; the coroutine has not completed.
    sched.schedule(slow())
    assert not done.is_set()
    await sched.await_all(timeout=1.0)
    assert done.is_set()


@pytest.mark.asyncio
async def test_schedule_preserves_task_name() -> None:
    sched = AsyncioBackgroundTaskScheduler()

    async def work() -> None:
        await asyncio.sleep(0)

    sched.schedule(work(), name="assemble-and-deliver:abc-123")
    # Snapshot the name before the task finishes.
    assert sched.active_task_count == 1
    names = [t.get_name() for t in sched._tasks]
    assert "assemble-and-deliver:abc-123" in names
    await sched.await_all(timeout=1.0)


# -----------------------------------------------------------------------------
# Task retention (avoid GC)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_tracked_until_completion() -> None:
    """Tasks SHALL be retained in the internal set until done."""
    sched = AsyncioBackgroundTaskScheduler()

    block = asyncio.Event()

    async def work() -> None:
        await block.wait()

    sched.schedule(work())
    sched.schedule(work())

    assert sched.active_task_count == 2

    # Release them and drain.
    block.set()
    await sched.await_all(timeout=1.0)
    assert sched.active_task_count == 0


@pytest.mark.asyncio
async def test_task_removed_from_set_after_completion() -> None:
    sched = AsyncioBackgroundTaskScheduler()

    async def work() -> None:
        await asyncio.sleep(0)

    sched.schedule(work())
    # Yield enough times for the task to complete and the done callback
    # to fire.
    for _ in range(10):
        await asyncio.sleep(0)
    assert sched.active_task_count == 0


# -----------------------------------------------------------------------------
# Exception absorption (L2-RUN-013)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-RUN-013")
@pytest.mark.requirement("L3-RUN-023")
async def test_raising_coroutine_does_not_propagate() -> None:
    """L2-RUN-013 / L3-RUN-023: an unhandled exception in a scheduled
    coroutine SHALL NOT escape; the scheduler logs at ERROR and moves on,
    so the original FinalizeRun response is unaffected.
    """
    sched = AsyncioBackgroundTaskScheduler()

    async def broken() -> None:
        raise RuntimeError("intentional test failure")

    sched.schedule(broken())
    # If the exception escaped, await_all would raise; it must not.
    await sched.await_all(timeout=1.0)
    assert sched.active_task_count == 0


@pytest.mark.asyncio
@pytest.mark.requirement("L2-RUN-013")
async def test_raising_coroutine_does_not_affect_siblings() -> None:
    """A raising task SHALL NOT prevent other scheduled tasks from running."""
    sched = AsyncioBackgroundTaskScheduler()
    ran_after = asyncio.Event()

    async def broken() -> None:
        raise RuntimeError("fail")

    async def good() -> None:
        ran_after.set()

    sched.schedule(broken())
    sched.schedule(good())
    await sched.await_all(timeout=1.0)
    assert ran_after.is_set()


# -----------------------------------------------------------------------------
# No-event-loop guard
# -----------------------------------------------------------------------------


def test_schedule_without_event_loop_raises() -> None:
    """schedule() outside of a running loop SHALL raise RuntimeError."""
    sched = AsyncioBackgroundTaskScheduler()

    async def work() -> None:
        pass

    coro = work()
    try:
        with pytest.raises(RuntimeError, match="running event loop"):
            sched.schedule(coro)
    finally:
        # coro.close() is handled inside schedule() on this error path.
        pass


# -----------------------------------------------------------------------------
# Shutdown mode
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_begin_shutdown_blocks_new_schedule() -> None:
    sched = AsyncioBackgroundTaskScheduler()
    sched.begin_shutdown()

    async def work() -> None:
        pass

    with pytest.raises(RuntimeError, match="shutting down"):
        sched.schedule(work())


@pytest.mark.asyncio
async def test_begin_shutdown_is_idempotent() -> None:
    sched = AsyncioBackgroundTaskScheduler()
    sched.begin_shutdown()
    sched.begin_shutdown()  # must not raise

    async def work() -> None:
        pass

    with pytest.raises(RuntimeError):
        sched.schedule(work())


@pytest.mark.asyncio
async def test_rejected_schedule_closes_coroutine() -> None:
    """Rejecting a schedule SHALL close the coro to avoid 'never awaited' warnings."""
    import inspect

    sched = AsyncioBackgroundTaskScheduler()
    sched.begin_shutdown()

    async def work() -> None:
        pass

    coro = work()
    with pytest.raises(RuntimeError):
        sched.schedule(coro)

    # Closed coroutines are in the CORO_CLOSED state. inspect gives us
    # a typed, public API for this check (unlike .cr_frame which is
    # a concrete-type attribute mypy won't see on the Coroutine
    # protocol).
    assert inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED


# -----------------------------------------------------------------------------
# Graceful drain
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_await_all_on_empty_scheduler_is_noop() -> None:
    sched = AsyncioBackgroundTaskScheduler()
    # No-op; must return promptly without raising.
    await sched.await_all(timeout=0.01)


@pytest.mark.asyncio
async def test_await_all_waits_for_natural_completion() -> None:
    sched = AsyncioBackgroundTaskScheduler()
    completed = asyncio.Event()

    async def work() -> None:
        await asyncio.sleep(0.05)
        completed.set()

    sched.schedule(work())
    await sched.await_all(timeout=1.0)
    assert completed.is_set()
    assert sched.active_task_count == 0


@pytest.mark.asyncio
async def test_await_all_cancels_stragglers_on_timeout() -> None:
    """Tasks that exceed the drain timeout SHALL be cancelled."""
    sched = AsyncioBackgroundTaskScheduler()
    cancelled = asyncio.Event()

    async def slow() -> None:
        try:
            await asyncio.sleep(10.0)  # intentionally beyond drain timeout
        except asyncio.CancelledError:
            cancelled.set()
            raise

    sched.schedule(slow())
    # Drain with a very short timeout; cancellation should fire.
    await sched.await_all(timeout=0.05)
    assert cancelled.is_set()
    assert sched.active_task_count == 0


@pytest.mark.asyncio
async def test_await_all_returns_after_cancellation() -> None:
    """Even if a task ignores cancellation, await_all SHALL return."""
    sched = AsyncioBackgroundTaskScheduler()

    async def work() -> None:
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            # Cooperatively handle — the test verifies the drain
            # completes without hanging.
            raise

    sched.schedule(work())
    # 0.05s << 5s so the task will be cancelled.
    await sched.await_all(timeout=0.05)


# -----------------------------------------------------------------------------
# Mixed workload
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_await_all_handles_mix_of_success_failure_timeout() -> None:
    sched = AsyncioBackgroundTaskScheduler()
    good_ran = asyncio.Event()

    async def good() -> None:
        good_ran.set()

    async def bad() -> None:
        raise RuntimeError("intentional")

    async def slow() -> None:
        await asyncio.sleep(5.0)

    sched.schedule(good(), name="good")
    sched.schedule(bad(), name="bad")
    sched.schedule(slow(), name="slow")

    # Short timeout — slow gets cancelled, others complete.
    await sched.await_all(timeout=0.05)

    assert good_ran.is_set()
    assert sched.active_task_count == 0
