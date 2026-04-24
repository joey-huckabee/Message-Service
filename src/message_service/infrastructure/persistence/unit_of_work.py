"""Concrete :class:`UnitOfWork` for the SQLite backend.

This implementation shares a single :class:`aiosqlite.Connection`
across the service process (v1; a pool is on the ROADMAP). Each
``async with factory() as uow`` call begins a transaction on the
shared connection and commits or rolls back on exit.

Because the connection is shared, only one UoW may be active at a
time in the current process. The asyncio single-threaded event loop
naturally serialises this: as long as use cases use the UoW with
``async with`` (not bare) and do not start overlapping nested UoWs,
concurrent requests will queue at the :class:`BEGIN` boundary via the
``busy_timeout`` PRAGMA.

Repository attributes are set at :meth:`__aenter__` time so every
repo sees the same connection under the same transaction.
Implementation classes are injected via the factory so this module
does not depend on concrete repos (they live in their own modules,
added in Increment 11b).

Requirement references
----------------------
L1-PERS-003 (repository pattern)
L2-RUN-003 (persistence + audit in a single transaction)
L3-RUN-004 (atomic writes)
L3-RUN-026, L3-RUN-027 (audit-first ordering; enforced at the call
site, not here)
"""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Self

import aiosqlite
import structlog

from message_service.application.ports.audit_log import AuditLog
from message_service.application.ports.run_repository import RunRepository
from message_service.application.ports.stage_repository import StageRepository
from message_service.application.ports.subscription_repository import (
    SubscriptionRepository,
)
from message_service.application.ports.sweeper_action_repository import (
    SweeperActionRepository,
)
from message_service.application.ports.unit_of_work import UnitOfWork
from message_service.domain.errors import PersistenceError

_log = structlog.get_logger(__name__)


# Factory callables: given a connection (bound to this UoW's
# transaction), return a repo instance. The repo's writes go through
# that connection, so they participate in the UoW's transaction
# automatically.
RunRepoFactory = Callable[[aiosqlite.Connection], RunRepository]
StageRepoFactory = Callable[[aiosqlite.Connection], StageRepository]
SubscriptionRepoFactory = Callable[[aiosqlite.Connection], SubscriptionRepository]
AuditLogFactory = Callable[[aiosqlite.Connection], AuditLog]
SweeperActionRepoFactory = Callable[[aiosqlite.Connection], SweeperActionRepository]


