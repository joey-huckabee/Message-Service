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
from message_service.infrastructure.persistence.filesystem.report_store import (
    FilesystemReportStore,
)
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
def report_store(tmp_path: Path) -> FilesystemReportStore:
    root = tmp_path / "reports"
    root.mkdir()
    return FilesystemReportStore(root=root)


@pytest.fixture
def service_like(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
    hasher: Argon2PasswordHasher,
    vocabulary: InMemoryTagVocabulary,
    report_store: FilesystemReportStore,
) -> _ServiceLike:
    return _ServiceLike(
        config=_ConfigStub(),
        clock=clock,
        uow_factory=uow_factory,
        report_store=report_store,
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


# -----------------------------------------------------------------------------
# POST /runs/{run_id}/resend  (Increment 19b)
# -----------------------------------------------------------------------------


class _ResendStub:
    """Stub resend use-case: records calls; configurable to raise."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.raise_run_not_found: bool = False
        self.raise_invalid_state: str | None = None

    async def execute(self, *, run_id: str, admin_user_id: int) -> None:
        self.calls.append((run_id, admin_user_id))
        if self.raise_run_not_found:
            from message_service.domain.errors import RunNotFoundError

            raise RunNotFoundError(
                f"run {run_id} does not exist",
                details={"run_id": run_id},
            )
        if self.raise_invalid_state is not None:
            from message_service.domain.errors import InvalidRunStateError

            raise InvalidRunStateError(
                f"run {run_id} state precondition failed",
                details={
                    "run_id": run_id,
                    "current_state": self.raise_invalid_state,
                    "permitted_states": ["FAILED", "SENT"],
                },
            )


@pytest.fixture
def resend_stub(service_like: _ServiceLike) -> _ResendStub:
    """Replace the real ResendRunUseCase with a recording stub for route tests."""
    stub = _ResendStub()
    service_like.resend_run = stub  # type: ignore[attr-defined]
    return stub


@pytest.mark.asyncio
async def test_resend_requires_session(http_client: httpx.AsyncClient) -> None:
    """Unauthenticated POST /runs/{id}/resend SHALL be blocked.

    The CSRF middleware runs outermost and rejects any POST without
    the matching cookie+header pair (which only exist after login),
    so an unauthenticated POST surfaces as 403 (CSRF) rather than
    401 (auth). Both prevent the action; the 403-from-CSRF is the
    consistent observable behavior on this code path.
    """
    response = await http_client.post("/runs/00000000-0000-4000-8000-000000000001/resend")
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-018")
async def test_resend_blocked_without_csrf_header(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-018: POST without X-CSRF-Token SHALL return 403."""
    await _login(http_client, uow_factory, hasher)
    response = await http_client.post("/runs/00000000-0000-4000-8000-000000000001/resend")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_resend_returns_202_on_success(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    resend_stub: _ResendStub,
) -> None:
    """A successful resend call SHALL return 202 Accepted."""
    csrf = await _login(http_client, uow_factory, hasher)
    response = await http_client.post(
        "/runs/00000000-0000-4000-8000-000000000aa1/resend",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 202
    assert resend_stub.calls == [("00000000-0000-4000-8000-000000000aa1", 1)]


@pytest.mark.asyncio
async def test_resend_returns_404_for_unknown_run(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    resend_stub: _ResendStub,
) -> None:
    """A RunNotFoundError SHALL surface as 404."""
    csrf = await _login(http_client, uow_factory, hasher)
    resend_stub.raise_run_not_found = True
    response = await http_client.post(
        "/runs/00000000-0000-4000-8000-deadbeef0001/resend",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-028")
async def test_resend_returns_409_for_non_resendable_state(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    resend_stub: _ResendStub,
) -> None:
    """L3-DASH-028: non-SENT/FAILED state SHALL return 409."""
    csrf = await _login(http_client, uow_factory, hasher)
    resend_stub.raise_invalid_state = "ORPHANED"
    response = await http_client.post(
        "/runs/00000000-0000-4000-8000-000000000bb1/resend",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert "ORPHANED" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-025")
async def test_resend_rejects_non_uuid_path(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-025: non-UUID4 path values SHALL return 422 even on POST."""
    csrf = await _login(http_client, uow_factory, hasher)
    response = await http_client.post(
        "/runs/not-a-uuid/resend",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422


# -----------------------------------------------------------------------------
# GET /runs/{run_id}/report  (Increment 19c)
# -----------------------------------------------------------------------------


_VIEWER_RUN = "00000000-0000-4000-8000-0000000000c1"


@pytest.mark.asyncio
async def test_get_report_requires_session(http_client: httpx.AsyncClient) -> None:
    """L1-AUTH-002: unauthenticated GET /runs/{id}/report SHALL return 401."""
    response = await http_client.get(f"/runs/{_VIEWER_RUN}/report")
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-029")
async def test_get_report_returns_saved_email_body(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    report_store: FilesystemReportStore,
) -> None:
    """L3-DASH-029: route SHALL return the saved body with text/html;charset=utf-8."""
    await _login(http_client, uow_factory, hasher)
    report_store.save_email_body(RunId(_VIEWER_RUN), "<html><body>π</body></html>")

    response = await http_client.get(f"/runs/{_VIEWER_RUN}/report")
    assert response.status_code == 200
    assert response.text == "<html><body>π</body></html>"
    assert response.headers["content-type"] == "text/html; charset=utf-8"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-029")
async def test_get_report_returns_404_when_no_saved_body(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-029: missing saved body SHALL surface as 404 (no info disclosure)."""
    await _login(http_client, uow_factory, hasher)
    response = await http_client.get(f"/runs/{_VIEWER_RUN}/report")
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-029")
async def test_get_report_rejects_non_uuid_path(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-025-style path validator SHALL apply to the report route."""
    await _login(http_client, uow_factory, hasher)
    response = await http_client.get("/runs/not-a-uuid/report")
    assert response.status_code == 422


# -----------------------------------------------------------------------------
# GET /runs/{run_id}/stages/{stage_id}/fragment  (Increment 19c)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_fragment_requires_session(http_client: httpx.AsyncClient) -> None:
    response = await http_client.get(f"/runs/{_VIEWER_RUN}/stages/extract/fragment")
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-030")
async def test_get_fragment_returns_saved_fragment(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    report_store: FilesystemReportStore,
) -> None:
    """L3-DASH-030: route SHALL return saved fragment HTML with text/html;charset=utf-8."""
    await _login(http_client, uow_factory, hasher)
    report_store.save_fragment(RunId(_VIEWER_RUN), StageId("extract"), "<p>frag</p>")

    response = await http_client.get(f"/runs/{_VIEWER_RUN}/stages/extract/fragment")
    assert response.status_code == 200
    assert response.text == "<p>frag</p>"
    assert response.headers["content-type"] == "text/html; charset=utf-8"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-030")
async def test_get_fragment_returns_404_when_stage_missing(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    report_store: FilesystemReportStore,
) -> None:
    """L3-DASH-030: absent stage fragment SHALL return 404 (uniform privacy)."""
    await _login(http_client, uow_factory, hasher)
    # Save one stage; ask for another. Same uniform-404 SHALL apply.
    report_store.save_fragment(RunId(_VIEWER_RUN), StageId("extract"), "<p>frag</p>")
    response = await http_client.get(f"/runs/{_VIEWER_RUN}/stages/transform/fragment")
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-030")
async def test_get_fragment_returns_404_when_run_missing(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-030: absent run SHALL also surface as the same 404."""
    await _login(http_client, uow_factory, hasher)
    response = await http_client.get(f"/runs/{_VIEWER_RUN}/stages/extract/fragment")
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-030")
async def test_get_fragment_rejects_non_uuid_run_id(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    await _login(http_client, uow_factory, hasher)
    response = await http_client.get("/runs/not-a-uuid/stages/extract/fragment")
    assert response.status_code == 422


# -----------------------------------------------------------------------------
# GET /runs/board -- embedded run-status board (L3-DASH-037)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-037")
async def test_runs_board_requires_session(http_client: httpx.AsyncClient) -> None:
    """L1-AUTH-002: unauthenticated GET /runs/board SHALL return 401."""
    response = await http_client.get("/runs/board")
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-037")
async def test_runs_board_renders_html_including_in_flight_runs(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """The board returns an HTML page embedding runs across all states.

    Unlike ``GET /runs`` (which defaults to terminal states), the board SHALL
    surface in-flight runs too, so an ``AGGREGATING`` run appears in the payload.
    """
    await _login(http_client, uow_factory, hasher)
    inflight = _make_run(
        run_id="00000000-0000-4000-8000-00000000a11e",
        state=RunState.AGGREGATING,
        created_at=_T0 + timedelta(minutes=5),
    )
    terminal = _make_run(
        run_id="00000000-0000-4000-8000-000000000002",
        state=RunState.SENT,
        created_at=_T0,
    )
    await _seed_runs(uow_factory, [inflight, terminal])

    response = await http_client.get("/runs/board")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert body.startswith("<!doctype html>")

    # Extract the embedded projection and assert it carries both runs with the
    # JSON-summary field set, including the in-flight one.
    import json as _json
    import re as _re

    match = _re.search(
        r'<script type="application/json" id="runs-data">(.*?)</script>', body, _re.DOTALL
    )
    assert match is not None
    embedded = _json.loads(match.group(1))
    by_id = {r["run_id"]: r for r in embedded}
    assert inflight.run_id in by_id
    assert terminal.run_id in by_id
    assert by_id[inflight.run_id]["state"] == "AGGREGATING"
    assert set(by_id[terminal.run_id]) == {
        "run_id",
        "pipeline_type",
        "state",
        "attachment_mode",
        "tags",
        "created_at",
        "updated_at",
    }


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-037")
async def test_runs_board_path_resolves_to_board_not_detail(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """``/runs/board`` SHALL hit the board route, not the UUID detail route.

    If ``/board`` were declared after ``/{run_id}`` it would be routed to the
    detail handler and rejected as a non-UUID (422). A 200 HTML response proves
    the ordering is correct.
    """
    await _login(http_client, uow_factory, hasher)
    response = await http_client.get("/runs/board")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-037")
async def test_runs_board_renders_with_no_runs(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """With no runs seeded, the board still returns a valid empty page."""
    await _login(http_client, uow_factory, hasher)
    response = await http_client.get("/runs/board")
    assert response.status_code == 200
    assert 'id="runs-data">[]</script>' in response.text
