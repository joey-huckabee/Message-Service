"""Infrastructure loop driving the sweeper + outbox dispatcher.

This module owns the periodic-scheduling concerns that the use cases
deliberately refuse: how often to call them, what to log on errors,
and how to increment the Prometheus outcome counter. The use cases
stay pure business logic; the loop stays pure scheduling.

Each tick runs the sweeper first (which may enqueue ``sweeper_actions``
rows inside the orphan transaction) and then drains the outbox via the
dispatcher. Pairing them in one tick:

* keeps freshly-enqueued rows from sitting idle for a full polling
  interval before being dispatched, and
* ensures any leftover pending rows from a prior process lifetime
  (crash recovery) drain on the very first tick after restart.

A dispatcher exception is caught and logged at this layer and does
not abort the next sweeper tick — the two are independent units of
work, and a transient handler-side problem must not stall orphan
detection.

Shutdown ordering note
----------------------
During service shutdown the bootstrap calls
``scheduler.begin_shutdown()`` → ``scheduler.await_all(timeout)``.
The latter sees this loop's task still running and cancels it;
``asyncio.CancelledError`` propagates up through the sleep or the
tick and we treat that as a normal exit. No shutdown logging is
needed at this layer — the bootstrap already emits
``shutdown_start`` / ``shutdown_complete``.

Requirement references
----------------------
L1-SWEEP-001 (background asyncio task)
L2-SWEEP-001 (create_task, cancelled on shutdown)
L2-SWEEP-002 (asyncio.sleep for polling, no APScheduler/Celery)
L2-SWEEP-003 (Prometheus counter with outcome label)
L2-SWEEP-006 (exactly-once via the outbox; the loop is what makes the
dispatch eventually happen after the sweeper enqueues)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog
from prometheus_client import Counter

if TYPE_CHECKING:
    from message_service.application.ports.background_task_scheduler import (
        BackgroundTaskScheduler,
    )
    from message_service.application.use_cases.sweeper import SweeperUseCase
    from message_service.application.use_cases.sweeper_action_dispatcher import (
        SweeperActionDispatcherUseCase,
    )

_log = structlog.get_logger(__name__)

# Single shared Counter — Prometheus discourages re-declaration in the
# same registry. Name and label values are pinned by L3-SWEEP-004:
# outcomes are one of {no_orphans_found, orphans_detected, sweeper_error}.
_SWEEPER_ITERATION_COUNTER = Counter(
    "message_service_sweeper_iterations_total",
    "Count of sweeper iterations by outcome.",
    labelnames=["outcome"],
)


class SweeperLoop:
    """Periodic poller that drives the sweeper + dispatcher pair.

    Attributes:
        sweeper: The :class:`SweeperUseCase` whose :meth:`tick` is
            invoked first each iteration.
        dispatcher: The :class:`SweeperActionDispatcherUseCase` whose
            :meth:`dispatch_pending` drains the outbox after each
            sweeper tick.
        scheduler: The :class:`BackgroundTaskScheduler` the loop is
            scheduled on.
        poll_interval_seconds: Delay between ticks. A larger value
            reduces load; a smaller value reduces worst-case
            orphan-detection latency. L2-SWEEP-002 pins the mechanism
            to ``asyncio.sleep`` so the value is honored precisely.
    """

    def __init__(
        self,
        *,
        sweeper: SweeperUseCase,
        dispatcher: SweeperActionDispatcherUseCase,
        scheduler: BackgroundTaskScheduler,
        poll_interval_seconds: int,
    ) -> None:
        """Bind to collaborators; does NOT start the loop.

        Call :meth:`start` to schedule the loop on the given
        ``scheduler``. Construction is pure assignment so the
        bootstrap can build the loop before the scheduler is ready
        to accept work.
        """
        self._sweeper = sweeper
        self._dispatcher = dispatcher
        self._scheduler = scheduler
        self._poll_interval = poll_interval_seconds
        self._stop_event = asyncio.Event()
        self._started = False

    def start(self) -> None:
        """Schedule the loop coroutine. Idempotent — second call is a no-op.

        Raises:
            RuntimeError: The scheduler is already in shutdown mode
                (bootstrap error — start should be called before any
                shutdown).
        """
        if self._started:
            _log.debug("sweeper_loop_start_noop_already_started")
            return
        self._scheduler.schedule(self._run(), name="sweeper_loop")
        self._started = True
        _log.info(
            "sweeper_loop_started",
            poll_interval_seconds=self._poll_interval,
        )

    def stop(self) -> None:
        """Signal the loop to exit at the next iteration boundary.

        The loop may still be mid-tick when this returns; callers that
        need to wait for actual completion should rely on the
        scheduler's ``await_all`` during shutdown.
        """
        self._stop_event.set()

    async def _run(self) -> None:
        """Inner loop coroutine: tick → sleep → check stop-event → repeat."""
        try:
            while not self._stop_event.is_set():
                await self._tick_once()
                # Sleep either for the full interval or until stop is
                # signaled, whichever comes first — prevents a slow
                # shutdown when the interval is large.
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._poll_interval,
                    )
                    # Event was set during wait — exit loop.
                    break
                except TimeoutError:
                    # Normal path: interval elapsed, continue.
                    continue
        except asyncio.CancelledError:
            # Bootstrap cancellation during shutdown is expected.
            _log.info("sweeper_loop_cancelled")
            raise

    async def _tick_once(self) -> None:
        """Run one sweeper tick + one dispatcher drain. Never re-raise.

        The two phases are independent: a dispatcher exception does
        not invalidate the sweeper's work this tick, and vice versa.
        Both surface in their own log entry.
        """
        try:
            sweeper_result = await self._sweeper.tick()
        except Exception:
            _SWEEPER_ITERATION_COUNTER.labels(outcome="sweeper_error").inc()
            _log.error("sweeper_tick_failed", exc_info=True)
            # Still attempt to drain whatever's already in the outbox
            # — a sweeper failure SHOULD NOT block dispatch of rows
            # enqueued on prior ticks.
            await self._dispatch_drain()
            return

        outcome = "orphans_detected" if sweeper_result.orphaned_count > 0 else "no_orphans_found"
        _SWEEPER_ITERATION_COUNTER.labels(outcome=outcome).inc()

        if sweeper_result.orphaned_count > 0:
            _log.info(
                "sweeper_tick_completed",
                outcome=outcome,
                orphaned_count=sweeper_result.orphaned_count,
                enqueued_actions=sweeper_result.enqueued_actions,
            )
        else:
            _log.debug("sweeper_tick_completed", outcome=outcome)

        await self._dispatch_drain()

    async def _dispatch_drain(self) -> None:
        """Drain the outbox; log; never re-raise."""
        try:
            result = await self._dispatcher.dispatch_pending()
        except Exception:
            _log.error("dispatcher_drain_failed", exc_info=True)
            return

        if result.claimed > 0:
            _log.info(
                "dispatcher_drain_completed",
                claimed=result.claimed,
                succeeded=result.succeeded,
                failed=result.failed,
            )
        else:
            _log.debug("dispatcher_drain_completed", claimed=0)


__all__ = ["SweeperLoop"]
