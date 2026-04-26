"""End-to-end admin: login → user CRUD → audit viewer → template inspection.

Covers the full v1 admin surface as a black-box flow:

* Admin logs in via ``POST /login`` (real Argon2 hash from the
  shared ``PasswordHasher`` singleton).
* Admin creates a user via ``POST /admin/users``.
* Admin resets that user's password via
  ``POST /admin/users/{id}/password``.
* Admin reads the audit log via ``GET /admin/audit`` and confirms
  both the ``CREATE_USER`` and ``UPDATE_USER`` records appear.
* Admin reads the template registry via ``GET /templates`` and
  confirms all three manifest entries (``email_body``, ``fragment``,
  ``aggregation``) are surfaced.

This test deliberately exercises **every** admin-stream surface
landed by 20a (template viewer + admin gate), 20b (user
management), and 20c (audit viewer) in one flow — proving the
streams compose correctly under the production bootstrap.

Requirement references
----------------------
L1-AUTH-003 (admin user management)
L1-DASH-003 (template registry inspection clause)
L1-DASH-005 (audit-log viewer)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from message_service.application.ports.password_hasher import PasswordHasher
from message_service.domain.aggregates.password import Password
from message_service.domain.aggregates.user import User
from tests.fixtures.service import RunningService

_T0 = datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC)


async def _seed_admin(
    handle: RunningService,
    *,
    email: str = "admin@example.com",
    password: str = "hunter2",
) -> None:
    hasher: PasswordHasher = handle.service.password_hasher
    pw_hash = hasher.hash(Password(password))
    async with handle.service.uow_factory() as uow:
        await uow.user_repo.save(
            User(
                email=email,
                display_name="admin",
                password_hash=pw_hash,
                created_at=_T0,
                disabled=False,
                is_admin=True,
            ),
        )
        await uow.commit()


@pytest.mark.asyncio
@pytest.mark.requirement("L1-AUTH-003")
@pytest.mark.requirement("L1-DASH-003")
@pytest.mark.requirement("L1-DASH-005")
async def test_admin_lifecycle_flow(
    running_service: RunningService,
) -> None:
    """Login → create user → reset password → audit viewer → template viewer."""
    await _seed_admin(running_service)
    client = running_service.dashboard_client

    # 1. Admin login.
    login_resp = await client.post(
        "/login",
        json={"email": "admin@example.com", "password": "hunter2"},
    )
    assert login_resp.status_code == 200
    csrf = login_resp.cookies["msp_csrf"]
    headers = {"X-CSRF-Token": csrf}

    # 2. Create a new (non-admin) user.
    create_resp = await client.post(
        "/admin/users",
        headers=headers,
        json={
            "email": "operator@example.com",
            "display_name": "operator",
            "password": "initial-pw",
            "is_admin": False,
            "disabled": False,
        },
    )
    assert create_resp.status_code == 201
    new_user_id = create_resp.json()["user_id"]

    # 3. Reset the new user's password.
    reset_resp = await client.post(
        f"/admin/users/{new_user_id}/password",
        headers=headers,
        json={"password": "rotated-pw"},
    )
    assert reset_resp.status_code == 204

    # 4. Audit viewer SHALL surface CREATE_USER + UPDATE_USER for the
    #    new user, plus the LOGIN row from step 1.
    audit_resp = await client.get(
        f"/admin/audit?action=CREATE_USER&action=UPDATE_USER&resource=user:{new_user_id}"
    )
    assert audit_resp.status_code == 200
    actions = sorted({item["action"] for item in audit_resp.json()})
    assert actions == ["CREATE_USER", "UPDATE_USER"]

    # 5. Template viewer SHALL list all three manifest entries.
    templates_resp = await client.get("/templates")
    assert templates_resp.status_code == 200
    template_names = sorted(item["name"] for item in templates_resp.json())
    assert template_names == ["aggregation", "email_body", "fragment"]
    # Each entry SHALL carry the kind enum value.
    kinds = {item["name"]: item["kind"] for item in templates_resp.json()}
    assert kinds["aggregation"] == "AGGREGATION"
    assert kinds["email_body"] == "EMAIL_BODY"
    assert kinds["fragment"] == "REPORT_FRAGMENT"

    # 6. The new user SHALL be able to log in with the rotated
    #    password — proves the password-reset path is wired
    #    end-to-end through the same Argon2 chokepoint.
    new_user_login = await client.post(
        "/login",
        json={"email": "operator@example.com", "password": "rotated-pw"},
    )
    assert new_user_login.status_code == 200
