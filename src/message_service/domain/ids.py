"""Strongly-typed identifier wrappers for domain entities.

Using :class:`typing.NewType` gives mypy enough information to reject
mix-ups (e.g., passing a :data:`StageId` where a :data:`RunId` is
expected) without adding runtime overhead. At runtime, these types are
indistinguishable from their underlying primitives (``str`` or
:class:`uuid.UUID`); the distinction is purely static.

Design notes
------------
- ``RunId``, ``StageId``, ``SubscriptionId``, ``UserId`` are distinct
  types for API safety.
- ``RunId`` wraps ``str`` (not ``uuid.UUID``) per **L3-RUN-002**: stored
  as strings to preserve lexicographic sort and avoid type round-trips
  through SQLite.
- ``StageId`` is the caller-supplied per-run stage identifier, not a
  surrogate key. Primary key on the stage_state table is
  ``(RunId, StageId)`` (L3-STAGE-002).
- ``SubscriptionId`` and ``UserId`` are surrogate keys minted by the
  persistence layer; we reserve ``int`` for these.
- Use :func:`new_run_id` to mint a fresh run identifier (L3-RUN-001:
  ``uuid.uuid4()`` exactly once per BeginRun).
- Use :func:`validate_run_id_str` to accept untrusted string input and
  return a typed ``RunId`` or raise ``MalformedRequestError``
  (L3-RUN-003).

Requirement references
----------------------
L3-RUN-001, L3-RUN-002, L3-RUN-003
L3-STAGE-002
L3-SUB-001, L3-SUB-007
"""

from __future__ import annotations

import re
import uuid
from typing import NewType

from message_service.domain.errors import MalformedRequestError

# -----------------------------------------------------------------------------
# NewType aliases
# -----------------------------------------------------------------------------

RunId = NewType("RunId", str)
"""Lowercase-hex canonical UUID string for a pipeline run.

Wraps ``str`` rather than :class:`uuid.UUID` per L3-RUN-002; string
storage preserves lexicographic sort and avoids SQLite type
round-trips.
"""

StageId = NewType("StageId", str)
"""Caller-supplied per-run stage identifier.

Part of the ``(RunId, StageId)`` primary key on the stage_state table
(L3-STAGE-002). Case-sensitive; no normalization.
"""

SubscriptionId = NewType("SubscriptionId", int)
"""Surrogate key for a subscription row (minted by persistence)."""

UserId = NewType("UserId", int)
"""Surrogate key for a user row (minted by persistence)."""


# -----------------------------------------------------------------------------
# Constructors and validators
# -----------------------------------------------------------------------------

# Canonical UUID-4 hex form, lowercase only. L3-RUN-003 mandates this exact
# pattern for validation of untrusted input.
_RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def new_run_id() -> RunId:
    """Mint a fresh :data:`RunId` via :func:`uuid.uuid4`.

    L3-RUN-001 mandates exactly one ``uuid.uuid4()`` call per BeginRun;
    use-case code SHALL call this function once per request rather than
    constructing UUIDs inline.

    Returns:
        A new :data:`RunId` in canonical lowercase-hex form.
    """
    return RunId(str(uuid.uuid4()))


def validate_run_id_str(value: str) -> RunId:
    """Accept an untrusted string and return a typed :data:`RunId`.

    Args:
        value: The untrusted input string (from gRPC, REST, or CLI).

    Returns:
        The same string, typed as :data:`RunId`.

    Raises:
        MalformedRequestError: If ``value`` does not match the canonical
            UUID-4 pattern. ``details`` includes the offending value.
    """
    if not _RUN_ID_PATTERN.match(value):
        raise MalformedRequestError(
            f"invalid run_id: {value!r}",
            details={"run_id": value, "expected_pattern": _RUN_ID_PATTERN.pattern},
        )
    return RunId(value)


__all__ = [
    "RunId",
    "StageId",
    "SubscriptionId",
    "UserId",
    "new_run_id",
    "validate_run_id_str",
]
