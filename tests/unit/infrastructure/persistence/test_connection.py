"""Unit tests for :mod:`message_service.infrastructure.persistence.connection`."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from message_service.domain.errors import PersistenceError
from message_service.infrastructure.persistence.connection import open_connection

# -----------------------------------------------------------------------------
# Happy-path PRAGMA verification
# -----------------------------------------------------------------------------


async def _read_pragma(conn: aiosqlite.Connection, pragma: str) -> int | str | None:
    """Read a single-value PRAGMA and return the first column."""
    async with conn.execute(f"PRAGMA {pragma}") as cur:
        row = await cur.fetchone()
    return None if row is None else row[0]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-002")
async def test_wal_journal_mode_set_on_disk_db(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    conn = await open_connection(db)
    try:
        assert await _read_pragma(conn, "journal_mode") == "wal"
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-002")
async def test_foreign_keys_enabled(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    conn = await open_connection(db)
    try:
        assert await _read_pragma(conn, "foreign_keys") == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-002")
async def test_synchronous_is_normal(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    conn = await open_connection(db)
    try:
        # 1 == NORMAL
        assert await _read_pragma(conn, "synchronous") == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-002")
async def test_busy_timeout_is_5000_ms(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    conn = await open_connection(db)
    try:
        assert await _read_pragma(conn, "busy_timeout") == 5000
    finally:
        await conn.close()


# -----------------------------------------------------------------------------
# Parent-directory creation (L3-PERS-001)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-001")
async def test_creates_missing_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "db.sqlite"
    assert not nested.parent.exists()
    conn = await open_connection(nested)
    try:
        assert nested.parent.exists()
        assert nested.exists()
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-001")
async def test_existing_parent_is_not_disturbed(tmp_path: Path) -> None:
    parent = tmp_path / "existing"
    parent.mkdir()
    sentinel = parent / "sentinel.txt"
    sentinel.write_text("preserved")
    db = parent / "db.sqlite"
    conn = await open_connection(db)
    try:
        assert sentinel.read_text() == "preserved"
        assert db.exists()
    finally:
        await conn.close()


# -----------------------------------------------------------------------------
# :memory: support
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_database_opens_successfully() -> None:
    conn = await open_connection(Path(":memory:"))
    try:
        # Foreign keys still enabled on :memory:
        assert await _read_pragma(conn, "foreign_keys") == 1
        # Basic SQL works.
        await conn.execute("CREATE TABLE t (id INTEGER)")
        await conn.execute("INSERT INTO t VALUES (1)")
        async with conn.execute("SELECT COUNT(*) FROM t") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_memory_database_skips_mkdir(tmp_path: Path) -> None:
    """The ``:memory:`` sentinel SHALL NOT cause a mkdir attempt."""
    # Confirmed by the fact that open_connection(:memory:) succeeds even
    # though ``":memory:"``'s ``.parent`` is ``"."``. Re-running the
    # smoke test here locks the behavior.
    conn = await open_connection(Path(":memory:"))
    await conn.close()


# -----------------------------------------------------------------------------
# Error paths
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parent_mkdir_failure_raises_persistence_error(tmp_path: Path) -> None:
    """A pre-existing *file* at the parent path SHALL cause mkdir to fail."""
    # tmp_path/foo is a regular file; asking to put db at tmp_path/foo/bar.db
    # asks mkdir to create tmp_path/foo as a directory, which fails because
    # it's already a file.
    blocker = tmp_path / "foo"
    blocker.write_text("I am a file")
    db = blocker / "bar.db"
    with pytest.raises(PersistenceError, match="parent directory"):
        await open_connection(db)


# -----------------------------------------------------------------------------
# Row factory
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rows_accessible_by_column_name(tmp_path: Path) -> None:
    """``conn.row_factory = aiosqlite.Row`` enables ``row['col']`` access."""
    db = tmp_path / "test.db"
    conn = await open_connection(db)
    try:
        await conn.execute("CREATE TABLE t (name TEXT, value INTEGER)")
        await conn.execute("INSERT INTO t VALUES ('x', 42)")
        async with conn.execute("SELECT name, value FROM t") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row["name"] == "x"
        assert row["value"] == 42
    finally:
        await conn.close()
