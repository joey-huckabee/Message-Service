"""Infrastructure loop driving the rendered-report retention pruner.

This module owns the periodic-scheduling concerns the use case
deliberately refuses: how often to call it, what to log on errors,
and how to coordinate clean shutdown. The use case stays pure
business logic; the loop stays pure scheduling.

Mirrors :class:`message_service.infrastructure.sweeper.loop.SweeperLoop`
in shape and discipline:

* Constructed once at bootstrap, NOT started inline. The CLI
  entrypoint calls :meth:`start` explicitly after both listeners
  bind their ports (per L3-PERS-030), matching the sweeper-loop
  ordering.
* Per-tick exceptions are caught and logged at this layer; the
  next tick proceeds normally. A single failed tick MUST NOT
  kill the loop because that would silently disable retention
  enforcement.
* Stop-event-aware sleep so a long ``prune_interval_seconds``
  doesn't make shutdown wait an entire interval.

Shutdown ordering note
----------------------
During service shutdown the bootstrap calls
``report_pruner_loop.stop()`` → ``scheduler.begin_shutdown()`` →
``scheduler.await_all(timeout)``. The latter sees this loop's task
still running and cancels it; ``asyncio.CancelledError`` propagates
up through the sleep or the tick and we treat that as a normal
exit.

Requirement references
----------------------
L1-PERS-004 (rendered-report retention)
L2-PERS-012 (pruner runs on BackgroundTaskScheduler at configurable
    cadence with per-tick bound)
L3-PERS-030 (lifecycle: started after gRPC bind, stopped in shutdown
    grace period)
L3-PERS-031 (per-tick algorithm — implemented by the use case;
    this loop just calls it)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from message_service.application.ports.background_task_scheduler import (
        BackgroundTaskScheduler,
    )
    from message_service.application.use_cases.report_pruner import (
        ReportPrunerUseCase,
    )

_log = structlog.get_logger(__name__)


class ReportPrunerLoop:
    """Periodic poller that drives the rendered-report retention pruner.

    Attributes:
        pruner: The :class:`ReportPrunerUseCase` whose
            :meth:`run_once` is invoked each iteration.
        scheduler: The :class:`BackgroundTaskScheduler` the loop is
            scheduled on. Same scheduler used by ``sweeper_loop``;
            the L2-PERS-004 mutex serializes pruner UoWs with all
            other UoW openings so no additional coordination is
            needed at this layer.
        poll_interval_seconds: Delay between ticks. Sourced from
            ``config.persistence.filesystem.prune_interval_seconds``
            (L3-PERS-029). Default 86400 (daily) is the production
            cadence; dev configs use shorter values for local
            iteration.
    """

    def __init__(
        self,
        *,
        pruner: ReportPrunerUseCase,
        scheduler: BackgroundTaskScheduler,
        poll_interval_seconds: int,
    ) -> None:
        """Bind to collaborators; does NOT start the loop.

        Call :meth:`start` to schedule the loop on the given
        ``scheduler``. Construction is pure assignment so the
        bootstrap can build the loop before the scheduler is ready
        to accept work.

        Raises:
            ValueError: ``poll_interval_seconds`` is not positive.
        """
        if poll_interval_seconds < 1:
            raise ValueError(f"poll_interval_seconds must be positive; got {poll_interval_seconds}")
        self._pruner = pruner
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
            _log.debug("report_pruner_loop_start_noop_already_started")
            return
        self._scheduler.schedule(self._run(), name="report_pruner_loop")
        self._started = True
        _log.info(
            "report_pruner_loop_started",
            poll_interval_seconds=self._poll_interval,
        )

    def stop(self) -> None:
        """Signal the loop to exit at the next iteration boundary.

        The loop may still be mid-tick when this returns; callers
        that need to wait for actual completion should rely on the
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
                # shutdown when the interval is large (the production
                # default is 86400 seconds).
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
            _log.info("report_pruner_loop_cancelled")
            raise

    async def _tick_once(self) -> None:
        """Run one pruner iteration. Never re-raise.

        A use-case exception (PersistenceError, OSError leaking from
        a deeper boundary, etc.) is caught and logged here; the loop
        continues on the next tick. This matches the sweeper-loop
        boundary-catch posture: the scheduling layer is responsible
        for keeping the periodic task alive across transient
        failures.
        """
        try:
            result = await self._pruner.run_once()
        except Exception:  # noqa: BLE001 — boundary catch: logged + loop continues next tick
            _log.error("report_pruner_tick_failed", exc_info=True)
            return

        # The use case already logs at INFO when work was done; only
        # log at DEBUG here for the no-op-tick case so production logs
        # don't fill with daily empty-tick entries.
        if result.runs_processed == 0:
            _log.debug(
                "report_pruner_tick_completed",
                runs_processed=0,
            )


__all__ = ["ReportPrunerLoop"]
