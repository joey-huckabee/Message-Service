"""Use case: ``ResendRun`` -- manually re-deliver a finalized run.

Per L1-DASH-003 / L2-DASH-008 the dashboard exposes a manual resend
that re-resolves the recipient list at request time (against the
**current** subscriber set) and re-renders from the persisted stage
contexts. The use case sits alongside :class:`AssembleAndDeliverUseCase`
and reuses its public :meth:`prepare_email` for the render step;
recipient resolution and audit are this use case's concern.

State preconditions
-------------------
Per L3-DASH-028 a resend is permitted only on runs in
:attr:`RunState.SENT` or :attr:`RunState.FAILED`. ``ORPHANED`` runs
never went through assembly (the sweeper transitions them while in
non-terminal states), so there is no rendered body to resend; the
use case raises :class:`InvalidRunStateError` for any state other
than the two terminal-with-content states. The route layer
translates this to HTTP 409 per L3-DASH-028.

Audit format
------------
Per L3-DASH-013 (reworded in the 2026-04-25 spec commit) the resend
emits a fresh audit row with ``action=AuditAction.RESEND_REPORT``
and the standard ``SUCCESS``/``FAILURE`` outcome semantics. Resend
does NOT overwrite the original ``SEND_REPORT`` audit; both rows
coexist and operators can see the run's full delivery history.

Resend does NOT transition the run's state. The run remains in its
original terminal state regardless of whether the resend succeeds
or fails.

Requirement references
----------------------
L1-DASH-003 (manual resend)
L2-DASH-008 (recipient resolution at resend time)
L3-DASH-012 (use the same RecipientResolver as the original send)
L3-DASH-013 (audit format with action=RESEND_REPORT)
L3-DASH-027 (re-render from persisted Stage context)
L3-DASH-028 (state precondition: SENT or FAILED only)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.domain.errors import (
    ContextSchemaViolationError,
    ContextSizeExceededError,
    EmailDeliveryError,
    InvalidRunStateError,
    RenderedSizeExceededError,
    TemplateRenderError,
)
from message_service.domain.state_machines.run_states import RunState

if TYPE_CHECKING:
    from collections.abc import Callable

    from message_service.application.ports.clock import Clock
    from message_service.application.ports.mailer import Mailer
    from message_service.application.ports.unit_of_work import UnitOfWork
    from message_service.application.use_cases.assemble_and_deliver import (
        AssembleAndDeliverUseCase,
    )
    from message_service.domain.ids import RunId

_log = structlog.get_logger(__name__)

_RESENDABLE_STATES: frozenset[RunState] = frozenset({RunState.SENT, RunState.FAILED})
"""Per L3-DASH-028: only SENT and FAILED runs may be resent.

