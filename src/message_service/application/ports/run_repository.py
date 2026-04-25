"""Port: persistence for :class:`~message_service.domain.run.Run` aggregates.

Mutation style: hybrid. Choose deliberately per call site:

* :meth:`RunRepository.save` accepts a fully-constructed :class:`Run`
  value. Use it for **creation** (BeginRun) and **multi-field
  transitions** where you want the aggregate's ``__post_init__``
  invariants to fire against the new state. The use case owns
  construction of the valid post-mutation value.

* :meth:`RunRepository.update_state` mutates only the ``state`` column
  plus ``updated_at``. Use it for **pure state transitions** where the
  use case has already validated the transition against
  :mod:`~message_service.domain.state_machines.run_states` and does not
  need to read the aggregate back. The sweeper uses this for batch
  orphan-to-``ORPHANED`` transitions.

Rule of thumb: if you'd otherwise read-modify-write, prefer
:meth:`save`; if you'd otherwise emit a naked ``UPDATE ... SET state =
?``, prefer :meth:`update_state`.

Requirement references
----------------------
L1-PERS-003 (repository pattern)
L2-RUN-003 (persistence in single transaction with audit)
L2-PERS-008 (ports are ABCs in application/ports/)
L3-RUN-004, L3-RUN-005
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime

from message_service.domain.aggregates.run import Run
from message_service.domain.ids import RunId
from message_service.domain.state_machines.run_states import RunState


class RunRepository(ABC):
    """Abstract repository for :class:`Run` aggregates.

    Implementations MUST:

    * Perform every persistent write inside a transaction that is
      atomic with the corresponding audit insert (L3-RUN-026). The
      adapter is responsible for orchestrating this; the port does not
      expose transaction boundaries.
    * Raise
      :class:`~message_service.domain.errors.RunNotFoundError` from
      :meth:`get` and :meth:`update_state` when the ``run_id`` is
      unknown.
    * Raise
      :class:`~message_service.domain.errors.PersistenceError` (or a
      subclass) on any infrastructure-level failure.
    """

    @abstractmethod
    async def save(self, run: Run) -> None:
        """Insert a new run or overwrite an existing one.

        Implementations use ``INSERT ... ON CONFLICT(run_id) DO UPDATE``
        or equivalent upsert semantics so that ``save`` is idempotent
        against re-delivery of the same logical state.

        Args:
            run: The aggregate to persist. Invariants are enforced by
                :class:`Run`'s ``__post_init__``; implementations may
                assume validity.

        Raises:
            PersistenceError: On infrastructure failure (connection
                lost, disk full, serialization conflict). The
                transaction is rolled back; the repository state is
                unchanged.
        """

    @abstractmethod
    async def get(self, run_id: RunId) -> Run:
        """Load a run by id.

        Args:
            run_id: The identifier to look up. MUST be a validated
                :class:`RunId`; callers that receive untrusted string
                input use
                :func:`~message_service.domain.ids.validate_run_id_str`
                first.

        Returns:
            The reconstructed :class:`Run` aggregate.

        Raises:
            RunNotFoundError: No run with ``run_id`` exists.
            PersistenceError: Infrastructure failure.
        """

    @abstractmethod
    async def update_state(
        self,
        run_id: RunId,
        new_state: RunState,
        now: datetime,
    ) -> None:
        """Update a run's state and ``updated_at`` in one statement.

        Does NOT re-read the aggregate; callers MUST have already
        validated the state transition against
        :func:`~message_service.domain.state_machines.run_states.transition`.

        Args:
            run_id: The run to update.
            new_state: The state to transition into. No state-machine
                check is performed here; the caller is responsible.
            now: UTC timestamp to record in ``updated_at``. Obtained
                from the injected
                :class:`~message_service.application.ports.clock.Clock`.

        Raises:
            RunNotFoundError: No run with ``run_id`` exists.
            PersistenceError: Infrastructure failure.
        """

    @abstractmethod
    async def list_in_states(
        self,
        states: frozenset[RunState],
    ) -> Sequence[Run]:
        """List runs currently in any of the given states.

        The sweeper uses this with ``frozenset({RunState.INITIATED,
        RunState.AGGREGATING})`` to find orphan candidates. Order is
        unspecified; callers that need a consistent order must sort
        themselves.

        Args:
            states: The target states. Empty set returns an empty
                sequence.

        Returns:
            A sequence of :class:`Run` aggregates currently in any of
            the given states. Empty sequence if none.

        Raises:
            PersistenceError: Infrastructure failure.
        """

    @abstractmethod
    async def list_expired(
        self,
        cutoff: datetime,
        active_states: frozenset[RunState],
        *,
        limit: int,
    ) -> Sequence[Run]:
        """List up to ``limit`` runs whose last transition is older than ``cutoff``.

        The comparison is against ``updated_at`` (L2-SWEEP-004), so a
        long-lived run that just transitioned is not treated as
        expired even if ``created_at`` is older than the cutoff.

        Args:
            cutoff: Runs with ``updated_at < cutoff`` AND ``state in
                active_states`` are returned. ``cutoff`` is computed by
                the sweeper as ``clock.now() - run_timeout_seconds``.
                Per L2-SWEEP-004 the comparison is against the
                ``last-transition`` timestamp (``updated_at``), not
                ``created_at``.
            active_states: Typically
                ``frozenset({RunState.INITIATED, RunState.AGGREGATING,
                RunState.READY, RunState.SENDING})``.
            limit: Maximum rows to return per call; bounds per-tick
                sweeper work per L2-SWEEP-010 / L3-SWEEP-008. Backlogs
                larger than this drain across multiple ticks.

        Returns:
            A sequence of at most ``limit`` expired runs ordered
            oldest-transition first. Empty sequence if none.

        Raises:
            ValueError: ``limit`` is not positive.
            PersistenceError: Infrastructure failure.
        """


__all__ = ["RunRepository"]
