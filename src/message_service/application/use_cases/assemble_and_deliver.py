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
   the run metadata, the stage-identifier summary list, and the
   per-stage email-body contributions split into ``before_contributions``
   and ``after_contributions`` buckets (L3-AGGR-005). Each bucket is
   sorted by ``(stage_order, stage_id)`` and carries the parsed
   ``email_body_context`` per contributing stage; the reference template
   renders BEFORE_STAGES_SUMMARY, then the summary, then
   AFTER_STAGES_SUMMARY. Stages that submitted no email body
   contribution appear in the summary list but in neither bucket.
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
:class:`ContextSchemaViolationError`, :class:`EmailDeliveryError`) are caught, translated into
``SENDING -> FAILED`` with a structured ``reason`` in the audit
details, and swallowed (the scheduler would log them anyway).

Unexpected errors propagate; the
:class:`BackgroundTaskScheduler` adapter catches them at the task
boundary for logging. The orphan sweeper eventually reclaims runs
stuck in ``SENDING`` after ``sweeper.run_timeout_seconds``.

Requirement references
----------------------
L1-RUN-004 (assembly and delivery triggered by FinalizeRun)
L1-AGGR-001 (per-stage email body contributions)
L1-AGGR-002, L1-AGGR-003 (attachment modes, stage ordering)
L2-AGGR-003 (email body position placement)
L1-SUB-004 (recipient list via subscription union)
L1-MAIL-001, L1-MAIL-005 (SMTP delivery, audit)
L2-AGGR-004, L2-AGGR-005, L2-AGGR-006, L2-AGGR-007, L2-AGGR-008
L2-MAIL-012 (delivery audit fields)
L3-AGGR-005 (email body before/after contribution buckets)
L3-AGGR-006 (aggregation template context shape)
L3-AGGR-007 (rendered-size failure)
L3-AGGR-008, L3-AGGR-009 (empty-report handling)
L3-AGGR-010, L3-AGGR-011 (filename sanitization)
L3-RUN-026 (audit before state change)
"""

from __future__ import annotations

import importlib.resources
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

import jinja2
import structlog

from message_service.application.ports.clock import Clock, iso_z
from message_service.application.ports.mailer import (
    EmailAttachment,
    Mailer,
    OutboundEmail,
)
from message_service.application.ports.metrics_recorder import (
    MetricsRecorder,
    NoOpMetricsRecorder,
)
from message_service.application.ports.report_store import NoOpReportStore, ReportStore
from message_service.application.ports.template_renderer import TemplateRenderer
from message_service.application.ports.unit_of_work import UnitOfWork
from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.domain.aggregates.email_body_position import EmailBodyPosition
from message_service.domain.aggregates.run import AttachmentMode, Run
from message_service.domain.aggregates.stage import Stage
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import (
    ContextSchemaViolationError,
    ContextSizeExceededError,
    EmailDeliveryError,
    EmailSizeExceededError,
    PersistenceError,
    RenderedSizeExceededError,
    TemplateRenderError,
)
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.run_states import (
    TERMINAL_STATES,
    RunState,
)
from message_service.domain.state_machines.run_states import (
    transition as transition_run,
)
from message_service.domain.state_machines.stage_states import StageState

_log = structlog.get_logger(__name__)


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
# Subject formatting (L2-MAIL-014, L3-MAIL-027/028/029)
# -----------------------------------------------------------------------------


def _build_subject(pipeline_type: str, run_id: RunId, template: str | None = None) -> str:
    """Compose the email Subject header per L2-MAIL-014.

    Sanitizes ``pipeline_type`` via the same regex as L3-AGGR-010 so
    CR/LF and other control characters are replaced with ``_`` before
    reaching the SMTP layer (L3-MAIL-028 / L3-MAIL-029). The
    ``OutboundEmail`` boundary's CR/LF assertion remains as a second
    line of defense.

    When ``template`` is provided (the per-pipeline override from
    ``pipelines.subject_templates``, L3-MAIL-032) the subject is rendered
    from it with the sanitized ``pipeline_type`` and the ``run_id``
    substituted; the template's placeholders were validated at config-load
    time (L3-MAIL-033). When ``template`` is ``None`` the default format is
    used unchanged, so behavior is byte-identical to v1 for pipelines with
    no configured override.

    Args:
        pipeline_type: Run's ``pipeline_type`` from config.
        run_id: Canonical UUID4 string.
        template: Optional ``str.format`` override referencing only
            ``{pipeline_type}`` and ``{run_id}``.

    Returns:
        Subject string; the default form is ``[{pipeline_safe}] run {run_id}``.
    """
    pipeline_safe = _sanitize_filename_component(pipeline_type)
    if template is not None:
        return template.format(pipeline_type=pipeline_safe, run_id=run_id)
    return f"[{pipeline_safe}] run {run_id}"


# -----------------------------------------------------------------------------
# Internal carrier types
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RenderedFragment:
    """A rendered stage report fragment plus its ordering metadata.

    Also carries the stage's email body contribution (L3-AGGR-005): the
    parsed ``email_body_context`` and its resolved
    ``email_body_position``. Both are ``None`` when the stage submitted
    no email body contribution — that stage participates in the report
    attachment but not in either email-body bucket.
    """

    stage_id: str
    stage_order: int
    rendered_html: str
    email_body_context: dict[str, Any] | None = None
    email_body_position: EmailBodyPosition | None = None

    @property
    def is_empty(self) -> bool:
        """Empty per L3-AGGR-008: whitespace-only rendered HTML."""
        return not self.rendered_html.strip()


@dataclass(frozen=True, slots=True)
class _FailureReason:
    """Classified failure reason used in FAILED audit details."""

    code: str
    details: dict[str, Any]


def _merge_failure_details(reason: _FailureReason) -> dict[str, Any]:
    """Merge a classified reason into audit details, keeping ``code`` authoritative.

    The run's ``failure_reason`` is drawn from the closed `L3-RUN-029` vocabulary
    (``code``). Some error-specific detail payloads carry their OWN
    ``failure_reason`` key that is a different concept — the mailer's
    ``EmailDeliveryError.details["failure_reason"]`` is the SMTP-level
    classification (``PERMANENT_SMTP_FAILURE`` / ``RETRIES_EXHAUSTED``), not a
    run failure_reason. Spreading ``reason.details`` verbatim used to clobber the
    authoritative ``code`` with that out-of-vocabulary value. Here the SMTP
    classification is relocated to ``smtp_failure_classification`` so the
    top-level ``failure_reason`` always stays within the closed vocabulary.
    """
    extra = dict(reason.details)
    smtp_classification = extra.pop("failure_reason", None)
    merged: dict[str, Any] = {"failure_reason": reason.code, **extra}
    if smtp_classification is not None:
        merged["smtp_failure_classification"] = smtp_classification
    return merged


@dataclass(frozen=True, slots=True)
class PreparedEmail:
    """Rendered email components for a run, ready for delivery.

    Returned by :meth:`AssembleAndDeliverUseCase.prepare_email`. The
    body and attachments are produced exactly the same way as the
    first-send path; the resend path (Increment 19b) reuses these
    bytes verbatim, so resends are byte-identical to the original
    delivery as long as the underlying templates and per-stage
    contexts have not changed.
    """

    run: Run
    body_html: str
    attachments: tuple[EmailAttachment, ...]


# Reason codes used when transitioning to FAILED. Strings rather than
# an enum because L2-MAIL-009 treats these as human-readable audit
# strings, not enforced constants.
_REASON_TEMPLATE_RENDER = "TEMPLATE_RENDER"
_REASON_RENDERED_SIZE_EXCEEDED = "RENDERED_SIZE_EXCEEDED"
_REASON_CONTEXT_SIZE_EXCEEDED = "CONTEXT_SIZE_EXCEEDED"
_REASON_CONTEXT_SCHEMA_VIOLATION = "CONTEXT_SCHEMA_VIOLATION"
_REASON_EMAIL_DELIVERY = "EMAIL_DELIVERY"
_REASON_EMAIL_SIZE_EXCEEDED = "EMAIL_SIZE_EXCEEDED"


# Admin-notification template (L3-MAIL-015). Loaded once at module
# import via importlib.resources from src/message_service/templates/
# email/admin_notification.j2. The template is compiled in a Jinja2
# Environment with autoescape=True per L3-MAIL-016 to neutralize any
# unexpected metacharacter in the run_id parameter.
#
# Distinct from the user-template renderer's SandboxedEnvironment
# because this template is service-internal and rendered with
# operator-trusted variables only (run_id, failure_reason, timestamp).
def _load_admin_notification_template() -> jinja2.Template:
    """Load + compile the admin-notification template at module import."""
    template_text = (
        importlib.resources.files("message_service.templates.email")
        .joinpath("admin_notification.j2")
        .read_text(encoding="utf-8")
    )
    env = jinja2.Environment(autoescape=True, undefined=jinja2.StrictUndefined)
    return env.from_string(template_text)


_ADMIN_NOTIFICATION_TEMPLATE: Final[jinja2.Template] = _load_admin_notification_template()
_ADMIN_NOTIFICATION_SUBJECT: Final[str] = "Message Service: EMAIL_SIZE_EXCEEDED for run {run_id}"


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
            ``templates.email_body_template_ref`` — the default email body
            template, used for any pipeline without an entry in
            ``email_body_template_overrides`` (L2-TMPL-015).
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
        admin_recipients: tuple[str, ...] = (),
        subject_templates: Mapping[str, str] | None = None,
        email_body_template_overrides: Mapping[str, TemplateRef] | None = None,
        metrics_recorder: MetricsRecorder | None = None,
        report_store: ReportStore | None = None,
    ) -> None:
        """Construct with ports and config values threaded through.

        Args:
            uow_factory: Zero-argument UoW factory.
            clock: Clock port.
            template_renderer: Renderer port.
            mailer: Mailer port.
            from_address: Configured sender address.
            email_body_template_ref: Configured email body template.
            admin_recipients: Configured ``mail.admin_recipients``
                tuple. Drives the L3-MAIL-031 admin notification on
                EMAIL_SIZE_EXCEEDED. Defaults to empty tuple for
                tests; an empty list at runtime causes the admin
                notification to be skipped with a WARNING log per
                L3-MAIL-031.
            subject_templates: Configured
                ``pipelines.subject_templates`` mapping (``pipeline_type``
                → subject ``str.format`` template, L3-MAIL-032). A run
                whose ``pipeline_type`` is a key renders its subject from
                the template; all others use the default format. Defaults
                to an empty mapping (no overrides).
            email_body_template_overrides: Configured
                ``pipelines.email_body_template_overrides`` mapping
                (``pipeline_type`` → ``TemplateRef``, L2-TMPL-015). A run
                whose ``pipeline_type`` is a key renders its email body from
                the override reference; all others use
                ``email_body_template_ref``. References are validated against
                the manifest at startup (L3-TMPL-034). Defaults to an empty
                mapping (no overrides).
            metrics_recorder: L1-OBS-002 metrics port (run state
                transitions, email delivery outcome, email size,
                run duration). Defaults to a NoOp instance for tests.
            report_store: L1-PERS-002 / L3-PERS-024 saved-render port.
                The first-delivery path writes per-stage fragments and
                the assembled email body so the dashboard's
                report-viewer routes can serve the exact bytes that
                were delivered. Defaults to a NoOp for tests; the resend
                path (which calls :meth:`prepare_email`) deliberately
                does NOT write to the store per L3-DASH-027.
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._template_renderer = template_renderer
        self._mailer = mailer
        self._from_address = from_address
        self._email_body_template_ref = email_body_template_ref
        self._admin_recipients = admin_recipients
        self._subject_templates: dict[str, str] = dict(subject_templates or {})
        self._email_body_template_overrides: dict[str, TemplateRef] = dict(
            email_body_template_overrides or {}
        )
        self._metrics = metrics_recorder or NoOpMetricsRecorder()
        self._report_store = report_store or NoOpReportStore()

    def build_subject(self, run: Run) -> str:
        """Compose the Subject header for a run (L2-MAIL-014 / L3-MAIL-034).

        The single subject chokepoint shared by the first-delivery path
        (:meth:`execute`) and the manual-resend path
        (:class:`~message_service.application.use_cases.resend_run.ResendRunUseCase`),
        so the ``L3-MAIL-027`` default format, the ``L3-MAIL-032`` per-pipeline
        ``subject_templates`` override, and the ``L3-MAIL-028`` sanitization
        apply identically regardless of which path sends the email.

        Args:
            run: The run whose Subject header to build.

        Returns:
            The Subject header string.
        """
        return _build_subject(
            run.pipeline_type,
            run.run_id,
            self._subject_templates.get(run.pipeline_type),
        )

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
                :class:`ContextSchemaViolationError`,
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
            self._save_fragments(run_id, rendered_fragments)
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
        except ContextSchemaViolationError as exc:
            # A stage context that fails its template's JSON-Schema surfaces here
            # (schema validation runs only in the renderer, only in this task).
            # Without this branch it escaped uncaught, stranding the run in
            # SENDING with no FAILED transition or audit until the sweeper
            # reclaimed it. Fail fast like the peer render errors (L3-RUN-029).
            await self._finalize_failed(
                run_id=run_id,
                reason=_FailureReason(
                    code=_REASON_CONTEXT_SCHEMA_VIOLATION,
                    details={"message": str(exc), **(exc.details or {})},
                ),
            )
            return

        # Recipient resolution must run in a UoW (repo is UoW-scoped).
        async with self._uow_factory() as uow:
            recipients = await uow.subscription_repo.list_recipients_for_run(
                pipeline_type=run.pipeline_type, tags=run.tags
            )

        # L1-OBS-002 / L3-OBS-009: email size observation.
        # Approximate the wire size as body + attachment content bytes
        # (per-attachment base64 overhead is constant ~1.37x; the
        # mailer's exact MIME size isn't needed for histogram bucketing).
        email_size_bytes = len(email_body_html.encode("utf-8")) + sum(
            len(a.content) for a in attachments
        )
        self._metrics.observe_email_size_bytes(email_size_bytes)

        # Zero-recipient short-circuit per design decision: finalize
        # as SENT with recipient_count=0; do not invoke Mailer.
        if not recipients:
            self._save_email_body(run_id, email_body_html)
            await self._finalize_sent(
                run_id=run_id,
                recipients=frozenset(),
                attachment_count=len(attachments),
            )
            # Treat zero-recipient SENT as a successful delivery for
            # outcome counting purposes — the run reached terminal SENT.
            self._metrics.record_email_delivery_outcome("success")
            return

        # Non-empty recipients: deliver or fail. The subject comes from the
        # shared build_subject chokepoint (L2-MAIL-014 / L3-MAIL-034) so the
        # default format, the per-pipeline subject_templates override
        # (L3-MAIL-032), and pipeline_type sanitization (L3-MAIL-028) apply
        # identically here and on the resend path.
        outbound = OutboundEmail(
            recipients=recipients,
            subject=self.build_subject(run),
            body_html=email_body_html,
            from_address=self._from_address,
            attachments=attachments,
        )

        try:
            await self._mailer.send(outbound)
        except EmailSizeExceededError as exc:
            # L3-MAIL-030 four-step sequence:
            # (1) persist the rendered email body so the dashboard
            #     resend interface can locate it (L3-MAIL-017 +
            #     L3-MAIL-024 — same path as a successful report).
            # (2) audit + transition in a single UoW; audit-first
            #     ordering per L3-RUN-026.
            # (3) send admin notification AFTER the UoW commits;
            #     failures here are logged but do NOT roll back.
            # (4) SMTP delivery of the failing email is NOT
            #     retried — the size check is L2-MAIL-008's
            #     pre-transmission gate, exceeded means permanent.
            self._save_email_body(run_id, email_body_html)
            await self._finalize_failed(
                run_id=run_id,
                reason=_FailureReason(
                    code=_REASON_EMAIL_SIZE_EXCEEDED,
                    details={
                        "message": str(exc),
                        "recipient_count": len(recipients),
                        **(exc.details or {}),
                    },
                ),
            )
            await self._send_admin_notification_for_size_exceeded(
                run_id=run_id,
                exc=exc,
            )
            self._metrics.record_email_delivery_outcome("permanent_failure")
            return
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
            # L1-OBS-002 / L3-OBS-009: outcome label distinguishes
            # transient (retried, then exhausted) vs permanent (no retry) per
            # L1-MAIL-002. The mailer classifies via ``details["failure_reason"]``
            # (RETRIES_EXHAUSTED = a transient failure that was retried to
            # exhaustion; PERMANENT_SMTP_FAILURE = fail-fast). It does NOT set a
            # ``retriable`` key, so keying on that always yielded
            # "permanent_failure" — even for genuinely transient outages.
            outcome = (
                "transient_failure"
                if exc.details and exc.details.get("failure_reason") == "RETRIES_EXHAUSTED"
                else "permanent_failure"
            )
            self._metrics.record_email_delivery_outcome(outcome)
            return

        self._save_email_body(run_id, email_body_html)
        await self._finalize_sent(
            run_id=run_id,
            recipients=recipients,
            attachment_count=len(attachments),
        )
        self._metrics.record_email_delivery_outcome("success")

    async def prepare_email(self, run_id: RunId) -> PreparedEmail:
        """Render the email components for a run without sending or transitioning.

        Used by :class:`ResendRunUseCase` (Increment 19b) to obtain
        the same body + attachments the first-send path produced,
        without re-running the state-transition machinery. The
        re-render replays against the persisted
        :attr:`Stage.report_context_json`; per L3-DASH-027 the resend
        path deliberately uses this rather than reading a saved
        on-disk render snapshot, so resend output is consistent with
        the first-send rendering even before the filesystem report
        store (Increment 19c) lands.

        Args:
            run_id: The run to render.

        Returns:
            A :class:`PreparedEmail` with the rendered body, the
            tuple of attachments per the run's attachment mode, and
            a snapshot of the run aggregate.

        Raises:
            RunNotFoundError: No run with this id exists.
            TemplateRenderError: A stage's template failed to render.
            RenderedSizeExceededError: Rendered output too large.
            ContextSizeExceededError: Per-stage context too large.
            ContextSchemaViolationError: Context failed JSON Schema validation.
        """
        async with self._uow_factory() as uow:
            run = await uow.run_repo.get(run_id)
        fragments = await self._load_and_render_stages(run_id)
        attachments = self._build_attachments(run, fragments)
        body_html = self._render_email_body(run, fragments)
        return PreparedEmail(run=run, body_html=body_html, attachments=attachments)

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
            ContextSchemaViolationError: Propagated from renderer.
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
            # Carry the stage's email body contribution alongside the
            # report fragment (L3-AGGR-005). Independent of the report:
            # a stage may contribute email body content with an empty or
            # absent report, and vice versa (L3-STAGE-009).
            email_body_context = (
                json.loads(stage.email_body_context_json)
                if stage.email_body_context_json is not None
                else None
            )
            fragments.append(
                _RenderedFragment(
                    stage_id=stage.stage_id,
                    stage_order=stage_order_by_id.get(stage.stage_id, 0),
                    rendered_html=rendered_html,
                    email_body_context=email_body_context,
                    email_body_position=stage.email_body_position,
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
            ContextSchemaViolationError: Aggregation context fails schema validation.
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
            if run.aggregation_template_ref is None:
                raise RuntimeError(
                    "SINGLE_AGGREGATED run has no aggregation_template_ref "
                    "(Run __post_init__ invariant violated)"
                )
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

        Besides the stages' identifying metadata (the ``stages`` summary
        list — report contributions themselves live in the attachments),
        the template receives the per-stage email body contributions
        (L1-AGGR-001) split into two position buckets (L3-AGGR-005):

        * ``before_contributions`` — stages whose resolved position is
          ``BEFORE_STAGES_SUMMARY``.
        * ``after_contributions`` — stages whose resolved position is
          ``AFTER_STAGES_SUMMARY``.

        Only stages carrying a non-null email body contribution appear
        in a bucket (a ``None`` position means no contribution). ``fragments``
        arrives sorted by ``(stage_order, stage_id)`` from
        :meth:`_load_and_render_stages`, so each bucket inherits that
        order (L3-AGGR-012). Each entry is ``{stage_id, stage_order,
        context}`` where ``context`` is the parsed
        ``email_body_context_json``. The reference template renders the
        before block, the summary, then the after block, realizing the
        BEFORE -> summary -> AFTER placement of L2-AGGR-003.

        Args:
            run: The run being assembled.
            fragments: Stage fragments in presentation order.

        Returns:
            Rendered HTML email body.

        Raises:
            TemplateRenderError: Body template render failure.
            RenderedSizeExceededError: Body exceeds max size.
            ContextSchemaViolationError: Body context fails schema validation.
        """

        def _bucket(position: EmailBodyPosition) -> list[dict[str, Any]]:
            return [
                {
                    "stage_id": f.stage_id,
                    "stage_order": f.stage_order,
                    "context": f.email_body_context,
                }
                for f in fragments
                if f.email_body_position is position
            ]

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
            "before_contributions": _bucket(EmailBodyPosition.BEFORE_STAGES_SUMMARY),
            "after_contributions": _bucket(EmailBodyPosition.AFTER_STAGES_SUMMARY),
        }
        # L3-TMPL-035: a per-pipeline override (pipelines.email_body_template_overrides)
        # takes precedence when configured; otherwise the service-wide default. Both
        # first-delivery and resend (prepare_email) route through here, so the override
        # applies identically to resends.
        body_template_ref = self._email_body_template_overrides.get(
            run.pipeline_type, self._email_body_template_ref
        )
        return self._template_renderer.render(body_template_ref, body_context)

    def _save_fragments(self, run_id: RunId, fragments: Sequence[_RenderedFragment]) -> None:
        """Persist each rendered fragment to the report store.

        Best-effort: a :class:`PersistenceError` is logged and swallowed
        so a saved-snapshot failure does not abort delivery (the email
        is the source of truth; the snapshot is for the dashboard
        viewer and can be backfilled manually).

        Skips empty fragments — they would represent zero-byte files
        with no diagnostic value, and the attachment-build path drops
        them anyway per L3-AGGR-008.
        """
        for fragment in fragments:
            if fragment.is_empty:
                continue
            try:
                self._report_store.save_fragment(
                    run_id, StageId(fragment.stage_id), fragment.rendered_html
                )
            except PersistenceError as exc:
                _log.warning(
                    "report_store_save_fragment_failed",
                    run_id=run_id,
                    stage_id=fragment.stage_id,
                    error=str(exc),
                    details=exc.details,
                )

    async def _send_admin_notification_for_size_exceeded(
        self,
        *,
        run_id: RunId,
        exc: EmailSizeExceededError,
    ) -> None:
        """Send the L3-MAIL-015 admin notification email after a size-exceeded failure.

        Called AFTER the audit + state-transition UoW commits per
        L3-MAIL-030 step 3. Failures here log at ERROR but do NOT
        roll back — the run is already FAILED, the audit row is
        already written, the rendered report is already persisted.

        When ``admin_recipients`` is empty (per L3-MAIL-031), the
        notification is skipped with a WARNING log and the method
        returns normally.
        """
        if not self._admin_recipients:
            _log.warning(
                "admin_notification_skipped_no_recipients",
                run_id=str(run_id),
                failure_reason=_REASON_EMAIL_SIZE_EXCEEDED,
            )
            return

        timestamp = self._clock.now()
        body = _ADMIN_NOTIFICATION_TEMPLATE.render(
            run_id=str(run_id),
            failure_reason=_REASON_EMAIL_SIZE_EXCEEDED,
            timestamp=iso_z(timestamp),
        )
        notification = OutboundEmail(
            recipients=frozenset(self._admin_recipients),
            subject=_ADMIN_NOTIFICATION_SUBJECT.format(run_id=str(run_id)),
            body_html=body,
            from_address=self._from_address,
            attachments=(),
        )
        try:
            await self._mailer.send(notification)
        except EmailDeliveryError as send_exc:
            _log.error(
                "admin_notification_send_failed",
                run_id=str(run_id),
                failure_reason=_REASON_EMAIL_SIZE_EXCEEDED,
                error_message=str(send_exc),
                details=send_exc.details,
            )
            return
        _log.info(
            "admin_notification_sent",
            run_id=str(run_id),
            failure_reason=_REASON_EMAIL_SIZE_EXCEEDED,
            recipient_count=len(self._admin_recipients),
            measured_bytes=(exc.details or {}).get("measured_bytes"),
            limit_bytes=(exc.details or {}).get("limit_bytes"),
        )

    def _save_email_body(self, run_id: RunId, html: str) -> None:
        """Persist the assembled email body to the report store.

        Best-effort: failures are logged and swallowed for the same
        reason as :meth:`_save_fragments`.
        """
        try:
            self._report_store.save_email_body(run_id, html)
        except PersistenceError as exc:
            _log.warning(
                "report_store_save_email_body_failed",
                run_id=run_id,
                error=str(exc),
                details=exc.details,
            )

    async def _finalize_sent(
        self,
        run_id: RunId,
        recipients: frozenset[str],
        attachment_count: int,
    ) -> None:
        """Transition SENDING -> SENT with SEND_REPORT audit.

        L3-RUN-034: if the run was concurrently swept to a terminal
        state (the orphan sweeper reclaims by age alone and may catch a
        slow ``SENDING`` delivery), the email has already been sent but
        the ``SENDING -> SENT`` edge is now illegal. Record a
        reconciliation audit event and leave the terminal state intact
        rather than raising an ``InvalidStateTransitionError`` out of the
        background task.
        """
        async with self._uow_factory() as uow:
            run = await uow.run_repo.get(run_id)
            now = self._clock.now()

            if run.state in TERMINAL_STATES:
                await self._record_reconciliation(
                    uow=uow,
                    run_id=run_id,
                    now=now,
                    terminal_state=run.state,
                    outcome=AuditOutcome.SUCCESS,
                    details_extra={
                        "recipient_count": len(recipients),
                        "attachment_count": attachment_count,
                    },
                )
                _log.warning(
                    "delivery_completed_after_terminal_transition",
                    run_id=run_id,
                    reconciled_terminal_state=run.state.value,
                    recipient_count=len(recipients),
                )
                return

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

            # Capture run.created_at + now for the duration histogram
            # (L1-OBS-002 / L3-OBS-009 / L3-OBS-011) — emit after
            # commit just below.
            duration_seconds = (now - run.created_at).total_seconds()

        # L1-OBS-002 metrics, post-commit.
        self._metrics.record_run_state_transition(RunState.SENT)
        self._metrics.observe_run_duration_seconds(duration_seconds)

    async def _finalize_failed(self, run_id: RunId, reason: _FailureReason) -> None:
        """Transition current state -> FAILED with SEND_REPORT audit.

        Handles the case where the run may still be in ``SENDING`` or
        any non-terminal state at the time of failure. The state
        machine permits any non-terminal -> FAILED edge.

        L3-RUN-034: if the run was concurrently swept to a terminal
        state, the ``-> FAILED`` edge is now illegal. Record a
        reconciliation audit event (preserving the classified
        ``failure_reason``) and leave the terminal state intact rather
        than raising.
        """
        async with self._uow_factory() as uow:
            run = await uow.run_repo.get(run_id)
            now = self._clock.now()

            if run.state in TERMINAL_STATES:
                await self._record_reconciliation(
                    uow=uow,
                    run_id=run_id,
                    now=now,
                    terminal_state=run.state,
                    outcome=AuditOutcome.FAILURE,
                    details_extra=_merge_failure_details(reason),
                )
                _log.warning(
                    "delivery_failed_after_terminal_transition",
                    run_id=run_id,
                    reconciled_terminal_state=run.state.value,
                    failure_reason=reason.code,
                )
                return

            audit_event = AuditEvent(
                timestamp=now,
                action=AuditAction.SEND_REPORT,
                actor="system:assemble_and_deliver",
                resource=f"run:{run_id}",
                outcome=AuditOutcome.FAILURE,
                details={
                    "run_id": run_id,
                    "prior_state": run.state.value,
                    "new_state": RunState.FAILED.value,
                    "timestamp": iso_z(now),
                    **_merge_failure_details(reason),
                },
            )

            transition_run(
                from_state=run.state,
                to_state=RunState.FAILED,
                run_id=run_id,
            )

            await uow.audit_log.record(audit_event)
            await uow.run_repo.update_state(run_id, RunState.FAILED, now)

            duration_seconds = (now - run.created_at).total_seconds()

        # L1-OBS-002 metrics, post-commit.
        self._metrics.record_run_state_transition(RunState.FAILED)
        self._metrics.observe_run_duration_seconds(duration_seconds)

    async def _record_reconciliation(
        self,
        *,
        uow: UnitOfWork,
        run_id: RunId,
        now: datetime,
        terminal_state: RunState,
        outcome: AuditOutcome,
        details_extra: dict[str, Any],
    ) -> None:
        """Record a SEND_REPORT audit row without a state transition (L3-RUN-034).

        Used when the run was concurrently swept to a terminal state
        while delivery was in flight. The audit row documents that the
        delivery attempt completed (with ``outcome``) *after* the run had
        already reached ``terminal_state``, so operators can correlate an
        already-sent (or already-failed) email with the terminal run. No
        ``runs.state`` UPDATE is issued — the sweeper's terminal state and
        any disposition it enqueued are left intact.
        """
        audit_event = AuditEvent(
            timestamp=now,
            action=AuditAction.SEND_REPORT,
            actor="system:assemble_and_deliver",
            resource=f"run:{run_id}",
            outcome=outcome,
            details={
                "run_id": run_id,
                "prior_state": terminal_state.value,
                "new_state": terminal_state.value,
                "reconciled_terminal_state": terminal_state.value,
                "timestamp": iso_z(now),
                **details_extra,
            },
        )
        await uow.audit_log.record(audit_event)

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
        # Note: this helper runs INSIDE an outer UoW, so the metric
        # record happens before the outer commit. If the outer UoW
        # rolls back the run state stays unchanged but the metric
        # increment is best-effort visible (Prometheus counters are
        # in-process, not transactional). v1 accepts this small
        # divergence — the audit log remains the truth.
        self._metrics.record_run_state_transition(to_state)


__all__ = ["AssembleAndDeliverUseCase", "PreparedEmail"]
