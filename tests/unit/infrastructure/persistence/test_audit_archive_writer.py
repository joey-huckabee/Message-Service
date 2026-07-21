"""Tests for :class:`FilesystemAuditArchiveWriter` (L3-OBS-043)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.infrastructure.persistence.audit_archive_writer import (
    FilesystemAuditArchiveWriter,
)

pytestmark = pytest.mark.allow_io

_AS_OF = datetime(2026, 7, 19, 6, 0, 0, tzinfo=UTC)


def _event(actor: str, when: datetime | None = None, audit_id: int | None = 1) -> AuditEvent:
    return AuditEvent(
        timestamp=when or datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC),
        action=AuditAction.LOGIN,
        actor=actor,
        resource="session:x",
        outcome=AuditOutcome.SUCCESS,
        details={"k": "v"},
        audit_id=audit_id,
    )


@pytest.mark.requirement("L3-OBS-043")
def test_archive_writes_jsonl_named_by_as_of_date(tmp_path: Path) -> None:
    """Events are written as one JSON object per line to audit-archive-<date>.jsonl."""
    writer = FilesystemAuditArchiveWriter(root=tmp_path)
    writer.archive([_event("user:1"), _event("user:2")], as_of=_AS_OF)

    archive = tmp_path / "audit-archive-2026-07-19.jsonl"
    assert archive.is_file()
    lines = archive.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert [r["actor"] for r in records] == ["user:1", "user:2"]
    # Each record carries the L3-OBS-043 shape, including audit_id for dedup.
    assert set(records[0]) == {
        "audit_id",
        "timestamp",
        "action",
        "actor",
        "resource",
        "outcome",
        "details",
    }
    assert records[0]["audit_id"] == 1
    assert records[0]["action"] == "LOGIN"
    assert records[0]["outcome"] == "SUCCESS"
    assert records[0]["details"] == {"k": "v"}


@pytest.mark.requirement("L3-OBS-043")
def test_archive_appends_across_calls_same_day(tmp_path: Path) -> None:
    """Two calls on the same as_of date append to the same file."""
    writer = FilesystemAuditArchiveWriter(root=tmp_path)
    writer.archive([_event("a")], as_of=_AS_OF)
    writer.archive([_event("b")], as_of=_AS_OF)

    archive = tmp_path / "audit-archive-2026-07-19.jsonl"
    lines = archive.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["actor"] for line in lines] == ["a", "b"]


@pytest.mark.requirement("L3-OBS-043")
def test_archive_empty_is_a_noop(tmp_path: Path) -> None:
    """An empty batch writes nothing (no file created)."""
    FilesystemAuditArchiveWriter(root=tmp_path).archive([], as_of=_AS_OF)
    assert list(tmp_path.iterdir()) == []
