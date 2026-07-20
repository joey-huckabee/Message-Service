"""Integration tests for the admin-on-behalf subscription use cases (L3-DASH-044).

Drives ``AdminSubscribeUseCase`` / ``AdminUnsubscribeUseCase`` against a real
in-memory SQLite UoW, asserting: target validation reuse, target-user existence,
delete scoping to the target, and — crucially — that the audit actor is the
acting admin (not the target recipient).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
from tests.fixtures.clocks import FakeClock

from message_service.application.use_cases.admin_subscriptions import (
    AdminSubscribeUseCase,
    AdminUnsubscribeUseCase,
)
from message_service.domain.aggregates.audit_event import AuditAction
from message_service.domain.aggregates.subscription import SubscriptionGranularity
from message_service.domain.aggregates.user import User
from message_service.domain.errors import (
    PersistenceError,
    SubscriptionNotFoundError,
    UnknownPipelineTypeError,
    UnknownTagError,
    UserNotFoundError,
)
from message_service.domain.ids import SubscriptionId, UserId
from message_service.infrastructure.persistence.audit_log import SqliteAuditLog
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations
from message_service.infrastructure.persistence.run_repository import SqliteRunRepository
from message_service.infrastructure.persistence.session_repository import SqliteSessionRepository
from message_service.infrastructure.persistence.stage_repository import SqliteStageRepository
from message_service.infrastructure.persistence.subscription_repository import (
    SqliteSubscriptionRepository,
)
from message_service.infrastructure.persistence.sweeper_action_repository import (
    SqliteSweeperActionRepository,
)
from message_service.infrastructure.persistence.unit_of_work import SqliteUnitOfWorkFactory
from message_service.infrastructure.persistence.user_repository import SqliteUserRepository
from message_service.infrastructure.tags.vocabulary_loader import InMemoryTagVocabulary

pytestmark = pytest.mark.allow_io

_T0 = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_PIPELINES = frozenset({"etl-nightly", "sales-rollup"})


@pytest.fixture
async def sqlite_conn() -> AsyncIterator[aiosqlite.Connection]:
    c = await open_connection(Path(":memory:"))
    try:
        await apply_migrations(c)
        yield c
    finally:
        await c.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(_T0)


@pytest.fixture
def uow_factory(sqlite_conn: aiosqlite.Connection, clock: FakeClock) -> SqliteUnitOfWorkFactory:
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
def vocabulary() -> InMemoryTagVocabulary:
    return InMemoryTagVocabulary(frozenset({"production", "finance"}))


@pytest.fixture
def admin_subscribe(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: FakeClock,
    vocabulary: InMemoryTagVocabulary,
) -> AdminSubscribeUseCase:
    return AdminSubscribeUseCase(
        uow_factory=uow_factory,
        clock=clock,
        tag_vocabulary=vocabulary,
        registered_pipelines=_PIPELINES,
    )


@pytest.fixture
def admin_unsubscribe(
    uow_factory: SqliteUnitOfWorkFactory, clock: FakeClock
) -> AdminUnsubscribeUseCase:
    return AdminUnsubscribeUseCase(uow_factory=uow_factory, clock=clock)


async def _seed_user(uow_factory: SqliteUnitOfWorkFactory, email: str) -> UserId:
    async with uow_factory() as uow:
        saved = await uow.user_repo.save(
            User(
                email=email,
                display_name=email.split("@")[0],
                password_hash="$argon2id$x",
                created_at=_T0,
                is_admin=False,
                disabled=False,
            )
        )
        await uow.commit()
    assert saved.user_id is not None
    return UserId(saved.user_id)


# -----------------------------------------------------------------------------
# AdminSubscribeUseCase
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-044")
async def test_admin_subscribe_creates_for_target_and_audits_to_admin(
    uow_factory: SqliteUnitOfWorkFactory, admin_subscribe: AdminSubscribeUseCase
) -> None:
    """Creates the subscription for the target; audit actor is the admin."""
    target = await _seed_user(uow_factory, "jane@example.com")
    admin_id = 99

    saved = await admin_subscribe.execute(
        admin_id=admin_id,
        target_user_id=target,
        granularity=SubscriptionGranularity.TAG,
        target_value="finance",
    )
    assert saved.user_id == target
    assert saved.target_value == "finance"

    async with uow_factory() as uow:
        subs = await uow.subscription_repo.list_for_user(target)
        events = list(await uow.audit_log.query(action=AuditAction.SUBSCRIBE))
    assert len(subs) == 1
    assert len(events) == 1
    assert events[0].actor == f"user:{admin_id}"  # admin, NOT the target
    assert events[0].resource == f"user:{target}"
    assert events[0].details["target_user_id"] == int(target)


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-044")
async def test_admin_subscribe_unknown_user_raises(
    admin_subscribe: AdminSubscribeUseCase,
) -> None:
    with pytest.raises(UserNotFoundError):
        await admin_subscribe.execute(
            admin_id=1,
            target_user_id=UserId(4242),
            granularity=SubscriptionGranularity.GLOBAL,
            target_value=None,
        )


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-044")
async def test_admin_subscribe_invalid_pipeline_raises(
    uow_factory: SqliteUnitOfWorkFactory, admin_subscribe: AdminSubscribeUseCase
) -> None:
    target = await _seed_user(uow_factory, "jane@example.com")
    with pytest.raises(UnknownPipelineTypeError):
        await admin_subscribe.execute(
            admin_id=1,
            target_user_id=target,
            granularity=SubscriptionGranularity.PIPELINE,
            target_value="no-such-pipeline",
        )


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-044")
async def test_admin_subscribe_invalid_tag_raises(
    uow_factory: SqliteUnitOfWorkFactory, admin_subscribe: AdminSubscribeUseCase
) -> None:
    target = await _seed_user(uow_factory, "jane@example.com")
    with pytest.raises(UnknownTagError):
        await admin_subscribe.execute(
            admin_id=1,
            target_user_id=target,
            granularity=SubscriptionGranularity.TAG,
            target_value="no-such-tag",
        )


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-044")
async def test_admin_subscribe_duplicate_raises(
    uow_factory: SqliteUnitOfWorkFactory, admin_subscribe: AdminSubscribeUseCase
) -> None:
    target = await _seed_user(uow_factory, "jane@example.com")
    await admin_subscribe.execute(
        admin_id=1,
        target_user_id=target,
        granularity=SubscriptionGranularity.GLOBAL,
        target_value=None,
    )
    with pytest.raises(PersistenceError):
        await admin_subscribe.execute(
            admin_id=1,
            target_user_id=target,
            granularity=SubscriptionGranularity.GLOBAL,
            target_value=None,
        )


# -----------------------------------------------------------------------------
# AdminUnsubscribeUseCase
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-044")
async def test_admin_unsubscribe_deletes_and_audits_to_admin(
    uow_factory: SqliteUnitOfWorkFactory,
    admin_subscribe: AdminSubscribeUseCase,
    admin_unsubscribe: AdminUnsubscribeUseCase,
) -> None:
    target = await _seed_user(uow_factory, "jane@example.com")
    saved = await admin_subscribe.execute(
        admin_id=1,
        target_user_id=target,
        granularity=SubscriptionGranularity.GLOBAL,
        target_value=None,
    )
    assert saved.subscription_id is not None

    await admin_unsubscribe.execute(
        admin_id=7, target_user_id=target, subscription_id=saved.subscription_id
    )

    async with uow_factory() as uow:
        subs = await uow.subscription_repo.list_for_user(target)
        events = list(await uow.audit_log.query(action=AuditAction.UNSUBSCRIBE))
    assert subs == []
    assert len(events) == 1
    assert events[0].actor == "user:7"
    assert events[0].resource == f"user:{target}"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-044")
async def test_admin_unsubscribe_unknown_id_raises(
    uow_factory: SqliteUnitOfWorkFactory, admin_unsubscribe: AdminUnsubscribeUseCase
) -> None:
    target = await _seed_user(uow_factory, "jane@example.com")
    with pytest.raises(SubscriptionNotFoundError):
        await admin_unsubscribe.execute(
            admin_id=1, target_user_id=target, subscription_id=SubscriptionId(999)
        )


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-044")
async def test_admin_unsubscribe_id_owned_by_other_user_raises(
    uow_factory: SqliteUnitOfWorkFactory,
    admin_subscribe: AdminSubscribeUseCase,
    admin_unsubscribe: AdminUnsubscribeUseCase,
) -> None:
    """A subscription owned by another user is 404 through the target's path."""
    owner = await _seed_user(uow_factory, "owner@example.com")
    other = await _seed_user(uow_factory, "other@example.com")
    saved = await admin_subscribe.execute(
        admin_id=1,
        target_user_id=owner,
        granularity=SubscriptionGranularity.GLOBAL,
        target_value=None,
    )
    assert saved.subscription_id is not None

    # Attempt to delete owner's subscription via `other`'s path → not found.
    with pytest.raises(SubscriptionNotFoundError):
        await admin_unsubscribe.execute(
            admin_id=1, target_user_id=other, subscription_id=saved.subscription_id
        )
    # And the subscription is still there.
    async with uow_factory() as uow:
        assert len(await uow.subscription_repo.list_for_user(owner)) == 1