ORPHANED is excluded because the sweeper-driven transition leaves
the run without rendered content; resending would have nothing to
deliver. Non-terminal states are excluded because the original send
has not yet completed (L2-RUN-006 prohibits manual operations on
non-terminal runs).
"""


class ResendRunUseCase:
    """Re-deliver a finalized run to the current subscriber set."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        mailer: Mailer,
        assemble_and_deliver: AssembleAndDeliverUseCase,
        from_address: str,
    ) -> None:
        """Bind dependencies.

        Args:
            uow_factory: For state lookup, recipient resolution, and
                audit insert.
            clock: For audit timestamp.
            mailer: For delivery (the same port the original send
                uses).
            assemble_and_deliver: Source of the render path
                (:meth:`prepare_email`); reused so resend output is
                identical to the original render path.
            from_address: Envelope from-address (typically
                ``mail.from_address`` from config).
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._mailer = mailer
        self._assemble = assemble_and_deliver
        self._from_address = from_address

    async def execute(self, *, run_id: RunId, admin_user_id: int) -> None:
        """Re-deliver ``run_id`` to current subscribers and audit.

        Args:
            run_id: The run to resend.
            admin_user_id: The session user triggering the resend;
                used as the audit ``actor`` per L3-DASH-013.

        Raises:
            RunNotFoundError: ``run_id`` does not exist.
            InvalidRunStateError: Run is not in ``SENT`` or ``FAILED``;
                the route layer translates this to HTTP 409.

        Notes:
        -----
        On an expected failure — a re-render error
        (``TemplateRenderError`` / ``RenderedSizeExceededError`` /
        ``ContextSizeExceededError`` / ``ContextSchemaViolationError``, e.g. a template removed or a
        context grown past a limit since the original send) or a
        transient mailer failure (``EmailDeliveryError``) — the use case
        records a ``FAILURE`` audit and returns silently; the route layer
        surfaces the failure via the audit's ``outcome`` field. We do NOT
        raise on these: the caller already committed to a resend by
        clicking the button, and the audit log carries the truth. Only
        precondition failures (unknown run, non-resendable state) raise.
        """
        # 1. Load + check state precondition.
        async with self._uow_factory() as uow:
            run = await uow.run_repo.get(run_id)  # raises RunNotFoundError
        if run.state not in _RESENDABLE_STATES:
            raise InvalidRunStateError(
                f"run {run_id} cannot be resent in state {run.state.value}",
                details={
                    "run_id": run_id,
                    "current_state": run.state.value,
                    "permitted_states": sorted(s.value for s in _RESENDABLE_STATES),
                },
            )

        # 2. Re-render via the shared assemble-and-deliver render
        # path (L3-DASH-027). prepare_email re-reads stages and
        # contexts each call so a resend is byte-identical to a
        # fresh render against the same persisted state. A re-render
        # failure is an expected outcome (the templates/contexts may have
        # changed since the original send): record a FAILURE audit and
        # return, mirroring the delivery-failure convention below — never
        # leak a render exception out to the route as an unhandled 500.
        try:
            prepared = await self._assemble.prepare_email(run_id)
        except (
            TemplateRenderError,
            RenderedSizeExceededError,
            ContextSizeExceededError,
            ContextSchemaViolationError,
        ) as exc:
            await self._record_render_failure(run_id=run_id, admin_user_id=admin_user_id, exc=exc)
            return

        # 3. Re-resolve current recipients (L2-DASH-008 / L3-DASH-012).
        async with self._uow_factory() as uow:
            recipients = await uow.subscription_repo.list_recipients_for_run(
                pipeline_type=run.pipeline_type,
                tags=run.tags,
            )

        # 4. Send + audit. Zero-recipient is treated as SUCCESS per
        # the same convention as the original send path (the resend
        # ran cleanly; nobody to deliver to is not a failure).
        outcome: AuditOutcome
        details_failure: dict[str, str] = {}
        if not recipients:
            outcome = AuditOutcome.SUCCESS
        else:
            from message_service.application.ports.mailer import OutboundEmail

            # Subject comes from the shared AssembleAndDeliverUseCase chokepoint
            # (L2-MAIL-014 / L3-MAIL-034) so resend honors the same default
            # format, per-pipeline subject_templates override, and sanitization
            # as the first-delivery path — not a resend-only format.
            outbound = OutboundEmail(
                recipients=recipients,
                subject=self._assemble.build_subject(run),
                body_html=prepared.body_html,
                from_address=self._from_address,
                attachments=prepared.attachments,
            )
            try:
                await self._mailer.send(outbound)
                outcome = AuditOutcome.SUCCESS
            except EmailDeliveryError as exc:
                outcome = AuditOutcome.FAILURE
                details_failure = {
                    "failure_reason": str(exc),
                }
                _log.warning(
                    "resend_failed",
                    run_id=run_id,
                    recipient_count=len(recipients),
                    error=str(exc),
                )

        # 5. Audit (L3-DASH-013).
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await uow.audit_log.record(
                AuditEvent(
                    timestamp=now,
                    action=AuditAction.RESEND_REPORT,
                    actor=f"user:{admin_user_id}",
                    resource=f"run:{run_id}",
                    outcome=outcome,
                    details={
                        "run_id": run_id,
                        "recipient_count": len(recipients),
                        "recipient_addresses": sorted(recipients),
                        "attachment_count": len(prepared.attachments),
                        **details_failure,
                    },
                ),
            )

        _log.info(
            "resend_completed",
            run_id=run_id,
            recipient_count=len(recipients),
            outcome=outcome.value,
            admin_user_id=admin_user_id,
        )

    async def _record_render_failure(
        self,
        *,
        run_id: RunId,
        admin_user_id: int,
        exc: TemplateRenderError
        | RenderedSizeExceededError
        | ContextSizeExceededError
        | ContextSchemaViolationError,
    ) -> None:
        """Record a RESEND_REPORT FAILURE audit for a re-render failure (L3-DASH-013).

        The resend never reached recipient resolution or delivery, so
        ``recipient_count`` and ``attachment_count`` are ``0``; the
        ``failure_reason`` classifies the render error by exception type.
        """
        now = self._clock.now()
        failure_reason = type(exc).__name__
        _log.warning(
            "resend_render_failed",
            run_id=run_id,
            failure_reason=failure_reason,
            error=str(exc),
        )
        async with self._uow_factory() as uow:
            await uow.audit_log.record(
                AuditEvent(
                    timestamp=now,
                    action=AuditAction.RESEND_REPORT,
                    actor=f"user:{admin_user_id}",
                    resource=f"run:{run_id}",
                    outcome=AuditOutcome.FAILURE,
                    details={
                        "run_id": run_id,
                        "recipient_count": 0,
                        "attachment_count": 0,
                        "failure_reason": failure_reason,
                        "error": str(exc),
                    },
                ),
            )


__all__ = ["ResendRunUseCase"]