class SqliteUnitOfWork(UnitOfWork):
    """Transactional scope for the SQLite backend.

    Do not construct directly; use :class:`SqliteUnitOfWorkFactory`
    which holds the shared connection and injects repo factories.
    """

    def __init__(
        self,
        *,
        conn: aiosqlite.Connection,
        run_repo_factory: RunRepoFactory,
        stage_repo_factory: StageRepoFactory,
        subscription_repo_factory: SubscriptionRepoFactory,
        audit_log_factory: AuditLogFactory,
        sweeper_action_repo_factory: SweeperActionRepoFactory,
    ) -> None:
        """Construct with a live connection and the five repo factories.

        Args:
            conn: Shared :class:`aiosqlite.Connection`. This UoW will
                issue ``BEGIN``/``COMMIT``/``ROLLBACK`` against it.
            run_repo_factory: Given the connection, produce a
                :class:`RunRepository` scoped to this UoW's transaction.
            stage_repo_factory: Same for :class:`StageRepository`.
            subscription_repo_factory: Same for
                :class:`SubscriptionRepository`.
            audit_log_factory: Same for :class:`AuditLog`.
            sweeper_action_repo_factory: Same for
                :class:`SweeperActionRepository`.
        """
        self._conn = conn
        self._run_repo_factory = run_repo_factory
        self._stage_repo_factory = stage_repo_factory
        self._subscription_repo_factory = subscription_repo_factory
        self._audit_log_factory = audit_log_factory
        self._sweeper_action_repo_factory = sweeper_action_repo_factory
        self._entered: bool = False
        self._finalized: bool = False

    # -- Context manager -------------------------------------------------

    async def __aenter__(self) -> Self:
        """Begin a transaction and bind the scoped repository instances.

        Raises:
            PersistenceError: Starting the transaction failed, or the
                UoW was already entered (non-reentrant).
        """
        if self._entered:
            raise PersistenceError(
                "SqliteUnitOfWork is not re-entrant",
                details={"reason": "__aenter__ called twice on the same instance"},
            )
        self._entered = True
        try:
            await self._conn.execute("BEGIN")
        except Exception as exc:
            raise PersistenceError(
                f"failed to begin SQLite transaction: {exc}",
                details={"reason": str(exc)},
            ) from exc

        # Bind repos. Each receives the same connection and therefore
        # participates in the same transaction.
        self.run_repo = self._run_repo_factory(self._conn)
        self.stage_repo = self._stage_repo_factory(self._conn)
        self.subscription_repo = self._subscription_repo_factory(self._conn)
        self.audit_log = self._audit_log_factory(self._conn)
        self.sweeper_action_repo = self._sweeper_action_repo_factory(self._conn)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Commit on clean exit; roll back on exception.

        Args:
            exc_type: Exception class if the ``async with`` block
                raised; ``None`` otherwise.
            exc_val: Exception instance.
            exc_tb: Traceback.

        Raises:
            PersistenceError: Commit failed. Rollback failures are
                logged but not raised (so the original exception is
                not masked).
        """
        if self._finalized:
            # commit() or rollback() was called explicitly inside the
            # block. The transaction is already closed.
            return

        if exc_type is not None:
            # The block raised. Roll back; the caller's exception
            # propagates.
            try:
                await self._conn.rollback()
            except Exception as rb_exc:
                _log.error(
                    "sqlite_rollback_failed",
                    reason=str(rb_exc),
                    exc_info=rb_exc,
                )
            return

        # Clean exit: commit.
        try:
            await self._conn.commit()
        except Exception as exc:
            # Commit failed. Attempt rollback to release the
            # transaction, then raise.
            try:
                await self._conn.rollback()
            except Exception as rb_exc:
                _log.error(
                    "sqlite_rollback_after_commit_failure",
                    reason=str(rb_exc),
                    exc_info=rb_exc,
                )
            raise PersistenceError(
                f"SQLite commit failed: {exc}",
                details={"reason": str(exc)},
            ) from exc

    # -- Explicit control ------------------------------------------------

    async def commit(self) -> None:
        """Commit early. After this, the repos must not be used."""
        if self._finalized:
            raise PersistenceError(
                "UnitOfWork already finalized",
                details={"method": "commit"},
            )
        try:
            await self._conn.commit()
        except Exception as exc:
            raise PersistenceError(
                f"SQLite commit failed: {exc}",
                details={"reason": str(exc)},
            ) from exc
        self._finalized = True

    async def rollback(self) -> None:
        """Roll back early. After this, the repos must not be used."""
        if self._finalized:
            raise PersistenceError(
                "UnitOfWork already finalized",
                details={"method": "rollback"},
            )
        try:
            await self._conn.rollback()
        except Exception as exc:
            raise PersistenceError(
                f"SQLite rollback failed: {exc}",
                details={"reason": str(exc)},
            ) from exc
        self._finalized = True


# -----------------------------------------------------------------------------
# Factory — holds the shared connection and per-UoW repo-factory bindings.
# -----------------------------------------------------------------------------


class SqliteUnitOfWorkFactory:
    """Produces :class:`SqliteUnitOfWork` instances sharing one connection.

    Constructed once at service start. Injected into use cases as a
    ``Callable[[], UnitOfWork]``. Each call returns a fresh UoW bound
    to the shared connection.

    Service bootstrap is responsible for calling :meth:`close` on
    shutdown to release the connection.
    """

    def __init__(
        self,
        *,
        conn: aiosqlite.Connection,
        run_repo_factory: RunRepoFactory,
        stage_repo_factory: StageRepoFactory,
        subscription_repo_factory: SubscriptionRepoFactory,
        audit_log_factory: AuditLogFactory,
        sweeper_action_repo_factory: SweeperActionRepoFactory,
    ) -> None:
        """Construct with the shared connection and repo factories."""
        self._conn = conn
        self._run_repo_factory = run_repo_factory
        self._stage_repo_factory = stage_repo_factory
        self._subscription_repo_factory = subscription_repo_factory
        self._audit_log_factory = audit_log_factory
        self._sweeper_action_repo_factory = sweeper_action_repo_factory

    def __call__(self) -> SqliteUnitOfWork:
        """Produce a fresh UoW bound to the shared connection."""
        return SqliteUnitOfWork(
            conn=self._conn,
            run_repo_factory=self._run_repo_factory,
            stage_repo_factory=self._stage_repo_factory,
            subscription_repo_factory=self._subscription_repo_factory,
            audit_log_factory=self._audit_log_factory,
            sweeper_action_repo_factory=self._sweeper_action_repo_factory,
        )

    async def close(self) -> None:
        """Release the shared connection. Idempotent."""
        if self._conn is None:
            return
        try:
            await self._conn.close()
        except Exception as exc:
            _log.warning("sqlite_connection_close_failed", reason=str(exc))


__all__ = [
    "AuditLogFactory",
    "RunRepoFactory",
    "SqliteUnitOfWork",
    "SqliteUnitOfWorkFactory",
    "StageRepoFactory",
    "SubscriptionRepoFactory",
    "SweeperActionRepoFactory",
]
