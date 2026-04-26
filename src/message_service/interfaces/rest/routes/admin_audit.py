"""Admin audit-log viewer route (Increment 20c).

Single read-only route under ``/admin/audit`` exposes the
``audit_log`` table to administrators with offset+limit pagination
and a small set of filters. The route is a faithful projection of
the table; the route SHALL NOT add new redaction logic — the
write-time guarantee from `L3-OBS-036` is the single source of
truth (per `L1-DASH-005` rationale).

* ``GET /admin/audit`` — paginated, filtered listing per
  `L2-DASH-015` / `L3-DASH-033` / `L3-DASH-034` / `L3-DASH-035`.

Filters (all optional; ANDed together):

* ``action`` — repeated query parameter; multiple values OR'd. Each
  must be a valid ``AuditAction`` enum value.
* ``actor`` — exact string match (substring search deferred per
  ROADMAP ``R-DASH-003``).
* ``resource`` — exact string match.
* ``from`` / ``to`` — inclusive ISO-Z timestamp bounds.

Pagination is by ``offset`` + ``limit`` (1-200, default 50). Ordering
is ``audit_id DESC`` so within-UoW timestamp ties stay stable across
pages — see the L3-DASH-034 rationale.

Requirement references
----------------------
L1-DASH-005
L2-DASH-007 (admin gate via require_admin)
L2-DASH-015 (route obligations + filters)
L2-DASH-016 (response shape + redaction passthrough)
L3-DASH-033, L3-DASH-034, L3-DASH-035
L3-OBS-036 (redaction at write time — inherited)
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict

from message_service.domain.aggregates.audit_event import AuditAction, AuditOutcome
from message_service.interfaces.rest.app import require_admin_factory

if TYPE_CHECKING:
    from message_service.bootstrap import Service
    from message_service.domain.aggregates.audit_event import AuditEvent


# -----------------------------------------------------------------------------
# Response model
# -----------------------------------------------------------------------------


def _iso_z(value: datetime) -> str:
    """Render a UTC datetime as an ISO-8601 string with the ``Z`` suffix."""
    return value.isoformat().replace("+00:00", "Z")


class AuditRecordResponse(BaseModel):
    """L3-DASH-035 per-record projection.

    Field set is exactly L2-DASH-016's enumeration. ``extra="forbid"``
    so a future field addition is an explicit response-shape change
    rather than a silent extension.
    """

    model_config = ConfigDict(extra="forbid")

    audit_id: int
    timestamp: str
    action: AuditAction
    actor: str
    resource: str
    outcome: AuditOutcome
    details: dict[str, Any]


def _project_event(event: AuditEvent) -> AuditRecordResponse:
    assert event.audit_id is not None, "viewer reads SHALL populate audit_id (L2-DASH-016)"
    return AuditRecordResponse(
        audit_id=event.audit_id,
        timestamp=_iso_z(event.timestamp),
        action=event.action,
        actor=event.actor,
        resource=event.resource,
        outcome=event.outcome,
        details=event.details,
    )


# -----------------------------------------------------------------------------
# Router factory
# -----------------------------------------------------------------------------


def build_admin_audit_router(service: Service) -> APIRouter:
    """Construct the audit-log viewer router bound to ``service``.

    Mounts a single ``GET /admin/audit`` route gated by
    ``require_admin``. POST / PATCH / DELETE on the same path are
    handled implicitly by FastAPI's default 405 — the L3-OBS-003 /
    L1-DASH-005 read-only obligation is structural rather than
    requiring an explicit handler.

    Args:
        service: The constructed ``Service`` whose ``uow_factory``
            (and through it, ``audit_log.list_paginated``) the route
            closes over.

    Returns:
        An :class:`APIRouter` mounted under ``/admin/audit``.
    """
    router = APIRouter(prefix="/admin/audit", tags=["admin-audit"])
    require_admin = require_admin_factory(service)

    @router.get("", status_code=status.HTTP_200_OK)
    async def list_audit(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        action: Annotated[list[AuditAction] | None, Query()] = None,
        actor: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
        resource: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
        from_: Annotated[
            datetime | None,
            Query(alias="from"),
        ] = None,
        to: Annotated[datetime | None, Query()] = None,
        _admin_id: int = Depends(require_admin),
    ) -> list[AuditRecordResponse]:
        """L3-DASH-033/034/035: paginated, filtered audit-log listing.

        Empty result sets return HTTP 200 with an empty list (per
        L3-DASH-033) — NOT 404. The response field set is exactly
        L2-DASH-016's enumeration; ``extra="forbid"`` on the model
        catches any silent shape drift in future maintenance.
        """
        del _admin_id
        actions = frozenset(action) if action else None
        async with service.uow_factory() as uow:
            events = await uow.audit_log.list_paginated(
                actions=actions,
                actor=actor,
                resource=resource,
                since=from_,
                until=to,
                limit=limit,
                offset=offset,
            )
        return [_project_event(e) for e in events]

    return router


__all__ = ["AuditRecordResponse", "build_admin_audit_router"]
