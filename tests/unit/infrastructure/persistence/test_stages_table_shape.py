"""Schema-shape inspection tests for the ``stages`` table.

Covers L3-STAGE-002 (table name + PK + FK shape) and L3-STAGE-017
(no per-stage `last_transition_at` column; the sweeper drives off
the run's `updated_at` instead).
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


def _stages_table_ddl() -> str:
    """Return the CREATE TABLE stages (...) DDL block from the migration file."""
    text = _INITIAL_SCHEMA.read_text(encoding="utf-8")
    match = re.search(
        r"CREATE TABLE stages\s*\((?P<body>.+?)\);",
        text,
        flags=re.DOTALL,
    )
    assert match is not None, "stages table DDL not found in initial migration"
    return match.group("body")


@pytest.mark.requirement("L3-STAGE-002")
def test_stages_table_named_stages() -> None:
    """L3-STAGE-002: the stage table SHALL be named ``stages``."""
    text = _INITIAL_SCHEMA.read_text(encoding="utf-8")
    assert re.search(r"CREATE TABLE stages\s*\(", text) is not None
    # No legacy `stage_state` table.
    assert "CREATE TABLE stage_state" not in text


@pytest.mark.requirement("L3-STAGE-002")
def test_stages_table_primary_key_is_run_id_stage_id() -> None:
    """L3-STAGE-002: composite PK on (run_id, stage_id)."""
    body = _stages_table_ddl()
    assert re.search(r"PRIMARY KEY\s*\(\s*run_id\s*,\s*stage_id\s*\)", body)


@pytest.mark.requirement("L3-STAGE-002")
def test_stages_table_foreign_key_to_runs_with_cascade() -> None:
    """L3-STAGE-002: FK on run_id with ON DELETE CASCADE."""
    body = _stages_table_ddl()
    pattern = re.compile(
        r"FOREIGN KEY\s*\(\s*run_id\s*\)\s+REFERENCES\s+runs\s*\(\s*run_id\s*\)"
        r"\s+ON DELETE CASCADE",
        flags=re.IGNORECASE,
    )
    assert pattern.search(body) is not None


@pytest.mark.requirement("L3-STAGE-017")
def test_stages_table_has_no_last_transition_at_column() -> None:
    """L3-STAGE-017: the sweeper drives off the run's ``updated_at``; the
    ``stages`` table SHALL NOT carry a per-stage ``last_transition_at``.
    """
    body = _stages_table_ddl()
    assert "last_transition_at" not in body, (
        "stages table SHALL NOT have a `last_transition_at` column (see L3-STAGE-017)"
    )


@pytest.mark.requirement("L3-STAGE-017")
def test_runs_table_carries_updated_at_for_sweeper() -> None:
    """L3-STAGE-017 (companion): the run's ``updated_at`` is the
    timestamp the sweeper compares against ``run_timeout_seconds``.
    """
    text = _INITIAL_SCHEMA.read_text(encoding="utf-8")
    runs_match = re.search(
        r"CREATE TABLE runs\s*\((?P<body>.+?)\);",
        text,
        flags=re.DOTALL,
    )
    assert runs_match is not None
    assert "updated_at" in runs_match.group("body")


@pytest.mark.requirement("L3-STAGE-004")
def test_stage_transition_function_signature() -> None:
    """L3-STAGE-004: ``transition`` takes (from_state, to_state, run_id,
    stage_id) and SHALL NOT carry a ``caller_context`` parameter.
    """
    import inspect

    from message_service.domain.state_machines import stage_states

    sig = inspect.signature(stage_states.transition)
    params = list(sig.parameters)
    assert params == ["from_state", "to_state", "run_id", "stage_id"], (
        f"transition signature drift: {params}"
    )
    assert "caller_context" not in params
