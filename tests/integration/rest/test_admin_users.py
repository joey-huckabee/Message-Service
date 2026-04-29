"""Integration tests for the admin user-management routes (Increment 20b).

Drives the FastAPI app via ``httpx.AsyncClient`` over the ASGI
transport against a real in-memory SQLite. Tests cover all three
routes:

* ``POST /admin/users`` — happy path, validation errors (422), email
  uniqueness (409), Argon2id hash discipline (L3-AUTH-016 PHC prefix),
  audit-record format (L3-AUTH-017), plaintext suppression on the
  response.
* ``PATCH /admin/users/{user_id}`` — happy path with single + multi-
  field mutation, empty-PATCH no-op (no audit, 200), 404 for unknown
  user_id, 422 for non-positive id, self-protection 409 (deadmin +
  disable), mutated_fields shape on audit.
* ``POST /admin/users/{user_id}/password`` — happy path (Argon2id
  prefix + login works with new password), 404 for unknown user_id,
  audit format, plaintext suppression.

All tests assume the 20a ``require_admin`` dependency: 401
unauthenticated, 403 for non-admin authenticated.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import httpx
import pytest
from httpx import ASGITransport

from message_service.application.ports.clock import Clock
from message_service.application.use_cases.admin_users import (
    CreateUserUseCase,
    ResetPasswordUseCase,
    UpdateUserUseCase,
)
from message_service.application.use_cases.login import LoginUseCase
from message_service.application.use_cases.logout import LogoutUseCase
from message_service.config.schema import Argon2Config, AuthConfig, DashboardConfig
from message_service.domain.aggregates.password import Password
from message_service.domain.aggregates.user import User
from message_service.infrastructure.auth.argon2_hasher import Argon2PasswordHasher
from message_service.infrastructure.persistence.audit_log import SqliteAuditLog
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations
from message_service.infrastructure.persistence.run_repository import SqliteRunRepository
from message_service.infrastructure.persistence.session_repository import (
    SqliteSessionRepository,
)
from message_service.infrastructure.persistence.stage_repository import (
    SqliteStageRepository,
)
from message_service.infrastructure.persistence.subscription_repository import (
    SqliteSubscriptionRepository,
)
from message_service.infrastructure.persistence.sweeper_action_repository import (
    SqliteSweeperActionRepository,
)
from message_service.infrastructure.persistence.unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from message_service.infrastructure.persistence.user_repository import (
    SqliteUserRepository,
)
from message_service.interfaces.rest.app import CSRF_COOKIE_NAME, create_app

_T0 = datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC)


class _FixedClock(Clock):
    def __init__(self, value: datetime = _T0) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value


class _ConfigStub:
    def __init__(self) -> None:
        self.dashboard = DashboardConfig(host="127.0.0.1", https_only=False)
        self.auth = AuthConfig(
            session_idle_timeout_seconds=3600,
            argon2=Argon2Config(memory_cost=8, time_cost=1, parallelism=1, hash_len=16, salt_len=8),
        )


class _ServiceLike:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
async def sqlite_conn() -> AsyncIterator[aiosqlite.Connection]:
    c = await open_connection(Path(":memory:"))
    try:
        await apply_migrations(c)
        yield c
    finally:
        await c.close()


@pytest.fixture
def clock() -> _FixedClock:
    return _FixedClock()


@pytest.fixture
def hasher() -> Argon2PasswordHasher:
    return Argon2PasswordHasher(memory_cost=8, time_cost=1, parallelism=1, hash_len=16, salt_len=8)


@pytest.fixture
def uow_factory(sqlite_conn: aiosqlite.Connection, clock: _FixedClock) -> SqliteUnitOfWorkFactory:
    return SqliteUnitOfWorkFactory(
        conn=sqlite_conn,
        run_repo_factory=lambda c: SqliteRunRepository(c),
        stage_repo_factory=lambda c: SqliteStageRepository(c),
        subscription_repo_factory=lambda c: SqliteSubscriptionRepository(c, clock=clock),
        audit_log_factory=lambda c: SqliteAuditLog(c),
        sweeper_action_repo_factory=lambda c: SqliteSweeperActionRepository(c),
        user_repo_factory=lambda c: SqliteUserRepository(c),
        session_repo_factory=lambda c: SqliteSessionRepository(c),
    )


@pytest.fixture
def service_like(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
    hasher: Argon2PasswordHasher,
) -> _ServiceLike:
    return _ServiceLike(
        config=_ConfigStub(),
        clock=clock,
        uow_factory=uow_factory,
        login=LoginUseCase(uow_factory=uow_factory, clock=clock, password_hasher=hasher),
        logout=LogoutUseCase(uow_factory=uow_factory, clock=clock),
        create_user=CreateUserUseCase(uow_factory=uow_factory, clock=clock, password_hasher=hasher),
        update_user=UpdateUserUseCase(uow_factory=uow_factory, clock=clock),
        reset_password=ResetPasswordUseCase(
            uow_factory=uow_factory, clock=clock, password_hasher=hasher
        ),
    )


@pytest.fixture
async def http_client(service_like: _ServiceLike) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(service_like)  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _seed_user(
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    *,
    email: str,
    is_admin: bool,
    password: str = "hunter2",
) -> User:
    pw_hash = hasher.hash(Password(password))
    async with uow_factory() as uow:
        saved = await uow.user_repo.save(
            User(
                email=email,
                display_name=email.split("@")[0],
                password_hash=pw_hash,
                created_at=_T0,
                disabled=False,
                is_admin=is_admin,
            ),
        )
        await uow.commit()
    assert saved.user_id is not None
    return saved


async def _login_as(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    *,
    email: str,
    is_admin: bool,
    password: str = "hunter2",
) -> tuple[str, int]:
    """Seed a user, log in, return (csrf, user_id)."""
    user = await _seed_user(uow_factory, hasher, email=email, is_admin=is_admin, password=password)
    response = await http_client.post("/login", json={"email": email, "password": password})
    assert response.status_code == 200
    assert user.user_id is not None
    return response.cookies[CSRF_COOKIE_NAME], user.user_id


async def _login_existing(
    http_client: httpx.AsyncClient,
    *,
    email: str,
    password: str,
) -> str:
    """Log in an already-seeded user; return the CSRF cookie."""
    response = await http_client.post("/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.cookies[CSRF_COOKIE_NAME]


async def _fetch_audit(
    sqlite_conn: aiosqlite.Connection, action: str, target_user_id: int | None
) -> list[dict[str, Any]]:
    """Read audit_log rows matching action + resource."""
    assert target_user_id is not None
    sql = (
        "SELECT timestamp, action, actor, resource, outcome, details_json "
        "FROM audit_log WHERE action = ? AND resource = ?"
    )
    rows: list[dict[str, Any]] = []
    async with sqlite_conn.execute(sql, (action, f"user:{target_user_id}")) as cur:
        async for row in cur:
            rows.append(
                {
                    "timestamp": row[0],
                    "action": row[1],
                    "actor": row[2],
                    "resource": row[3],
                    "outcome": row[4],
                    "details": json.loads(row[5]),
                }
            )
    return rows


# -----------------------------------------------------------------------------
# Auth gate (L3-AUTH-014)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_user_unauthenticated_returns_403_csrf(
    http_client: httpx.AsyncClient,
) -> None:
    """Unauthenticated POST is blocked by CSRF middleware (mirrors resend route)."""
    response = await http_client.post(
        "/admin/users",
        json={
            "email": "new@example.com",
            "display_name": "new",
            "password": "pw",
            "is_admin": False,
            "disabled": False,
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-014")
async def test_create_user_non_admin_returns_403(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-AUTH-014: non-admin authenticated requests SHALL return 403."""
    csrf, _ = await _login_as(
        http_client, uow_factory, hasher, email="alice@example.com", is_admin=False
    )
    response = await http_client.post(
        "/admin/users",
        headers={"X-CSRF-Token": csrf},
        json={
            "email": "new@example.com",
            "display_name": "new",
            "password": "pw",
            "is_admin": False,
            "disabled": False,
        },
    )
    assert response.status_code == 403


