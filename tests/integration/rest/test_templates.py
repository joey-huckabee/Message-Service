"""Integration tests for the template registry inspection routes (Increment 20a).

Drives the FastAPI app via ``httpx.AsyncClient`` over the ASGI
transport against a real in-memory SQLite (for sessions/users) and an
:class:`InMemoryTemplateRepository` seeded with a fixed set of
metadata entries plus a real on-disk JSON schema file under
``tmp_path``.

Asserts:

* Auth gate: 401 unauthenticated, 403 non-admin, 200 admin.
* L3-DASH-021: `is_admin` is re-checked per request — flipping the
  flag in the DB takes effect on the next request without re-login.
* L3-DASH-014: GET allowed; POST / PATCH / DELETE → 405.
* L3-DASH-031: list shape, ordering by ``(name, version)``, schema
  contents included when readable.
* L3-DASH-032: detail shape, 404 on unknown ``(name, version)``.
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
from message_service.application.use_cases.login import LoginUseCase
from message_service.application.use_cases.logout import LogoutUseCase
from message_service.config.schema import Argon2Config, AuthConfig, DashboardConfig
from message_service.domain.aggregates.password import Password
from message_service.domain.aggregates.template_metadata import (
    TemplateKind,
    TemplateMetadata,
)
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
from message_service.infrastructure.templating.manifest_loader import (
    InMemoryTemplateRepository,
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
    """Smallest object that ``create_app`` + the templates router access."""

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
def schema_file(tmp_path: Path) -> Path:
    """Write a small JSON schema file the templates can reference."""
    path = tmp_path / "stage_schema.json"
    path.write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft-07/schema#",
                "type": "object",
                "properties": {"metric": {"type": "number"}},
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def template_repo(tmp_path: Path, schema_file: Path) -> InMemoryTemplateRepository:
    """Three templates spanning all three TemplateKind values.

    One has a schema (``stage_report``), two do not (``email_body``,
    ``aggregation``). One template (``aggregation``) has a higher
    ``version`` than ``stage_report`` to exercise (name, version)
    ordering. ``email_body`` carries a description; the other two do
    not — covers the optional-field projection.
    """
    entries = {
        ("stage_report", "1.0.0"): TemplateMetadata(
            name="stage_report",
            version="1.0.0",
            kind=TemplateKind.REPORT_FRAGMENT,
            source_path=tmp_path / "stage.html.j2",
            context_schema_path=schema_file,
            description=None,
        ),
        ("email_body", "1.0.0"): TemplateMetadata(
            name="email_body",
            version="1.0.0",
            kind=TemplateKind.EMAIL_BODY,
            source_path=tmp_path / "email.html.j2",
            context_schema_path=None,
            description="Default email body",
        ),
        ("aggregation", "2.0.0"): TemplateMetadata(
            name="aggregation",
            version="2.0.0",
            kind=TemplateKind.AGGREGATION,
            source_path=tmp_path / "agg.html.j2",
            context_schema_path=None,
            description=None,
        ),
    }
    return InMemoryTemplateRepository(entries)


async def _seed_user(
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    *,
    email: str,
    is_admin: bool,
) -> User:
    pw_hash = hasher.hash(Password("hunter2"))
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


@pytest.fixture
def service_like(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
    hasher: Argon2PasswordHasher,
    template_repo: InMemoryTemplateRepository,
) -> _ServiceLike:
    return _ServiceLike(
        config=_ConfigStub(),
        clock=clock,
        uow_factory=uow_factory,
        template_repo=template_repo,
        login=LoginUseCase(uow_factory=uow_factory, clock=clock, password_hasher=hasher),
        logout=LogoutUseCase(uow_factory=uow_factory, clock=clock),
    )


@pytest.fixture
async def http_client(service_like: _ServiceLike) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(service_like)  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _login_as(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    *,
    email: str,
    is_admin: bool,
) -> str:
    """Seed a user with the requested admin flag, log in, return CSRF cookie."""
    await _seed_user(uow_factory, hasher, email=email, is_admin=is_admin)
    response = await http_client.post("/login", json={"email": email, "password": "hunter2"})
    assert response.status_code == 200
    return response.cookies[CSRF_COOKIE_NAME]


# -----------------------------------------------------------------------------
# Auth gate (L3-DASH-011, L3-DASH-021, L1-AUTH-002)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_templates_unauthenticated_returns_401(
    http_client: httpx.AsyncClient,
) -> None:
    """L1-AUTH-002: unauthenticated requests SHALL return 401."""
    response = await http_client.get("/templates")
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-011")
async def test_get_templates_non_admin_returns_403(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-011: non-admin users SHALL receive 403 from admin routes."""
    await _login_as(http_client, uow_factory, hasher, email="alice@example.com", is_admin=False)
    response = await http_client.get("/templates")
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-021")
async def test_admin_gate_rechecks_is_admin_per_request(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    sqlite_conn: aiosqlite.Connection,
) -> None:
    """L3-DASH-021: ``is_admin`` SHALL be re-checked on every request.

    Log in as admin, succeed; flip ``is_admin`` to False in the DB;
    next request SHALL fail with 403 without re-logging-in. Confirms
    the gate is not session-cached.
    """
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    first = await http_client.get("/templates")
    assert first.status_code == 200

    # Flip ``is_admin`` to False directly via SQL — the v1 user repo
    # only supports inserts, and admin-driven user mutation is the
    # subject of Increment 20b. Using raw SQL here exercises the
    # invariant L3-DASH-021 cares about: re-check on every request.
    await sqlite_conn.execute(
        "UPDATE users SET is_admin = 0 WHERE email = ?",
        ("admin@example.com",),
    )
    await sqlite_conn.commit()

    second = await http_client.get("/templates")
    assert second.status_code == 403


