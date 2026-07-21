"""Filesystem implementation of :class:`AuditArchiveWriter` (L3-OBS-043).

Appends expired audit records as newline-delimited JSON (one object per line) to
a per-date file ``audit-archive-<YYYY-MM-DD>.jsonl`` under a configured
directory. The write is flushed and ``fsync``-ed before returning so a crash
between archive and delete cannot lose already-"archived" records.

Delivery is **at-least-once**: archive precedes delete, so if the delete step
fails after a successful archive the same rows are re-fetched and re-archived on
the next tick — appending a duplicate line. Each record therefore carries its
``audit_id`` so a downstream consumer can deduplicate; the archive is an
append-only journal, not a deduplicated store. (This is a deliberate LOW-cost
choice over a transactional outbox, which the retention pruner does not warrant.)
The blocking file I/O is offloaded off the event loop by the caller (the pruner
runs ``archive`` via ``asyncio.to_thread``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from message_service.application.ports.audit_archive_writer import AuditArchiveWriter
from message_service.application.ports.clock import iso_z
from message_service.domain.aggregates.audit_event import AuditEvent


def _event_to_record(event: AuditEvent) -> dict[str, object]:
    """Serialize one audit event to a JSON-ready dict (L3-OBS-043 shape).

    Includes ``audit_id`` (the source-row primary key) so a consumer can
    deduplicate the at-least-once archive. Rows come from the viewer read path
    (``fetch_older_than``), which populates ``audit_id``.
    """
    return {
        "audit_id": event.audit_id,
        "timestamp": iso_z(event.timestamp),
        "action": event.action.value,
        "actor": event.actor,
        "resource": event.resource,
        "outcome": event.outcome.value,
        "details": event.details,
    }


class FilesystemAuditArchiveWriter(AuditArchiveWriter):
    """Append expired audit records to a per-date JSONL file under ``root``."""

    def __init__(self, root: Path) -> None:
        """Bind to the archive directory (assumed to exist and be writable)."""
        self._root = root

    def archive(self, events: Sequence[AuditEvent], *, as_of: datetime) -> None:
        """Append every event as a JSON line, flushed + fsynced before return.

        Args:
            events: Expired records to archive; a no-op when empty.
            as_of: The pruner tick's clock time; names the ``audit-archive-
                <date>.jsonl`` file so ticks on the same day share a file.

        Raises:
            OSError: The archive file could not be written.
        """
        if not events:
            return
        target = self._root / f"audit-archive-{as_of.date().isoformat()}.jsonl"
        lines = [
            json.dumps(_event_to_record(e), separators=(",", ":"), sort_keys=True) for e in events
        ]
        with target.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(f"{line}\n" for line in lines))
            fh.flush()
            os.fsync(fh.fileno())


__all__ = ["FilesystemAuditArchiveWriter"]
