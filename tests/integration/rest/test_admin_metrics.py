"""Integration tests for the embedded metrics dashboard route (L3-DASH-016).

Drives the FastAPI app via ``httpx.AsyncClient`` over ASGI against a real
in-memory SQLite, and verifies the admin gate + that an admin receives the
self-contained HTML dashboard embedding the parsed metric model.
"""

from __future__ import annotations

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
from message_service.infrastructure.persistence.user_repository import SqliteUserRepository
from message_service.interfaces.rest.app import create_app

_T0 = datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC)


class _FixedClock(Clock):
    def now(self) -> datetime:
        return _T0


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


@pytest.fixture
async def sqlite_conn() -> AsyncIterator[aiosqlite.Connection]:
    c = await open_connection(Path(":memory:"))
    try:
        await apply_migrations(c)
        yield c
    finally:
        await c.close()


@pytest.fixture
def hasher() -> Argon2PasswordHasher:
    return Argon2PasswordHasher(memory_cost=8, time_cost=1, parallelism=1, hash_len=16, salt_len=8)


@pytest.fixture
def uow_factory(sqlite_conn: aiosqlite.Connection) -> SqliteUnitOfWorkFactory:
    clock = _FixedClock()
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
async def http_client(
    uow_factory: SqliteUnitOfWorkFactory, hasher: Argon2PasswordHasher
) -> AsyncIterator[httpx.AsyncClient]:
    clock = _FixedClock()
    service = _ServiceLike(
        config=_ConfigStub(),
        clock=clock,
        uow_factory=uow_factory,
        login=LoginUseCase(uow_factory=uow_factory, clock=clock, password_hasher=hasher),
        logout=LogoutUseCase(uow_factory=uow_factory, clock=clock),
    )
    app = create_app(service)  # type: ignore[arg-type]
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
) -> None:
    async with uow_factory() as uow:
        await uow.user_repo.save(
            User(
                email=email,
                display_name=email.split("@")[0],
                password_hash=hasher.hash(Password("hunter2")),
                created_at=_T0,
                disabled=False,
                is_admin=is_admin,
            ),
        )
        await uow.commit()
    resp = await http_client.post("/login", json={"email": email, "password": "hunter2"})
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-016")
async def test_admin_metrics_unauthenticated_returns_401(http_client: httpx.AsyncClient) -> None:
    """GET /admin/metrics without a session SHALL be rejected (admin-gated)."""
    resp = await http_client.get("/admin/metrics")
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-016")
async def test_admin_metrics_non_admin_returns_403(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """An authenticated non-admin SHALL be forbidden."""
    await _login_as(http_client, uow_factory, hasher, email="alice@example.com", is_admin=False)
    resp = await http_client.get("/admin/metrics")
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-016")
async def test_admin_metrics_admin_receives_dashboard_html(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """An admin SHALL receive the self-contained HTML dashboard with embedded data."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    resp = await http_client.get("/admin/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    # Self-contained page: embedded JSON model + render target + inlined assets.
    assert '<script type="application/json" id="metrics-data">' in body
    assert 'id="panels"' in body
    assert "createElementNS" in body  # the renderer JS is inlined, not linked
    assert "http://" not in body.replace("http://www.w3.org/", "")  # no external origin
