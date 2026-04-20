"""Unit tests for :mod:`message_service.domain.aggregates.run`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from message_service.domain.aggregates.run import AttachmentMode, Run
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.run_states import RunState

_RID = RunId("00000000-0000-4000-8000-000000000001")
_T0 = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)


def _minimal_run(**overrides: object) -> Run:
    """Build a Run with sensible defaults; tests override per-field."""
    fields: dict[str, object] = {
        "run_id": _RID,
        "pipeline_type": "etl-nightly",
        "tags": frozenset({"production"}),
        "declared_stages": frozenset({StageId("extract"), StageId("transform")}),
        "state": RunState.INITIATED,
        "attachment_mode": AttachmentMode.SINGLE_AGGREGATED,
        "aggregation_template_ref": TemplateRef(name="nightly", version="1.0"),
        "created_at": _T0,
        "updated_at": _T0,
    }
    fields.update(overrides)
    return Run(**fields)  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# Construction
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-RUN-003")
def test_run_constructs_with_valid_values() -> None:
    run = _minimal_run()
    assert run.run_id == _RID
    assert run.state == RunState.INITIATED


@pytest.mark.requirement("L2-RUN-003")
def test_run_is_frozen() -> None:
    run = _minimal_run()
    with pytest.raises((AttributeError, TypeError)):
        run.state = RunState.AGGREGATING  # type: ignore[misc]


@pytest.mark.requirement("L2-RUN-003")
def test_run_uses_slots() -> None:
    """slots=True prevents arbitrary attribute assignment; no __dict__."""
    run = _minimal_run()
    assert not hasattr(run, "__dict__")


# -----------------------------------------------------------------------------
# Equality
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-RUN-003")
def test_run_equality_is_value_based() -> None:
    a = _minimal_run()
    b = _minimal_run()
    assert a == b


@pytest.mark.requirement("L2-RUN-003")
def test_run_with_different_state_is_not_equal() -> None:
    a = _minimal_run(state=RunState.INITIATED)
    b = _minimal_run(state=RunState.AGGREGATING)
    assert a != b


# -----------------------------------------------------------------------------
# Timestamp invariants
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-025")
def test_run_rejects_naive_created_at() -> None:
    naive = datetime(2026, 4, 19, 12, 0, 0)
    with pytest.raises(ValueError, match="created_at"):
        _minimal_run(created_at=naive, updated_at=_T0)


@pytest.mark.requirement("L3-RUN-025")
def test_run_rejects_naive_updated_at() -> None:
    naive = datetime(2026, 4, 19, 12, 0, 0)
    with pytest.raises(ValueError, match="updated_at"):
        _minimal_run(created_at=_T0, updated_at=naive)


@pytest.mark.requirement("L2-RUN-003")
def test_run_rejects_updated_at_before_created_at() -> None:
    earlier = _T0 - timedelta(seconds=1)
    with pytest.raises(ValueError, match="updated_at"):
        _minimal_run(created_at=_T0, updated_at=earlier)


# -----------------------------------------------------------------------------
# Attachment mode invariants (L3-RUN-011, L3-RUN-018)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-RUN-011")
def test_single_aggregated_requires_aggregation_template() -> None:
    with pytest.raises(ValueError, match="aggregation_template_ref"):
        _minimal_run(
            attachment_mode=AttachmentMode.SINGLE_AGGREGATED,
            aggregation_template_ref=None,
        )


@pytest.mark.requirement("L2-RUN-011")
def test_per_stage_does_not_require_aggregation_template() -> None:
    """PER_STAGE runs without aggregation_template_ref SHALL be valid."""
    run = _minimal_run(
        attachment_mode=AttachmentMode.PER_STAGE,
        aggregation_template_ref=None,
    )
    assert run.aggregation_template_ref is None


# -----------------------------------------------------------------------------
# Stage-set properties
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-015")
def test_run_permits_empty_declared_stages() -> None:
    run = _minimal_run(declared_stages=frozenset())
    assert len(run.declared_stages) == 0


@pytest.mark.requirement("L3-RUN-014")
def test_declared_stages_is_frozenset() -> None:
    run = _minimal_run()
    assert isinstance(run.declared_stages, frozenset)
