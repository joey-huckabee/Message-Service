"""Unit tests for :mod:`message_service.domain.state_machines.run_states`.

Every test carries a ``@pytest.mark.requirement`` marker linking to the
specific L3 requirement it verifies. See ``docs/TRACE-MATRIX.md``.
"""

from __future__ import annotations

from enum import Enum

import pytest

from message_service.domain.errors import InvalidStateTransitionError
from message_service.domain.state_machines.run_states import (
    NON_TERMINAL_STATES,
    TERMINAL_STATES,
    TRANSITIONS,
    RunState,
    can_transition,
    transition,
)

# -----------------------------------------------------------------------------
# Module structure (L3-RUN-006, L3-RUN-030)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-030")
def test_run_state_is_str_enum() -> None:
    """RunState SHALL be a string-valued enum for JSON-serializable persistence."""
    assert issubclass(RunState, str)
    assert issubclass(RunState, Enum)


@pytest.mark.requirement("L3-RUN-030")
def test_run_state_values_match_names() -> None:
    """StrEnum values match names, so persisted state is human-readable."""
    for state in RunState:
        assert state.value == state.name


@pytest.mark.requirement("L3-RUN-006")
def test_transition_table_is_frozen_dict_of_frozensets() -> None:
    """TRANSITIONS SHALL be inspectable without side effects."""
    assert isinstance(TRANSITIONS, dict)
    for src, dests in TRANSITIONS.items():
        assert isinstance(src, RunState)
        assert isinstance(dests, frozenset)


# -----------------------------------------------------------------------------
# Terminal/non-terminal partition (L2-RUN-006)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-RUN-006")
def test_terminal_states_are_exactly_sent_failed_orphaned() -> None:
    assert frozenset({RunState.SENT, RunState.FAILED, RunState.ORPHANED}) == TERMINAL_STATES


@pytest.mark.requirement("L2-RUN-006")
def test_partition_is_disjoint_and_covers_all_states() -> None:
    """Every state is in exactly one of terminal/non-terminal sets."""
    assert set(RunState) == TERMINAL_STATES | NON_TERMINAL_STATES
    assert frozenset() == TERMINAL_STATES & NON_TERMINAL_STATES


@pytest.mark.requirement("L2-RUN-006")
@pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
def test_terminal_states_have_no_outgoing_transitions(state: RunState) -> None:
    """Terminal states reject all outgoing edges."""
    assert TRANSITIONS.get(state, frozenset()) == frozenset()


# -----------------------------------------------------------------------------
# Canonical happy-path edges (L2-RUN-004)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-RUN-004")
@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (RunState.INITIATED, RunState.AGGREGATING),
        (RunState.AGGREGATING, RunState.READY),
        (RunState.READY, RunState.SENDING),
        (RunState.SENDING, RunState.SENT),
    ],
)
def test_canonical_happy_path_edges_permitted(src: RunState, dst: RunState) -> None:
    """The canonical happy path is INITIATED -> AGGREGATING -> READY -> SENDING -> SENT."""
    assert can_transition(src, dst)


# -----------------------------------------------------------------------------
# Abort-on-error paths (L3-RUN-028)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-028")
@pytest.mark.parametrize("src", sorted(NON_TERMINAL_STATES))
def test_every_non_terminal_state_can_transition_to_failed(src: RunState) -> None:
    """Any non-terminal state SHALL be permitted to transition directly to FAILED."""
    assert can_transition(src, RunState.FAILED)


@pytest.mark.requirement("L3-RUN-028")
@pytest.mark.parametrize("src", sorted(NON_TERMINAL_STATES))
def test_every_non_terminal_state_can_transition_to_orphaned(src: RunState) -> None:
    """The sweeper can transition any non-terminal state to ORPHANED."""
    assert can_transition(src, RunState.ORPHANED)


# -----------------------------------------------------------------------------
# Exhaustive property-style check (L3-RUN-007)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-007")
def test_all_state_pairs_respect_transition_table() -> None:
    """Enumerate every (src, dst) pair and assert can_transition matches the table.

    This is the non-hypothesis version of L3-RUN-007; a hypothesis-based
    version can replace this later if generation coverage is desired.
    """
    for src in RunState:
        permitted = TRANSITIONS.get(src, frozenset())
        for dst in RunState:
            if dst in permitted:
                assert can_transition(src, dst), f"{src} -> {dst} should be permitted"
            else:
                assert not can_transition(src, dst), f"{src} -> {dst} should be forbidden"


# -----------------------------------------------------------------------------
# transition() raises InvalidStateTransitionError on illegal edges (L2-RUN-005)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-008")
def test_illegal_transition_raises_with_structured_details() -> None:
    """Raised exception SHALL carry from_state, to_state, run_id in details."""
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        transition(
            from_state=RunState.INITIATED,
            to_state=RunState.SENT,  # not permitted
            run_id="abcdef00-1234-4000-8000-000000000000",
        )
    assert exc_info.value.details == {
        "from_state": "INITIATED",
        "to_state": "SENT",
        "run_id": "abcdef00-1234-4000-8000-000000000000",
    }


@pytest.mark.requirement("L3-RUN-009")
@pytest.mark.parametrize("src", sorted(TERMINAL_STATES))
@pytest.mark.parametrize(
    "dst",
    [RunState.INITIATED, RunState.AGGREGATING, RunState.READY, RunState.SENDING],
)
def test_terminal_states_reject_transitions_to_non_terminal(src: RunState, dst: RunState) -> None:
    """Attempts to transition a terminal run to any other state SHALL raise."""
    with pytest.raises(InvalidStateTransitionError):
        transition(
            from_state=src,
            to_state=dst,
            run_id="abcdef00-1234-4000-8000-000000000000",
        )


# -----------------------------------------------------------------------------
# transition() returns the new state on success
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-RUN-004")
def test_valid_transition_returns_new_state() -> None:
    result = transition(
        from_state=RunState.INITIATED,
        to_state=RunState.AGGREGATING,
        run_id="abcdef00-1234-4000-8000-000000000000",
    )
    assert result is RunState.AGGREGATING
