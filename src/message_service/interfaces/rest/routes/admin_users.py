"""Admin user-management routes (Increment 20b).

Three routes under ``/admin/users`` give administrators a self-service
mechanism to create, update, and reset passwords on user accounts —
covering the user-management surface of `L1-AUTH-003`. All three
routes are gated by the ``require_admin`` dependency 20a delivered.

* ``POST /admin/users`` — create a new account (`L3-AUTH-014/015`).
* ``PATCH /admin/users/{user_id}`` — update ``display_name``,
  ``is_admin``, or ``disabled`` (every field optional). Self-protection
  guardrail per `L2-AUTH-009` rejects self-deadmin and self-disable
  with HTTP 409.
* ``POST /admin/users/{user_id}/password`` — reset password.

The plaintext password from request bodies is wrapped immediately in
the :class:`Password` value object so it cannot accidentally land in
log records or response bodies — the redacted ``__repr__`` /
``__str__`` from `L3-AUTH-004` carry through here.

Requirement references
----------------------
L1-AUTH-003
L2-AUTH-007, L2-AUTH-008, L2-AUTH-009
L3-AUTH-014, L3-AUTH-015, L3-AUTH-016, L3-AUTH-017
L3-OBS-035 (audit-record format inherited via the use cases)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field

from message_service.application.ports.clock import iso_z
from message_service.domain.aggregates.password import Password
from message_service.domain.aggregates.subscription import SubscriptionGranularity
from message_service.domain.errors import (
    DuplicateEmailError,
    InvalidEmailError,
    PersistenceError,
    SelfProtectionError,
    SubscriptionNotFoundError,
    UnknownPipelineTypeError,
    UnknownTagError,
    UserNotFoundError,
)
from message_service.domain.ids import SubscriptionId, UserId
from message_service.interfaces.rest.app import require_admin_factory

if TYPE_CHECKING:
    from message_service.bootstrap import Service
    from message_service.domain.aggregates.subscription import Subscription
    from message_service.domain.aggregates.user import User


# -----------------------------------------------------------------------------
# Request models  (L3-AUTH-015)
# -----------------------------------------------------------------------------


class CreateUserRequest(BaseModel):
    """Body of ``POST /admin/users``.

    All five fields required. ``extra="forbid"`` rejects unknown keys
    with HTTP 422 per `L3-AUTH-015`. Length limits chosen to match the
    SQLite columns + sensible UI defaults; the email format check is
    in the use case rather than pydantic so the failure surfaces as
    :class:`InvalidEmailError` (with the targeted email in details)
    rather than a generic pydantic validation message.
    """

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=254)
    display_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)
    is_admin: bool
    disabled: bool


class UpdateUserRequest(BaseModel):
    """Body of ``PATCH /admin/users/{user_id}``.

    Every field optional. Empty body is a no-op success per
    `L3-AUTH-015` (not 422). ``extra="forbid"`` still rejects unknown
    keys.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    is_admin: bool | None = None
    disabled: bool | None = None


class ResetPasswordRequest(BaseModel):
    """Body of ``POST /admin/users/{user_id}/password``."""

    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=512)


# -----------------------------------------------------------------------------
# Response models
# -----------------------------------------------------------------------------


class UserResponse(BaseModel):
    """Per-user projection.

    The ``password_hash`` is deliberately omitted; admin clients that
    need to verify a password use the login endpoint rather than the
    admin surface.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: int
    email: str
    display_name: str
    is_admin: bool
    disabled: bool


class UpdateUserResponse(BaseModel):
    """Update result with the ``mutated_fields`` audit-trail surface."""

    model_config = ConfigDict(extra="forbid")

    user_id: int
    email: str
    display_name: str
    is_admin: bool
    disabled: bool
    mutated_fields: list[str]


class UserListItemResponse(BaseModel):
    """Recipient-roster list item (L3-DASH-043).

    Adds ``created_at`` to the :class:`UserResponse` field set; ``password_hash``
    is deliberately absent so the hash never reaches the wire.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: int
    email: str
    display_name: str
    is_admin: bool
    disabled: bool
    created_at: str


class AdminCreateSubscriptionRequest(BaseModel):
    """Body of ``POST /admin/users/{user_id}/subscriptions`` (L3-DASH-045)."""

    model_config = ConfigDict(extra="forbid")

    granularity: SubscriptionGranularity
    target_value: str | None = Field(default=None, max_length=255)


class AdminSubscriptionResponse(BaseModel):
    """An admin-managed subscription projection (L3-DASH-045)."""

    model_config = ConfigDict(extra="forbid")

    subscription_id: int
    granularity: SubscriptionGranularity
    target_value: str | None


# -----------------------------------------------------------------------------
# Projections
# -----------------------------------------------------------------------------


def _project_user_list_item(user: User) -> UserListItemResponse:
    """Project a persisted :class:`User` to a roster list item (no hash)."""
    assert user.user_id is not None  # list_paginated only returns persisted rows
    return UserListItemResponse(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        is_admin=user.is_admin,
        disabled=user.disabled,
        created_at=iso_z(user.created_at),
    )


def _project_subscription(sub: Subscription) -> AdminSubscriptionResponse:
    """Project a :class:`Subscription` to the admin API shape."""
    return AdminSubscriptionResponse(
        subscription_id=int(sub.subscription_id),
        granularity=sub.granularity,
        target_value=sub.target_value,
    )


# -----------------------------------------------------------------------------
# Router factory
# -----------------------------------------------------------------------------


