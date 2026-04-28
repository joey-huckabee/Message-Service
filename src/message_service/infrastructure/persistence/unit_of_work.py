"""Concrete :class:`UnitOfWork` for the SQLite backend.

This implementation shares a single :class:`aiosqlite.Connection`
across the service process. Each ``async with factory() as uow``
call begins a transaction on the shared connection and commits or
rolls back on exit.

Because the connection is shared, at most one UoW may hold a
transaction at any time — concurrent UoW openings would otherwise
collide at ``BEGIN`` with ``sqlite3.OperationalError: cannot start
a transaction within a transaction``. The factory owns an
:class:`asyncio.Lock` and threads it into every UoW it produces;
each UoW acquires the lock before issuing ``BEGIN`` and releases it
exactly once on every transaction-closing path (``__aexit__``
clean-commit; ``__aexit__`` exception-driven rollback; explicit
:meth:`SqliteUnitOfWork.commit`; explicit
:meth:`SqliteUnitOfWork.rollback`). Combined commit-then-rollback
spans (when the commit raises and the best-effort rollback also
runs) release the lock exactly once across the combined span,
regardless of whether the rollback itself raises.

The lock is constructed lazily on the factory's first ``__call__``
rather than at ``__init__``, so the factory remains
event-loop-agnostic at construction (bootstrap may run before the
running event loop is established).

The pool architecture that previously appeared in the spec is
preserved verbatim with re-evaluation triggers in
``docs/archive/connection-pool-architecture.md``.

Repository attributes are set at :meth:`__aenter__` time so every
repo sees the same connection under the same transaction.
Implementation classes are injected via the factory so this module
does not depend on concrete repos (they live in their own modules,
added in Increment 11b).

Requirement references
----------------------
L1-PERS-003 (repository pattern)
L2-PERS-004, L3-PERS-006, L3-PERS-007, L3-PERS-021 (single shared
connection + asyncio mutex serialization)
L2-RUN-003 (persistence + audit in a single transaction)
L3-RUN-004 (atomic writes)
L3-RUN-026, L3-RUN-027 (audit-first ordering; enforced at the call
site, not here)
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import TracebackType
from typing import Self

import aiosqlite
import structlog

from message_service.application.ports.audit_log import AuditLog
from message_service.application.ports.run_repository import RunRepository
from message_service.application.ports.session_repository import SessionRepository
from message_service.application.ports.stage_repository import StageRepository
from message_service.application.ports.subscription_repository import (
    SubscriptionRepository,
)
from message_service.application.ports.sweeper_action_repository import (
    SweeperActionRepository,
)
from message_service.application.ports.unit_of_work import UnitOfWork
from message_service.application.ports.user_repository import UserRepository
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
UserRepoFactory = Callable[[aiosqlite.Connection], UserRepository]
SessionRepoFactory = Callable[[aiosqlite.Connection], SessionRepository]


class SqliteUnitOfWork(UnitOfWork):
    """Transactional scope for the SQLite backend.

    Do not construct directly; use :class:`SqliteUnitOfWorkFactory`
    which holds the shared connection and injects repo factories.
    """

    def __init__(
        self,
        *,
        conn: aiosqlite.Connection,
        lock: asyncio.Lock,
        run_repo_factory: RunRepoFactory,
        stage_repo_factory: StageRepoFactory,
        subscription_repo_factory: SubscriptionRepoFactory,
        audit_log_factory: AuditLogFactory,
        sweeper_action_repo_factory: SweeperActionRepoFactory,
        user_repo_factory: UserRepoFactory,
        session_repo_factory: SessionRepoFactory,
    ) -> None:
        """Construct with a live connection, a serialization lock, and the seven repo factories.

        Args:
            conn: Shared :class:`aiosqlite.Connection`. This UoW will
                issue ``BEGIN``/``COMMIT``/``ROLLBACK`` against it.
            lock: :class:`asyncio.Lock` shared with all UoWs produced
                by the same factory. Acquired before ``BEGIN`` and
                released exactly once per UoW lifecycle (see
                L2-PERS-004, L3-PERS-006, L3-PERS-007).
            run_repo_factory: Given the connection, produce a
                :class:`RunRepository` scoped to this UoW's transaction.
            stage_repo_factory: Same for :class:`StageRepository`.
            subscription_repo_factory: Same for
                :class:`SubscriptionRepository`.
            audit_log_factory: Same for :class:`AuditLog`.
            sweeper_action_repo_factory: Same for
                :class:`SweeperActionRepository`.
            user_repo_factory: Same for :class:`UserRepository`
                (Increment 16).
            session_repo_factory: Same for :class:`SessionRepository`
                (Increment 16).
        """
        self._conn = conn
        self._lock = lock
        self._run_repo_factory = run_repo_factory
        self._stage_repo_factory = stage_repo_factory
        self._subscription_repo_factory = subscription_repo_factory
        self._audit_log_factory = audit_log_factory
        self._sweeper_action_repo_factory = sweeper_action_repo_factory
        self._user_repo_factory = user_repo_factory
        self._session_repo_factory = session_repo_factory
        self._entered: bool = False
        self._finalized: bool = False
        self._lock_held: bool = False

    # -- Context manager -------------------------------------------------

    async def __aenter__(self) -> Self:
        """Begin a transaction and bind the scoped repository instances.

        Acquires the factory-shared :class:`asyncio.Lock` BEFORE
        issuing ``BEGIN`` so concurrent UoW openings serialize at the
        Python layer rather than colliding inside SQLite. If ``BEGIN``
        fails the lock is released before the exception propagates,
        because no transaction was opened against the connection.

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

        await self._lock.acquire()
        self._lock_held = True
        try:
            await self._conn.execute("BEGIN")
        except Exception as exc:
            # No transaction is open on the connection; release the
            # lock so other waiters can proceed.
            self._release_lock()
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
        self.user_repo = self._user_repo_factory(self._conn)
        self.session_repo = self._session_repo_factory(self._conn)
        return self

    def _release_lock(self) -> None:
        """Release the serialization lock if held; idempotent.

        Used on every transaction-closing path. The ``_lock_held``
        flag ensures the lock is released exactly once per UoW
        lifecycle even when control flow crosses commit-and-rollback.
        """
        if self._lock_held:
            self._lock_held = False
            self._lock.release()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Commit on clean exit; roll back on exception.

        The serialization lock is released exactly once per UoW
        lifecycle on every path (clean commit, exception rollback,
        commit-fail-then-rollback) via ``try/finally``.

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
            # block. The transaction and the lock are already
            # released; nothing to do here.
            return

        try:
            if exc_type is not None:
                # The block raised. Roll back; the caller's exception
                # propagates.
                try:
                    await self._conn.rollback()
                except Exception as rb_exc:  # noqa: BLE001 — best-effort rollback; original exc propagates
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
                # transaction, then raise. The lock release at the
                # outer ``finally`` covers both the commit and the
                # follow-up rollback as a single span — exactly-once
                # release per UoW lifecycle, regardless of whether
                # the rollback also raises.
                try:
                    await self._conn.rollback()
                except Exception as rb_exc:  # noqa: BLE001 — best-effort rollback; commit-fail still raises
                    _log.error(
                        "sqlite_rollback_after_commit_failure",
                        reason=str(rb_exc),
                        exc_info=rb_exc,
                    )
                raise PersistenceError(
                    f"SQLite commit failed: {exc}",
                    details={"reason": str(exc)},
                ) from exc
        finally:
            self._release_lock()

    # -- Explicit control ------------------------------------------------

    async def commit(self) -> None:
        """Commit early. After this, the repos must not be used.

        On success, releases the serialization lock and marks the UoW
        finalized. On failure, leaves both flags untouched so the
        enclosing :meth:`__aexit__` can run its cleanup rollback under
        the same lock and release it via the outer ``try/finally``.
        Net effect: the lock is released exactly once per UoW
        lifecycle on every path.
        """
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
        self._release_lock()

    async def rollback(self) -> None:
        """Roll back early. After this, the repos must not be used.

        On success, releases the serialization lock and marks the UoW
        finalized. On failure, leaves both flags untouched (matching
        the symmetric behavior of :meth:`commit`); :meth:`__aexit__`
        will see ``_finalized=False`` and attempt its own rollback,
        which is the original cleanup contract.
        """
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
        self._release_lock()


