"""Integration tests for the past-runs dashboard routes (Increment 19a).

Drives the FastAPI app via ``httpx.AsyncClient`` over the ASGI
transport against a real in-memory SQLite. Seeds runs directly via
the repository and asserts:

* L3-DASH-022/023: query-param defaults, range constraints, 422 for
  out-of-range values.
* L3-DASH-024: ordering (created_at DESC, run_id DESC tiebreaker),
  pagination slicing.
* L3-DASH-025/026: run-detail UUID validator + response shape.
* Auth gate: unauthenticated requests return 401.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import httpx
import pytest
from httpx import ASGITransport

from message_service.application.ports.clock import Clock
from message_service.application.use_cases.get_run_detail import GetRunDetailUseCase
from message_service.application.use_cases.list_past_runs import ListPastRunsUseCase
from message_service.application.use_cases.login import LoginUseCase
from message_service.application.use_cases.logout import LogoutUseCase
from message_service.application.use_cases.subscribe import SubscribeUseCase
from message_service.application.use_cases.unsubscribe import UnsubscribeUseCase
from message_service.config.schema import Argon2Config, AuthConfig, DashboardConfig
from message_service.domain.aggregates.declared_stage import DeclaredStage
from message_service.domain.aggregates.password import Password
from message_service.domain.aggregates.run import AttachmentMode, Run
from message_service.domain.aggregates.stage import Stage
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.aggregates.user import User
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.run_states import RunState
from message_service.domain.state_machines.stage_states import StageState
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
from message_service.infrastructure.tags.vocabulary_loader import InMemoryTagVocabulary
from message_service.interfaces.rest.app import CSRF_COOKIE_NAME, create_app

_T0 = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)


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
    """Smallest object that ``create_app`` + the runs router access."""

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
def vocabulary() -> InMemoryTagVocabulary:
    return InMemoryTagVocabulary(frozenset({"production"}))


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


async def _seed_user(
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    *,
    email: str = "alice@example.com",
) -> User:
    pw_hash = hasher.hash(Password("hunter2"))
    async with uow_factory() as uow:
        saved = await uow.user_repo.save(
            User(
                email=email,
                display_name="alice",
                password_hash=pw_hash,
                created_at=_T0,
                disabled=False,
                is_admin=False,
            ),
        )
        await uow.commit()
    assert saved.user_id is not None
    return saved


def _make_run(
    *,
    run_id: str,
    state: RunState = RunState.SENT,
    created_at: datetime = _T0,
    declared_stages: tuple[DeclaredStage, ...] | None = None,
) -> Run:
    if declared_stages is None:
        declared_stages = (
            DeclaredStage(
                stage_id=StageId("extract"),
                stage_order=0,
                report_template_ref=TemplateRef(name="r", version="1.0"),
            ),
            DeclaredStage(
                stage_id=StageId("transform"),
                stage_order=1,
                report_template_ref=TemplateRef(name="r", version="1.0"),
            ),
        )
    return Run(
        run_id=RunId(run_id),
        pipeline_type="etl-nightly",
        tags=frozenset({"production"}),
        declared_stages=declared_stages,
        state=state,
        attachment_mode=AttachmentMode.SINGLE_AGGREGATED,
        aggregation_template_ref=TemplateRef(name="agg", version="1.0"),
        subscription_predicate_tags=frozenset({"production"}),
        created_at=created_at,
        updated_at=created_at,
    )


def _make_stage(*, run_id: str, stage_id: str, state: StageState = StageState.PENDING) -> Stage:
    return Stage(
        run_id=RunId(run_id),
        stage_id=StageId(stage_id),
        state=state,
        report_template_ref=TemplateRef(name="r", version="1.0"),
        submitted_at=_T0 if state is not StageState.PENDING else None,
    )


async def _seed_runs(
    uow_factory: SqliteUnitOfWorkFactory,
    runs: list[Run],
    stages: list[Stage] | None = None,
) -> None:
    async with uow_factory() as uow:
        for r in runs:
            await uow.run_repo.save(r)
        for s in stages or []:
            await uow.stage_repo.save(s)
        await uow.commit()


@pytest.fixture
def service_like(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
    hasher: Argon2PasswordHasher,
    vocabulary: InMemoryTagVocabulary,
) -> _ServiceLike:
    return _ServiceLike(
        config=_ConfigStub(),
        clock=clock,
        uow_factory=uow_factory,
        login=LoginUseCase(uow_factory=uow_factory, clock=clock, password_hasher=hasher),
        logout=LogoutUseCase(uow_factory=uow_factory, clock=clock),
        subscribe=SubscribeUseCase(
            uow_factory=uow_factory,
            clock=clock,
            tag_vocabulary=vocabulary,
            registered_pipelines=frozenset({"etl-nightly"}),
        ),
        unsubscribe=UnsubscribeUseCase(uow_factory=uow_factory, clock=clock),
        list_past_runs=ListPastRunsUseCase(uow_factory=uow_factory),
        get_run_detail=GetRunDetailUseCase(uow_factory=uow_factory),
    )


@pytest.fixture
async def http_client(service_like: _ServiceLike) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(service_like)  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _login(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> str:
    """Seed a user, log in, return the CSRF cookie value."""
    await _seed_user(uow_factory, hasher)
    response = await http_client.post(
        "/login", json={"email": "alice@example.com", "password": "hunter2"}
    )
    assert response.status_code == 200
    return response.cookies[CSRF_COOKIE_NAME]


# -----------------------------------------------------------------------------
# Auth gate
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_runs_requires_session(http_client: httpx.AsyncClient) -> None:
    """L1-AUTH-002: unauthenticated GET /runs SHALL return 401."""
    response = await http_client.get("/runs")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_run_detail_requires_session(http_client: httpx.AsyncClient) -> None:
    response = await http_client.get("/runs/00000000-0000-4000-8000-000000000001")
    assert response.status_code == 401


# -----------------------------------------------------------------------------
# GET /runs -- pagination + ordering + state filter
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-023")
@pytest.mark.requirement("L3-DASH-024")
async def test_get_runs_default_returns_terminal_runs_most_recent_first(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-023: default state filter is TERMINAL_STATES.
    L3-DASH-024: results SHALL be ordered created_at DESC, run_id DESC.
    """
    await _login(http_client, uow_factory, hasher)
    runs = [
        _make_run(
            run_id=f"00000000-0000-4000-8000-{i:012d}",
            state=RunState.SENT,
            created_at=_T0 + timedelta(minutes=i),
        )
        for i in range(3)
    ]
    # An in-flight run that the default filter SHALL exclude.
    runs.append(
        _make_run(
            run_id="00000000-0000-4000-8000-aaaaaaaaaaaa",
            state=RunState.AGGREGATING,
            created_at=_T0 + timedelta(minutes=10),
        )
    )
    await _seed_runs(uow_factory, runs)

    response = await http_client.get("/runs")
    assert response.status_code == 200
    body = response.json()
    # Three terminal runs, latest first.
    assert [r["run_id"] for r in body] == [
        runs[2].run_id,
        runs[1].run_id,
        runs[0].run_id,
    ]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-023")
