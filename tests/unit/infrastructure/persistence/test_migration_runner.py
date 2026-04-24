"""Unit tests for :mod:`message_service.infrastructure.persistence.migration_runner`."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from message_service.domain.errors import PersistenceError
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


async def _table_exists(conn: aiosqlite.Connection, name: str) -> bool:
    async with conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ) as cur:
        return await cur.fetchone() is not None


async def _applied_versions(conn: aiosqlite.Connection) -> list[int]:
    async with conn.execute("SELECT version FROM _migrations ORDER BY version") as cur:
        rows = await cur.fetchall()
    return [r[0] for r in rows]


# -----------------------------------------------------------------------------
# Packaged migrations — the default path
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-PERS-003")
async def test_packaged_migrations_create_expected_tables(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    conn = await open_connection(db)
    try:
        applied = await apply_migrations(conn)
        assert [m.version for m in applied] == [1, 2]
        # Domain tables from 001 plus the sweeper outbox from 002.
        for table in (
            "users",
            "runs",
            "stages",
            "subscriptions",
            "audit_log",
            "sweeper_actions",
        ):
            assert await _table_exists(conn, table)
        # Plus the bookkeeping table.
        assert await _table_exists(conn, "_migrations")
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-005")
async def test_reapply_is_noop(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    conn = await open_connection(db)
    try:
        first = await apply_migrations(conn)
        assert first  # non-empty: initial apply
        second = await apply_migrations(conn)
        assert second == []  # no-op
        # Bookkeeping table records each applied migration exactly once.
        assert await _applied_versions(conn) == [1, 2]
    finally:
        await conn.close()


# -----------------------------------------------------------------------------
# 002_sweeper_actions schema details (L3-SWEEP-010)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-010")
async def test_sweeper_actions_schema_shape(tmp_path: Path) -> None:
    """The outbox table SHALL have the columns dispatcher logic depends on."""
    db = tmp_path / "test.db"
    conn = await open_connection(db)
    try:
        await apply_migrations(conn)
        async with conn.execute("PRAGMA table_info(sweeper_actions)") as cur:
            rows = await cur.fetchall()
        # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
        cols = {
            row[1]: {"type": row[2], "notnull": bool(row[3]), "pk": bool(row[5])} for row in rows
        }
        assert set(cols.keys()) == {
            "action_id",
            "run_id",
            "action_name",
            "enqueued_at",
            "claimed_at",
            "completed_at",
            "attempts",
            "last_error",
        }
        # Primary key + non-null discipline matches the dispatcher contract:
        # claimed_at/completed_at/last_error are mutated in place over the
        # row's lifecycle, so they must permit NULL.
        assert cols["action_id"]["pk"] is True
        assert cols["run_id"]["notnull"] is True
        assert cols["action_name"]["notnull"] is True
        assert cols["enqueued_at"]["notnull"] is True
        assert cols["claimed_at"]["notnull"] is False
        assert cols["completed_at"]["notnull"] is False
        assert cols["last_error"]["notnull"] is False
        assert cols["attempts"]["notnull"] is True
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-010")
async def test_sweeper_actions_pending_index_is_partial(tmp_path: Path) -> None:
    """The dispatcher's claim query relies on a partial index over
    unclaimed rows; the index SHALL filter on ``claimed_at IS NULL`` so
    completed rows do not bloat the index."""
    db = tmp_path / "test.db"
    conn = await open_connection(db)
    try:
        await apply_migrations(conn)
        async with conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND name='idx_sweeper_actions_pending'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None, "expected partial index idx_sweeper_actions_pending"
        sql = row[0]
        assert "WHERE claimed_at IS NULL" in sql
        assert "enqueued_at" in sql
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-010")
async def test_sweeper_actions_check_constraints_block_invalid_writes(
    tmp_path: Path,
) -> None:
    """The CHECK constraints SHALL block: unknown action_name; completed_at
    set without claimed_at; claimed_at strictly before enqueued_at."""
    db = tmp_path / "test.db"
    conn = await open_connection(db)
    try:
        await apply_migrations(conn)
        # Need a parent run for the FK.
        await conn.execute(
            "INSERT INTO runs ("
            "  run_id, pipeline_type, state, attachment_mode, "
            "  aggregation_template_name, aggregation_template_version, "
            "  tags_json, declared_stages_json, "
            "  subscription_predicate_tags_json, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "00000000-0000-4000-8000-000000000001",
                "etl-default",
                "ORPHANED",
                "PER_STAGE",
                None,
                None,
                "[]",
                "[]",
                "[]",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
        await conn.commit()

        # Unknown action_name.
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO sweeper_actions (run_id, action_name, enqueued_at) VALUES (?, ?, ?)",
                (
                    "00000000-0000-4000-8000-000000000001",
                    "DEFINITELY_NOT_AN_ACTION",
                    "2026-01-01T00:00:00Z",
                ),
            )
        await conn.rollback()

        # completed_at set without claimed_at.
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO sweeper_actions "
                "(run_id, action_name, enqueued_at, claimed_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    "00000000-0000-4000-8000-000000000001",
                    "NOTIFY_ADMINS",
                    "2026-01-01T00:00:00Z",
                    None,
                    "2026-01-01T00:00:01Z",
                ),
            )
        await conn.rollback()

        # claimed_at before enqueued_at.
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO sweeper_actions "
                "(run_id, action_name, enqueued_at, claimed_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    "00000000-0000-4000-8000-000000000001",
                    "NOTIFY_ADMINS",
                    "2026-01-01T00:00:05Z",
                    "2026-01-01T00:00:00Z",
                ),
            )
        await conn.rollback()
    finally:
        await conn.close()


# -----------------------------------------------------------------------------
# Custom migration set (via override)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_migrations_applied_in_order(tmp_path: Path) -> None:
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "001_first.sql").write_text("CREATE TABLE first_t (id INTEGER);")
    (mig / "002_second.sql").write_text("CREATE TABLE second_t (id INTEGER);")
    (mig / "003_third.sql").write_text("CREATE TABLE third_t (id INTEGER);")
    conn = await open_connection(Path(":memory:"))
    try:
        applied = await apply_migrations(conn, migrations_dir=mig)
        assert [m.version for m in applied] == [1, 2, 3]
        assert await _applied_versions(conn) == [1, 2, 3]
        for t in ("first_t", "second_t", "third_t"):
            assert await _table_exists(conn, t)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_partial_reapply_only_runs_pending(tmp_path: Path) -> None:
    """If 001 is already applied, 002 is the only one to run on reapply."""
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "001_first.sql").write_text("CREATE TABLE first_t (id INTEGER);")
    conn = await open_connection(Path(":memory:"))
    try:
        applied = await apply_migrations(conn, migrations_dir=mig)
        assert [m.version for m in applied] == [1]

        # Add 002 and reapply.
        (mig / "002_second.sql").write_text("CREATE TABLE second_t (id INTEGER);")
        applied = await apply_migrations(conn, migrations_dir=mig)
        assert [m.version for m in applied] == [2]
        assert await _applied_versions(conn) == [1, 2]
    finally:
        await conn.close()


# -----------------------------------------------------------------------------
# L3-PERS-004: naming + gap detection
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-004")
async def test_gap_in_version_sequence_fails(tmp_path: Path) -> None:
    """001, 003 without 002 SHALL fail startup."""
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "001_first.sql").write_text("CREATE TABLE a (id INTEGER);")
    (mig / "003_third.sql").write_text("CREATE TABLE c (id INTEGER);")
    conn = await open_connection(Path(":memory:"))
    try:
        with pytest.raises(PersistenceError, match="gap"):
            await apply_migrations(conn, migrations_dir=mig)
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-004")
async def test_non_sequential_starting_version_fails(tmp_path: Path) -> None:
    """A first migration of 002 (not 001) SHALL fail startup."""
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "002_second.sql").write_text("CREATE TABLE a (id INTEGER);")
    conn = await open_connection(Path(":memory:"))
    try:
        with pytest.raises(PersistenceError, match="gap"):
            await apply_migrations(conn, migrations_dir=mig)
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-004")
async def test_missing_three_digit_prefix_fails(tmp_path: Path) -> None:
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "1_short_prefix.sql").write_text("CREATE TABLE a (id INTEGER);")
    conn = await open_connection(Path(":memory:"))
    try:
        with pytest.raises(PersistenceError, match="NNN_description"):
            await apply_migrations(conn, migrations_dir=mig)
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-004")
async def test_non_conforming_filename_fails(tmp_path: Path) -> None:
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "bad-name.sql").write_text("CREATE TABLE a (id INTEGER);")
    conn = await open_connection(Path(":memory:"))
    try:
        with pytest.raises(PersistenceError, match="does not match"):
            await apply_migrations(conn, migrations_dir=mig)
    finally:
        await conn.close()


# -----------------------------------------------------------------------------
# SQL failure rolls back the migration
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failing_migration_raises_and_does_not_record(
    tmp_path: Path,
) -> None:
    """A SQL error in a migration SHALL raise PersistenceError and leave the
    ``_migrations`` table without that version."""
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "001_first.sql").write_text("CREATE TABLE a (id INTEGER);")
    (mig / "002_bad.sql").write_text("CREATE TABLE b (this is not valid SQL);")
    conn = await open_connection(Path(":memory:"))
    try:
        with pytest.raises(PersistenceError, match="002_bad"):
            await apply_migrations(conn, migrations_dir=mig)
        # 001 was recorded, 002 was not.
        assert await _applied_versions(conn) == [1]
        # Table 'b' does not exist.
        assert not await _table_exists(conn, "b")
    finally:
        await conn.close()


# -----------------------------------------------------------------------------
# _migrations table shape
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-005")
async def test_migrations_table_records_version_name_timestamp(
    tmp_path: Path,
) -> None:
    db = tmp_path / "test.db"
    conn = await open_connection(db)
    try:
        await apply_migrations(conn)
        async with conn.execute(
            "SELECT version, name, applied_at FROM _migrations ORDER BY version"
        ) as cur:
            rows = list(await cur.fetchall())
        assert len(rows) >= 1
        v, name, applied_at = rows[0]
        assert v == 1
        assert name == "001_initial_schema"
        # ISO-8601 with Z suffix
        assert applied_at.endswith("Z")
    finally:
        await conn.close()


# -----------------------------------------------------------------------------
# Empty migrations set
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_migrations_directory_is_noop(tmp_path: Path) -> None:
    mig = tmp_path / "migrations"
    mig.mkdir()
    conn = await open_connection(Path(":memory:"))
    try:
        applied = await apply_migrations(conn, migrations_dir=mig)
        assert applied == []
        # Bookkeeping table created even with no migrations.
        assert await _table_exists(conn, "_migrations")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_non_sql_files_ignored(tmp_path: Path) -> None:
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "001_first.sql").write_text("CREATE TABLE a (id INTEGER);")
    (mig / "README.md").write_text("documentation")
    (mig / "notes.txt").write_text("misc")
    conn = await open_connection(Path(":memory:"))
    try:
        applied = await apply_migrations(conn, migrations_dir=mig)
        assert [m.version for m in applied] == [1]
    finally:
        await conn.close()
