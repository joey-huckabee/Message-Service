"""Subscription CRUD routes (Increment 18).

Three routes, all per-user-scoped via the ``require_session``
dependency:

* ``GET /subscriptions`` -- list the authenticated user's
  subscriptions (L3-DASH-008).
* ``POST /subscriptions`` -- create a subscription for the
  authenticated user. The Pydantic body model declares only
  ``granularity`` and ``target_value``; ``user_id`` is taken
  exclusively from the session context per L2-DASH-005 / L3-DASH-009.
* ``DELETE /subscriptions/{subscription_id}`` -- delete an owned
  subscription. Cross-user attempts return HTTP 403 per L2-DASH-004
  / L3-DASH-007; non-existent ids return HTTP 404. The route
  validator (FastAPI int coercion) returns HTTP 422 for non-integer
  path values per L3-DASH-019.

The router does not mount itself; the application factory in
``interfaces/rest/app.py`` calls :func:`build_subscriptions_router`
during ``create_app`` and passes the resulting router to
``app.include_router``. This keeps the use-case wiring at the
composition boundary rather than in module-level globals.

Requirement references
----------------------
L1-DASH-001, L1-SUB-001, L1-SUB-002
L2-DASH-001, L2-DASH-004, L2-DASH-005
L3-DASH-007, L3-DASH-008, L3-DASH-009, L3-DASH-019
L3-OBS-031, L3-OBS-032
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, ConfigDict, Field

from message_service.domain.aggregates.subscription import SubscriptionGranularity
from message_service.domain.errors import (
    SubscriptionForbiddenError,
    SubscriptionNotFoundError,
    UnknownPipelineTypeError,
    UnknownTagError,
)
from message_service.domain.ids import SubscriptionId, UserId
from message_service.interfaces.rest.app import require_session

if TYPE_CHECKING:
    from message_service.bootstrap import Service

# -----------------------------------------------------------------------------
# Wire models
# -----------------------------------------------------------------------------


class CreateSubscriptionRequest(BaseModel):
    """Body of ``POST /subscriptions`` (L3-DASH-009).

    Only ``granularity`` and ``target_value`` are accepted; the
    request body MUST NOT carry ``user_id`` or any other field
    (`extra='forbid'`). The owning user is taken from the session.
    """

    model_config = ConfigDict(extra="forbid")

    granularity: SubscriptionGranularity
    target_value: str | None = Field(default=None, max_length=255)


class SubscriptionResponse(BaseModel):
    """Public projection of a :class:`Subscription` returned to the dashboard."""

    model_config = ConfigDict(extra="forbid")

    subscription_id: int
    granularity: SubscriptionGranularity
    target_value: str | None
    created_at: str  # ISO-8601 with Z suffix


# -----------------------------------------------------------------------------
# Router factory
# -----------------------------------------------------------------------------


def build_subscriptions_router(service: Service) -> APIRouter:
    """Construct the subscription CRUD router bound to a service.

    Args:
        service: The constructed service whose ``subscribe`` /
            ``unsubscribe`` use cases plus ``uow_factory`` the routes
            close over.

    Returns:
        An :class:`APIRouter` with three routes mounted under
        ``/subscriptions``.
    """
    router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

    @router.get("")
    async def list_subscriptions(
        user_id: int = Depends(require_session),
    ) -> list[SubscriptionResponse]:
        """L3-DASH-008: list the authenticated user's subscriptions."""
        async with service.uow_factory() as uow:
            rows = await uow.subscription_repo.list_for_user(UserId(user_id))
        return [
            SubscriptionResponse(
                subscription_id=int(row.subscription_id),
                granularity=row.granularity,
                target_value=row.target_value,
                created_at=row.created_at.isoformat().replace("+00:00", "Z"),
            )
            for row in rows
        ]

    @router.post("", status_code=status.HTTP_201_CREATED)
    async def create_subscription(
        body: CreateSubscriptionRequest,
        user_id: int = Depends(require_session),
    ) -> SubscriptionResponse:
        """L3-DASH-008 / L3-DASH-009: create a subscription for the session user.

        Surfaces 422 for unknown pipeline / tag targets per the
        repo's L3-SUB-004 validation; the use case does not classify
        bad-target as 5xx because the user-supplied ``target_value``
        is the cause.
        """
        try:
            saved = await service.subscribe.execute(
                user_id=UserId(user_id),
                granularity=body.granularity,
                target_value=body.target_value,
            )
        except (UnknownPipelineTypeError, UnknownTagError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

        return SubscriptionResponse(
            subscription_id=int(saved.subscription_id),
            granularity=saved.granularity,
            target_value=saved.target_value,
            created_at=saved.created_at.isoformat().replace("+00:00", "Z"),
        )

    @router.delete(
        "/{subscription_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_subscription(
        request: Request,
        subscription_id: int = Path(..., ge=1),
        user_id: int = Depends(require_session),
    ) -> None:
        """L3-DASH-007 / L3-DASH-008 / L3-DASH-019: delete an owned subscription.

        - 404 if no row with that id exists.
        - 403 if the row exists but belongs to another user
          (L3-DASH-007).
        - 422 if the path value is not a positive integer (returned
          automatically by FastAPI's path-parameter validation per
          L3-DASH-019).
        """
        # ``request`` is referenced so FastAPI keeps it in the
        # signature; the route delegates entirely to the use case.
        del request
        try:
            await service.unsubscribe.execute(
                subscription_id=SubscriptionId(subscription_id),
                user_id=UserId(user_id),
            )
        except SubscriptionNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except SubscriptionForbiddenError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="subscription belongs to another user",
            ) from exc

    return router


__all__ = [
    "CreateSubscriptionRequest",
    "SubscriptionResponse",
    "build_subscriptions_router",
]