async def test_get_runs_explicit_states_overrides_default(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """When ``states`` is supplied, the route SHALL use it verbatim."""
    await _login(http_client, uow_factory, hasher)
    initiated = _make_run(
        run_id="00000000-0000-4000-8000-000000000001",
        state=RunState.INITIATED,
    )
    sent = _make_run(run_id="00000000-0000-4000-8000-000000000002", state=RunState.SENT)
    await _seed_runs(uow_factory, [initiated, sent])

    response = await http_client.get("/runs?states=INITIATED")
    assert response.status_code == 200
    body = response.json()
    assert [r["run_id"] for r in body] == [initiated.run_id]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-023")
async def test_get_runs_pagination_slices(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """``limit`` + ``offset`` SHALL slice the result window."""
    await _login(http_client, uow_factory, hasher)
    runs = [
        _make_run(
            run_id=f"00000000-0000-4000-8000-{i:012d}",
            state=RunState.SENT,
            created_at=_T0 + timedelta(minutes=i),
        )
        for i in range(5)
    ]
    await _seed_runs(uow_factory, runs)

    page1 = await http_client.get("/runs?limit=2&offset=0")
    page2 = await http_client.get("/runs?limit=2&offset=2")
    assert page1.status_code == 200
    assert page2.status_code == 200
    assert len(page1.json()) == 2
    assert len(page2.json()) == 2
    seen = {r["run_id"] for r in page1.json()} | {r["run_id"] for r in page2.json()}
    assert len(seen) == 4


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-022")
@pytest.mark.requirement("L3-DASH-023")
async def test_get_runs_rejects_out_of_range_limit(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-023: limit > 200 SHALL return 422."""
    await _login(http_client, uow_factory, hasher)
    response = await http_client.get("/runs?limit=999")
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-022")
async def test_get_runs_rejects_unknown_state_value(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-022: unknown enum value in ``states`` SHALL return 422."""
    await _login(http_client, uow_factory, hasher)
    response = await http_client.get("/runs?states=NOT_A_REAL_STATE")
    assert response.status_code == 422


# -----------------------------------------------------------------------------
# GET /runs/{run_id}
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-025")
@pytest.mark.requirement("L3-DASH-026")
async def test_get_run_detail_returns_run_and_ordered_stages(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-025/026: detail returns run + stages in declared order."""
    await _login(http_client, uow_factory, hasher)
    run = _make_run(run_id="00000000-0000-4000-8000-000000000aa1", state=RunState.SENT)
    stages = [
        _make_stage(run_id=run.run_id, stage_id="extract", state=StageState.SUBMITTED),
        _make_stage(run_id=run.run_id, stage_id="transform", state=StageState.PENDING),
    ]
    await _seed_runs(uow_factory, [run], stages=stages)

    response = await http_client.get(f"/runs/{run.run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["run"]["run_id"] == run.run_id
    assert body["run"]["pipeline_type"] == "etl-nightly"
    assert [s["stage_id"] for s in body["stages"]] == ["extract", "transform"]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-025")
async def test_get_run_detail_returns_404_for_unknown_run(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    await _login(http_client, uow_factory, hasher)
    response = await http_client.get("/runs/00000000-0000-4000-8000-deadbeef0000")
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-025")
async def test_get_run_detail_rejects_non_uuid_path(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-025: non-UUID4 path values SHALL return 422."""
    await _login(http_client, uow_factory, hasher)
    response = await http_client.get("/runs/not-a-uuid")
    assert response.status_code == 422