def build_admin_users_router(service: Service) -> APIRouter:
    """Construct the admin user-management router bound to ``service``.

    Args:
        service: The constructed :class:`Service` whose admin-user
            use cases the routes close over.

    Returns:
        An :class:`APIRouter` mounted under ``/admin/users``.
    """
    router = APIRouter(prefix="/admin/users", tags=["admin-users"])
    require_admin = require_admin_factory(service)

    @router.get("")
    async def list_users(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        _admin_id: int = Depends(require_admin),
    ) -> list[UserListItemResponse]:
        """L3-DASH-043: paginated recipient roster for the admin console.

        Admin-gated. ``limit`` defaults to 50 (max 200); ``offset`` defaults to
        0. Each item projects the account metadata without ``password_hash``.
        """
        del _admin_id  # auth gate only
        async with service.uow_factory() as uow:
            users = await uow.user_repo.list_paginated(limit=limit, offset=offset)
        return [_project_user_list_item(u) for u in users]

    @router.post("", status_code=status.HTTP_201_CREATED)
    async def create_user(
        body: CreateUserRequest,
        admin_id: int = Depends(require_admin),
    ) -> UserResponse:
        """Create a new user account. L3-AUTH-014/015/016/017."""
        try:
            result = await service.create_user.execute(
                admin_id=admin_id,
                email=body.email,
                display_name=body.display_name,
                password=Password(body.password),
                is_admin=body.is_admin,
                disabled=body.disabled,
            )
        except InvalidEmailError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="email format is invalid",
            ) from exc
        except DuplicateEmailError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="email already in use",
            ) from exc
        return UserResponse(
            user_id=result.user_id,
            email=result.email,
            display_name=result.display_name,
            is_admin=result.is_admin,
            disabled=result.disabled,
        )

    @router.patch("/{user_id}")
    async def update_user(
        body: UpdateUserRequest,
        user_id: Annotated[int, Path(ge=1)],
        admin_id: int = Depends(require_admin),
    ) -> UpdateUserResponse:
        """Update a user. L3-AUTH-014/015/017."""
        try:
            result = await service.update_user.execute(
                admin_id=admin_id,
                target_user_id=user_id,
                display_name=body.display_name,
                is_admin=body.is_admin,
                disabled=body.disabled,
            )
        except UserNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="user not found",
            ) from exc
        except SelfProtectionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "administrators cannot remove their own admin privilege"
                    " or disable their own account"
                ),
            ) from exc
        return UpdateUserResponse(
            user_id=result.user_id,
            email=result.email,
            display_name=result.display_name,
            is_admin=result.is_admin,
            disabled=result.disabled,
            mutated_fields=list(result.mutated_fields),
        )

    @router.post("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
    async def reset_password(
        body: ResetPasswordRequest,
        user_id: Annotated[int, Path(ge=1)],
        admin_id: int = Depends(require_admin),
    ) -> None:
        """Reset a user's password. L3-AUTH-014/016/017."""
        try:
            await service.reset_password.execute(
                admin_id=admin_id,
                target_user_id=user_id,
                new_password=Password(body.password),
            )
        except UserNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="user not found",
            ) from exc

    # -------------------------------------------------------------------------
    # Admin-on-behalf subscription management (L3-DASH-045)
    # -------------------------------------------------------------------------

    @router.get("/{user_id}/subscriptions")
    async def list_user_subscriptions(
        user_id: Annotated[int, Path(ge=1)],
        _admin_id: int = Depends(require_admin),
    ) -> list[AdminSubscriptionResponse]:
        """List a recipient's subscriptions (admin-gated)."""
        del _admin_id  # auth gate only
        async with service.uow_factory() as uow:
            subs = await uow.subscription_repo.list_for_user(UserId(user_id))
        return [_project_subscription(s) for s in subs]

    @router.post("/{user_id}/subscriptions", status_code=status.HTTP_201_CREATED)
    async def create_user_subscription(
        body: AdminCreateSubscriptionRequest,
        user_id: Annotated[int, Path(ge=1)],
        admin_id: int = Depends(require_admin),
    ) -> AdminSubscriptionResponse:
        """Create a subscription for a recipient on their behalf (L3-DASH-045).

        404 if the target user does not exist; 422 for an unknown pipeline/tag
        target; 409 if the recipient already has an identical subscription.
        """
        try:
            saved = await service.admin_subscribe.execute(
                admin_id=admin_id,
                target_user_id=UserId(user_id),
                granularity=body.granularity,
                target_value=body.target_value,
            )
        except UserNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="user not found",
            ) from exc
        except (UnknownPipelineTypeError, UnknownTagError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        except PersistenceError as exc:
            # The unique index on (user_id, granularity, target_value) rejects
            # duplicates; surface that as 409, re-raise anything else (→ 500).
            if "UNIQUE" in str(exc.details.get("reason", "")):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="recipient already has this subscription",
                ) from exc
            raise
        return _project_subscription(saved)

    @router.delete(
        "/{user_id}/subscriptions/{subscription_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_user_subscription(
        user_id: Annotated[int, Path(ge=1)],
        subscription_id: Annotated[int, Path(ge=1)],
        admin_id: int = Depends(require_admin),
    ) -> None:
        """Delete a recipient's subscription (L3-DASH-045).

        404 when the id is not a subscription of ``user_id`` (an admin cannot
        remove another recipient's subscription through this path).
        """
        try:
            await service.admin_unsubscribe.execute(
                admin_id=admin_id,
                target_user_id=UserId(user_id),
                subscription_id=SubscriptionId(subscription_id),
            )
        except SubscriptionNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="subscription not found",
            ) from exc

    return router


__all__ = [
    "CreateUserRequest",
    "ResetPasswordRequest",
    "UpdateUserRequest",
    "UpdateUserResponse",
    "UserResponse",
    "build_admin_users_router",
]
