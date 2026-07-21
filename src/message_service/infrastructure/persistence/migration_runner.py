"""Hand-rolled migration runner.

Discovers migration files in :mod:`.migrations` following the naming
convention ``NNN_description.sql`` (three-digit zero-padded prefix per
L3-PERS-004), applies them in numerical order, and tracks applied
versions in the ``_migrations`` bookkeeping table (L3-PERS-005).

Re-running at startup is a no-op once the table is in sync. Gaps in
the NNN sequence fail startup with :class:`PersistenceError` rather
than silently skipping.

Transaction per migration
-------------------------
Each migration file is applied inside its own transaction. On failure,
the transaction rolls back and :class:`PersistenceError` propagates,
so a half-applied migration cannot leave the schema in an
intermediate state.

Requirement references
----------------------
L2-PERS-003 (migration runner)
L3-PERS-004 (naming convention, gap detection)
L3-PERS-005 (_migrations table)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import as_file, files
from pathlib import Path

import aiosqlite
import structlog

from message_service.domain.errors import PersistenceError

_log = structlog.get_logger(__name__)

# NNN_<description>.sql
_MIGRATION_NAME_RE: re.Pattern[str] = re.compile(r"^(\d{3})_([A-Za-z0-9_]+)\.sql$")


@dataclass(frozen=True, slots=True)
class _MigrationFile:
    """A discovered migration file."""

    version: int
    name: str  # filename without extension, e.g. "001_initial_schema"
    source: str  # SQL body


async def apply_migrations(
    conn: aiosqlite.Connection,
    *,
    migrations_dir: Path | None = None,
) -> list[_MigrationFile]:
    """Discover and apply any pending migrations.

    Args:
        conn: Open, PRAGMA-configured connection.
        migrations_dir: Override the migration directory. When ``None``
            (the default), load migrations from the packaged
            ``message_service.infrastructure.persistence.migrations``
            module via :mod:`importlib.resources`. An override is
            useful for tests that want to exercise the runner against
            a custom migration set.

    Returns:
        Migrations that were freshly applied during this call, in the
        order they were applied. Empty list if the schema was already
        up to date.

    Raises:
        PersistenceError: Any migration file failed to apply; the
            ``_migrations`` table missed a row; the file sequence has
            a gap; or a filename failed the naming convention.
    """
    migrations = _discover(migrations_dir)

    await _ensure_migrations_table(conn)
    applied_versions = await _fetch_applied_versions(conn)

    newly_applied: list[_MigrationFile] = []
    for m in migrations:
        if m.version in applied_versions:
            continue
        _log.info("applying_migration", version=m.version, name=m.name)
        await _apply_one(conn, m)
        newly_applied.append(m)

    if newly_applied:
        _log.info(
            "migrations_applied",
            count=len(newly_applied),
            versions=[m.version for m in newly_applied],
        )
    else:
        _log.info("migrations_up_to_date")

    return newly_applied


# -----------------------------------------------------------------------------
# Discovery
# -----------------------------------------------------------------------------


def _discover(override: Path | None) -> list[_MigrationFile]:
    """Load migration files from disk or from the packaged resources.

    Args:
        override: When not ``None``, read from this directory.

    Returns:
        Migrations sorted by version ascending. Gaps in the version
        sequence raise :class:`PersistenceError`.
    """
    if override is not None:
        files_list: list[tuple[str, str]] = []
        for path in sorted(override.iterdir()):
            if path.suffix != ".sql":
                continue
            files_list.append((path.name, path.read_text(encoding="utf-8")))
    else:
        files_list = _load_packaged_migrations()

    parsed: list[_MigrationFile] = []
    for filename, source in files_list:
        match = _MIGRATION_NAME_RE.match(filename)
        if match is None:
            raise PersistenceError(
                f"migration file {filename!r} does not match NNN_description.sql",
                details={"filename": filename, "pattern": _MIGRATION_NAME_RE.pattern},
            )
        version = int(match.group(1))
        parsed.append(
            _MigrationFile(
                version=version,
                name=filename.removesuffix(".sql"),
                source=source,
            )
        )

    parsed.sort(key=lambda m: m.version)

    # L3-PERS-004: detect gaps. Expected sequence is 1, 2, 3, ...
    for expected_one_based_index, m in enumerate(parsed, start=1):
        if m.version != expected_one_based_index:
            raise PersistenceError(
                f"migration version sequence has a gap: expected "
                f"{expected_one_based_index}, found {m.version}",
                details={
                    "expected_version": expected_one_based_index,
                    "found_version": m.version,
                    "name": m.name,
                    "discovered_versions": [x.version for x in parsed],
                },
            )

    return parsed


def _load_packaged_migrations() -> list[tuple[str, str]]:
    """Load migrations from the packaged ``migrations`` subpackage."""
    pkg = files("message_service.infrastructure.persistence.migrations")
    collected: list[tuple[str, str]] = []
    for entry in pkg.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        if not name.endswith(".sql"):
            continue
        # Read the SQL text. ``as_file`` handles both directory layouts
        # (editable installs) and zipped wheels.
        with as_file(entry) as fs_path:
            source = fs_path.read_text(encoding="utf-8")
        collected.append((name, source))
    return collected


# -----------------------------------------------------------------------------
# Apply
# -----------------------------------------------------------------------------


async def _ensure_migrations_table(conn: aiosqlite.Connection) -> None:
    """Create the bookkeeping table if absent (L3-PERS-005)."""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    await conn.commit()


async def _fetch_applied_versions(conn: aiosqlite.Connection) -> set[int]:
    """Return the set of versions already in the ``_migrations`` table."""
    async with conn.execute("SELECT version FROM _migrations") as cur:
        rows = await cur.fetchall()
    return {row[0] for row in rows}


async def _apply_one(conn: aiosqlite.Connection, m: _MigrationFile) -> None:
    """Apply one migration and its bookkeeping row atomically (L3-PERS-036).

    ``executescript`` performs NO implicit transaction wrapping — its only
    implicit action is committing an already-pending transaction *before*
    running the script (Python ``sqlite3`` semantics). A bare
    ``executescript(m.source)`` therefore autocommits each DDL statement as
    it runs, so a failure on, say, the third statement of a three-statement
    migration leaves the first two committed; the ``rollback`` below cannot
    undo them, and the next startup re-runs the migration from the top and
    bricks on e.g. ``duplicate column``.

    To make the migration atomic we frame the body AND the ``_migrations``
    bookkeeping insert in one explicit ``BEGIN … COMMIT`` inside the script.
    If any statement fails, the transaction is left open and uncommitted and
    the ``rollback`` discards every earlier statement — so the migration is
    all-or-nothing and safely retryable on the next startup.

    The bookkeeping row is inlined because ``executescript`` accepts no bind
    parameters; ``version`` is an ``int`` and ``name`` matches
    ``^[A-Za-z0-9_]+$`` (the discovery regex), so neither can carry SQL
    metacharacters. Migration bodies must not contain their own transaction
    control (``BEGIN``/``COMMIT``); ours provides it.
    """
    now = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    insert = (
        "INSERT INTO _migrations (version, name, applied_at) "
        f"VALUES ({m.version}, '{m.name}', '{now}');"
    )
    script = f"BEGIN;\n{m.source}\n{insert}\nCOMMIT;"
    try:
        await conn.executescript(script)
    except Exception as exc:
        # The BEGIN opened a transaction that executescript never reached
        # COMMIT on; roll it back so no partial migration persists.
        await conn.rollback()
        raise PersistenceError(
            f"failed to apply migration {m.name}: {exc}",
            details={"version": m.version, "name": m.name, "reason": str(exc)},
        ) from exc


__all__ = ["apply_migrations"]
