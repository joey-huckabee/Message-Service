"""Open a single :mod:`aiosqlite` connection with the v1 PRAGMA profile.

v1 uses a single shared connection (not a pool); concurrent UoW
openings are serialized in-process via an :class:`asyncio.Lock`
held across BEGIN→COMMIT (see :mod:`message_service.infrastructure.
persistence.unit_of_work` and L2-PERS-004). SQLite's WAL mode plus
the ``busy_timeout`` PRAGMA below cover cross-process file
contention, but the in-process serialization is the lock, not the
PRAGMA. The pool architecture and the conditions under which it
should be revisited are preserved verbatim in
``docs/archive/connection-pool-architecture.md``.

Startup PRAGMA sequence (L3-PERS-002):

* ``journal_mode=WAL`` — write-ahead logging; enables concurrent reads.
* ``synchronous=NORMAL`` — fsync at transaction commits only (not
  every page write). Safe with WAL for the "no ACID-break on crash"
  guarantee we need.
* ``foreign_keys=ON`` — enforce FK constraints (off by default in
  SQLite).
* ``busy_timeout=5000`` — five seconds before a busy table returns
  ``SQLITE_BUSY``; covers the window where the writer is committing.

Each PRAGMA is verified via a read-back; any divergence is logged.
``journal_mode=WAL`` returning something other than ``"wal"`` (e.g.,
on a filesystem that doesn't support it) logs a WARNING and proceeds
per L3-PERS-003.

Requirement references
----------------------
L2-PERS-002 (startup PRAGMA)
L3-PERS-001 (mkdir parent)
L3-PERS-002 (PRAGMA sequence and read-back)
L3-PERS-003 (non-WAL warning)
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import structlog

from message_service.domain.errors import PersistenceError

_log = structlog.get_logger(__name__)


async def open_connection(sqlite_path: Path) -> aiosqlite.Connection:
    """Open an :mod:`aiosqlite` connection with the service's PRAGMA profile.

    The connection returned is already configured and ready to use.
    The caller owns it and is responsible for closing it.

    Args:
        sqlite_path: Filesystem path to the SQLite database file.
            Parent directory is created if missing (L3-PERS-001). A
            ``:memory:`` path is accepted for tests.

    Returns:
        A live :class:`aiosqlite.Connection` with PRAGMAs applied.

    Raises:
        PersistenceError: ``sqlite3`` refused to open the file, or a
            PRAGMA could not be set. Parent-directory creation errors
            are surfaced as :class:`PersistenceError` as well.
    """
    # L3-PERS-001: ensure parent exists for disk-backed paths. For
    # ``:memory:`` (or any path that looks special), skip the mkdir.
    if str(sqlite_path) != ":memory:":
        try:
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PersistenceError(
                f"could not create parent directory for SQLite DB: {sqlite_path.parent}",
                details={"path": str(sqlite_path), "reason": str(exc)},
            ) from exc

    try:
        conn = await aiosqlite.connect(str(sqlite_path))
    except Exception as exc:  # pragma: no cover — aiosqlite is lenient
        raise PersistenceError(
            f"could not open SQLite database at {sqlite_path}",
            details={"path": str(sqlite_path), "reason": str(exc)},
        ) from exc

    # Return rows as sqlite3.Row so column-name access works
    # (row["run_id"] vs row[0]).
    conn.row_factory = aiosqlite.Row

    await _apply_pragmas(conn, sqlite_path)
    return conn


async def _apply_pragmas(conn: aiosqlite.Connection, sqlite_path: Path) -> None:
    """Apply and verify the PRAGMA sequence.

    Each PRAGMA is set, then read back via a separate query. Divergent
    read-backs log WARNING per L3-PERS-003 and proceed.

    Args:
        conn: Open connection to configure.
        sqlite_path: Original path, for log context only.
    """
    # 1. journal_mode=WAL. Returns the resulting mode string; a
    # filesystem that doesn't support WAL (e.g., some NFS setups)
    # falls back to "delete" or similar and logs a WARNING.
    async with conn.execute("PRAGMA journal_mode=WAL") as cur:
        row = await cur.fetchone()
    resolved_mode = row[0] if row is not None else None
    if resolved_mode != "wal":
        _log.warning(
            "sqlite_journal_mode_not_wal",
            path=str(sqlite_path),
            resolved_mode=resolved_mode,
        )

    # 2. synchronous=NORMAL
    await conn.execute("PRAGMA synchronous=NORMAL")
    async with conn.execute("PRAGMA synchronous") as cur:
        row = await cur.fetchone()
    # synchronous: 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA
    if row is not None and row[0] != 1:
        _log.warning(
            "sqlite_synchronous_not_normal",
            path=str(sqlite_path),
            resolved=row[0],
        )

    # 3. foreign_keys=ON
    await conn.execute("PRAGMA foreign_keys=ON")
    async with conn.execute("PRAGMA foreign_keys") as cur:
        row = await cur.fetchone()
    if row is None or row[0] != 1:
        raise PersistenceError(
            "SQLite refused to enable foreign_keys",
            details={"path": str(sqlite_path), "resolved": row[0] if row else None},
        )

    # 4. busy_timeout=5000 (ms)
    await conn.execute("PRAGMA busy_timeout=5000")
    async with conn.execute("PRAGMA busy_timeout") as cur:
        row = await cur.fetchone()
    if row is None or row[0] != 5000:
        _log.warning(
            "sqlite_busy_timeout_not_set",
            path=str(sqlite_path),
            resolved=row[0] if row else None,
        )


__all__ = ["open_connection"]
