"""Infrastructure loop driving the audit-log retention pruner.

Mirrors :class:`message_service.infrastructure.sweeper.loop.SweeperLoop`
and :class:`message_service.infrastructure.persistence.report_pruner_loop.ReportPrunerLoop`
in shape and discipline:

* Constructed once at bootstrap, NOT started inline. The CLI
  entrypoint calls :meth:`start` explicitly after both listeners
  bind their ports, matching the sweeper-loop and report-pruner
  ordering.
* Per-tick exceptions are caught and logged at this layer; the
  next tick proceeds normally. A single failed tick MUST NOT kill
  the loop because that would silently disable retention
  enforcement of the audit log.
* Stop-event-aware sleep so a long ``cleanup_interval_hours``
  (production default 24) doesn't make shutdown wait an entire
  interval.

Shutdown ordering note
----------------------
During service shutdown the bootstrap calls
``audit_log_pruner_loop.stop()`` → ``scheduler.begin_shutdown()`` →
``scheduler.await_all(timeout)``. The latter sees this loop's task
still running and cancels it; ``asyncio.CancelledError`` propagates
up through the sleep or the tick and we treat that as a normal
exit.

Requirement references
----------------------
L1-OBS-003 (append-only audit log + configurable retention)
L2-OBS-008 (retention enforced by daily cleanup task)
L2-OBS-009 (asyncio scheduling shared with the sweeper pattern)
L3-OBS-014 (24h cadence default)
L3-OBS-017 (asyncio.create_task + cancellation pattern; expressed
    via BackgroundTaskScheduler in v1)
L3-OBS-040 (no audit row for the prune action; logging only)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from message_service.application.ports.background_task_scheduler import (
        BackgroundTaskScheduler,
    )
    from message_service.application.use_cases.audit_log_pruner import (
        AuditLogPrunerUseCase,
    )

_log = structlog.get_logger(__name__)


class AuditLogPrunerLoop:
    """Periodic poller that drives the audit-log retention pruner.

    Attributes:
        pruner: The :class:`AuditLogPrunerUseCase` whose
            :meth:`run_once` is invoked each iteration.
        scheduler: The :class:`BackgroundTaskScheduler` the loop is
            scheduled on. Same scheduler used by ``sweeper_loop``
            and ``report_pruner_loop``; the L2-PERS-004 mutex
            serializes pruner UoWs with all other UoW openings so
            no additional coordination is needed at this layer.
        poll_interval_seconds: Delay between ticks, derived from
            ``config.observability.audit.cleanup_interval_hours``
            (L3-OBS-014). Default 24h is the production cadence;
            dev configs use shorter values for local iteration.
    """

    def __init__(
        self,
        *,
        pruner: AuditLogPrunerUseCase,
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
            _log.debug("audit_log_pruner_loop_start_noop_already_started")
            return
        self._scheduler.schedule(self._run(), name="audit_log_pruner_loop")
        self._started = True
        _log.info(
            "audit_log_pruner_loop_started",
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
                # shutdown when the interval is large (production
                # default is 86400 seconds at 24h cadence).
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
            _log.info("audit_log_pruner_loop_cancelled")
            raise

    async def _tick_once(self) -> None:
        """Run one pruner iteration. Never re-raise.

        A use-case exception (PersistenceError, etc.) is caught and
        logged here; the loop continues on the next tick. Matches
        the sweeper-loop and report-pruner-loop boundary-catch
        posture: the scheduling layer is responsible for keeping
        the periodic task alive across transient failures.
        """
        try:
            await self._pruner.run_once()
        except Exception:  # noqa: BLE001 — boundary catch: logged + loop continues next tick
            _log.error("audit_log_pruner_tick_failed", exc_info=True)
            return


__all__ = ["AuditLogPrunerLoop"]