# -----------------------------------------------------------------------------
# HTTP method allow-list (L3-DASH-014)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-014")
async def test_post_templates_returns_405(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-014: POST against /templates SHALL return 405."""
    csrf = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    response = await http_client.post("/templates", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 405


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-014")
async def test_delete_template_detail_returns_405(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-014: DELETE against /templates/{name}/{version} SHALL return 405."""
    csrf = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    response = await http_client.delete(
        "/templates/stage_report/1.0.0", headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 405


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-014")
async def test_patch_template_detail_returns_405(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    csrf = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    response = await http_client.patch(
        "/templates/stage_report/1.0.0", headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 405


# -----------------------------------------------------------------------------
# GET /templates  (L3-DASH-031)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-015")
@pytest.mark.requirement("L3-DASH-031")
async def test_get_templates_list_returns_all_with_full_projection(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-015 + L3-DASH-031: list SHALL carry exactly the documented projection.

    The exact-keyset assertion below covers L3-DASH-015's positive
    clause (name/version/schema_path/source_path/schema contents
    appear) and its negative clause (rendered past-report contents
    do NOT appear, and neither does the Jinja2 source body).
    L3-DASH-031 extends this with `kind` and `description`.
    """
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    response = await http_client.get("/templates")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 3

    # The (name, version) ordering is asserted by the next test; here
    # we just check field-presence on every item.
    for item in body:
        assert set(item.keys()) == {
            "name",
            "version",
            "kind",
            "source_path",
            "context_schema_path",
            "context_schema",
            "description",
        }
        assert item["kind"] in {"REPORT_FRAGMENT", "EMAIL_BODY", "AGGREGATION"}


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-031")
async def test_get_templates_list_ordered_by_name_version_ascending(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-031: ordering SHALL be deterministic, by (name, version) ascending."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    response = await http_client.get("/templates")
    body = response.json()
    pairs = [(item["name"], item["version"]) for item in body]
    assert pairs == sorted(pairs)


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-031")
async def test_get_templates_list_includes_parsed_schema_when_present(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """The ``stage_report`` template has a schema; it SHALL appear parsed."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    response = await http_client.get("/templates")
    body = response.json()
    by_name = {item["name"]: item for item in body}
    stage = by_name["stage_report"]
    assert stage["context_schema_path"] is not None
    assert isinstance(stage["context_schema"], dict)
    assert stage["context_schema"]["type"] == "object"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-031")
async def test_get_templates_list_returns_null_schema_when_absent(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """Templates without a ``context_schema_path`` SHALL surface as null."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    response = await http_client.get("/templates")
    body = response.json()
    by_name = {item["name"]: item for item in body}
    email = by_name["email_body"]
    assert email["context_schema_path"] is None
    assert email["context_schema"] is None


# -----------------------------------------------------------------------------
# GET /templates/{name}/{version}  (L3-DASH-032)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-032")
async def test_get_template_detail_returns_single_object(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-032: detail SHALL return the same projection as a list element."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    response = await http_client.get("/templates/stage_report/1.0.0")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "stage_report"
    assert body["version"] == "1.0.0"
    assert body["kind"] == "REPORT_FRAGMENT"
    assert isinstance(body["context_schema"], dict)


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-032")
async def test_get_template_detail_unknown_returns_404(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-032: unknown (name, version) pairs SHALL return 404."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    response = await http_client.get("/templates/does_not_exist/1.0.0")
    assert response.status_code == 404
    assert "template not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-032")
async def test_get_template_detail_unknown_version_returns_404(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """An existing name with a non-matching version SHALL also return 404."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    response = await http_client.get("/templates/stage_report/9.9.9")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_template_detail_unauthenticated_returns_401(
    http_client: httpx.AsyncClient,
) -> None:
    response = await http_client.get("/templates/stage_report/1.0.0")
    assert response.status_code == 401
