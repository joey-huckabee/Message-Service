"""Port: schedule a coroutine to run in the background without blocking.

The port exists to satisfy L2-RUN-013: ``FinalizeRun`` enqueues the
assembly-and-delivery workflow as an asyncio task and returns without
blocking on the workflow's completion.

A thin port (rather than direct :func:`asyncio.create_task`) buys two
things:

* **Testability**: use-case tests supply a mock that records the
  scheduled coroutine without actually running it, keeping tests
  deterministic and fast.
* **Graceful shutdown**: the concrete adapter maintains a set of
  in-flight tasks and lets the service ``await`` them during SIGTERM
  handling, bounded by
  ``observability.shutdown_grace_period_seconds``.

The scheduler does not own retry semantics. If the scheduled coroutine
raises, the adapter logs the exception and discards the task — the
workflow itself is responsible for writing a FAILED state transition
if recoverable errors were exhausted.

Requirement references
----------------------
L2-RUN-013 (FinalizeRun non-blocking)
L1-OBS-002 (graceful shutdown)
L3-OBS-019 through L3-OBS-024 (shutdown coordination)

ROADMAP
-------
* Durable scheduling via an outbox table (survives process restart)
  — deferred until multi-node deployment is in scope.
* Prioritised scheduling / backpressure — not needed at v1 throughput.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Coroutine
from typing import Any


class BackgroundTaskScheduler(ABC):
    """Abstract scheduler for fire-and-forget coroutines.

    Implementations MUST:

    * Return to the caller without awaiting the scheduled coroutine
      (non-blocking).
    * Retain a reference to the created task until it completes so it
      is not garbage-collected mid-flight (a classic asyncio trap).
    * On service shutdown, await all outstanding tasks up to the
      configured grace period; log and abandon any that exceed it.
    * Catch and log unhandled exceptions from scheduled coroutines —
      a background workflow raising must not crash the service.
    """

    @abstractmethod
    def schedule(self, coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> None:
        """Enqueue ``coro`` to run on the current event loop.

        Args:
            coro: The coroutine to schedule. Typically a use-case's
                ``execute()`` bound to a prepared command.
            name: Optional task name for logging and diagnostics. When
                omitted, the adapter derives a name from the coroutine
                itself (e.g., ``"AssembleAndDeliverUseCase.execute"``).

        Raises:
            RuntimeError: No running event loop, or the scheduler has
                been stopped during shutdown (no new tasks accepted
                after grace period begins).
        """


__all__ = ["BackgroundTaskScheduler"]
