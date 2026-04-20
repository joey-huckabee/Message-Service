"""Port: access to the controlled tag vocabulary.

The vocabulary is loaded from ``tags.vocabulary_path`` at service
start (L2-SUB-006) and is never hot-reloaded in v1. This port exposes
read-only containment and enumeration; the adapter that owns the
loaded set is trivially small and lives in ``infrastructure/``.

Why this is a port at all, rather than a loaded ``frozenset[str]``
passed around directly: future implementations may source the
vocabulary from something other than a TOML file (LDAP group list,
database table). The port keeps use cases agnostic.

Requirement references
----------------------
L2-SUB-006
L3-SUB-009, L3-SUB-010
L3-RUN-012, L3-RUN-013
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class TagVocabulary(ABC):
    """Abstract controlled tag set.

    Implementations MUST:

    * Return ``True`` from :meth:`contains` only for tags present in
      the loaded vocabulary, with no normalization (L3-RUN-012:
      case-sensitive exact match).
    * Make :meth:`all_tags` idempotent and cheap — the common call
      site is the dashboard rendering a picker.
    """

    @abstractmethod
    def contains(self, tag: str) -> bool:
        """Return whether ``tag`` is in the vocabulary.

        Args:
            tag: The tag name to check. Compared case-sensitively.

        Returns:
            ``True`` iff the tag is registered in the vocabulary.
        """

    @abstractmethod
    def all_tags(self) -> frozenset[str]:
        """Return the entire registered vocabulary.

        Returns:
            Frozen set of every registered tag name. Empty if the
            vocabulary file declared no tags.
        """


__all__ = ["TagVocabulary"]
