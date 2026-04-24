"""Unit tests for :class:`SqliteSubscriptionRepository`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from message_service.application.ports.clock import Clock
from message_service.domain.aggregates.subscription import SubscriptionGranularity
from message_service.domain.errors import PersistenceError
from message_service.domain.ids import SubscriptionId, UserId
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations
from message_service.infrastructure.persistence.subscription_repository import (
    SqliteSubscriptionRepository,
)

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

_T0 = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


class _FixedClock(Clock):
    def __init__(self, value: datetime) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value


async def _seed_users(
    conn: aiosqlite.Connection,
    users: list[tuple[str, str, int]],
) -> None:
    """Seed users with (email, display_name, disabled) tuples."""
    for email, name, disabled in users:
        await conn.execute(
            "INSERT INTO users (email, display_name, disabled, created_at) VALUES (?, ?, ?, ?)",
            (email, name, disabled, "2026-04-21T00:00:00Z"),
        )


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    c = await open_connection(Path(":memory:"))
    try:
        await apply_migrations(c)
        yield c
    finally:
        await c.close()


@pytest.fixture
async def repo(conn: aiosqlite.Connection) -> SqliteSubscriptionRepository:
    return SqliteSubscriptionRepository(conn, clock=_FixedClock(_T0))


# -----------------------------------------------------------------------------
# add
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SUB-003")
async def test_add_global_subscription_persists(
    repo: SqliteSubscriptionRepository, conn: aiosqlite.Connection
) -> None:
    await _seed_users(conn, [("alice@x", "Alice", 0)])
    sub = await repo.add(UserId(1), SubscriptionGranularity.GLOBAL, None)
    await conn.commit()

    assert sub.granularity is SubscriptionGranularity.GLOBAL
    assert sub.target_value is None
    assert sub.user_id == 1
    assert sub.created_at == _T0


@pytest.mark.asyncio
async def test_add_pipeline_subscription_persists(
    repo: SqliteSubscriptionRepository, conn: aiosqlite.Connection
) -> None:
    await _seed_users(conn, [("alice@x", "Alice", 0)])
    sub = await repo.add(UserId(1), SubscriptionGranularity.PIPELINE, "etl-nightly")
    assert sub.target_value == "etl-nightly"


@pytest.mark.asyncio
async def test_add_tag_subscription_persists(
    repo: SqliteSubscriptionRepository, conn: aiosqlite.Connection
) -> None:
    await _seed_users(conn, [("alice@x", "Alice", 0)])
    sub = await repo.add(UserId(1), SubscriptionGranularity.TAG, "critical")
    assert sub.target_value == "critical"


@pytest.mark.asyncio
async def test_add_global_with_target_value_rejected(
    repo: SqliteSubscriptionRepository, conn: aiosqlite.Connection
) -> None:
    await _seed_users(conn, [("alice@x", "Alice", 0)])
    with pytest.raises(PersistenceError, match="GLOBAL"):
        await repo.add(UserId(1), SubscriptionGranularity.GLOBAL, "x")


@pytest.mark.asyncio
async def test_add_pipeline_without_target_rejected(
    repo: SqliteSubscriptionRepository, conn: aiosqlite.Connection
) -> None:
    await _seed_users(conn, [("alice@x", "Alice", 0)])
    with pytest.raises(PersistenceError, match="PIPELINE"):
        await repo.add(UserId(1), SubscriptionGranularity.PIPELINE, None)


# -----------------------------------------------------------------------------
# L3-SUB-001: uniqueness
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SUB-001")
async def test_duplicate_global_subscription_rejected(
    repo: SqliteSubscriptionRepository, conn: aiosqlite.Connection
) -> None:
    await _seed_users(conn, [("alice@x", "Alice", 0)])
    await repo.add(UserId(1), SubscriptionGranularity.GLOBAL, None)
    await conn.commit()

    with pytest.raises(PersistenceError, match="duplicate"):
        await repo.add(UserId(1), SubscriptionGranularity.GLOBAL, None)


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SUB-001")
async def test_duplicate_pipeline_subscription_rejected(
    repo: SqliteSubscriptionRepository, conn: aiosqlite.Connection
) -> None:
    await _seed_users(conn, [("alice@x", "Alice", 0)])
    await repo.add(UserId(1), SubscriptionGranularity.PIPELINE, "etl-nightly")
    await conn.commit()

    with pytest.raises(PersistenceError, match="duplicate"):
        await repo.add(UserId(1), SubscriptionGranularity.PIPELINE, "etl-nightly")


@pytest.mark.asyncio
async def test_distinct_pipeline_subscriptions_allowed(
    repo: SqliteSubscriptionRepository, conn: aiosqlite.Connection
) -> None:
    """Same user + same granularity + different target_value SHALL be OK."""
    await _seed_users(conn, [("alice@x", "Alice", 0)])
    await repo.add(UserId(1), SubscriptionGranularity.PIPELINE, "etl-nightly")
    await repo.add(UserId(1), SubscriptionGranularity.PIPELINE, "backup-daily")
    # No raise.


# -----------------------------------------------------------------------------
# remove (idempotent)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_deletes_subscription(
    repo: SqliteSubscriptionRepository, conn: aiosqlite.Connection
) -> None:
    await _seed_users(conn, [("alice@x", "Alice", 0)])
    sub = await repo.add(UserId(1), SubscriptionGranularity.GLOBAL, None)
    await conn.commit()

    await repo.remove(sub.subscription_id)
    await conn.commit()

    subs = await repo.list_for_user(UserId(1))
    assert list(subs) == []


@pytest.mark.asyncio
async def test_remove_missing_subscription_is_noop(
    repo: SqliteSubscriptionRepository,
) -> None:
    await repo.remove(SubscriptionId(9999))  # must not raise


# -----------------------------------------------------------------------------
# list_for_user
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_for_user_returns_all_subscriptions(
    repo: SqliteSubscriptionRepository, conn: aiosqlite.Connection
) -> None:
    await _seed_users(conn, [("alice@x", "Alice", 0), ("bob@x", "Bob", 0)])
    await repo.add(UserId(1), SubscriptionGranularity.GLOBAL, None)
    await repo.add(UserId(1), SubscriptionGranularity.TAG, "critical")
    await repo.add(UserId(2), SubscriptionGranularity.GLOBAL, None)
    await conn.commit()

    alice_subs = await repo.list_for_user(UserId(1))
    assert len(alice_subs) == 2
    granularities = {s.granularity for s in alice_subs}
    assert granularities == {
        SubscriptionGranularity.GLOBAL,
        SubscriptionGranularity.TAG,
    }


@pytest.mark.asyncio
async def test_list_for_user_empty_for_unknown_user(
    repo: SqliteSubscriptionRepository,
) -> None:
    subs = await repo.list_for_user(UserId(9999))
    assert list(subs) == []


# -----------------------------------------------------------------------------
# list_recipients_for_run — the interesting one
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-SUB-004")
async def test_global_subscription_matches_every_run(
    repo: SqliteSubscriptionRepository, conn: aiosqlite.Connection
) -> None:
    await _seed_users(conn, [("alice@x", "Alice", 0)])
    await repo.add(UserId(1), SubscriptionGranularity.GLOBAL, None)
    await conn.commit()

    r1 = await repo.list_recipients_for_run("etl-nightly", frozenset())
    r2 = await repo.list_recipients_for_run("backup", frozenset({"prod"}))
    assert r1 == frozenset({"alice@x"})
    assert r2 == frozenset({"alice@x"})


@pytest.mark.asyncio
@pytest.mark.requirement("L1-SUB-004")
async def test_pipeline_subscription_matches_matching_pipeline_only(
    repo: SqliteSubscriptionRepository, conn: aiosqlite.Connection
) -> None:
    await _seed_users(conn, [("bob@x", "Bob", 0)])
    await repo.add(UserId(1), SubscriptionGranularity.PIPELINE, "etl-nightly")
    await conn.commit()

    matched = await repo.list_recipients_for_run("etl-nightly", frozenset())
    missed = await repo.list_recipients_for_run("backup", frozenset())
    assert matched == frozenset({"bob@x"})
    assert missed == frozenset()


@pytest.mark.asyncio
@pytest.mark.requirement("L1-SUB-004")
async def test_tag_subscription_matches_matching_tag(
    repo: SqliteSubscriptionRepository, conn: aiosqlite.Connection
) -> None:
    await _seed_users(conn, [("carol@x", "Carol", 0)])
    await repo.add(UserId(1), SubscriptionGranularity.TAG, "critical")
    await conn.commit()

    matched = await repo.list_recipients_for_run("etl-nightly", frozenset({"critical", "prod"}))
    missed = await repo.list_recipients_for_run("etl-nightly", frozenset({"prod"}))
    empty_tags = await repo.list_recipients_for_run("etl-nightly", frozenset())
    assert matched == frozenset({"carol@x"})
    assert missed == frozenset()
    assert empty_tags == frozenset()


@pytest.mark.asyncio
async def test_empty_tags_does_not_break_query(
    repo: SqliteSubscriptionRepository, conn: aiosqlite.Connection
) -> None:
    """Empty tags SHALL NOT produce a syntactically broken ``IN ()``."""
    await _seed_users(conn, [("alice@x", "Alice", 0)])
    await repo.add(UserId(1), SubscriptionGranularity.GLOBAL, None)
    await conn.commit()

    # Would fail with sqlite3.OperationalError if the TAG branch
    # leaked into the WHERE.
    r = await repo.list_recipients_for_run("etl-nightly", frozenset())
    assert r == frozenset({"alice@x"})


@pytest.mark.asyncio
async def test_user_with_multiple_matching_subs_dedups(
    repo: SqliteSubscriptionRepository, conn: aiosqlite.Connection
) -> None:
    """One user, two matching rules → one email in the result."""
    await _seed_users(conn, [("bob@x", "Bob", 0)])
    await repo.add(UserId(1), SubscriptionGranularity.PIPELINE, "etl-nightly")
    await repo.add(UserId(1), SubscriptionGranularity.TAG, "critical")
    await conn.commit()

    r = await repo.list_recipients_for_run("etl-nightly", frozenset({"critical"}))
    assert r == frozenset({"bob@x"})


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SUB-017")
async def test_disabled_user_excluded_from_recipients(
    repo: SqliteSubscriptionRepository, conn: aiosqlite.Connection
) -> None:
    await _seed_users(
        conn,
        [
            ("active@x", "Active", 0),
            ("disabled@x", "Disabled", 1),
        ],
    )
    await repo.add(UserId(1), SubscriptionGranularity.GLOBAL, None)
    await repo.add(UserId(2), SubscriptionGranularity.GLOBAL, None)
    await conn.commit()

    r = await repo.list_recipients_for_run("etl", frozenset())
    assert r == frozenset({"active@x"})


@pytest.mark.asyncio
async def test_no_subscribers_returns_empty_frozenset(
    repo: SqliteSubscriptionRepository,
) -> None:
    r = await repo.list_recipients_for_run("etl", frozenset({"prod"}))
    assert r == frozenset()


@pytest.mark.asyncio
async def test_returns_frozenset_type(
    repo: SqliteSubscriptionRepository,
) -> None:
    r = await repo.list_recipients_for_run("etl", frozenset())
    assert isinstance(r, frozenset)
