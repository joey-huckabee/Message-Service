"""Use case: ``BeginRun``.

Mints a new run, validates every input, persists the ``Run`` +
per-stage ``Stage`` rows, records an audit event, and returns the
minted :data:`RunId`.

Validation order (fail-fast; emit audit=FAILURE before raising):

1. ``pipeline_type`` against the configured pipeline registry.
2. Every ``tag`` against the tag vocabulary. Collect *all* invalid tags
   before raising (L3-RUN-013).
3. Stage-id uniqueness within ``declared_stages`` (L3-RUN-014). Collect
   *all* duplicates.
4. Every template ref — ``aggregation_template_ref`` (when used) and
   every declared stage's ``report_template_ref`` (L3-RUN-016).
5. ``SINGLE_AGGREGATED`` + missing ``aggregation_template_ref`` →
   :class:`MissingAggregationTemplateError`. (PER_STAGE silently
   ignores any supplied aggregation template per L3-RUN-018.)

On success the use case opens a :class:`UnitOfWork`, records the
``BEGIN_RUN`` audit event *first* (L3-RUN-026), persists the ``Run``,
then persists one ``Stage`` per declared stage, and returns the minted
:data:`RunId`. Transaction commits on clean exit; any exception rolls
back.

Requirement references
----------------------
L1-RUN-002 (mint UUID), L1-RUN-003 (validate all fields)
L2-RUN-001, L2-RUN-003, L2-RUN-007, L2-RUN-008, L2-RUN-009, L2-RUN-010,
    L2-RUN-011
L3-RUN-001, L3-RUN-004, L3-RUN-013, L3-RUN-014, L3-RUN-016,
    L3-RUN-017, L3-RUN-018, L3-RUN-019, L3-RUN-025, L3-RUN-026
"""

from __future__ import annotations

from collections.abc import Callable

from message_service.application.ports.clock import Clock, iso_z
from message_service.application.ports.tag_vocabulary import TagVocabulary
from message_service.application.ports.template_repository import TemplateRepository
from message_service.application.ports.unit_of_work import UnitOfWork
from message_service.application.use_cases.begin_run_command import BeginRunCommand
from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.domain.aggregates.declared_stage import DeclaredStage
from message_service.domain.aggregates.run import AttachmentMode, Run
from message_service.domain.aggregates.stage import Stage
from message_service.domain.errors import (
    DuplicateStageIdError,
    MissingAggregationTemplateError,
    UnknownPipelineTypeError,
    UnknownTagError,
    UnknownTemplateError,
)
from message_service.domain.ids import RunId, StageId, new_run_id
from message_service.domain.state_machines.run_states import RunState
from message_service.domain.state_machines.stage_states import StageState


