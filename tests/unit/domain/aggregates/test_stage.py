"""Unit tests for :mod:`message_service.domain.aggregates.stage`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from message_service.domain.aggregates.email_body_position import EmailBodyPosition
from message_service.domain.aggregates.stage import Stage
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.stage_states import StageState

_RID = RunId("00000000-0000-4000-8000-000000000001")
_SID = StageId("extract")
_T0 = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)
_TPL = TemplateRef(name="extract_report", version="1.0")


def _stage(**overrides: object) -> Stage:
    fields: dict[str, object] = {
        "run_id": _RID,
        "stage_id": _SID,
        "state": StageState.PENDING,
        "report_template_ref": _TPL,
    }
    fields.update(overrides)
    return Stage(**fields)  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# Construction
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-STAGE-003")
def test_stage_constructs_in_pending_without_submission() -> None:
    s = _stage()
    assert s.state == StageState.PENDING
    assert s.submitted_at is None
    assert s.report_context_json is None


@pytest.mark.requirement("L2-STAGE-003")
def test_stage_is_frozen() -> None:
    s = _stage()
    with pytest.raises((AttributeError, TypeError)):
        s.state = StageState.SUBMITTED  # type: ignore[misc]


# -----------------------------------------------------------------------------
# submitted_at invariants (L3-STAGE-007)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-STAGE-007")
def test_pending_stage_must_not_have_submitted_at() -> None:
    with pytest.raises(ValueError, match="PENDING"):
        _stage(state=StageState.PENDING, submitted_at=_T0)


@pytest.mark.requirement("L3-STAGE-007")
@pytest.mark.parametrize("state", [StageState.SUBMITTED, StageState.ACCEPTED, StageState.RETRIED])
def test_submission_state_requires_submitted_at(state: StageState) -> None:
    with pytest.raises(ValueError, match="submitted_at"):
        _stage(state=state, submitted_at=None)


@pytest.mark.requirement("L3-STAGE-007")
def test_submitted_state_accepts_valid_submitted_at() -> None:
    s = _stage(state=StageState.SUBMITTED, submitted_at=_T0)
    assert s.submitted_at == _T0


@pytest.mark.requirement("L3-RUN-025")
def test_stage_rejects_naive_submitted_at() -> None:
    naive = datetime(2026, 4, 19, 12, 0, 0)
    with pytest.raises(ValueError, match="submitted_at"):
        _stage(state=StageState.SUBMITTED, submitted_at=naive)


# -----------------------------------------------------------------------------
# Independent context fields (L3-STAGE-009)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-STAGE-009")
def test_stage_report_and_email_body_contexts_are_independent() -> None:
    s_report_only = _stage(
        state=StageState.SUBMITTED,
        submitted_at=_T0,
        report_context_json='{"x": 1}',
        email_body_context_json=None,
    )
    s_body_only = _stage(
        state=StageState.SUBMITTED,
        submitted_at=_T0,
        report_context_json=None,
        email_body_context_json='{"y": 2}',
        email_body_position=EmailBodyPosition.AFTER_STAGES_SUMMARY,
    )
    assert s_report_only.email_body_context_json is None
    assert s_body_only.report_context_json is None
