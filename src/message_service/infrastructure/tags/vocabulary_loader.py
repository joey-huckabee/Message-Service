"""In-memory :class:`TagVocabulary` adapter loaded from a TOML file.

The tag vocabulary file format (per L3-SUB-009):

.. code-block:: toml

    [[tag]]
    name = "production"
    description = "Production-tier runs (optional)"

    [[tag]]
    name = "critical"

Every ``[[tag]]`` table is required to have a ``name`` field; every
other key (``description``) is optional. Unknown keys raise
:class:`ConfigurationError` (L3-SUB-009).

Tag names must match the regex ``^[a-z][a-z0-9_-]{0,63}$`` per
L3-SUB-010. Non-conforming names raise :class:`ConfigurationError`
with the offending name in ``details``.

Loading happens once at service start (L2-SUB-006); no hot-reload.

Requirement references
----------------------
L1-SUB-003 (controlled vocabulary)
L2-SUB-006 (load from config at startup)
L3-SUB-009 (TOML format + unknown-key rejection)
L3-SUB-010 (name regex)
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from message_service.application.ports.tag_vocabulary import TagVocabulary
from message_service.domain.errors import ConfigurationError

# L3-SUB-010: tag name regex. Anchored; total length 1..64.
_TAG_NAME_RE: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

# Permitted keys on a [[tag]] table. Any other key at this level
# raises ConfigurationError per L3-SUB-009.
_PERMITTED_TAG_KEYS: frozenset[str] = frozenset({"name", "description"})

# Permitted top-level keys in the vocabulary file.
_PERMITTED_TOP_LEVEL_KEYS: frozenset[str] = frozenset({"tag"})


class InMemoryTagVocabulary(TagVocabulary):
    """:class:`TagVocabulary` backed by an in-memory ``frozenset``.

    Case-sensitive exact match per L3-RUN-012. Zero-cost ``contains``;
    :meth:`all_tags` returns the pre-computed frozen set without copy.
    """

    def __init__(self, tags: frozenset[str]) -> None:
        """Construct from a pre-validated frozen set of tag names.

        Prefer :func:`load_tag_vocabulary` for the common case of
        loading from a TOML file; use this constructor for tests or
        when the vocabulary is computed from another source.

        Args:
            tags: The complete tag vocabulary. Caller is responsible
                for name-regex validation.
        """
        self._tags = tags

    def contains(self, tag: str) -> bool:  # noqa: D102 — documented on the port
        return tag in self._tags

    def all_tags(self) -> frozenset[str]:  # noqa: D102 — documented on the port
        return self._tags


def load_tag_vocabulary(vocabulary_path: Path) -> InMemoryTagVocabulary:
    """Load and validate a tag vocabulary TOML file.

    Args:
        vocabulary_path: Absolute or pre-resolved path to the
            vocabulary file (typically ``config.tags.vocabulary_path``).

    Returns:
        An :class:`InMemoryTagVocabulary` ready for use.

    Raises:
        ConfigurationError: Any of the following conditions:

            * The file cannot be opened or parsed as TOML.
            * The file contains a top-level key other than ``tag``.
            * A ``[[tag]]`` entry is missing its ``name`` field, or
              contains a key outside the permitted set.
            * A tag name does not match ``^[a-z][a-z0-9_-]{0,63}$``.
            * A tag name appears in more than one ``[[tag]]`` entry.
    """
    if not vocabulary_path.exists():
        raise ConfigurationError(
            f"tag vocabulary file not found: {vocabulary_path}",
            details={"path": str(vocabulary_path)},
        )

    try:
        with vocabulary_path.open("rb") as f:
            data: dict[str, Any] = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(
            f"tag vocabulary TOML parse failed: {exc}",
            details={"path": str(vocabulary_path), "parser_error": str(exc)},
        ) from exc

    # Reject unknown top-level keys.
    unknown_top = set(data.keys()) - _PERMITTED_TOP_LEVEL_KEYS
    if unknown_top:
        raise ConfigurationError(
            f"tag vocabulary has unknown top-level key(s): {sorted(unknown_top)}",
            details={
                "path": str(vocabulary_path),
                "unknown_keys": sorted(unknown_top),
                "permitted_keys": sorted(_PERMITTED_TOP_LEVEL_KEYS),
            },
        )

    tag_entries: list[dict[str, Any]] = data.get("tag", [])
    if not isinstance(tag_entries, list):
        raise ConfigurationError(
            "tag vocabulary 'tag' key must be an array of tables ([[tag]])",
            details={"path": str(vocabulary_path), "actual_type": type(tag_entries).__name__},
        )

    seen: set[str] = set()
    names: set[str] = set()
    for index, entry in enumerate(tag_entries):
        if not isinstance(entry, dict):
            raise ConfigurationError(
                f"tag vocabulary entry at index {index} is not a table",
                details={"path": str(vocabulary_path), "index": index},
            )

        # Reject unknown keys within the [[tag]] table (L3-SUB-009).
        unknown = set(entry.keys()) - _PERMITTED_TAG_KEYS
        if unknown:
            raise ConfigurationError(
                f"tag vocabulary entry at index {index} has unknown key(s): {sorted(unknown)}",
                details={
                    "path": str(vocabulary_path),
                    "index": index,
                    "unknown_keys": sorted(unknown),
                    "permitted_keys": sorted(_PERMITTED_TAG_KEYS),
                },
            )

        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ConfigurationError(
                f"tag vocabulary entry at index {index} missing required 'name' field",
                details={"path": str(vocabulary_path), "index": index},
            )

        # Regex validation per L3-SUB-010.
        if not _TAG_NAME_RE.match(name):
            raise ConfigurationError(
                f"tag name {name!r} does not match required pattern {_TAG_NAME_RE.pattern}",
                details={
                    "path": str(vocabulary_path),
                    "index": index,
                    "name": name,
                    "pattern": _TAG_NAME_RE.pattern,
                },
            )

        if name in seen:
            raise ConfigurationError(
                f"tag name {name!r} appears more than once in the vocabulary",
                details={"path": str(vocabulary_path), "name": name},
            )
        seen.add(name)
        names.add(name)

    return InMemoryTagVocabulary(frozenset(names))


__all__ = ["InMemoryTagVocabulary", "load_tag_vocabulary"]
