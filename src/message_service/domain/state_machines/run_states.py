"""Run lifecycle state machine.

Implements the state set and permitted transitions for a pipeline run,
per L1-RUN-002 and its L2/L3 derivations.

Design
------
* The state is a :class:`enum.StrEnum` (L3-RUN-030), making comparisons
  fast via identity but also trivially JSON-serializable for persistence.
* The transition table is a module-level frozen dict of sets (L3-RUN-006),
  inspectable without any side effects.
* :class:`InvalidStateTransitionError` (from :mod:`message_service.domain.errors`)
  is raised on any attempted transition outside the table (L2-RUN-005).
* Terminal states reject all outgoing transitions (L2-RUN-006, L3-RUN-009).

Reserved
--------
Transitions directly to ``FAILED`` from any non-terminal state are
explicitly permitted to support abort-on-error paths (L3-RUN-028).

Requirement references
----------------------
L1-RUN-002, L2-RUN-004, L2-RUN-005, L2-RUN-006,
L3-RUN-006, L3-RUN-007, L3-RUN-009, L3-RUN-028, L3-RUN-030
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from message_service.domain.errors import InvalidStateTransitionError


class RunState(StrEnum):
    """Lifecycle states for a pipeline run.

    StrEnum for JSON-serializable persistence and human-readable values.

    Non-terminal states: ``INITIATED``, ``AGGREGATING``, ``READY``, ``SENDING``.
    Terminal states: ``SENT``, ``FAILED``, ``ORPHANED``.
    """

    INITIATED = "INITIATED"
    AGGREGATING = "AGGREGATING"
    READY = "READY"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    ORPHANED = "ORPHANED"


TERMINAL_STATES: Final[frozenset[RunState]] = frozenset(
    {RunState.SENT, RunState.FAILED, RunState.ORPHANED}
)
"""States from which no further transitions are permitted (L2-RUN-006)."""


NON_TERMINAL_STATES: Final[frozenset[RunState]] = frozenset(set(RunState) - TERMINAL_STATES)
"""States from which outgoing transitions are possible."""


# Permitted transitions per L2-RUN-004. Frozen dict of frozensets so the
# table cannot be mutated at runtime (L3-RUN-006).
#
# Every non-terminal state may additionally transition to FAILED
# (L3-RUN-028) and to ORPHANED (sweeper). These are injected below rather
# than spelled out per row, to avoid duplication.

_EXPLICIT_TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.INITIATED: {RunState.AGGREGATING},
    RunState.AGGREGATING: {RunState.READY},
    RunState.READY: {RunState.SENDING},
    RunState.SENDING: {RunState.SENT},
    # terminal states intentionally absent — they reject all outgoing edges
}

# Inject the always-available failure and orphan edges from every non-terminal state.
for _src in NON_TERMINAL_STATES:
    _EXPLICIT_TRANSITIONS.setdefault(_src, set()).update({RunState.FAILED, RunState.ORPHANED})

TRANSITIONS: Final[dict[RunState, frozenset[RunState]]] = {
    src: frozenset(dests) for src, dests in _EXPLICIT_TRANSITIONS.items()
}
"""The complete transition table for :class:`RunState` (read-only)."""


def can_transition(from_state: RunState, to_state: RunState) -> bool:
    """Return True iff the transition is permitted by the table.

    Args:
        from_state: The current state.
        to_state: The proposed next state.

    Returns:
        True if the transition is present in :data:`TRANSITIONS`.
    """
    return to_state in TRANSITIONS.get(from_state, frozenset())


def transition(
    *,
    from_state: RunState,
    to_state: RunState,
    run_id: str,
) -> RunState:
    """Validate and perform a state transition.

    Args:
        from_state: The current run state.
        to_state: The desired next state.
        run_id: The run identifier, included in exception details for diagnosis.

    Returns:
        The new state (same as ``to_state`` on success).

    Raises:
        InvalidStateTransitionError: If the transition is not permitted. The
            exception's ``details`` dict carries ``from_state``, ``to_state``,
            and ``run_id`` (L3-RUN-008).
    """
    if not can_transition(from_state, to_state):
        raise InvalidStateTransitionError(
            f"illegal run transition {from_state} -> {to_state}",
            details={
                "from_state": from_state.value,
                "to_state": to_state.value,
                "run_id": run_id,
            },
        )
    return to_state


__all__ = [
    "NON_TERMINAL_STATES",
    "TERMINAL_STATES",
    "TRANSITIONS",
    "RunState",
    "can_transition",
    "transition",
]
