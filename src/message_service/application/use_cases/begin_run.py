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
from typing import cast

from message_service.application.ports.clock import Clock, iso_z
from message_service.application.ports.metrics_recorder import (
    MetricsRecorder,
    NoOpMetricsRecorder,
)
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
from message_service.domain.aggregates.template_ref import TemplateRef
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

# L3-TMPL-009: the literal sentinel string `"latest"` (case-sensitive,
# lowercase). Any other value is treated as an explicit version and is
# passed through resolution unchanged.
_LATEST_SENTINEL = "latest"


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
        metrics_recorder: MetricsRecorder | None = None,
    ) -> None:
        """Construct the use case with its port dependencies.

        Args:
            pipeline_registry: Frozen set of accepted pipeline types.
            tag_vocabulary: Port for per-tag validity lookup.
            template_repo: Port for template existence checks.
            uow_factory: Zero-argument callable returning a fresh UoW
                per ``execute`` call.
            clock: Port for current UTC timestamp.
            metrics_recorder: Port for L1-OBS-002 metrics. Defaults
                to a NoOp instance for tests; production passes the
                Prometheus adapter from bootstrap.
        """
        self._pipeline_registry = pipeline_registry
        self._tag_vocabulary = tag_vocabulary
        self._template_repo = template_repo
        self._uow_factory = uow_factory
        self._clock = clock
        self._metrics = metrics_recorder or NoOpMetricsRecorder()

    def _maybe_resolve_latest(
        self,
        ref: TemplateRef | None,
        *,
        role: str,
        stage_id: str | None = None,
    ) -> TemplateRef | None:
        """Resolve a `"latest"` sentinel to a pinned version, or pass through.

        Per L3-TMPL-009/010: when ``ref.version`` is the literal
        sentinel string ``"latest"``, call
        :meth:`TemplateRepository.resolve_latest` to obtain a
        :class:`TemplateRef` with the highest-semver canonical
        version. Any other version is passed through unchanged
        (validated separately by the existence check at step 5b).

        ``None`` input passes through (the caller is iterating over
        an optional aggregation_template_ref).

        Failure path: ``resolve_latest`` raises
        :class:`UnknownTemplateError` when no manifest entry matches
        the ``name``. We catch and re-raise with the same role-aware
        details shape the existence-check errors use, so callers see
        a consistent error envelope across the two failure modes
        (unknown name vs. unknown version).
        """
        if ref is None or ref.version != _LATEST_SENTINEL:
            return ref
        try:
            return self._template_repo.resolve_latest(ref.name)
        except UnknownTemplateError as exc:
            details: dict[str, str] = {
                "name": ref.name,
                "version": ref.version,
                "role": role,
            }
            if stage_id is not None:
                details["stage_id"] = stage_id
            raise UnknownTemplateError(
                f"unknown {role}: no manifest entries for name {ref.name!r}",
                details=details,
            ) from exc

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
        # 5a. Resolve any `"latest"` version sentinels (L3-TMPL-009/010 /
        #     L3-TMPL-011). Resolution happens BEFORE the existence
        #     check (5b) and BEFORE the aggregate is constructed (6),
        #     so the Run aggregate carries pinned versions, not the
        #     literal sentinel. Subsequent manifest updates SHALL NOT
        #     mutate already-initiated runs (L3-TMPL-011 freeze).
        #
        #     PER_STAGE silently ignores any supplied
        #     aggregation_template_ref (L3-RUN-018), so its resolution is
        #     gated on SINGLE_AGGREGATED mode — a stray
        #     ``aggregation_template_ref`` (including a ``"latest"``
        #     sentinel whose name has no manifest entry) on a PER_STAGE
        #     request must NOT trigger resolution/validation and reject an
        #     otherwise-valid run.
        # ---------------------------------------------------------------
        resolved_aggregation_ref = (
            self._maybe_resolve_latest(
                cmd.aggregation_template_ref,
                role="aggregation_template",
            )
            if cmd.attachment_mode is AttachmentMode.SINGLE_AGGREGATED
            else None
        )
        # _maybe_resolve_latest always returns a non-None TemplateRef when
        # given a non-None input. The cast is purely for type-narrowing.
        resolved_stage_refs: tuple[TemplateRef, ...] = tuple(
            cast(
                "TemplateRef",
                self._maybe_resolve_latest(
                    ds.report_template_ref,
                    role="report_template",
                    stage_id=ds.stage_id,
                ),
            )
            for ds in cmd.declared_stages
        )

        # ---------------------------------------------------------------
        # 5b. Template existence (L2-RUN-010 / L3-RUN-016 / L3-RUN-017)
        # ---------------------------------------------------------------
        # PER_STAGE silently ignores aggregation_template_ref per L3-RUN-018.
        if (
            cmd.attachment_mode is AttachmentMode.SINGLE_AGGREGATED
            and resolved_aggregation_ref is not None
            and not self._template_repo.exists(resolved_aggregation_ref)
        ):
            raise UnknownTemplateError(
                f"unknown aggregation_template: {resolved_aggregation_ref!r}",
                details={
                    "name": resolved_aggregation_ref.name,
                    "version": resolved_aggregation_ref.version,
                    "role": "aggregation_template",
                },
            )
        for ds, resolved_ref in zip(cmd.declared_stages, resolved_stage_refs, strict=True):
            if not self._template_repo.exists(resolved_ref):
                raise UnknownTemplateError(
                    f"unknown report_template for stage {ds.stage_id!r}: {resolved_ref!r}",
                    details={
                        "name": resolved_ref.name,
                        "version": resolved_ref.version,
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
        # Use the resolved ref so any `"latest"` from the request lands
        # on the aggregate as the pinned canonical version.
        effective_aggregation_ref = (
            resolved_aggregation_ref
            if cmd.attachment_mode is AttachmentMode.SINGLE_AGGREGATED
            else None
        )

        declared = tuple(
            DeclaredStage(
                stage_id=StageId(ds.stage_id),
                stage_order=ds.stage_order,
                report_template_ref=resolved_ref,
            )
            for ds, resolved_ref in zip(cmd.declared_stages, resolved_stage_refs, strict=True)
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

        # L1-OBS-002 / L3-OBS-009: emit transition metrics after the
        # commit. Metric writes after-the-fact never roll back, but
        # the events they describe are durable.
        self._metrics.record_run_state_transition(run.state)
        for stage in initial_stages:
            self._metrics.record_stage_state_transition(stage.state)

        return run_id


__all__ = ["BeginRunUseCase"]
