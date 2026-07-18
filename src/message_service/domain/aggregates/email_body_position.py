"""The :class:`EmailBodyPosition` value: placement of a stage's email body contribution.

A stage that submits an email body contribution (per L1-AGGR-001)
declares where its content is placed relative to the run-level stage
summary block in the assembled email body (L2-AGGR-003): before it or
after it.

The proto carries a third sentinel, ``EMAIL_BODY_POSITION_UNSPECIFIED``
(the proto3 zero default). The gRPC boundary resolves that sentinel to
:attr:`AFTER_STAGES_SUMMARY` before it reaches the domain (L3-AGGR-004),
so this enum models only the two real placements. A stage with no email
body contribution stores ``None`` — not a member of this enum — per
L3-AGGR-018.

Requirement references
----------------------
L2-AGGR-003, L3-AGGR-004, L3-AGGR-005, L3-AGGR-018
"""

from __future__ import annotations

from enum import StrEnum


class EmailBodyPosition(StrEnum):
    """Placement of a stage's email body contribution relative to the summary.

    StrEnum so the value round-trips through the ``email_body_position``
    TEXT column and structured logs as a human-readable token.
    """

    BEFORE_STAGES_SUMMARY = "BEFORE_STAGES_SUMMARY"
    AFTER_STAGES_SUMMARY = "AFTER_STAGES_SUMMARY"


__all__ = ["EmailBodyPosition"]
