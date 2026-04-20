"""Domain aggregate types: :class:`Run`, :class:`Stage`, :class:`Subscription`, and peers.

Aggregates are frozen value objects representing persisted domain
state. They enforce construction-time invariants in ``__post_init__``
but carry no behavior beyond that; state transitions happen in use-case
code via the
:mod:`~message_service.domain.state_machines` transition functions.

Aggregates at this layer MUST NOT import from ``application/`` or
``infrastructure/``. The lone upward dependency permitted is
:mod:`message_service.domain.ids` (ID newtypes) and
:mod:`message_service.domain.state_machines` (the state enums).
"""

from __future__ import annotations

from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.domain.aggregates.run import AttachmentMode, Run
from message_service.domain.aggregates.stage import Stage
from message_service.domain.aggregates.subscription import (
    Subscription,
    SubscriptionGranularity,
)
from message_service.domain.aggregates.template_metadata import (
    TemplateKind,
    TemplateMetadata,
)
from message_service.domain.aggregates.template_ref import TemplateRef

__all__ = [
    "AttachmentMode",
    "AuditAction",
    "AuditEvent",
    "AuditOutcome",
    "Run",
    "Stage",
    "Subscription",
    "SubscriptionGranularity",
    "TemplateKind",
    "TemplateMetadata",
    "TemplateRef",
]
