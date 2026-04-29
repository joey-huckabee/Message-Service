"""Schema-shape inspection tests for the ``audit_log`` table.

Covers L3-OBS-012 (column set + indexes) and L3-OBS-013 (details_json
type and PersistenceError on non-serializable fields — verified via
existing audit-log integration tests; here we only pin the structural
shape).
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


def _audit_log_ddl() -> str:
    text = _INITIAL_SCHEMA.read_text(encoding="utf-8")
    match = re.search(
        r"CREATE TABLE audit_log\s*\((?P<body>.+?)\);",
        text,
        flags=re.DOTALL,
    )
    assert match is not None, "audit_log table DDL not found"
    return match.group("body")


@pytest.mark.requirement("L3-OBS-012")
def test_audit_log_table_columns_match_spec() -> None:
    """L3-OBS-012: audit_log SHALL have audit_id (PK AUTOINCREMENT),
    timestamp, action, actor, resource, outcome, details_json columns.
    """
    body = _audit_log_ddl()
    assert re.search(r"audit_id\s+INTEGER\s+PRIMARY KEY\s+AUTOINCREMENT", body)
    for col in ("timestamp", "action", "actor", "resource", "outcome"):
        assert re.search(rf"{col}\s+TEXT\s+NOT NULL", body), (
            f"audit_log SHALL declare {col} as TEXT NOT NULL"
        )
    assert re.search(r"details_json\s+TEXT\s+NOT NULL", body)


@pytest.mark.requirement("L3-OBS-012")
def test_audit_log_outcome_check_constraint() -> None:
    """L3-OBS-012: outcome SHALL be CHECK-constrained to {SUCCESS, FAILURE}."""
    body = _audit_log_ddl()
    assert re.search(r"CHECK\s*\(\s*outcome\s+IN\s*\(", body)
    for value in ("SUCCESS", "FAILURE"):
        assert f"'{value}'" in body, f"outcome CHECK SHALL include {value!r}"


@pytest.mark.requirement("L3-OBS-012")
def test_audit_log_indexes_present() -> None:
    """L3-OBS-012: indexes on timestamp, resource, action SHALL exist
    so retention queries (timestamp filter) and dashboard filters
    (resource / action) are efficient.
    """
    text = _INITIAL_SCHEMA.read_text(encoding="utf-8")
    for col in ("timestamp", "resource", "action"):
        pattern = re.compile(
            rf"CREATE INDEX\s+\w+\s+ON\s+audit_log\(\s*{col}\s*\)",
        )
        assert pattern.search(text) is not None, f"audit_log SHALL have an index on {col}"


@pytest.mark.requirement("L3-OBS-013")
def test_audit_log_details_json_column_is_text_not_null() -> None:
    """L3-OBS-013: ``details_json`` SHALL be a NOT NULL TEXT column;
    callers serialize their dict to JSON before insert, so empty
    dicts go in as `'{}'` rather than NULL.
    """
    body = _audit_log_ddl()
    assert re.search(r"details_json\s+TEXT\s+NOT NULL", body)