# -----------------------------------------------------------------------------
# Factory — holds the shared connection and per-UoW repo-factory bindings.
# -----------------------------------------------------------------------------


class SqliteUnitOfWorkFactory:
    """Produces :class:`SqliteUnitOfWork` instances sharing one connection.

    Constructed once at service start. Injected into use cases as a
    ``Callable[[], UnitOfWork]``. Each call returns a fresh UoW bound
    to the shared connection and the same shared serialization lock,
    so concurrent UoW openings serialize at the Python layer (see
    L2-PERS-004 + L3-PERS-006).

    The :class:`asyncio.Lock` is constructed lazily on the first
    ``__call__`` rather than at ``__init__``, so the factory remains
    event-loop-agnostic at construction. Bootstrap may run before the
    running event loop is established; constructing the lock at
    bootstrap would bind it to whichever loop happens to be current
    at that moment, which can differ from the loop the use cases run
    on. Lazy construction defers the binding until a UoW is first
    requested, which by definition happens inside the running loop.

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
        user_repo_factory: UserRepoFactory,
        session_repo_factory: SessionRepoFactory,
    ) -> None:
        """Construct with the shared connection and repo factories."""
        self._conn = conn
        self._run_repo_factory = run_repo_factory
        self._stage_repo_factory = stage_repo_factory
        self._subscription_repo_factory = subscription_repo_factory
        self._audit_log_factory = audit_log_factory
        self._sweeper_action_repo_factory = sweeper_action_repo_factory
        self._user_repo_factory = user_repo_factory
        self._session_repo_factory = session_repo_factory
        self._lock: asyncio.Lock | None = None

    def __call__(self) -> SqliteUnitOfWork:
        """Produce a fresh UoW bound to the shared connection + lock."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return SqliteUnitOfWork(
            conn=self._conn,
            lock=self._lock,
            run_repo_factory=self._run_repo_factory,
            stage_repo_factory=self._stage_repo_factory,
            subscription_repo_factory=self._subscription_repo_factory,
            audit_log_factory=self._audit_log_factory,
            sweeper_action_repo_factory=self._sweeper_action_repo_factory,
            user_repo_factory=self._user_repo_factory,
            session_repo_factory=self._session_repo_factory,
        )

    async def close(self) -> None:
        """Release the shared connection. Idempotent."""
        if self._conn is None:
            return
        try:
            await self._conn.close()
        except Exception as exc:  # noqa: BLE001 — close is idempotent best-effort; warn-and-continue
            _log.warning("sqlite_connection_close_failed", reason=str(exc))


__all__ = [
    "AuditLogFactory",
    "RunRepoFactory",
    "SessionRepoFactory",
    "SqliteUnitOfWork",
    "SqliteUnitOfWorkFactory",
    "StageRepoFactory",
    "SubscriptionRepoFactory",
    "SweeperActionRepoFactory",
    "UserRepoFactory",
]
