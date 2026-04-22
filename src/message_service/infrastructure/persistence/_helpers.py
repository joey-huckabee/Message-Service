"""Shared helpers for SQLite repository adapters.

Contains:

* :func:`parse_iso_z` — inverse of
  :func:`~message_service.application.ports.clock.iso_z`. Handles both
  the ``Z`` suffix and explicit ``+00:00`` offset forms.
* :func:`dumps_json` — deterministic JSON encoder (sorted keys, compact
  separators) matching the size-measurement convention used elsewhere.
* :func:`loads_json` — paired decoder; wraps :class:`json.JSONDecodeError`
  as :class:`PersistenceError` because corrupt JSON in a persisted row
  is a "could not read your data back" condition, not a client error.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from message_service.domain.errors import PersistenceError


def parse_iso_z(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, accepting the ``Z`` suffix.

    Args:
        value: An ISO-8601 string. Accepts both ``2026-04-21T00:00:00Z``
            (as produced by :func:`iso_z`) and
            ``2026-04-21T00:00:00+00:00`` for tolerance against
            ingested legacy data.

    Returns:
        A timezone-aware :class:`datetime` in UTC.

    Raises:
        PersistenceError: ``value`` could not be parsed. The bad string
            is included in ``details``.
    """
    try:
        # fromisoformat in 3.11+ handles the "Z" suffix directly. In
        # 3.12+ this is guaranteed. Keep the replace() as a belt-and-
        # braces fallback for any older-stored value that might leak in.
        normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise PersistenceError(
            f"could not parse persisted timestamp {value!r}: {exc}",
            details={"value": value, "reason": str(exc)},
        ) from exc
    # Guarantee tz-aware UTC output even if the incoming string had a
    # non-UTC offset.
    if dt.tzinfo is None:
        raise PersistenceError(
            f"persisted timestamp {value!r} has no timezone info",
            details={"value": value},
        )
    return dt.astimezone(UTC)


def dumps_json(obj: Any) -> str:
    """Serialize ``obj`` with deterministic key order and compact separators.

    Matches the convention used for context-size measurement in the
    templating adapter and for idempotent audit-detail round-tripping.

    Args:
        obj: Any JSON-serializable value.

    Returns:
        The serialized string.

    Raises:
        PersistenceError: ``obj`` is not JSON-serializable.
    """
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise PersistenceError(
            f"value is not JSON-serializable: {exc}",
            details={"reason": str(exc)},
        ) from exc


def loads_json(value: str, *, field: str) -> Any:
    """Decode a persisted JSON string.

    Args:
        value: The stored JSON text.
        field: Column/field name included in error ``details`` for
            diagnosis.

    Returns:
        The deserialized value.

    Raises:
        PersistenceError: Text is not valid JSON.
    """
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise PersistenceError(
            f"could not decode persisted JSON field {field!r}: {exc}",
            details={"field": field, "value": value, "reason": str(exc)},
        ) from exc


__all__ = ["dumps_json", "loads_json", "parse_iso_z"]
