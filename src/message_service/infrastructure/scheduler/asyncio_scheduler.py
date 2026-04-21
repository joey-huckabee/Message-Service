"""Concrete :class:`BackgroundTaskScheduler` using :func:`asyncio.create_task`.

Responsibilities beyond the port contract:

* **Task retention** — scheduled tasks are kept in an internal set so
  they are not garbage-collected mid-flight (a classic asyncio trap
  the
  :class:`~message_service.application.ports.background_task_scheduler.BackgroundTaskScheduler`
  docstring explicitly flags).
* **Exception absorption** — unhandled exceptions from scheduled
  coroutines are caught at the task-done callback and logged via
  :mod:`structlog`. A raising workflow does not crash the service.
* **Shutdown coordination** — :meth:`await_all` awaits all in-flight
  tasks up to a caller-provided timeout, cancels any that exceed it,
  and awaits cancellations. After :meth:`begin_shutdown` is called,
  further :meth:`schedule` calls raise :class:`RuntimeError` per the
  port contract.

Shutdown is a two-phase process from the service bootstrap's
perspective:

1. SIGTERM handler calls :meth:`begin_shutdown` — this "closes the
   door" so no more tasks can be scheduled.
2. SIGTERM handler then calls ``await scheduler.await_all(timeout=...)``
   — this drains in-flight tasks with the configured grace period
   (``service.shutdown_grace_period_seconds``).

:meth:`await_all` is safe to call without first calling
:meth:`begin_shutdown` (for tests), but in production the
``begin_shutdown`` step prevents a racy "new task scheduled during
shutdown" condition.

Requirement references
----------------------
L1-OBS-002 (graceful shutdown)
L2-RUN-013 (non-blocking; asyncio task)
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import structlog

from message_service.application.ports.background_task_scheduler import (
    BackgroundTaskScheduler,
)

_log = structlog.get_logger(__name__)


class AsyncioBackgroundTaskScheduler(BackgroundTaskScheduler):
    """Schedules coroutines via :func:`asyncio.create_task` with lifecycle tracking.

    Instances are typically constructed once at service start and
    passed to every use case that needs to schedule background work
    (currently only
    :class:`~message_service.application.use_cases.finalize_run.FinalizeRunUseCase`).

    Attributes:
        active_task_count: Number of in-flight scheduled tasks. Useful
            for metrics and testing.
    """

    def __init__(self) -> None:
        """Construct an empty scheduler.

        The scheduler starts in the *open* state — :meth:`schedule`
        accepts new work. The event loop must be running when
        :meth:`schedule` is called; construction itself does not
        require one.
        """
        self._tasks: set[asyncio.Task[Any]] = set()
        self._shutting_down: bool = False

    # -- Port contract ---------------------------------------------------

    def schedule(  # noqa: D102 — documented on the port
        self, coro: Coroutine[Any, Any, Any], *, name: str | None = None
    ) -> None:
        if self._shutting_down:
            # Close the coroutine cleanly so pytest/asyncio does not
            # warn about "never awaited".
            coro.close()
            raise RuntimeError("scheduler is shutting down; no new tasks accepted")

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            # No running event loop. Close the coroutine to avoid a
            # "never awaited" warning, then surface the error.
            coro.close()
            raise RuntimeError(
                "BackgroundTaskScheduler.schedule requires a running event loop"
            ) from exc

        task = loop.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

    # -- Inspection ------------------------------------------------------

    @property
    def active_task_count(self) -> int:
        """Number of currently in-flight tasks.

        Useful for metrics (``scheduler.active_tasks`` gauge) and for
        tests that need to assert the scheduler is quiescent.
        """
        return len(self._tasks)

    # -- Shutdown --------------------------------------------------------

    def begin_shutdown(self) -> None:
        """Reject further :meth:`schedule` calls.

        Idempotent: calling twice is a no-op. The typical call site is
        a SIGTERM handler that flips this flag, then awaits
        :meth:`await_all` with the configured grace period.
        """
        self._shutting_down = True

    async def await_all(self, timeout: float) -> None:
        """Await in-flight tasks up to ``timeout`` seconds, then cancel stragglers.

        Args:
            timeout: Wall-clock seconds to wait for tasks to finish
                naturally before cancelling them. Typically
                ``config.service.shutdown_grace_period_seconds``.

        Upon timeout, every still-running task is cancelled and awaited
        to completion (cancellation is cooperative; tasks that ignore
        :class:`asyncio.CancelledError` stay running but we do not
        wait further). Cancellations and natural completions are both
        logged.

        Safe to call on an empty scheduler (no-op).
        """
        if not self._tasks:
            return

        # Take a snapshot — self._tasks is mutated by the done callbacks
        # as tasks complete under us.
        in_flight = list(self._tasks)
        _log.info(
            "scheduler_awaiting_inflight_tasks",
            count=len(in_flight),
            timeout_seconds=timeout,
        )

        done, pending = await asyncio.wait(in_flight, timeout=timeout)
        _log.info(
            "scheduler_drain_status",
            completed=len(done),
            pending_at_timeout=len(pending),
        )

        if pending:
            for task in pending:
                task.cancel()
            # Wait for cancellations to propagate. Use return_exceptions
            # to swallow the CancelledError that every cancelled task
            # raises; otherwise asyncio.gather re-raises the first one
            # and we lose visibility into the others.
            await asyncio.gather(*pending, return_exceptions=True)
            _log.warning(
                "scheduler_cancelled_timed_out_tasks",
                count=len(pending),
            )

    # -- Internals -------------------------------------------------------

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        """Drop the task from the retention set; log any unhandled exception.

        Called by asyncio when the task completes, whether by normal
        return, exception, or cancellation.
        """
        self._tasks.discard(task)

        # Cancellation is not an error; it is the expected outcome
        # when await_all times out.
        if task.cancelled():
            _log.info("scheduled_task_cancelled", task_name=task.get_name())
            return

        exc = task.exception()
        if exc is None:
            # Normal completion.
            return

        # Unhandled exception. Absorb per L2-RUN-013.
        _log.error(
            "scheduled_task_raised",
            task_name=task.get_name(),
            exception_class=type(exc).__name__,
            message=str(exc),
            exc_info=exc,
        )


__all__ = ["AsyncioBackgroundTaskScheduler"]
