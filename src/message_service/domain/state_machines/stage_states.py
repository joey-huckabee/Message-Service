"""Stage lifecycle state machine.

Implements the per-stage state set and permitted transitions, per
L1-STAGE-001 and its L2/L3 derivations.

Design
------
* Each declared stage in a run has its own state machine instance,
  keyed on ``(run_id, stage_id)``.
* The state set includes ``IN_PROGRESS`` which is **reserved** in v1 for a
  future stage-heartbeat RPC (L2-STAGE-002, L3-STAGE-018). No v1 code
  path transitions into ``IN_PROGRESS``; a runtime assertion enforces
  this.
* Terminal states: ``ACCEPTED``, ``TIMEOUT``, ``FAILED``. Transitions
  from these states are rejected.

Requirement references
----------------------
L1-STAGE-001, L2-STAGE-001, L2-STAGE-002,
L3-STAGE-001, L3-STAGE-002, L3-STAGE-003, L3-STAGE-018
"""

from __future__ import annotations

from enum import Enum
from typing import Final

from message_service.domain.errors import InvalidStateTransitionError


class StageState(str, Enum):
    """Lifecycle states for a single stage within a run.

    String-valued enum for persistence and JSON serialization.

    Non-terminal states: ``PENDING``, ``IN_PROGRESS`` (reserved for v2),
    ``SUBMITTED``, ``RETRIED``.
    Terminal states: ``ACCEPTED``, ``TIMEOUT``, ``FAILED``.
    """

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"  # reserved for future heartbeat mechanism
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    RETRIED = "RETRIED"
    TIMEOUT = "TIMEOUT"
    FAILED = "FAILED"


TERMINAL_STATES: Final[frozenset[StageState]] = frozenset(
    {StageState.ACCEPTED, StageState.TIMEOUT, StageState.FAILED}
)
"""States from which no further transitions are permitted."""


NON_TERMINAL_STATES: Final[frozenset[StageState]] = frozenset(set(StageState) - TERMINAL_STATES)
"""States from which outgoing transitions are possible."""


RESERVED_FOR_V2: Final[frozenset[StageState]] = frozenset({StageState.IN_PROGRESS})
"""States reserved for future releases. No v1 code path SHALL enter these
states (L2-STAGE-002, L3-STAGE-018)."""


# Permitted transitions per L2-STAGE-002. Note that IN_PROGRESS has zero
# inbound edges in v1 — the state exists in the enum for forward
# compatibility but is unreachable. L3-STAGE-003 asserts this via a test.

_EXPLICIT_TRANSITIONS: dict[StageState, set[StageState]] = {
    StageState.PENDING: {StageState.SUBMITTED},
    StageState.SUBMITTED: {StageState.RETRIED, StageState.ACCEPTED},
    StageState.RETRIED: {StageState.RETRIED, StageState.ACCEPTED},
    # IN_PROGRESS intentionally absent from both sides — reserved
    # terminal states (ACCEPTED, TIMEOUT, FAILED) intentionally absent as keys
}

# Every non-terminal state (except the reserved IN_PROGRESS) may
# additionally transition to TIMEOUT and FAILED.
for _src in NON_TERMINAL_STATES - RESERVED_FOR_V2:
    _EXPLICIT_TRANSITIONS.setdefault(_src, set()).update({StageState.TIMEOUT, StageState.FAILED})

TRANSITIONS: Final[dict[StageState, frozenset[StageState]]] = {
    src: frozenset(dests) for src, dests in _EXPLICIT_TRANSITIONS.items()
}
"""The complete transition table for :class:`StageState` (read-only)."""


def can_transition(from_state: StageState, to_state: StageState) -> bool:
    """Return True iff the transition is permitted by the table."""
    return to_state in TRANSITIONS.get(from_state, frozenset())


def transition(
    *,
    from_state: StageState,
    to_state: StageState,
    run_id: str,
    stage_id: str,
) -> StageState:
    """Validate and perform a stage state transition.

    Args:
        from_state: The current stage state.
        to_state: The desired next state.
        run_id: The run identifier (for exception details).
        stage_id: The stage identifier (for exception details).

    Returns:
        The new state.

    Raises:
        InvalidStateTransitionError: If the transition is not permitted.
    """
    if not can_transition(from_state, to_state):
        raise InvalidStateTransitionError(
            f"illegal stage transition {from_state} -> {to_state}",
            details={
                "from_state": from_state.value,
                "to_state": to_state.value,
                "run_id": run_id,
                "stage_id": stage_id,
            },
        )
    return to_state


__all__ = [
    "StageState",
    "TRANSITIONS",
    "TERMINAL_STATES",
    "NON_TERMINAL_STATES",
    "RESERVED_FOR_V2",
    "can_transition",
    "transition",
]
