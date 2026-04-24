"""Stub :class:`DispositionHandler` implementations for v1.

These implementations satisfy the :class:`DispositionHandler`
interface for all four configured actions but defer the actual
behavior for two of them (``SEND_PARTIAL_FLAGGED`` and
``NOTIFY_SUBSCRIBERS``) to a later increment, since those require
reusing the :class:`AssembleAndDeliverUseCase` machinery with a
"this report is partial" marker — a design discussion we want to
have on its own turn rather than bundle into the sweeper plumbing.

Implementations provided:

* :class:`DiscardSilentlyHandler` — real. Drops the run on the
  floor; the ORPHANED state transition is the entire story. The
  sweeper's audit event from the ORPHAN transition serves as the
  record.
* :class:`NotifyAdminsHandler` — log-only v1. Emits a structured
  ``sweeper_admin_notification`` log entry at WARNING level with the
  run id, prior state, and tags. Deployments that aggregate logs
  can alert on the event; a future increment can add real
  out-of-band admin mail or webhook dispatch.

Deferred handlers — raising :class:`NotImplementedError` so the
sweeper use case's validation catches any config that references
them before runtime:

* :class:`SendPartialFlaggedHandler` — reuses AssembleAndDeliver
  with an ``is_partial`` flag baked into the email body context.
* :class:`NotifySubscribersHandler` — sends an out-of-band "your
  subscribed run orphaned" note to the run's subscribed users.

When Joey is ready to implement the deferred ones, these two
classes become the target files to flesh out.

Requirement references
----------------------
L1-SWEEP-003, L2-SWEEP-008
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import structlog

from message_service.application.ports.disposition_handler import DispositionHandler

if TYPE_CHECKING:
    from message_service.config.schema import DispositionAction
    from message_service.domain.aggregates.run import Run

_log = structlog.get_logger(__name__)


class DiscardSilentlyHandler(DispositionHandler):
    """Drop the run without further action beyond the ORPHANED transition."""

    action_id: ClassVar[DispositionAction] = "DISCARD_SILENTLY"

    async def handle(self, run: Run) -> None:  # noqa: D102
        _log.debug(
            "sweeper_discard_silently",
            run_id=str(run.run_id),
            pipeline_type=run.pipeline_type,
        )


class NotifyAdminsHandler(DispositionHandler):
    """Log-only admin notification for v1.

    A future addendum can replace this with real out-of-band mail or
    webhook dispatch (SNS, PagerDuty, etc.). For now the structured
    log event serves as the integration point — operators tail the
    service log and alert on ``sweeper_admin_notification``.
    """

    action_id: ClassVar[DispositionAction] = "NOTIFY_ADMINS"

    async def handle(self, run: Run) -> None:  # noqa: D102
        _log.warning(
            "sweeper_admin_notification",
            run_id=str(run.run_id),
            pipeline_type=run.pipeline_type,
            prior_state=run.state.value,
            tags=sorted(run.tags),
            created_at=run.created_at.isoformat(),
            last_transition_at=run.updated_at.isoformat(),
        )


class SendPartialFlaggedHandler(DispositionHandler):
    """Placeholder for the partial-report delivery handler.

    Deferred to a subsequent increment. Raising rather than silently
    no-op'ing so misconfiguration is loud.
    """

    action_id: ClassVar[DispositionAction] = "SEND_PARTIAL_FLAGGED"

    async def handle(self, run: Run) -> None:  # noqa: D102
        raise NotImplementedError(
            "SEND_PARTIAL_FLAGGED disposition is not yet implemented; "
            "remove it from sweeper.disposition_actions or await the "
            "next increment."
        )


class NotifySubscribersHandler(DispositionHandler):
    """Placeholder for the subscribers-orphan-notification handler.

    Deferred to a subsequent increment. Raising rather than silently
    no-op'ing so misconfiguration is loud.
    """

    action_id: ClassVar[DispositionAction] = "NOTIFY_SUBSCRIBERS"

    async def handle(self, run: Run) -> None:  # noqa: D102
        raise NotImplementedError(
            "NOTIFY_SUBSCRIBERS disposition is not yet implemented; "
            "remove it from sweeper.disposition_actions or await the "
            "next increment."
        )


__all__ = [
    "DiscardSilentlyHandler",
    "NotifyAdminsHandler",
    "NotifySubscribersHandler",
    "SendPartialFlaggedHandler",
]
