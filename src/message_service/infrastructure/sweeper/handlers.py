""":class:`DispositionHandler` implementations for v1.

The two implemented handlers below cover the action ids that bootstrap
registers and that ``config.sweeper.disposition_actions`` may safely
reference today:

* :class:`DiscardSilentlyHandler` — drops the run on the floor; the
  ORPHANED state transition is the entire story. The sweeper's audit
  event from the ORPHAN transition serves as the record.
* :class:`NotifyAdminsHandler` — log-only v1. Emits a structured
  ``sweeper_admin_notification`` log entry at WARNING level with the
  run id, prior state, and tags. Deployments that aggregate logs can
  alert on the event; a future increment can add real out-of-band
  admin mail or webhook dispatch.

Two action ids in the :data:`~message_service.config.schema.DispositionAction`
literal — ``SEND_PARTIAL_FLAGGED`` and ``NOTIFY_SUBSCRIBERS`` — are
deliberately not registered. Configs that reference them are rejected
by :class:`~message_service.application.use_cases.sweeper.SweeperUseCase`'s
constructor at startup (raises
:class:`~message_service.domain.errors.ConfigurationError`), surfacing
the misconfiguration loud-and-early rather than at first orphan. When a
later increment implements those actions, add the corresponding handler
classes here and register them in
:func:`message_service.bootstrap.service.build_service`.

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


def build_disposition_handler_registry() -> dict[DispositionAction, DispositionHandler]:
    """Return the registry of action ids to implemented handlers.

    Single source of truth for which :data:`DispositionAction` identifiers
    are actually runnable in this build. :func:`message_service.bootstrap.
    service.build_service` consumes the result directly; the
    :class:`SweeperUseCase` constructor uses the keys to reject configs
    that reference unimplemented actions at startup.

    A new dict is returned on each call so callers can mutate without
    affecting subsequent constructions.
    """
    return {
        "DISCARD_SILENTLY": DiscardSilentlyHandler(),
        "NOTIFY_ADMINS": NotifyAdminsHandler(),
    }


__all__ = [
    "DiscardSilentlyHandler",
    "NotifyAdminsHandler",
    "build_disposition_handler_registry",
]
