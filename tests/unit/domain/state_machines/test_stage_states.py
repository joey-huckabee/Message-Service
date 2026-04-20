"""Unit tests for :mod:`message_service.domain.state_machines.stage_states`."""

from __future__ import annotations

from enum import StrEnum

import pytest

from message_service.domain.errors import InvalidStateTransitionError
from message_service.domain.state_machines.stage_states import (
    NON_TERMINAL_STATES,
    RESERVED_FOR_V2,
    TERMINAL_STATES,
    TRANSITIONS,
    StageState,
    can_transition,
    transition,
)

# -----------------------------------------------------------------------------
# Module structure (L3-STAGE-001)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-STAGE-001")
def test_stage_state_is_str_enum() -> None:
    assert issubclass(StageState, StrEnum)


@pytest.mark.requirement("L3-STAGE-001")
def test_stage_state_values_match_names() -> None:
    for state in StageState:
        assert state.value == state.name


# -----------------------------------------------------------------------------
# Terminal/non-terminal partition
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-STAGE-001")
def test_terminal_states_are_accepted_timeout_failed() -> None:
    assert (
        frozenset({StageState.ACCEPTED, StageState.TIMEOUT, StageState.FAILED}) == TERMINAL_STATES
    )


@pytest.mark.requirement("L2-STAGE-001")
def test_partition_covers_all_states() -> None:
    assert set(StageState) == TERMINAL_STATES | NON_TERMINAL_STATES
    assert frozenset() == TERMINAL_STATES & NON_TERMINAL_STATES


@pytest.mark.requirement("L2-STAGE-001")
@pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
def test_terminal_states_have_no_outgoing_transitions(state: StageState) -> None:
    assert TRANSITIONS.get(state, frozenset()) == frozenset()


# -----------------------------------------------------------------------------
# IN_PROGRESS is reserved for v2 (L2-STAGE-002, L3-STAGE-003, L3-STAGE-018)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-STAGE-018")
def test_in_progress_is_declared_reserved() -> None:
    """IN_PROGRESS SHALL be in RESERVED_FOR_V2."""
    assert StageState.IN_PROGRESS in RESERVED_FOR_V2


@pytest.mark.requirement("L3-STAGE-003")
def test_in_progress_has_no_inbound_edges() -> None:
    """No state in the transition table SHALL target IN_PROGRESS in v1."""
    for src, dests in TRANSITIONS.items():
        assert StageState.IN_PROGRESS not in dests, (
            f"{src} -> IN_PROGRESS should not be permitted in v1"
        )


@pytest.mark.requirement("L3-STAGE-003")
def test_in_progress_has_no_outbound_edges() -> None:
    """IN_PROGRESS SHALL have no transitions out of it in v1 either."""
    assert TRANSITIONS.get(StageState.IN_PROGRESS, frozenset()) == frozenset()


# -----------------------------------------------------------------------------
# Canonical happy-path edges (L2-STAGE-002)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-STAGE-002")
@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (StageState.PENDING, StageState.SUBMITTED),
        (StageState.SUBMITTED, StageState.RETRIED),
        (StageState.RETRIED, StageState.RETRIED),
        (StageState.SUBMITTED, StageState.ACCEPTED),
        (StageState.RETRIED, StageState.ACCEPTED),
    ],
)
def test_happy_path_edges_permitted(src: StageState, dst: StageState) -> None:
    assert can_transition(src, dst)


# -----------------------------------------------------------------------------
# Timeout and failure edges from any non-reserved non-terminal state
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-STAGE-002")
@pytest.mark.parametrize("src", sorted(NON_TERMINAL_STATES - RESERVED_FOR_V2))
def test_every_active_state_can_transition_to_timeout(src: StageState) -> None:
    assert can_transition(src, StageState.TIMEOUT)


@pytest.mark.requirement("L2-STAGE-002")
@pytest.mark.parametrize("src", sorted(NON_TERMINAL_STATES - RESERVED_FOR_V2))
def test_every_active_state_can_transition_to_failed(src: StageState) -> None:
    assert can_transition(src, StageState.FAILED)


# -----------------------------------------------------------------------------
# Exhaustive pair check
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-STAGE-002")
def test_all_state_pairs_respect_transition_table() -> None:
    for src in StageState:
        permitted = TRANSITIONS.get(src, frozenset())
        for dst in StageState:
            if dst in permitted:
                assert can_transition(src, dst), f"{src} -> {dst} should be permitted"
            else:
                assert not can_transition(src, dst), f"{src} -> {dst} should be forbidden"


# -----------------------------------------------------------------------------
# transition() behaviour
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-STAGE-002")
def test_valid_transition_returns_new_state() -> None:
    result = transition(
        from_state=StageState.PENDING,
        to_state=StageState.SUBMITTED,
        run_id="abcdef00-1234-4000-8000-000000000000",
        stage_id="stage-a",
    )
    assert result is StageState.SUBMITTED


@pytest.mark.requirement("L3-STAGE-003")
def test_transition_to_in_progress_is_rejected() -> None:
    """Direct attempt to transition to IN_PROGRESS SHALL raise."""
    with pytest.raises(InvalidStateTransitionError):
        transition(
            from_state=StageState.PENDING,
            to_state=StageState.IN_PROGRESS,
            run_id="abcdef00-1234-4000-8000-000000000000",
            stage_id="stage-a",
        )


@pytest.mark.requirement("L2-STAGE-002")
def test_illegal_transition_carries_structured_details() -> None:
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        transition(
            from_state=StageState.PENDING,
            to_state=StageState.ACCEPTED,  # must go PENDING -> SUBMITTED first
            run_id="abcdef00-1234-4000-8000-000000000000",
            stage_id="stage-a",
        )
    details = exc_info.value.details
    assert details["from_state"] == "PENDING"
    assert details["to_state"] == "ACCEPTED"
    assert details["run_id"] == "abcdef00-1234-4000-8000-000000000000"
    assert details["stage_id"] == "stage-a"


@pytest.mark.requirement("L2-STAGE-001")
@pytest.mark.parametrize("src", sorted(TERMINAL_STATES))
def test_terminal_stage_states_reject_further_transitions(src: StageState) -> None:
    with pytest.raises(InvalidStateTransitionError):
        transition(
            from_state=src,
            to_state=StageState.PENDING,
            run_id="abcdef00-1234-4000-8000-000000000000",
            stage_id="stage-a",
        )
