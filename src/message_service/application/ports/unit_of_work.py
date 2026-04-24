"""Port: transactional scope for coordinated multi-repository writes.

The :class:`UnitOfWork` port groups :class:`RunRepository`,
:class:`StageRepository`, and :class:`AuditLog` into a single
transactional boundary so that use cases can make multi-entity writes
atomically (L3-RUN-004) with audit-first ordering preserved
(L3-RUN-026).

Usage in a use case::

    async def execute(self, cmd: BeginRunCommand) -> RunId:
        # ... validation happens outside the UoW ...

        async with self.uow_factory() as uow:
            await uow.audit_log.record(begin_event)  # audit first
            await uow.run_repo.save(run)
            for stage in initial_stages:
                await uow.stage_repo.save(stage)
            # Implicit commit on clean exit.
        return run.run_id

Clean exit commits the transaction. Any exception during the block
rolls back; no partial state persists.

Repository references exposed by the UoW are **scoped to this
transaction**. They share a connection (SQLite) or a session (future
back-ends) with the UoW itself and with each other. Do NOT use them
outside the ``async with`` block.

Design notes
------------
- Separate :meth:`commit` and :meth:`rollback` are exposed for use
  cases that need explicit control (rare). The ``__aexit__`` default is
  commit-on-clean-exit / rollback-on-exception.
- A use case that only *reads* does not need the UoW; it can accept a
  plain :class:`RunRepository` directly. The UoW is for coordinated
  writes.
- The port references subsystem port types (``RunRepository``,
  ``StageRepository``, ``AuditLog``) but does not import their concrete
  implementations. Adapters provide a concrete UoW that bundles
  concrete adapter instances.

Requirement references
----------------------
L1-PERS-003 (repository pattern)
L2-RUN-003 (persistence in single transaction with audit)
L3-RUN-004, L3-RUN-026, L3-RUN-027
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Self

from message_service.application.ports.audit_log import AuditLog
from message_service.application.ports.run_repository import RunRepository
from message_service.application.ports.stage_repository import StageRepository
from message_service.application.ports.subscription_repository import SubscriptionRepository
from message_service.application.ports.sweeper_action_repository import (
    SweeperActionRepository,
)


class UnitOfWork(ABC):
    """Abstract transactional scope.

    Implementations MUST:

    * Begin a transaction in :meth:`__aenter__`.
    * Commit on clean exit from :meth:`__aexit__` (when ``exc_type`` is
      ``None``).
    * Roll back when :meth:`__aexit__` is called with an exception, or
      when :meth:`rollback` is invoked explicitly.
    * Ensure :attr:`run_repo`, :attr:`stage_repo`,
      :attr:`subscription_repo`, and :attr:`audit_log` share the same
      underlying transaction so writes through any of them commit or
      roll back together.

    Attributes:
        run_repo: :class:`RunRepository` scoped to this transaction.
        stage_repo: :class:`StageRepository` scoped to this transaction.
        subscription_repo: :class:`SubscriptionRepository` scoped to
            this transaction. Read by
            :class:`AssembleAndDeliverUseCase` for recipient resolution
            (L1-SUB-004).
        audit_log: :class:`AuditLog` scoped to this transaction.
        sweeper_action_repo: :class:`SweeperActionRepository` scoped to
            this transaction. Used by :class:`SweeperUseCase` to enqueue
            disposition outbox rows in the same transaction as the
            ORPHANED transition (L2-SWEEP-006).
    """

    run_repo: RunRepository
    stage_repo: StageRepository
    subscription_repo: SubscriptionRepository
    audit_log: AuditLog
    sweeper_action_repo: SweeperActionRepository

    @abstractmethod
    async def __aenter__(self) -> Self:
        """Begin the transaction and return ``self``.

        Returns:
            The unit-of-work instance, with repository attributes ready
            to use. Typically bound via ``async with factory() as uow:``.

        Raises:
            PersistenceError: If the underlying connection cannot be
                acquired or the transaction cannot be started.
        """

    @abstractmethod
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Commit on clean exit; roll back if the block raised.

        Args:
            exc_type: Exception class if the ``async with`` block
                raised; ``None`` otherwise.
            exc_val: The exception instance.
            exc_tb: The traceback.

        Raises:
            PersistenceError: If commit fails. Rollback failures are
                logged but not raised (the original exception, if any,
                takes precedence).
        """

    @abstractmethod
    async def commit(self) -> None:
        """Explicitly commit the transaction.

        Rarely needed — the default ``__aexit__`` behavior covers most
        use cases. Calling :meth:`commit` inside the block ends the
        transaction early; subsequent writes through the scoped repos
        are undefined.

        Raises:
            PersistenceError: Commit failed.
        """

    @abstractmethod
    async def rollback(self) -> None:
        """Explicitly roll back the transaction.

        Calling :meth:`rollback` inside the block aborts the
        transaction; subsequent writes through the scoped repos are
        undefined. Exiting the block then completes without a second
        rollback.

        Raises:
            PersistenceError: Rollback failed.
        """


__all__ = ["UnitOfWork"]
