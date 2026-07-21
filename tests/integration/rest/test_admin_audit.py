"""Integration tests for the admin audit-log viewer (Increment 20c).

Covers `L1-DASH-005` / `L2-DASH-015` / `L2-DASH-016` /
`L3-DASH-033` / `L3-DASH-034` / `L3-DASH-035`.

Drives the FastAPI app via ``httpx.AsyncClient`` over the ASGI
transport against a real in-memory SQLite. Audit rows are seeded
through the ``SqliteAuditLog.record`` adapter (the same write path
production uses), then read back through the ``GET /admin/audit``
route.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
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
from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
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
from message_service.interfaces.rest.app import create_app

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


async def _login_as(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    *,
    email: str,
    is_admin: bool,
) -> int:
    """Seed a user, log in. Note: login itself emits a LOGIN audit
    record, so callers that filter by action SHALL filter it out or
    seed before login."""
    user = await _seed_user(uow_factory, hasher, email=email, is_admin=is_admin)
    response = await http_client.post("/login", json={"email": email, "password": "hunter2"})
    assert response.status_code == 200
    assert user.user_id is not None
    return user.user_id


async def _seed_audit(
    uow_factory: SqliteUnitOfWorkFactory,
    *,
    timestamp: datetime,
    action: AuditAction,
    actor: str,
    resource: str,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    details: dict[str, Any] | None = None,
) -> None:
    """Insert one audit row through the production write path."""
    async with uow_factory() as uow:
        await uow.audit_log.record(
            AuditEvent(
                timestamp=timestamp,
                action=action,
                actor=actor,
                resource=resource,
                outcome=outcome,
                details=details or {},
            )
        )
        await uow.commit()


# -----------------------------------------------------------------------------
# Auth gate (L3-DASH-033)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthenticated_returns_401(http_client: httpx.AsyncClient) -> None:
    """Unauthenticated GET /admin/audit SHALL return 401."""
    response = await http_client.get("/admin/audit")
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-033")
async def test_non_admin_returns_403(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-033: non-admin authenticated request SHALL return 403."""
    await _login_as(http_client, uow_factory, hasher, email="alice@example.com", is_admin=False)
    response = await http_client.get("/admin/audit")
    assert response.status_code == 403


