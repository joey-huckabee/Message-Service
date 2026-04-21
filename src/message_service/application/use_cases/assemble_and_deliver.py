"""Use case: ``AssembleAndDeliver`` (background phase of FinalizeRun).

Scheduled by :class:`FinalizeRunUseCase` via :class:`BackgroundTaskScheduler`
after the synchronous phase commits the ``AGGREGATING -> READY``
transition. This use case takes a ``run_id`` and produces one email
delivered via the configured :class:`Mailer`, or a ``FAILED`` terminal
state with a structured audit record if any expected error path is
hit.

Workflow
--------

1. Transition ``READY -> SENDING`` (UoW #1, audit first).
2. Load the run and its stages. Sort stages by ``(stage_order,
   stage_id)`` per L2-AGGR-007 / L2-AGGR-008. Stages still in
   ``PENDING`` at finalization time are excluded from the report
   (they will be handled by the orphan sweeper).
3. Render each stage's report template with its stored context. A
   stage with ``report_context_json is None`` or an empty rendered
   fragment is "empty" per L3-AGGR-008 and participates differently
   depending on attachment mode.
4. Build attachments:

   * ``SINGLE_AGGREGATED`` mode (L2-AGGR-004, L3-AGGR-006): render
     the run's aggregation template once with a context of
     ``{run_id, pipeline_type, run_metadata, stages: [...]}``, where
     ``stages`` is the ordered list of ``{stage_id, stage_order,
     rendered_html}`` dicts. Produces one attachment named
     ``{pipeline_type}_{run_id}.html``.
   * ``PER_STAGE`` mode (L2-AGGR-005): each stage with a non-empty
     rendered fragment becomes its own attachment named
     ``{pipeline_type}_{run_id}_{stage_id}.html``. Stages with empty
     reports are excluded (L3-AGGR-008, L3-AGGR-009).

5. Render the email body via the configured
   ``templates.email_body_template_ref`` with a context containing
   the run metadata and the email-body contributions from each stage
   (ordered per L3-AGGR-005: BEFORE_STAGES_SUMMARY, then the default
   summary, then AFTER_STAGES_SUMMARY). *Email-body ordering within
   BEFORE/AFTER buckets is by stage order; the default summary block
   is assembled from stage identifiers — no stage-supplied content.*
6. Resolve recipients via
   :meth:`SubscriptionRepository.list_recipients_for_run`. If the
   set is empty, skip :meth:`Mailer.send` and transition to ``SENT``
   with ``recipient_count=0`` in the audit details.
7. Otherwise build an :class:`OutboundEmail` and call
   :meth:`Mailer.send`.
8. Transition ``SENDING -> SENT`` (UoW #2) with a ``SEND_REPORT``
   audit event carrying recipient count and addresses (L2-MAIL-012).

Error handling
--------------

Expected domain errors (:class:`TemplateRenderError`,
:class:`RenderedSizeExceededError`, :class:`ContextSizeExceededError`,
:class:`EmailDeliveryError`) are caught, translated into
``SENDING -> FAILED`` with a structured ``reason`` in the audit
details, and swallowed (the scheduler would log them anyway).

Unexpected errors propagate; the
:class:`BackgroundTaskScheduler` adapter catches them at the task
boundary for logging. The orphan sweeper eventually reclaims runs
stuck in ``SENDING`` after ``sweeper.run_timeout_seconds``.

Requirement references
----------------------
L1-RUN-004 (assembly and delivery triggered by FinalizeRun)
L1-AGGR-002, L1-AGGR-003 (attachment modes, stage ordering)
L1-SUB-004 (recipient list via subscription union)
L1-MAIL-001, L1-MAIL-005 (SMTP delivery, audit)
L2-AGGR-004, L2-AGGR-005, L2-AGGR-006, L2-AGGR-007, L2-AGGR-008
L2-MAIL-012 (delivery audit fields)
L3-AGGR-006 (aggregation template context shape)
L3-AGGR-007 (rendered-size failure)
L3-AGGR-008, L3-AGGR-009 (empty-report handling)
L3-AGGR-010, L3-AGGR-011 (filename sanitization)
L3-RUN-026 (audit before state change)
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from message_service.application.ports.clock import Clock, iso_z
from message_service.application.ports.mailer import (
    EmailAttachment,
    Mailer,
    OutboundEmail,
)
from message_service.application.ports.template_renderer import TemplateRenderer
from message_service.application.ports.unit_of_work import UnitOfWork
from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.domain.aggregates.run import AttachmentMode, Run
from message_service.domain.aggregates.stage import Stage
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import (
    ContextSizeExceededError,
    EmailDeliveryError,
    RenderedSizeExceededError,
    TemplateRenderError,
)
from message_service.domain.ids import RunId
from message_service.domain.state_machines.run_states import (
    RunState,
)
from message_service.domain.state_machines.run_states import (
    transition as transition_run,
)
from message_service.domain.state_machines.stage_states import StageState

# -----------------------------------------------------------------------------
# Filename sanitization (L3-AGGR-010, L3-AGGR-011)
# -----------------------------------------------------------------------------

_FILENAME_UNSAFE = re.compile(r"[^a-zA-Z0-9._-]")
_MAX_FILENAME_BYTES = 255


def _sanitize_filename_component(component: str) -> str:
    """Replace every char outside ``[A-Za-z0-9._-]`` with ``_`` (L3-AGGR-010)."""
    return _FILENAME_UNSAFE.sub("_", component)


def _build_attachment_filename(
    pipeline_type: str, run_id: RunId, stage_id: str | None = None
) -> str:
    """Compose an attachment filename per L2-AGGR-006.

    Truncates at POSIX ``NAME_MAX`` (255 bytes) per L3-AGGR-011 by
    shortening the middle component of the name while preserving the
    ``.html`` suffix.

    Args:
        pipeline_type: Run's pipeline type; sanitized.
        run_id: Run identifier (already a canonical UUID string; no
            sanitization needed).
        stage_id: When provided, produces a per-stage filename;
            otherwise produces the aggregated filename.

    Returns:
        Filename string, never exceeding 255 bytes.
    """
    pipeline_safe = _sanitize_filename_component(pipeline_type)
    if stage_id is None:
        base = f"{pipeline_safe}_{run_id}"
    else:
        stage_safe = _sanitize_filename_component(stage_id)
        base = f"{pipeline_safe}_{run_id}_{stage_safe}"

    suffix = ".html"
    # Enforce the 255-byte ceiling by truncating the base. UTF-8 byte
    # length is used because POSIX NAME_MAX is measured in bytes.
    max_base_bytes = _MAX_FILENAME_BYTES - len(suffix.encode("utf-8"))
    base_bytes = base.encode("utf-8")
    if len(base_bytes) > max_base_bytes:
        # Naive byte truncation is safe here because every character in
        # the sanitized set is single-byte ASCII. The regex strips
        # anything outside [A-Za-z0-9._-] to underscores before we get
        # here, so there are no multi-byte sequences to split.
        base = base_bytes[:max_base_bytes].decode("ascii", errors="ignore")

    return base + suffix


# -----------------------------------------------------------------------------
# Internal carrier types
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RenderedFragment:
    """A rendered stage report fragment plus its ordering metadata."""

    stage_id: str
    stage_order: int
    rendered_html: str

    @property
    def is_empty(self) -> bool:
        """Empty per L3-AGGR-008: whitespace-only rendered HTML."""
        return not self.rendered_html.strip()


@dataclass(frozen=True, slots=True)
class _FailureReason:
    """Classified failure reason used in FAILED audit details."""

    code: str
    details: dict[str, Any]


# Reason codes used when transitioning to FAILED. Strings rather than
# an enum because L2-MAIL-009 treats these as human-readable audit
# strings, not enforced constants.
_REASON_TEMPLATE_RENDER = "TEMPLATE_RENDER"
_REASON_RENDERED_SIZE_EXCEEDED = "RENDERED_SIZE_EXCEEDED"
_REASON_CONTEXT_SIZE_EXCEEDED = "CONTEXT_SIZE_EXCEEDED"
_REASON_EMAIL_DELIVERY = "EMAIL_DELIVERY"


# -----------------------------------------------------------------------------
# Use case
# -----------------------------------------------------------------------------


class AssembleAndDeliverUseCase:
    """Background workflow: assemble and deliver a finalized run's email.

    Dependencies are constructor-injected. Produced once at service
    start and re-used; the :meth:`execute` coroutine is scheduled
    afresh per run.

    Attributes:
        uow_factory: Zero-argument callable returning a fresh UoW per
            UoW-scoped operation. This workflow opens two UoWs per
            successful run (one for SENDING, one for SENT or FAILED).
        clock: :class:`Clock` port.
        template_renderer: :class:`TemplateRenderer` port.
        mailer: :class:`Mailer` port.
        from_address: Configured ``mail.from_address`` value; threaded
            through to each :class:`OutboundEmail`.
        email_body_template_ref: Configured
            ``templates.email_body_template_ref`` (service-wide in v1;
            see ROADMAP R-TMPL-001 for per-pipeline option).
    """

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        template_renderer: TemplateRenderer,
        mailer: Mailer,
        from_address: str,
        email_body_template_ref: TemplateRef,
    ) -> None:
        """Construct with ports and config values threaded through.

        Args:
            uow_factory: Zero-argument UoW factory.
            clock: Clock port.
            template_renderer: Renderer port.
            mailer: Mailer port.
            from_address: Configured sender address.
            email_body_template_ref: Configured email body template.
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._template_renderer = template_renderer
        self._mailer = mailer
        self._from_address = from_address
        self._email_body_template_ref = email_body_template_ref

    async def execute(self, run_id: RunId) -> None:
        """Drive the assembly-and-delivery workflow for ``run_id``.

        The coroutine is scheduled by
        :class:`FinalizeRunUseCase` after the ``AGGREGATING -> READY``
        commit. It is NOT awaited by the caller; it runs on the event
        loop until completion or error.

        Args:
            run_id: The finalized run to deliver.

        Raises:
            Exception: Unexpected errors propagate to the scheduler's
                task exception handler. Expected domain errors
                (:class:`TemplateRenderError`,
                :class:`RenderedSizeExceededError`,
                :class:`ContextSizeExceededError`,
                :class:`EmailDeliveryError`) are caught and translated
                into ``FAILED`` transitions with structured audit.
        """
        # UoW #1: transition READY -> SENDING. Runs even before any
        # assembly work; if this transition fails, we never touch
        # templates or SMTP.
        async with self._uow_factory() as uow:
            run = await uow.run_repo.get(run_id)
            await self._transition(
                uow=uow,
                run=run,
                to_state=RunState.SENDING,
                audit_action=AuditAction.RUN_STATE_TRANSITION,
                details_extra={},
            )

        # Now do the actual work. Any expected error here leads to a
        # SENDING -> FAILED transition in its own UoW.
        try:
            rendered_fragments = await self._load_and_render_stages(run_id)
            attachments = self._build_attachments(run, rendered_fragments)
            email_body_html = self._render_email_body(run, rendered_fragments)
        except TemplateRenderError as exc:
            await self._finalize_failed(
                run_id=run_id,
                reason=_FailureReason(
                    code=_REASON_TEMPLATE_RENDER,
                    details={"message": str(exc), **(exc.details or {})},
                ),
            )
            return
        except RenderedSizeExceededError as exc:
            await self._finalize_failed(
                run_id=run_id,
                reason=_FailureReason(
                    code=_REASON_RENDERED_SIZE_EXCEEDED,
                    details={"message": str(exc), **(exc.details or {})},
                ),
            )
            return
        except ContextSizeExceededError as exc:
            await self._finalize_failed(
                run_id=run_id,
                reason=_FailureReason(
                    code=_REASON_CONTEXT_SIZE_EXCEEDED,
                    details={"message": str(exc), **(exc.details or {})},
                ),
            )
            return

        # Recipient resolution must run in a UoW (repo is UoW-scoped).
        async with self._uow_factory() as uow:
            recipients = await uow.subscription_repo.list_recipients_for_run(
                pipeline_type=run.pipeline_type, tags=run.tags
            )

        # Zero-recipient short-circuit per design decision: finalize
        # as SENT with recipient_count=0; do not invoke Mailer.
        if not recipients:
            await self._finalize_sent(
                run_id=run_id,
                recipients=frozenset(),
                attachment_count=len(attachments),
            )
            return

        # Non-empty recipients: deliver or fail.
        outbound = OutboundEmail(
            recipients=recipients,
            subject=f"Run {run_id} — {run.pipeline_type}",
            body_html=email_body_html,
            from_address=self._from_address,
            attachments=attachments,
        )

        try:
            await self._mailer.send(outbound)
        except EmailDeliveryError as exc:
            await self._finalize_failed(
                run_id=run_id,
                reason=_FailureReason(
                    code=_REASON_EMAIL_DELIVERY,
                    details={
                        "message": str(exc),
                        "recipient_count": len(recipients),
                        **(exc.details or {}),
                    },
                ),
            )
            return

        await self._finalize_sent(
            run_id=run_id,
            recipients=recipients,
            attachment_count=len(attachments),
        )

    # ------------------------------------------------------------------
    # Workflow steps (extracted for readability + testability)
    # ------------------------------------------------------------------

    async def _load_and_render_stages(self, run_id: RunId) -> list[_RenderedFragment]:
        """Load stages, sort, render each. Reading happens inside a UoW.

        Stages in ``PENDING`` state are excluded from assembly per
        L3-AGGR-008 (only submitted content participates). Stages in
        ``TIMEOUT`` or ``FAILED`` are also excluded.

        Args:
            run_id: Target run.

        Returns:
            Rendered fragments sorted by ``(stage_order, stage_id)``.

        Raises:
            TemplateRenderError: Propagated from renderer.
            RenderedSizeExceededError: Propagated from renderer.
            ContextSizeExceededError: Propagated from renderer.
        """
        async with self._uow_factory() as uow:
            stages: Sequence[Stage] = await uow.stage_repo.list_by_run(run_id)

        # Only stages that actually submitted content participate in
        # assembly. PENDING/TIMEOUT/FAILED are excluded.
        included_states = {StageState.SUBMITTED, StageState.ACCEPTED, StageState.RETRIED}
        submitted = [s for s in stages if s.state in included_states]

        # Sort: primary by stage_order ascending, secondary by stage_id
        # lexicographic ascending (L2-AGGR-007, L2-AGGR-008).
        # NOTE: the StageRepository does not currently return
        # stage_order; it's declared on the Run aggregate. We could
        # look it up there, but for simplicity and to avoid coupling,
        # we fetch the Run and reference its declared_stages tuple
        # which already carries (stage_id, stage_order).
        async with self._uow_factory() as uow:
            run = await uow.run_repo.get(run_id)
        stage_order_by_id: dict[str, int] = {
            ds.stage_id: ds.stage_order for ds in run.declared_stages
        }

        def _sort_key(s: Stage) -> tuple[int, str]:
            return (stage_order_by_id.get(s.stage_id, 0), s.stage_id)

        submitted_sorted = sorted(submitted, key=_sort_key)

        fragments: list[_RenderedFragment] = []
        for stage in submitted_sorted:
            if stage.report_context_json is None:
                rendered_html = ""
            else:
                ctx = json.loads(stage.report_context_json)
                rendered_html = self._template_renderer.render(stage.report_template_ref, ctx)
            fragments.append(
                _RenderedFragment(
                    stage_id=stage.stage_id,
                    stage_order=stage_order_by_id.get(stage.stage_id, 0),
                    rendered_html=rendered_html,
                )
            )
        return fragments

    def _build_attachments(
        self, run: Run, fragments: list[_RenderedFragment]
    ) -> tuple[EmailAttachment, ...]:
        """Build attachments according to attachment mode.

        Args:
            run: The run being assembled.
            fragments: Pre-rendered stage fragments, in presentation
                order.

        Returns:
            Tuple of attachments. May be empty if PER_STAGE and every
            stage's rendered fragment is empty (L3-AGGR-009).

        Raises:
            TemplateRenderError: Aggregation template render failure.
            RenderedSizeExceededError: Aggregation exceeds max size.
        """
        if run.attachment_mode is AttachmentMode.SINGLE_AGGREGATED:
            # Aggregation template context per L3-AGGR-006.
            agg_context: dict[str, Any] = {
                "run_id": run.run_id,
                "pipeline_type": run.pipeline_type,
                "run_metadata": {
                    "tags": sorted(run.tags),
                    "created_at": iso_z(run.created_at),
                },
                "stages": [
                    {
                        "stage_id": f.stage_id,
                        "stage_order": f.stage_order,
                        "rendered_html": f.rendered_html,
                    }
                    for f in fragments
                ],
            }
            # aggregation_template_ref is guaranteed non-None here by
            # Run's __post_init__ invariant for SINGLE_AGGREGATED mode.
            assert run.aggregation_template_ref is not None
            aggregated_html = self._template_renderer.render(
                run.aggregation_template_ref, agg_context
            )
            filename = _build_attachment_filename(run.pipeline_type, run.run_id)
            return (
                EmailAttachment(
                    filename=filename,
                    content_type="text/html; charset=utf-8",
                    content=aggregated_html.encode("utf-8"),
                ),
            )

        # PER_STAGE: one attachment per non-empty fragment. Empty
        # fragments are dropped per L3-AGGR-008.
        attachments: list[EmailAttachment] = []
        for fragment in fragments:
            if fragment.is_empty:
                continue
            filename = _build_attachment_filename(
                run.pipeline_type, run.run_id, stage_id=fragment.stage_id
            )
            attachments.append(
                EmailAttachment(
                    filename=filename,
                    content_type="text/html; charset=utf-8",
                    content=fragment.rendered_html.encode("utf-8"),
                )
            )
        return tuple(attachments)

    def _render_email_body(self, run: Run, fragments: list[_RenderedFragment]) -> str:
        """Render the email body template.

        The email body template receives the stages' identifying
        metadata (not their report contributions — those are in the
        attachments). Custom email body contributions from
        ``SubmitStageReport`` (per L1-AGGR-001) are a future
        enhancement; v1 passes stage identifiers only.

        Args:
            run: The run being assembled.
            fragments: Stage fragments in presentation order.

        Returns:
            Rendered HTML email body.

        Raises:
            TemplateRenderError: Body template render failure.
            RenderedSizeExceededError: Body exceeds max size.
        """
        body_context: dict[str, Any] = {
            "run_id": run.run_id,
            "pipeline_type": run.pipeline_type,
            "run_metadata": {
                "tags": sorted(run.tags),
                "created_at": iso_z(run.created_at),
            },
            "stages": [
                {
                    "stage_id": f.stage_id,
                    "stage_order": f.stage_order,
                    "had_content": not f.is_empty,
                }
                for f in fragments
            ],
            "attachment_mode": run.attachment_mode.value,
        }
        return self._template_renderer.render(self._email_body_template_ref, body_context)

    async def _finalize_sent(
        self,
        run_id: RunId,
        recipients: frozenset[str],
        attachment_count: int,
    ) -> None:
        """Transition SENDING -> SENT with SEND_REPORT audit."""
        async with self._uow_factory() as uow:
            run = await uow.run_repo.get(run_id)
            now = self._clock.now()

            audit_event = AuditEvent(
                timestamp=now,
                action=AuditAction.SEND_REPORT,
                actor="system:assemble_and_deliver",
                resource=f"run:{run_id}",
                outcome=AuditOutcome.SUCCESS,
                details={
                    "run_id": run_id,
                    "recipient_count": len(recipients),
                    "recipient_addresses": sorted(recipients),
                    "attachment_count": attachment_count,
                    "prior_state": run.state.value,
                    "new_state": RunState.SENT.value,
                    "timestamp": iso_z(now),
                },
            )

            transition_run(
                from_state=run.state,
                to_state=RunState.SENT,
                run_id=run_id,
            )

            await uow.audit_log.record(audit_event)
            await uow.run_repo.update_state(run_id, RunState.SENT, now)

    async def _finalize_failed(self, run_id: RunId, reason: _FailureReason) -> None:
        """Transition current state -> FAILED with SEND_REPORT audit.

        Handles the case where the run may still be in ``SENDING`` or
        any non-terminal state at the time of failure. The state
        machine permits any non-terminal -> FAILED edge.
        """
        async with self._uow_factory() as uow:
            run = await uow.run_repo.get(run_id)
            now = self._clock.now()

            audit_event = AuditEvent(
                timestamp=now,
                action=AuditAction.SEND_REPORT,
                actor="system:assemble_and_deliver",
                resource=f"run:{run_id}",
                outcome=AuditOutcome.FAILURE,
                details={
                    "run_id": run_id,
                    "failure_reason": reason.code,
                    "prior_state": run.state.value,
                    "new_state": RunState.FAILED.value,
                    "timestamp": iso_z(now),
                    **reason.details,
                },
            )

            transition_run(
                from_state=run.state,
                to_state=RunState.FAILED,
                run_id=run_id,
            )

            await uow.audit_log.record(audit_event)
            await uow.run_repo.update_state(run_id, RunState.FAILED, now)

    async def _transition(
        self,
        uow: UnitOfWork,
        run: Run,
        to_state: RunState,
        audit_action: AuditAction,
        details_extra: dict[str, Any],
    ) -> None:
        """Audit + transition inside an existing UoW.

        Used for the READY -> SENDING step; the final SENT/FAILED
        transitions open their own UoWs via :meth:`_finalize_sent` and
        :meth:`_finalize_failed`.
        """
        now = self._clock.now()
        audit_event = AuditEvent(
            timestamp=now,
            action=audit_action,
            actor="system:assemble_and_deliver",
            resource=f"run:{run.run_id}",
            outcome=AuditOutcome.SUCCESS,
            details={
                "run_id": run.run_id,
                "prior_state": run.state.value,
                "new_state": to_state.value,
                "timestamp": iso_z(now),
                **details_extra,
            },
        )
        transition_run(
            from_state=run.state,
            to_state=to_state,
            run_id=run.run_id,
        )
        await uow.audit_log.record(audit_event)
        await uow.run_repo.update_state(run.run_id, to_state, now)


__all__ = ["AssembleAndDeliverUseCase"]
