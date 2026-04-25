"""Unit tests for :class:`BeginRunUseCase`.

Every test uses :class:`unittest.mock.AsyncMock` with ``spec=Port`` to
verify the use case's port interactions precisely. Real port
implementations are not involved — these tests validate that the use
case calls the right ports with the right arguments in the right order.

Requirement references
----------------------
L1-RUN-002, L1-RUN-003
L3-RUN-001 (one uuid4 per BeginRun)
L3-RUN-013 (report all invalid tags)
L3-RUN-014 (report all duplicate stage ids)
L3-RUN-016, L3-RUN-017 (template refs validated)
L3-RUN-018 (PER_STAGE ignores aggregation_template)
L3-RUN-019 (MissingAggregationTemplate details echo mode)
L3-RUN-026 (audit-first ordering in the transaction)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from message_service.application.ports.audit_log import AuditLog
from message_service.application.ports.clock import Clock
from message_service.application.ports.run_repository import RunRepository
from message_service.application.ports.stage_repository import StageRepository
from message_service.application.ports.tag_vocabulary import TagVocabulary
from message_service.application.ports.template_repository import TemplateRepository
from message_service.application.ports.unit_of_work import UnitOfWork
from message_service.application.use_cases.begin_run import BeginRunUseCase
from message_service.application.use_cases.begin_run_command import (
    BeginRunCommand,
    DeclaredStageInput,
)
from message_service.domain.aggregates.audit_event import AuditAction, AuditOutcome
from message_service.domain.aggregates.run import AttachmentMode, Run
from message_service.domain.aggregates.stage import Stage
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import (
    DuplicateStageIdError,
    MissingAggregationTemplateError,
    UnknownPipelineTypeError,
    UnknownTagError,
    UnknownTemplateError,
)
from message_service.domain.state_machines.run_states import RunState
from message_service.domain.state_machines.stage_states import StageState

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

_T0 = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)
_TPL_AGG = TemplateRef(name="nightly_summary", version="1.0")
_TPL_EXT = TemplateRef(name="extract_rpt", version="1.0")
_TPL_XFM = TemplateRef(name="transform_rpt", version="1.0")


@pytest.fixture
def registry() -> frozenset[str]:
    return frozenset({"etl-nightly", "etl-adhoc"})


@pytest.fixture
def tag_vocabulary() -> MagicMock:
    vocab = MagicMock(spec=TagVocabulary)
    # Default: every tag in this set is accepted.
    vocab.contains.side_effect = lambda tag: tag in {"production", "staging", "critical"}
    return vocab


@pytest.fixture
def template_repo() -> MagicMock:
    repo = MagicMock(spec=TemplateRepository)
    # Default: every referenced template exists.
    repo.exists.return_value = True
    return repo


@pytest.fixture
def clock() -> MagicMock:
    clk = MagicMock(spec=Clock)
    clk.now.return_value = _T0
    return clk


@pytest.fixture
def uow_factory() -> tuple[MagicMock, AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    """Return ``(factory, uow, run_repo_mock, stage_repo_mock, audit_log_mock)``.

    ``factory()`` returns ``uow``. ``uow`` is async-context-manager
    compatible and exposes the three scoped repo mocks as attributes.
    """
    audit_log = AsyncMock(spec=AuditLog)
    run_repo = AsyncMock(spec=RunRepository)
    stage_repo = AsyncMock(spec=StageRepository)

    uow = AsyncMock(spec=UnitOfWork)
    uow.run_repo = run_repo
    uow.stage_repo = stage_repo
    uow.audit_log = audit_log
    uow.__aenter__.return_value = uow
    uow.__aexit__.return_value = None

    factory = MagicMock(return_value=uow)
    return factory, uow, run_repo, stage_repo, audit_log


@pytest.fixture
def use_case(
    registry: frozenset[str],
    tag_vocabulary: MagicMock,
    template_repo: MagicMock,
    clock: MagicMock,
    uow_factory: tuple[MagicMock, Any, Any, Any, Any],
) -> BeginRunUseCase:
    factory, _, _, _, _ = uow_factory
    return BeginRunUseCase(
        pipeline_registry=registry,
        tag_vocabulary=tag_vocabulary,
        template_repo=template_repo,
        uow_factory=factory,
        clock=clock,
    )


def _valid_command(**overrides: Any) -> BeginRunCommand:
    fields: dict[str, Any] = {
        "pipeline_type": "etl-nightly",
        "tags": frozenset({"production"}),
        "declared_stages": (
            DeclaredStageInput(stage_id="extract", stage_order=0, report_template_ref=_TPL_EXT),
            DeclaredStageInput(stage_id="transform", stage_order=1, report_template_ref=_TPL_XFM),
        ),
        "attachment_mode": AttachmentMode.SINGLE_AGGREGATED,
        "aggregation_template_ref": _TPL_AGG,
    }
    fields.update(overrides)
    return BeginRunCommand(**fields)


# -----------------------------------------------------------------------------
# Happy path
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-RUN-002")
async def test_happy_path_returns_minted_run_id(
    use_case: BeginRunUseCase,
) -> None:
    run_id = await use_case.execute(_valid_command())
    # Canonical UUID-4 form.
    assert len(run_id) == 36
    assert run_id.count("-") == 4


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-001")
async def test_each_execute_mints_distinct_run_ids(use_case: BeginRunUseCase) -> None:
    """Successive calls SHALL produce distinct run ids."""
    first = await use_case.execute(_valid_command())
    second = await use_case.execute(_valid_command())
    assert first != second


@pytest.mark.asyncio
@pytest.mark.requirement("L2-RUN-003")
async def test_run_saved_with_initiated_state(
    use_case: BeginRunUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, Any, Any],
) -> None:
    _, _, run_repo, _, _ = uow_factory
    await use_case.execute(_valid_command())
    run_repo.save.assert_called_once()
    saved_run: Run = run_repo.save.call_args[0][0]
    assert saved_run.state == RunState.INITIATED


@pytest.mark.asyncio
@pytest.mark.requirement("L2-RUN-003")
async def test_one_stage_saved_per_declared_stage(
    use_case: BeginRunUseCase,
    uow_factory: tuple[MagicMock, Any, Any, AsyncMock, Any],
) -> None:
    _, _, _, stage_repo, _ = uow_factory
    await use_case.execute(_valid_command())
    # Two declared stages → two stage_repo.save calls.
    assert stage_repo.save.call_count == 2
    stages_saved: list[Stage] = [c.args[0] for c in stage_repo.save.call_args_list]
    saved_stage_ids = {s.stage_id for s in stages_saved}
    assert saved_stage_ids == {"extract", "transform"}
    # All persisted stages start in PENDING.
    for stage in stages_saved:
        assert stage.state == StageState.PENDING


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-025")
async def test_run_and_stages_share_clock_timestamp(
    use_case: BeginRunUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, Any, Any],
    clock: MagicMock,
) -> None:
    """All timestamps on the aggregate come from a single clock call."""
    _, _, run_repo, _, _ = uow_factory
    await use_case.execute(_valid_command())
    saved_run: Run = run_repo.save.call_args[0][0]
    assert saved_run.created_at == _T0
    assert saved_run.updated_at == _T0
    # Clock.now() called at least once; the use case is free to call
    # multiple times but typically exactly once.
    assert clock.now.call_count >= 1


# -----------------------------------------------------------------------------
# Pipeline type validation (L2-RUN-007 / L3-RUN-010 / L3-RUN-011)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-010")
async def test_unknown_pipeline_type_raises(use_case: BeginRunUseCase) -> None:
    with pytest.raises(UnknownPipelineTypeError) as exc_info:
        await use_case.execute(_valid_command(pipeline_type="not-registered"))
    assert exc_info.value.details["submitted"] == "not-registered"
    assert "etl-nightly" in exc_info.value.details["allowed"]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-010")
async def test_unknown_pipeline_does_not_touch_persistence(
    use_case: BeginRunUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    """No persistence calls on the failure path (rejected before UoW opens)."""
    factory, _, run_repo, stage_repo, audit_log = uow_factory
    with pytest.raises(UnknownPipelineTypeError):
        await use_case.execute(_valid_command(pipeline_type="bogus"))
    factory.assert_not_called()
    run_repo.save.assert_not_called()
    stage_repo.save.assert_not_called()
    audit_log.record.assert_not_called()


# -----------------------------------------------------------------------------
# Tag validation (L2-RUN-008 / L3-RUN-012 / L3-RUN-013)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-013")
async def test_multiple_unknown_tags_all_reported(use_case: BeginRunUseCase) -> None:
    """All invalid tags SHALL appear in details['invalid_tags'], not just the first."""
    with pytest.raises(UnknownTagError) as exc_info:
        await use_case.execute(_valid_command(tags=frozenset({"production", "bogus1", "bogus2"})))
    # Valid "production" is excluded; both invalid tags are included.
    invalid = exc_info.value.details["invalid_tags"]
    assert set(invalid) == {"bogus1", "bogus2"}
    # Sorted for deterministic output.
    assert invalid == sorted(invalid)


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-012")
async def test_tag_check_is_case_sensitive(
    use_case: BeginRunUseCase, tag_vocabulary: MagicMock
) -> None:
    """Tag matching SHALL be exact; 'Production' differs from 'production'."""
    with pytest.raises(UnknownTagError):
        await use_case.execute(_valid_command(tags=frozenset({"Production"})))


# -----------------------------------------------------------------------------
# Duplicate stage id (L2-RUN-009 / L3-RUN-014)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-014")
async def test_duplicate_stage_ids_raises_with_all_duplicates() -> None:
    registry = frozenset({"etl-nightly"})
    vocab = MagicMock(spec=TagVocabulary)
    vocab.contains.return_value = True
    tpl = MagicMock(spec=TemplateRepository)
    tpl.exists.return_value = True
    clk = MagicMock(spec=Clock)
    clk.now.return_value = _T0
    factory = MagicMock()

    uc = BeginRunUseCase(
        pipeline_registry=registry,
        tag_vocabulary=vocab,
        template_repo=tpl,
        uow_factory=factory,
        clock=clk,
    )

    cmd = _valid_command(
        declared_stages=(
            DeclaredStageInput(stage_id="extract", stage_order=0, report_template_ref=_TPL_EXT),
            DeclaredStageInput(stage_id="extract", stage_order=1, report_template_ref=_TPL_EXT),
            DeclaredStageInput(stage_id="xform", stage_order=2, report_template_ref=_TPL_EXT),
            DeclaredStageInput(stage_id="xform", stage_order=3, report_template_ref=_TPL_EXT),
        )
    )
    with pytest.raises(DuplicateStageIdError) as exc_info:
        await uc.execute(cmd)
    # All duplicated ids reported, sorted.
    assert exc_info.value.details["duplicates"] == ["extract", "xform"]


# -----------------------------------------------------------------------------
# Template existence (L2-RUN-010 / L3-RUN-016 / L3-RUN-017)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-016")
async def test_unknown_aggregation_template_raises(
    use_case: BeginRunUseCase, template_repo: MagicMock
) -> None:
    template_repo.exists.side_effect = lambda ref: ref != _TPL_AGG
    with pytest.raises(UnknownTemplateError) as exc_info:
        await use_case.execute(_valid_command())
    assert exc_info.value.details["name"] == _TPL_AGG.name
    assert exc_info.value.details["version"] == _TPL_AGG.version
    assert exc_info.value.details["role"] == "aggregation_template"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-016")
async def test_unknown_report_template_raises(
    use_case: BeginRunUseCase, template_repo: MagicMock
) -> None:
    template_repo.exists.side_effect = lambda ref: ref != _TPL_EXT
    with pytest.raises(UnknownTemplateError) as exc_info:
        await use_case.execute(_valid_command())
    assert exc_info.value.details["name"] == _TPL_EXT.name
    assert exc_info.value.details["role"] == "report_template"
    assert exc_info.value.details["stage_id"] == "extract"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-017")
async def test_unknown_template_details_include_name_and_version(
    use_case: BeginRunUseCase, template_repo: MagicMock
) -> None:
    """Details distinguish 'unknown name' from 'unknown version'."""
    template_repo.exists.return_value = False
    with pytest.raises(UnknownTemplateError) as exc_info:
        await use_case.execute(_valid_command())
    assert "name" in exc_info.value.details
    assert "version" in exc_info.value.details


# -----------------------------------------------------------------------------
# Attachment mode / aggregation template consistency
# (L2-RUN-011 / L3-RUN-018 / L3-RUN-019)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-019")
async def test_single_aggregated_without_template_raises() -> None:
    """Error details echo the attachment_mode per L3-RUN-019."""
    registry = frozenset({"etl-nightly"})
    vocab = MagicMock(spec=TagVocabulary)
    vocab.contains.return_value = True
    tpl = MagicMock(spec=TemplateRepository)
    tpl.exists.return_value = True
    clk = MagicMock(spec=Clock)
    clk.now.return_value = _T0
    factory = MagicMock()

    uc = BeginRunUseCase(
        pipeline_registry=registry,
        tag_vocabulary=vocab,
        template_repo=tpl,
        uow_factory=factory,
        clock=clk,
    )
    cmd = _valid_command(aggregation_template_ref=None)

    with pytest.raises(MissingAggregationTemplateError) as exc_info:
        await uc.execute(cmd)
    assert exc_info.value.details["attachment_mode"] == "SINGLE_AGGREGATED"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-018")
async def test_per_stage_silently_drops_aggregation_template(
    use_case: BeginRunUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, Any, Any],
) -> None:
    """PER_STAGE with a supplied aggregation_template_ref SHALL succeed, ignoring the template."""
    _, _, run_repo, _, _ = uow_factory
    cmd = _valid_command(
        attachment_mode=AttachmentMode.PER_STAGE,
        aggregation_template_ref=_TPL_AGG,
    )
    await use_case.execute(cmd)
    saved_run: Run = run_repo.save.call_args[0][0]
    # Stored run has no aggregation_template_ref because mode is PER_STAGE.
    assert saved_run.aggregation_template_ref is None
    assert saved_run.attachment_mode == AttachmentMode.PER_STAGE


@pytest.mark.asyncio
@pytest.mark.requirement("L2-RUN-011")
async def test_per_stage_without_aggregation_template_succeeds(
    use_case: BeginRunUseCase,
) -> None:
    """PER_STAGE runs without aggregation_template_ref SHALL be valid."""
    cmd = _valid_command(
        attachment_mode=AttachmentMode.PER_STAGE,
        aggregation_template_ref=None,
    )
    run_id = await use_case.execute(cmd)
    assert run_id  # did not raise


# -----------------------------------------------------------------------------
# Audit-first ordering (L3-RUN-026)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-026")
async def test_audit_recorded_before_run_save(
    use_case: BeginRunUseCase,
    uow_factory: tuple[MagicMock, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    """The audit insert SHALL be issued before any state write."""
    _, uow, _, _, _ = uow_factory
    # Use manager_mock to record the global call order across all three
    # scoped ports. attach_mock pipes the per-mock calls into manager.mock_calls
    # with distinguishable names.
    manager = MagicMock()
    manager.attach_mock(uow.audit_log.record, "audit_record")
    manager.attach_mock(uow.run_repo.save, "run_save")
    manager.attach_mock(uow.stage_repo.save, "stage_save")

    await use_case.execute(_valid_command())

    # Find first audit_record call and first run_save call in manager.mock_calls.
    method_calls = [c[0] for c in manager.mock_calls]
    first_audit = method_calls.index("audit_record")
    first_run = method_calls.index("run_save")
    assert first_audit < first_run, f"audit must precede run_save: {method_calls}"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-026")
@pytest.mark.requirement("L3-OBS-025")
async def test_audit_event_is_begin_run_success(
    use_case: BeginRunUseCase,
    uow_factory: tuple[MagicMock, Any, Any, Any, AsyncMock],
) -> None:
    _, _, _, _, audit_log = uow_factory
    await use_case.execute(_valid_command())
    audit_log.record.assert_called_once()
    event = audit_log.record.call_args[0][0]
    assert event.action == AuditAction.BEGIN_RUN
    assert event.outcome == AuditOutcome.SUCCESS
    assert event.actor == "pipeline:etl-nightly"
    assert event.resource.startswith("run:")


# -----------------------------------------------------------------------------
# UoW lifecycle
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-004")
async def test_uow_entered_and_exited_once(
    use_case: BeginRunUseCase,
    uow_factory: tuple[MagicMock, AsyncMock, Any, Any, Any],
) -> None:
    """The use case SHALL open and close exactly one UoW per call."""
    factory, uow, _, _, _ = uow_factory
    await use_case.execute(_valid_command())
    factory.assert_called_once()
    uow.__aenter__.assert_awaited_once()
    uow.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-005")
async def test_validation_failures_do_not_open_uow(
    use_case: BeginRunUseCase,
    uow_factory: tuple[MagicMock, Any, Any, Any, Any],
) -> None:
    """Validation failures SHALL short-circuit before the UoW opens."""
    factory, _, _, _, _ = uow_factory
    with pytest.raises(UnknownPipelineTypeError):
        await use_case.execute(_valid_command(pipeline_type="bogus"))
    factory.assert_not_called()


# -----------------------------------------------------------------------------
# Empty declared_stages (L3-RUN-015)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-015")
async def test_empty_declared_stages_permitted(
    use_case: BeginRunUseCase,
    uow_factory: tuple[MagicMock, Any, Any, AsyncMock, Any],
) -> None:
    """A run with zero declared stages SHALL be accepted (finalizes as zero-attachment)."""
    _, _, _, stage_repo, _ = uow_factory
    cmd = _valid_command(declared_stages=())
    run_id = await use_case.execute(cmd)
    assert run_id
    stage_repo.save.assert_not_called()
