"""Schema-shape inspection tests for `subscriptions`, `sessions`, and `users`.

Covers:
- L3-SUB-019 (granularity CHECK constraint enforces enum values)
- L3-SUB-001 partial-unique-index (already covered by integration tests, but
  shape-asserted here for static-analysis trace)
- L3-SUB-017 (users.disabled BOOLEAN NOT NULL DEFAULT 0)
- L3-AUTH-007 (sessions table: token_hash PK, user_id, created_at,
  last_activity_at; no plaintext token column)
- L3-AUTH-006 (token generation strategy — tested by inspection of
  the call site since random tokens are stochastic)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_INITIAL_SCHEMA = (
    _PROJECT_ROOT
    / "src"
    / "message_service"
    / "infrastructure"
    / "persistence"
    / "migrations"
    / "001_initial_schema.sql"
)
_AUTH_SCHEMA = (
    _PROJECT_ROOT
    / "src"
    / "message_service"
    / "infrastructure"
    / "persistence"
    / "migrations"
    / "003_auth_schema.sql"
)


def _table_ddl(text: str, table_name: str) -> str:
    match = re.search(
        rf"CREATE TABLE {table_name}\s*\((?P<body>.+?)\);",
        text,
        flags=re.DOTALL,
    )
    assert match is not None, f"{table_name} table DDL not found"
    return match.group("body")


# -----------------------------------------------------------------------------
# L3-SUB-019: granularity CHECK constraint
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-SUB-019")
def test_subscriptions_granularity_check_constraint() -> None:
    """L3-SUB-019: a CHECK constraint SHALL restrict ``granularity`` to
    the documented enum values.
    """
    text = _INITIAL_SCHEMA.read_text(encoding="utf-8")
    body = _table_ddl(text, "subscriptions")
    assert re.search(r"CHECK\s*\(\s*granularity\s+IN\s*\(", body)
    for value in ("GLOBAL", "PIPELINE", "TAG"):
        assert f"'{value}'" in body, f"granularity CHECK missing value {value!r}"


# -----------------------------------------------------------------------------
# L3-SUB-001 (partial-unique-index shape)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-SUB-001")
def test_subscriptions_partial_unique_indexes() -> None:
    """L3-SUB-001: SQLite treats multiple NULLs as distinct in a unique
    index, so the (user_id, granularity, target_value) uniqueness is
    enforced via two partial indexes (non-global + global).
    """
    text = _INITIAL_SCHEMA.read_text(encoding="utf-8")
    nonglobal = re.search(
        r"CREATE UNIQUE INDEX\s+idx_subscriptions_unique_nonglobal\s+"
        r"ON subscriptions\(user_id, granularity, target_value\)\s+"
        r"WHERE target_value IS NOT NULL",
        text,
    )
    global_ix = re.search(
        r"CREATE UNIQUE INDEX\s+idx_subscriptions_unique_global\s+"
        r"ON subscriptions\(user_id\)\s+"
        r"WHERE granularity = 'GLOBAL'",
        text,
    )
    assert nonglobal is not None
    assert global_ix is not None


# -----------------------------------------------------------------------------
# L3-SUB-017: users.disabled column
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-SUB-017")
def test_users_disabled_column_shape() -> None:
    """L3-SUB-017: ``users.disabled`` SHALL be a non-null integer with
    default 0 (SQLite uses INTEGER for boolean).
    """
    text = _INITIAL_SCHEMA.read_text(encoding="utf-8")
    body = _table_ddl(text, "users")
    # SQLite stores BOOLEAN as INTEGER; allow either spelling for forward-compat.
    pattern = re.compile(
        r"disabled\s+(BOOLEAN|INTEGER)\s+NOT NULL\s+DEFAULT\s+0",
        flags=re.IGNORECASE,
    )
    assert pattern.search(body) is not None, (
        "users.disabled column SHALL be `<INTEGER|BOOLEAN> NOT NULL DEFAULT 0`"
    )


# -----------------------------------------------------------------------------
# L3-AUTH-007: sessions table shape
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-AUTH-007")
def test_sessions_table_has_required_columns() -> None:
    """L3-AUTH-007: sessions table SHALL have ``token_hash`` (PK),
    ``user_id``, ``created_at``, ``last_activity_at`` columns.
    """
    text = _AUTH_SCHEMA.read_text(encoding="utf-8")
    body = _table_ddl(text, "sessions")
    assert re.search(r"token_hash\s+TEXT\s+PRIMARY KEY", body)
    assert re.search(r"user_id\s+INTEGER\s+NOT NULL", body)
    assert re.search(r"created_at\s+TEXT\s+NOT NULL", body)
    assert re.search(r"last_activity_at\s+TEXT\s+NOT NULL", body)


@pytest.mark.requirement("L3-AUTH-007")
def test_sessions_table_does_not_store_plaintext_token() -> None:
    """L3-AUTH-007: plaintext tokens SHALL NOT be stored — only the hash."""
    text = _AUTH_SCHEMA.read_text(encoding="utf-8")
    body = _table_ddl(text, "sessions")
    # No `token` column (without `_hash` suffix) — the only token-related
    # column is `token_hash`.
    forbidden = re.compile(r"^\s*token\s+(TEXT|VARCHAR)", flags=re.MULTILINE)
    assert forbidden.search(body) is None, (
        "sessions table SHALL NOT have a plaintext `token` column"
    )


# -----------------------------------------------------------------------------
# L3-AUTH-006: session-token generation via secrets.token_urlsafe(32)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-SUB-012")
def test_no_sighup_handler_registered_for_tag_vocabulary_reload() -> None:
    """L3-SUB-012: hot-reload of the tag vocabulary is out of scope for v1.
    No production code SHALL register a `SIGHUP` handler that re-reads
    the vocabulary. Operators must restart the service.
    """
    main_path = _PROJECT_ROOT / "src" / "message_service" / "__main__.py"
    text = main_path.read_text(encoding="utf-8")
    assert "SIGHUP" not in text, (
        "v1 SHALL NOT register a SIGHUP handler for tag-vocabulary reload "
        "(see L3-SUB-012; the deferred-features entry covers future hot-reload)"
    )


@pytest.mark.requirement("L3-AUTH-006")
def test_login_use_case_uses_secrets_token_urlsafe_32() -> None:
    """L3-AUTH-006: session tokens SHALL be generated via
    ``secrets.token_urlsafe(32)``. Inspection of the login use case
    pins the call site so future drift is caught at static-analysis time.
    """
    login_path = (
        _PROJECT_ROOT / "src" / "message_service" / "application" / "use_cases" / "login.py"
    )
    text = login_path.read_text(encoding="utf-8")
    assert "secrets.token_urlsafe(32)" in text, (
        "LoginUseCase SHALL generate session tokens via "
        "`secrets.token_urlsafe(32)` (256 bits of entropy)"
    )
