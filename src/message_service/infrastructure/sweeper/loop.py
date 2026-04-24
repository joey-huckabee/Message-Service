"""Infrastructure loop driving :class:`SweeperUseCase`.

This module owns the periodic-scheduling concerns that the use case
deliberately refuses: it picks how often to call :meth:`tick`, what
to log on errors, and how to increment the Prometheus outcome
counter. The use case stays pure business logic; the loop stays
pure scheduling.

The loop is started by calling :meth:`start` (which asks the
:class:`BackgroundTaskScheduler` to schedule the internal
coroutine) and stopped by :meth:`stop` (which flips an internal
event so the loop exits at the next iteration boundary).

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
    """Periodic poller that drives the sweeper use case.

    Attributes:
        use_case: The :class:`SweeperUseCase` whose :meth:`tick` is
            invoked each iteration.
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
        use_case: SweeperUseCase,
        scheduler: BackgroundTaskScheduler,
        poll_interval_seconds: int,
    ) -> None:
        """Bind to collaborators; does NOT start the loop.

        Call :meth:`start` to schedule the loop on the given
        ``scheduler``. Construction is pure assignment so the
        bootstrap can build the loop before the scheduler is ready
        to accept work.
        """
        self._use_case = use_case
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
        """Run one tick and update metrics; never re-raise."""
        try:
            result = await self._use_case.tick()
        except Exception:
            _SWEEPER_ITERATION_COUNTER.labels(outcome="sweeper_error").inc()
            _log.error("sweeper_tick_failed", exc_info=True)
            return

        outcome = "orphans_detected" if result.orphaned_count > 0 else "no_orphans_found"
        _SWEEPER_ITERATION_COUNTER.labels(outcome=outcome).inc()

        if result.orphaned_count > 0:
            _log.info(
                "sweeper_tick_completed",
                outcome=outcome,
                orphaned_count=result.orphaned_count,
                dispatched_actions=result.dispatched_actions,
                handler_failures=result.handler_failures,
            )
        else:
            _log.debug("sweeper_tick_completed", outcome=outcome)


__all__ = ["SweeperLoop"]