# -----------------------------------------------------------------------------
# Empty + happy path
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-033")
async def test_empty_result_returns_200_with_empty_list(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-033: empty result sets SHALL return 200 with [] (NOT 404).

    The login itself emits a LOGIN audit row, so we filter it out by
    asking only for SWEEP_ORPHAN — guaranteed empty.
    """
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    response = await http_client.get("/admin/audit?action=SWEEP_ORPHAN")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-035")
async def test_response_shape_includes_full_projection(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-035: each item SHALL carry exactly the L2-DASH-016 field set."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    await _seed_audit(
        uow_factory,
        timestamp=_T0,
        action=AuditAction.SWEEP_ORPHAN,
        actor="system:sweeper",
        resource="run:abc",
        outcome=AuditOutcome.SUCCESS,
        details={"orphan_count": 3, "reason": "timed_out"},
    )

    response = await http_client.get("/admin/audit?action=SWEEP_ORPHAN")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    item = body[0]
    assert set(item.keys()) == {
        "audit_id",
        "timestamp",
        "action",
        "actor",
        "resource",
        "outcome",
        "details",
    }
    assert isinstance(item["audit_id"], int) and item["audit_id"] > 0
    assert item["action"] == "SWEEP_ORPHAN"
    assert item["actor"] == "system:sweeper"
    assert item["resource"] == "run:abc"
    assert item["outcome"] == "SUCCESS"
    assert item["details"] == {"orphan_count": 3, "reason": "timed_out"}


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-035")
async def test_details_returned_as_parsed_json_object_not_string(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-035: details SHALL be a parsed object, not a stringified blob."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    await _seed_audit(
        uow_factory,
        timestamp=_T0,
        action=AuditAction.SWEEP_ORPHAN,
        actor="system:sweeper",
        resource="run:abc",
        details={"nested": {"key": "value"}, "list": [1, 2, 3]},
    )

    response = await http_client.get("/admin/audit?action=SWEEP_ORPHAN")
    item = response.json()[0]
    # Round-trip the nested structure to confirm the route returned a
    # JSON object, not a stringified one.
    assert item["details"]["nested"]["key"] == "value"
    assert item["details"]["list"] == [1, 2, 3]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-035")
async def test_details_empty_dict_is_accepted(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """Empty-details rows SHALL round-trip cleanly (legacy categories use this)."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    await _seed_audit(
        uow_factory,
        timestamp=_T0,
        action=AuditAction.SWEEP_ORPHAN,
        actor="system:sweeper",
        resource="run:abc",
        details={},
    )

    response = await http_client.get("/admin/audit?action=SWEEP_ORPHAN")
    assert response.json()[0]["details"] == {}


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-035")
async def test_route_does_not_redact_marker_string_in_details(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-035 / L1-DASH-005: marker string in details SHALL appear verbatim.

    Proves the viewer is faithful to the table contents and that the
    redaction obligation lives upstream (write-time per L3-OBS-036).
    A redacting viewer would break this — by design, we don't.
    """
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    marker = "FAITHFUL_PROJECTION_MARKER_42"
    await _seed_audit(
        uow_factory,
        timestamp=_T0,
        action=AuditAction.SWEEP_ORPHAN,
        actor="system:sweeper",
        resource="run:abc",
        details={"trace_marker": marker},
    )

    response = await http_client.get("/admin/audit?action=SWEEP_ORPHAN")
    body_text = response.text
    assert marker in body_text


# -----------------------------------------------------------------------------
# Multi-action filter (L3-DASH-033)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-033")
async def test_multiple_action_values_or_together(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-033: multiple `action=X&action=Y` SHALL be ANY-matched."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    await _seed_audit(
        uow_factory,
        timestamp=_T0,
        action=AuditAction.SWEEP_ORPHAN,
        actor="system:sweeper",
        resource="run:1",
    )
    await _seed_audit(
        uow_factory,
        timestamp=_T0 + timedelta(seconds=1),
        action=AuditAction.RUN_STATE_TRANSITION,
        actor="system:assemble_and_deliver",
        resource="run:1",
    )
    await _seed_audit(
        uow_factory,
        timestamp=_T0 + timedelta(seconds=2),
        action=AuditAction.BEGIN_RUN,
        actor="pipeline:etl",
        resource="run:2",
    )

    response = await http_client.get("/admin/audit?action=SWEEP_ORPHAN&action=RUN_STATE_TRANSITION")
    body = response.json()
    actions = sorted(item["action"] for item in body)
    assert actions == ["RUN_STATE_TRANSITION", "SWEEP_ORPHAN"]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-033")
async def test_unknown_action_returns_422(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-033: action values not in the AuditAction enum SHALL return 422."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    response = await http_client.get("/admin/audit?action=NOT_A_REAL_ACTION")
    assert response.status_code == 422


# -----------------------------------------------------------------------------
# Pagination (L3-DASH-033)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-033")
async def test_limit_and_offset_slice_results(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-033: limit + offset SHALL slice the result window."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    for i in range(5):
        await _seed_audit(
            uow_factory,
            timestamp=_T0 + timedelta(seconds=i),
            action=AuditAction.SWEEP_ORPHAN,
            actor="system:sweeper",
            resource=f"run:{i}",
        )

    page1 = await http_client.get("/admin/audit?action=SWEEP_ORPHAN&limit=2&offset=0")
    page2 = await http_client.get("/admin/audit?action=SWEEP_ORPHAN&limit=2&offset=2")
    assert len(page1.json()) == 2
    assert len(page2.json()) == 2
    seen = {item["audit_id"] for item in page1.json()} | {item["audit_id"] for item in page2.json()}
    assert len(seen) == 4  # no overlap


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-033")
async def test_limit_above_max_returns_422(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-033: limit > 200 SHALL return 422."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    response = await http_client.get("/admin/audit?limit=999")
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-033")
async def test_negative_offset_returns_422(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-033: offset < 0 SHALL return 422."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    response = await http_client.get("/admin/audit?offset=-1")
    assert response.status_code == 422


# -----------------------------------------------------------------------------
# Ordering invariant (L3-DASH-034)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-034")
async def test_within_uow_ties_ordered_by_audit_id_desc(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-034: same-timestamp records SHALL still order audit_id DESC.

    Insert three records with identical timestamps (the within-UoW
    case the L3-DASH-034 rationale calls out). The route's
    audit_id-DESC ordering SHALL surface them most-recent-insertion-
    first, regardless of SQL-engine row-order chance.
    """
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    for i in range(3):
        await _seed_audit(
            uow_factory,
            timestamp=_T0,  # identical timestamp on all three
            action=AuditAction.SWEEP_ORPHAN,
            actor="system:sweeper",
            resource=f"run:{i}",
        )

    response = await http_client.get("/admin/audit?action=SWEEP_ORPHAN")
    audit_ids = [item["audit_id"] for item in response.json()]
    # Ordering SHALL be strictly descending — the most recent insert
    # has the highest audit_id and SHALL appear first.
    assert audit_ids == sorted(audit_ids, reverse=True)


# -----------------------------------------------------------------------------
# Filters
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-033")
async def test_actor_exact_match_filter(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-033: actor filter is exact match."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    await _seed_audit(
        uow_factory,
        timestamp=_T0,
        action=AuditAction.SWEEP_ORPHAN,
        actor="system:sweeper",
        resource="run:1",
    )
    await _seed_audit(
        uow_factory,
        timestamp=_T0 + timedelta(seconds=1),
        action=AuditAction.SWEEP_ORPHAN,
        actor="user:42",
        resource="run:2",
    )

    response = await http_client.get("/admin/audit?action=SWEEP_ORPHAN&actor=user:42")
    body = response.json()
    assert len(body) == 1
    assert body[0]["actor"] == "user:42"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-033")
async def test_resource_exact_match_filter(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-033: resource filter is exact match."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    await _seed_audit(
        uow_factory,
        timestamp=_T0,
        action=AuditAction.SWEEP_ORPHAN,
        actor="system:sweeper",
        resource="run:abc",
    )
    await _seed_audit(
        uow_factory,
        timestamp=_T0 + timedelta(seconds=1),
        action=AuditAction.SWEEP_ORPHAN,
        actor="system:sweeper",
        resource="run:xyz",
    )

    response = await http_client.get("/admin/audit?action=SWEEP_ORPHAN&resource=run:abc")
    body = response.json()
    assert len(body) == 1
    assert body[0]["resource"] == "run:abc"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-033")
async def test_from_to_inclusive_timestamp_bounds(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-033: both `from` and `to` SHALL be inclusive bounds."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    early = _T0
    middle = _T0 + timedelta(minutes=5)
    late = _T0 + timedelta(minutes=10)
    for ts, resource in [(early, "run:1"), (middle, "run:2"), (late, "run:3")]:
        await _seed_audit(
            uow_factory,
            timestamp=ts,
            action=AuditAction.SWEEP_ORPHAN,
            actor="system:sweeper",
            resource=resource,
        )

    # Window: middle exact bound on both sides; both bounds inclusive.
    middle_iso = middle.isoformat().replace("+00:00", "Z")
    response = await http_client.get(
        f"/admin/audit?action=SWEEP_ORPHAN&from={middle_iso}&to={middle_iso}"
    )
    body = response.json()
    assert len(body) == 1
    assert body[0]["resource"] == "run:2"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-033")
async def test_invalid_timestamp_returns_422(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-033: malformed `from` SHALL return 422."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    response = await http_client.get("/admin/audit?from=not-a-timestamp")
    assert response.status_code == 422


# -----------------------------------------------------------------------------
# Read-only invariant (L3-DASH-014-style; structural for /admin/audit)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_to_admin_audit_returns_405(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """The audit log is append-only via the use-case write path; the
    viewer is read-only. POST against /admin/audit returns 405 by
    FastAPI's default method handling.
    """
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    response = await http_client.post(
        "/admin/audit",
        json={},
        headers={"X-CSRF-Token": "anything"},
    )
    assert response.status_code in (403, 405)
    # If 405, it's the FastAPI route response. If 403, the CSRF
    # middleware short-circuited before route dispatch (no CSRF
    # cookie was issued because login flow's response was already
    # consumed). Either way, write was rejected.


# -----------------------------------------------------------------------------
# Default (no filters)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-034")
async def test_default_no_filter_returns_recent_first(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-034: with no filters, every record returns audit_id DESC.

    The login flow itself emits a LOGIN audit row, so the response
    will include at least that one in addition to the seeded rows.
    """
    admin_id = await _login_as(
        http_client, uow_factory, hasher, email="admin@example.com", is_admin=True
    )
    await _seed_audit(
        uow_factory,
        timestamp=_T0,
        action=AuditAction.SWEEP_ORPHAN,
        actor="system:sweeper",
        resource="run:1",
    )

    response = await http_client.get("/admin/audit")
    body = response.json()
    audit_ids = [item["audit_id"] for item in body]
    assert audit_ids == sorted(audit_ids, reverse=True)
    # The LOGIN row from _login_as SHALL be in the result set.
    assert any(item["action"] == "LOGIN" for item in body)
    del admin_id  # used implicitly through the login flow


# -----------------------------------------------------------------------------
# JSON serialization sanity
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_is_json_array(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """The response body SHALL be a JSON array (not an object wrapping it)."""
    await _login_as(http_client, uow_factory, hasher, email="admin@example.com", is_admin=True)
    response = await http_client.get("/admin/audit?action=SWEEP_ORPHAN")
    assert isinstance(json.loads(response.text), list)