class BeginRunUseCase:
    """Orchestrator for the ``BeginRun`` gRPC.

    Dependencies (all ports) are injected via constructor; no global
    state or service-locator lookups. Use cases are typically
    constructed once at service start and re-used per request.

    Attributes:
        pipeline_registry: Frozen set of pipeline types accepted by
            this deployment, loaded from
            ``config.pipelines.registered``. O(1) membership test.
        tag_vocabulary: :class:`TagVocabulary` port for per-tag
            validity.
        template_repo: :class:`TemplateRepository` port for template
            existence.
        uow_factory: Zero-argument callable returning a fresh
            :class:`UnitOfWork`. Each ``execute`` call gets its own
            transaction.
        clock: :class:`Clock` port for timestamps.
    """

    def __init__(
        self,
        *,
        pipeline_registry: frozenset[str],
        tag_vocabulary: TagVocabulary,
        template_repo: TemplateRepository,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
    ) -> None:
        """Construct the use case with its port dependencies.

        Args:
            pipeline_registry: Frozen set of accepted pipeline types.
            tag_vocabulary: Port for per-tag validity lookup.
            template_repo: Port for template existence checks.
            uow_factory: Zero-argument callable returning a fresh UoW
                per ``execute`` call.
            clock: Port for current UTC timestamp.
        """
        self._pipeline_registry = pipeline_registry
        self._tag_vocabulary = tag_vocabulary
        self._template_repo = template_repo
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, cmd: BeginRunCommand) -> RunId:
        """Validate, mint, persist, audit, and return the new ``RunId``.

        Args:
            cmd: Validated input command. Pydantic has already ensured
                basic shape (non-empty pipeline_type, non-negative
                stage_orders, etc.).

        Returns:
            The minted :data:`RunId`.

        Raises:
            UnknownPipelineTypeError: pipeline_type not in registry.
            UnknownTagError: one or more tags not in vocabulary.
            DuplicateStageIdError: duplicate stage_id in declared_stages.
            UnknownTemplateError: a referenced template is not in the
                manifest.
            MissingAggregationTemplateError: SINGLE_AGGREGATED mode with
                no aggregation_template_ref.
            PersistenceError: Transaction failed; nothing persisted.
        """
        # ---------------------------------------------------------------
        # 1. Pipeline-type validation (L2-RUN-007 / L3-RUN-010)
        # ---------------------------------------------------------------
        if cmd.pipeline_type not in self._pipeline_registry:
            raise UnknownPipelineTypeError(
                f"pipeline_type not registered: {cmd.pipeline_type!r}",
                details={
                    "submitted": cmd.pipeline_type,
                    "allowed": sorted(self._pipeline_registry),
                },
            )

        # ---------------------------------------------------------------
        # 2. Tag validation (L2-RUN-008 / L3-RUN-012 / L3-RUN-013)
        # ---------------------------------------------------------------
        invalid_tags = sorted(t for t in cmd.tags if not self._tag_vocabulary.contains(t))
        if invalid_tags:
            raise UnknownTagError(
                f"unknown tag(s): {invalid_tags}",
                details={"invalid_tags": invalid_tags},
            )

        # ---------------------------------------------------------------
        # 3. Duplicate stage-id detection (L2-RUN-009 / L3-RUN-014)
        # ---------------------------------------------------------------
        stage_id_counts: dict[str, int] = {}
        for ds in cmd.declared_stages:
            stage_id_counts[ds.stage_id] = stage_id_counts.get(ds.stage_id, 0) + 1
        duplicates = sorted(sid for sid, count in stage_id_counts.items() if count > 1)
        if duplicates:
            raise DuplicateStageIdError(
                f"duplicate stage_id(s) in declared_stages: {duplicates}",
                details={"duplicates": duplicates},
            )

        # ---------------------------------------------------------------
        # 4. Attachment-mode / aggregation-template consistency
        #    (L2-RUN-011 / L3-RUN-018 / L3-RUN-019)
        # ---------------------------------------------------------------
        if (
            cmd.attachment_mode is AttachmentMode.SINGLE_AGGREGATED
            and cmd.aggregation_template_ref is None
        ):
            raise MissingAggregationTemplateError(
                "SINGLE_AGGREGATED requires aggregation_template_ref",
                details={"attachment_mode": cmd.attachment_mode.value},
            )

        # ---------------------------------------------------------------
        # 5. Template existence (L2-RUN-010 / L3-RUN-016 / L3-RUN-017)
        # ---------------------------------------------------------------
        # PER_STAGE silently ignores aggregation_template_ref per L3-RUN-018.
        if (
            cmd.attachment_mode is AttachmentMode.SINGLE_AGGREGATED
            and cmd.aggregation_template_ref is not None
            and not self._template_repo.exists(cmd.aggregation_template_ref)
        ):
            raise UnknownTemplateError(
                f"unknown aggregation_template: {cmd.aggregation_template_ref!r}",
                details={
                    "name": cmd.aggregation_template_ref.name,
                    "version": cmd.aggregation_template_ref.version,
                    "role": "aggregation_template",
                },
            )
        for ds in cmd.declared_stages:
            if not self._template_repo.exists(ds.report_template_ref):
                raise UnknownTemplateError(
                    f"unknown report_template for stage {ds.stage_id!r}: "
                    f"{ds.report_template_ref!r}",
                    details={
                        "name": ds.report_template_ref.name,
                        "version": ds.report_template_ref.version,
                        "role": "report_template",
                        "stage_id": ds.stage_id,
                    },
                )

        # ---------------------------------------------------------------
        # 6. Construct aggregate values
        # ---------------------------------------------------------------
        run_id = new_run_id()  # L3-RUN-001: exactly one uuid4() per BeginRun
        now = self._clock.now()

        # PER_STAGE mode: silently drop any supplied aggregation_template_ref.
        effective_aggregation_ref = (
            cmd.aggregation_template_ref
            if cmd.attachment_mode is AttachmentMode.SINGLE_AGGREGATED
            else None
        )

        declared = tuple(
            DeclaredStage(
                stage_id=StageId(ds.stage_id),
                stage_order=ds.stage_order,
                report_template_ref=ds.report_template_ref,
            )
            for ds in cmd.declared_stages
        )

        run = Run(
            run_id=run_id,
            pipeline_type=cmd.pipeline_type,
            tags=cmd.tags,
            declared_stages=declared,
            state=RunState.INITIATED,
            attachment_mode=cmd.attachment_mode,
            aggregation_template_ref=effective_aggregation_ref,
            subscription_predicate_tags=cmd.tags,
            created_at=now,
            updated_at=now,
        )

        initial_stages = tuple(
            Stage(
                run_id=run_id,
                stage_id=ds.stage_id,
                state=StageState.PENDING,
                report_template_ref=ds.report_template_ref,
            )
            for ds in declared
        )

        audit_event = AuditEvent(
            timestamp=now,
            action=AuditAction.BEGIN_RUN,
            actor=f"pipeline:{cmd.pipeline_type}",
            resource=f"run:{run_id}",
            outcome=AuditOutcome.SUCCESS,
            details={
                "pipeline_type": cmd.pipeline_type,
                "tags": sorted(cmd.tags),
                "declared_stage_ids": [ds.stage_id for ds in declared],
                "attachment_mode": cmd.attachment_mode.value,
                "timestamp": iso_z(now),
            },
        )

        # ---------------------------------------------------------------
        # 7. Persist in one transaction: audit first, then run, then stages.
        #    L3-RUN-026 enforces audit-before-state ordering.
        # ---------------------------------------------------------------
        async with self._uow_factory() as uow:
            await uow.audit_log.record(audit_event)
            await uow.run_repo.save(run)
            for stage in initial_stages:
                await uow.stage_repo.save(stage)

        return run_id


__all__ = ["BeginRunUseCase"]