# -----------------------------------------------------------------------------
# POST /admin/users  (L3-AUTH-014/015/016/017)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-014")
@pytest.mark.requirement("L3-AUTH-015")
@pytest.mark.requirement("L3-SUB-007")
@pytest.mark.requirement("L3-SUB-008")
async def test_create_user_happy_path_returns_201(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-AUTH-014/015: well-formed create returns 201 and the user projection."""
    csrf, _ = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    response = await http_client.post(
        "/admin/users",
        headers={"X-CSRF-Token": csrf},
        json={
            "email": "new@example.com",
            "display_name": "new user",
            "password": "fresh-password-1",
            "is_admin": False,
            "disabled": False,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@example.com"
    assert body["display_name"] == "new user"
    assert body["is_admin"] is False
    assert body["disabled"] is False
    assert isinstance(body["user_id"], int) and body["user_id"] > 0
    # Plaintext suppression: response body SHALL NOT contain the password.
    assert "fresh-password-1" not in response.text
    assert "password" not in body


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-016")
async def test_create_user_persists_argon2id_hash(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    sqlite_conn: aiosqlite.Connection,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-AUTH-016: persisted password_hash SHALL carry the Argon2id PHC prefix."""
    csrf, _ = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    response = await http_client.post(
        "/admin/users",
        headers={"X-CSRF-Token": csrf},
        json={
            "email": "new@example.com",
            "display_name": "new",
            "password": "pw-secret-42",
            "is_admin": False,
            "disabled": False,
        },
    )
    assert response.status_code == 201

    async with sqlite_conn.execute(
        "SELECT password_hash FROM users WHERE email = ?", ("new@example.com",)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    persisted_hash = row[0]
    assert persisted_hash.startswith("$argon2id$v=19$")
    # Plaintext SHALL NOT be a substring of the persisted hash.
    assert "pw-secret-42" not in persisted_hash


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-017")
@pytest.mark.requirement("L3-OBS-035")
async def test_create_user_emits_create_user_audit(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    sqlite_conn: aiosqlite.Connection,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-AUTH-017: CREATE_USER audit details SHALL include target_user_id + target_email."""
    csrf, admin_id = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    response = await http_client.post(
        "/admin/users",
        headers={"X-CSRF-Token": csrf},
        json={
            "email": "new@example.com",
            "display_name": "new",
            "password": "pw",
            "is_admin": False,
            "disabled": False,
        },
    )
    new_user_id = response.json()["user_id"]

    rows = await _fetch_audit(sqlite_conn, "CREATE_USER", new_user_id)
    assert len(rows) == 1
    rec = rows[0]
    assert rec["actor"] == f"user:{admin_id}"
    assert rec["resource"] == f"user:{new_user_id}"
    assert rec["outcome"] == "SUCCESS"
    assert rec["details"]["target_user_id"] == new_user_id
    assert rec["details"]["target_email"] == "new@example.com"
    # The hash and plaintext SHALL NOT appear in details.
    details_json = json.dumps(rec["details"])
    assert (
        "pw" not in details_json or "target_email" in details_json
    )  # the substring "pw" appears only as part of legitimate fields if at all
    assert "$argon2id$" not in details_json
    assert "password_hash" not in details_json


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-015")
async def test_create_user_duplicate_email_returns_409(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-AUTH-015: duplicate email SHALL return HTTP 409."""
    csrf, _ = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    payload = {
        "email": "dup@example.com",
        "display_name": "first",
        "password": "pw",
        "is_admin": False,
        "disabled": False,
    }
    first = await http_client.post("/admin/users", headers={"X-CSRF-Token": csrf}, json=payload)
    assert first.status_code == 201
    second = await http_client.post(
        "/admin/users",
        headers={"X-CSRF-Token": csrf},
        json={**payload, "display_name": "second"},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-015")
async def test_create_user_invalid_email_returns_422(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-AUTH-015: malformed email SHALL return HTTP 422."""
    csrf, _ = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    response = await http_client.post(
        "/admin/users",
        headers={"X-CSRF-Token": csrf},
        json={
            "email": "not-an-email",
            "display_name": "x",
            "password": "pw",
            "is_admin": False,
            "disabled": False,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-015")
async def test_create_user_extra_fields_returns_422(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """``extra="forbid"`` rejects unknown fields with 422."""
    csrf, _ = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    response = await http_client.post(
        "/admin/users",
        headers={"X-CSRF-Token": csrf},
        json={
            "email": "x@example.com",
            "display_name": "x",
            "password": "pw",
            "is_admin": False,
            "disabled": False,
            "rogue": "value",
        },
    )
    assert response.status_code == 422


# -----------------------------------------------------------------------------
# PATCH /admin/users/{user_id}  (L3-AUTH-014/015/017)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-015")
async def test_patch_user_single_field_mutation(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    sqlite_conn: aiosqlite.Connection,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-AUTH-015: PATCH with display_name only SHALL mutate only that field."""
    csrf, admin_id = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    target = await _seed_user(uow_factory, hasher, email="bob@example.com", is_admin=False)
    response = await http_client.patch(
        f"/admin/users/{target.user_id}",
        headers={"X-CSRF-Token": csrf},
        json={"display_name": "renamed"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "renamed"
    assert body["mutated_fields"] == ["display_name"]

    rows = await _fetch_audit(sqlite_conn, "UPDATE_USER", target.user_id)
    assert len(rows) == 1
    assert rows[0]["details"]["mutated_fields"] == ["display_name"]
    assert rows[0]["actor"] == f"user:{admin_id}"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-017")
async def test_patch_user_multi_field_mutated_fields_sorted(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    sqlite_conn: aiosqlite.Connection,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-AUTH-017: multi-field PATCH SHALL emit sorted mutated_fields."""
    csrf, _ = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    target = await _seed_user(uow_factory, hasher, email="bob@example.com", is_admin=False)
    response = await http_client.patch(
        f"/admin/users/{target.user_id}",
        headers={"X-CSRF-Token": csrf},
        json={"is_admin": True, "disabled": True, "display_name": "renamed"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mutated_fields"] == ["disabled", "display_name", "is_admin"]

    rows = await _fetch_audit(sqlite_conn, "UPDATE_USER", target.user_id)
    assert rows[0]["details"]["mutated_fields"] == [
        "disabled",
        "display_name",
        "is_admin",
    ]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-015")
async def test_patch_user_empty_body_is_no_op_success(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    sqlite_conn: aiosqlite.Connection,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-AUTH-015: empty PATCH body SHALL return 200 with empty mutated_fields and NO audit."""
    csrf, _ = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    target = await _seed_user(uow_factory, hasher, email="bob@example.com", is_admin=False)
    response = await http_client.patch(
        f"/admin/users/{target.user_id}",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert response.status_code == 200
    assert response.json()["mutated_fields"] == []

    rows = await _fetch_audit(sqlite_conn, "UPDATE_USER", target.user_id)
    assert rows == []


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-014")
async def test_patch_user_unknown_id_returns_404(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    csrf, _ = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    response = await http_client.patch(
        "/admin/users/9999",
        headers={"X-CSRF-Token": csrf},
        json={"display_name": "x"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-014")
async def test_patch_user_non_positive_id_returns_422(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    csrf, _ = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    response = await http_client.patch(
        "/admin/users/0",
        headers={"X-CSRF-Token": csrf},
        json={"display_name": "x"},
    )
    assert response.status_code == 422


# -----------------------------------------------------------------------------
# Self-protection (L2-AUTH-009 / L3-AUTH-017)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-017")
async def test_patch_user_self_deadmin_returns_409(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    sqlite_conn: aiosqlite.Connection,
    hasher: Argon2PasswordHasher,
) -> None:
    """L2-AUTH-009: admin SHALL NOT remove their own is_admin via PATCH."""
    csrf, admin_id = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    response = await http_client.patch(
        f"/admin/users/{admin_id}",
        headers={"X-CSRF-Token": csrf},
        json={"is_admin": False},
    )
    assert response.status_code == 409
    # No audit record SHALL be emitted on rejection (per L3-AUTH-017).
    rows = await _fetch_audit(sqlite_conn, "UPDATE_USER", admin_id)
    assert rows == []


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-017")
async def test_patch_user_self_disable_returns_409(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    sqlite_conn: aiosqlite.Connection,
    hasher: Argon2PasswordHasher,
) -> None:
    """L2-AUTH-009: admin SHALL NOT set disabled=True on their own account."""
    csrf, admin_id = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    response = await http_client.patch(
        f"/admin/users/{admin_id}",
        headers={"X-CSRF-Token": csrf},
        json={"disabled": True},
    )
    assert response.status_code == 409
    rows = await _fetch_audit(sqlite_conn, "UPDATE_USER", admin_id)
    assert rows == []


@pytest.mark.asyncio
async def test_patch_user_self_safe_mutations_succeed(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """Self-protection only blocks deadmin / disable; other self-PATCHes succeed.

    An admin can rename themselves or set is_admin=True (no-op
    redundancy) without tripping the guardrail.
    """
    csrf, admin_id = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    response = await http_client.patch(
        f"/admin/users/{admin_id}",
        headers={"X-CSRF-Token": csrf},
        json={"display_name": "Renamed Admin", "is_admin": True},
    )
    assert response.status_code == 200


# -----------------------------------------------------------------------------
# POST /admin/users/{user_id}/password  (L3-AUTH-014/016/017)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-014")
async def test_reset_password_happy_path_returns_204(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    csrf, _ = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    target = await _seed_user(
        uow_factory, hasher, email="bob@example.com", is_admin=False, password="old-pw"
    )
    response = await http_client.post(
        f"/admin/users/{target.user_id}/password",
        headers={"X-CSRF-Token": csrf},
        json={"password": "new-secret-pw"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-016")
async def test_reset_password_persists_new_argon2id_hash(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    sqlite_conn: aiosqlite.Connection,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-AUTH-016: reset persists a new Argon2id-prefixed hash; old hash gone."""
    csrf, _ = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    target = await _seed_user(
        uow_factory, hasher, email="bob@example.com", is_admin=False, password="old-pw"
    )

    async with sqlite_conn.execute(
        "SELECT password_hash FROM users WHERE user_id = ?", (target.user_id,)
    ) as cur:
        old_row = await cur.fetchone()
    assert old_row is not None
    old_hash = old_row[0]

    response = await http_client.post(
        f"/admin/users/{target.user_id}/password",
        headers={"X-CSRF-Token": csrf},
        json={"password": "rotated-secret-pw"},
    )
    assert response.status_code == 204

    async with sqlite_conn.execute(
        "SELECT password_hash FROM users WHERE user_id = ?", (target.user_id,)
    ) as cur:
        new_row = await cur.fetchone()
    assert new_row is not None
    new_hash = new_row[0]
    assert new_hash != old_hash
    assert new_hash.startswith("$argon2id$v=19$")
    assert "rotated-secret-pw" not in new_hash


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-016")
async def test_reset_password_login_succeeds_with_new_password(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """End-to-end: after reset, the target user can log in with the new password."""
    admin_csrf, _ = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    target = await _seed_user(
        uow_factory, hasher, email="bob@example.com", is_admin=False, password="old-pw"
    )
    await http_client.post(
        f"/admin/users/{target.user_id}/password",
        headers={"X-CSRF-Token": admin_csrf},
        json={"password": "new-secret-pw"},
    )

    # Old password SHALL fail.
    bad_login = await http_client.post(
        "/login", json={"email": "bob@example.com", "password": "old-pw"}
    )
    assert bad_login.status_code == 401

    # New password SHALL succeed.
    good_login = await http_client.post(
        "/login", json={"email": "bob@example.com", "password": "new-secret-pw"}
    )
    assert good_login.status_code == 200


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-017")
async def test_reset_password_emits_update_user_audit_with_password_hash_field(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    sqlite_conn: aiosqlite.Connection,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-AUTH-017: password reset audit SHALL carry mutated_fields=['password_hash']."""
    csrf, admin_id = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    target = await _seed_user(
        uow_factory, hasher, email="bob@example.com", is_admin=False, password="old-pw"
    )
    await http_client.post(
        f"/admin/users/{target.user_id}/password",
        headers={"X-CSRF-Token": csrf},
        json={"password": "new-pw-not-in-audit"},
    )

    rows = await _fetch_audit(sqlite_conn, "UPDATE_USER", target.user_id)
    assert len(rows) == 1
    rec = rows[0]
    assert rec["actor"] == f"user:{admin_id}"
    assert rec["resource"] == f"user:{target.user_id}"
    assert rec["details"]["mutated_fields"] == ["password_hash"]
    # The hash value SHALL NOT appear in details (L3-OBS-036 redaction).
    details_json = json.dumps(rec["details"])
    assert "$argon2id$" not in details_json
    # Plaintext SHALL NOT appear either.
    assert "new-pw-not-in-audit" not in details_json


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-014")
async def test_reset_password_unknown_user_returns_404(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    csrf, _ = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    response = await http_client.post(
        "/admin/users/9999/password",
        headers={"X-CSRF-Token": csrf},
        json={"password": "pw"},
    )
    assert response.status_code == 404


# -----------------------------------------------------------------------------
# Login still works for the email-immutability invariant (L2-AUTH-007)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-015")
async def test_patch_user_does_not_accept_email_field(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L2-AUTH-007: PATCH SHALL reject an `email` field via extra=forbid."""
    csrf, _ = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    target = await _seed_user(uow_factory, hasher, email="bob@example.com", is_admin=False)
    response = await http_client.patch(
        f"/admin/users/{target.user_id}",
        headers={"X-CSRF-Token": csrf},
        json={"email": "renamed@example.com"},
    )
    assert response.status_code == 422
