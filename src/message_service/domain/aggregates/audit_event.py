"""Append-only audit event for security and governance tracking.

The audit log is the canonical record of state-changing actions: who
did what, when, and against which resource. Records are immutable once
written; the :class:`~message_service.application.ports.audit_log.AuditLog`
port exposes only ``record`` and ``query`` — no update or delete.

Recording is transactional with state change (L3-RUN-026): audit insert
precedes the business-state update within the same DB transaction, so a
persistence failure leaves no orphaned state mutation.

Per-action format conventions (actor / resource / required ``details``
fields) are pinned by the L3-OBS-025..036 cluster (run-lifecycle,
state-transition, sweeper, subscription, and auth audit categories
authored in 25f) and L3-DASH-013 (manual resend, reworded in the
2026-04-25 spec commit ``5d94a29`` to use ``action=RESEND_REPORT``).

Requirement references
----------------------
L1-OBS-003 (retention + audit scope)
L2-OBS-002, L2-OBS-005
L2-OBS-013..017 (audit-record categories)
L3-RUN-026, L3-RUN-027 (audit-precedes-state-mutation invariant)
L3-OBS-025..036 (per-action format pins)
L3-DASH-013 (RESEND_REPORT format pin)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class AuditAction(StrEnum):
    """The action being recorded.

    Values mirror the use-case names so logs are grep-friendly. Add new
    values as new use cases appear; the stdlib ``StrEnum`` permits
    value-based lookup via ``AuditAction("BEGIN_RUN")``.
    """

    BEGIN_RUN = "BEGIN_RUN"
    SUBMIT_STAGE_REPORT = "SUBMIT_STAGE_REPORT"
    FINALIZE_RUN = "FINALIZE_RUN"
    RUN_STATE_TRANSITION = "RUN_STATE_TRANSITION"
    STAGE_STATE_TRANSITION = "STAGE_STATE_TRANSITION"
    SEND_REPORT = "SEND_REPORT"
    RESEND_REPORT = "RESEND_REPORT"
    SWEEP_ORPHAN = "SWEEP_ORPHAN"
    DISPATCHER_ACTION_ABANDONED = "DISPATCHER_ACTION_ABANDONED"
    SUBSCRIBE = "SUBSCRIBE"
    UNSUBSCRIBE = "UNSUBSCRIBE"
    CREATE_USER = "CREATE_USER"
    UPDATE_USER = "UPDATE_USER"
    LOGIN = "LOGIN"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"


class AuditOutcome(StrEnum):
    """Whether the action succeeded.

    ``SUCCESS`` and ``FAILURE`` are the only v1 values. Future granular
    outcomes (e.g., ``PARTIAL``) can be added; enum widening is
    backwards-compatible.
    """

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """A single audit log record.

    Attributes:
        timestamp: UTC wall-clock at record time (from injected
            :class:`~message_service.application.ports.clock.Clock`).
        action: What was attempted.
        actor: Free-form actor identifier — ``"user:42"``,
            ``"pipeline:etl-nightly"``, ``"system:sweeper"``, etc. Kept
            as a string because actors come from multiple identity
            systems with no unified ID space in v1.
        resource: Free-form target identifier — ``"run:<uuid>"``,
            ``"stage:<run>:<stage>"``, ``"user:42"``, etc. Same
            rationale as ``actor``.
        outcome: Success or failure.
        details: Structured contextual data. Per-action format
            conventions are pinned by the L3-OBS-025..036 cluster and
            L3-DASH-013 (see module docstring). Stored as a JSON
            column at persistence time.
        audit_id: The ``audit_log`` table primary key, populated by
            the persistence adapter on read. ``None`` for records
            that have not yet been persisted (the ``record`` write
            path receives an instance with ``audit_id=None`` and
            never reads back a populated id; the
            ``L1-DASH-005`` viewer-read path receives instances with
            ``audit_id`` set so clients can deep-link to a specific
            record per ``L2-DASH-016``).
    """

    timestamp: datetime
    action: AuditAction
    actor: str
    resource: str
    outcome: AuditOutcome
    details: dict[str, Any] = field(default_factory=dict)
    audit_id: int | None = None

    def __post_init__(self) -> None:
        """Validate timestamp and string fields.

        Raises:
            ValueError: If ``timestamp`` is naive, or if ``actor`` or
                ``resource`` is empty.
        """
        if self.timestamp.tzinfo is None:
            raise ValueError("AuditEvent.timestamp must be timezone-aware")
        if not self.actor:
            raise ValueError("AuditEvent.actor must be non-empty")
        if not self.resource:
            raise ValueError("AuditEvent.resource must be non-empty")


__all__ = ["AuditAction", "AuditEvent", "AuditOutcome"]
