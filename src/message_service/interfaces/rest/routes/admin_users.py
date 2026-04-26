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

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field

from message_service.domain.aggregates.password import Password
from message_service.domain.errors import (
    DuplicateEmailError,
    InvalidEmailError,
    SelfProtectionError,
    UserNotFoundError,
)
from message_service.interfaces.rest.app import require_admin_factory

if TYPE_CHECKING:
    from message_service.bootstrap import Service


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

    return router


__all__ = [
    "CreateUserRequest",
    "ResetPasswordRequest",
    "UpdateUserRequest",
    "UpdateUserResponse",
    "UserResponse",
    "build_admin_users_router",
]
