"""Port: durable archive writer for expired audit records (L2-OBS-019).

When audit archival is configured, the retention pruner writes the batch of
expired records to a durable archive via this port *before* deleting them, so a
site's long-term investigative needs survive retention pruning. Implementations
SHALL make the write durable (flushed to the OS) before returning, and SHALL
raise on failure so the pruner can abort the tick's deletion (fail-safe: retain,
never lose).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime

from message_service.domain.aggregates.audit_event import AuditEvent


class AuditArchiveWriter(ABC):
    """Writes expired audit records to a durable archive (L3-OBS-043)."""

    @abstractmethod
    def archive(self, events: Sequence[AuditEvent], *, as_of: datetime) -> None:
        """Durably persist ``events`` to the archive.

        MUST complete and flush the write before returning, and MUST raise
        (rather than silently drop) on any failure so the caller can skip the
        subsequent deletion.

        Args:
            events: The expired records to archive, in the order the pruner
                fetched them.
            as_of: The pruner tick's clock time; implementations use it (not the
                host wall clock) to derive any date-based archive file name so
                behavior stays deterministic under an injected clock.

        Raises:
            OSError: The archive could not be written.
        """


__all__ = ["AuditArchiveWriter"]
