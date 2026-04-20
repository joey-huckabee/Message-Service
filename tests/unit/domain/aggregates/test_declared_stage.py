"""Unit tests for :mod:`message_service.domain.aggregates.declared_stage`."""

from __future__ import annotations

import pytest

from message_service.domain.aggregates.declared_stage import DeclaredStage
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.ids import StageId

_TPL = TemplateRef(name="x", version="1")


@pytest.mark.requirement("L2-RUN-009")
def test_declared_stage_constructs_with_valid_values() -> None:
    ds = DeclaredStage(stage_id=StageId("extract"), stage_order=0, report_template_ref=_TPL)
    assert ds.stage_id == "extract"
    assert ds.stage_order == 0
    assert ds.report_template_ref == _TPL


@pytest.mark.requirement("L2-RUN-009")
def test_declared_stage_is_frozen() -> None:
    ds = DeclaredStage(stage_id=StageId("x"), stage_order=0, report_template_ref=_TPL)
    with pytest.raises((AttributeError, TypeError)):
        ds.stage_order = 99  # type: ignore[misc]


@pytest.mark.requirement("L2-RUN-009")
def test_declared_stage_rejects_negative_order() -> None:
    with pytest.raises(ValueError, match="stage_order"):
        DeclaredStage(stage_id=StageId("x"), stage_order=-1, report_template_ref=_TPL)


@pytest.mark.requirement("L2-RUN-009")
def test_declared_stage_equality_is_value_based() -> None:
    a = DeclaredStage(stage_id=StageId("x"), stage_order=0, report_template_ref=_TPL)
    b = DeclaredStage(stage_id=StageId("x"), stage_order=0, report_template_ref=_TPL)
    c = DeclaredStage(stage_id=StageId("x"), stage_order=1, report_template_ref=_TPL)
    assert a == b
    assert a != c
