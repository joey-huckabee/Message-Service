"""A pointer to a specific version of a registered Jinja2 template.

Templates are not content; they are names. Submitters reference a
template by ``(name, version)``; the service resolves the pair against
its manifest (loaded from ``templates.manifest_path``) to locate the
Jinja2 source file.

Validation of whether a :class:`TemplateRef` exists is the responsibility
of the :class:`~message_service.application.ports.template_repository.TemplateRepository`
port; this dataclass is a parsed-but-unresolved reference.

Requirement references
----------------------
L2-TMPL-001, L2-TMPL-002
L3-RUN-016, L3-RUN-017
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TemplateRef:
    """A ``(name, version)`` pointer to a registered template.

    Equality and hashing follow value semantics — two refs with the same
    name and version are equal regardless of construction path.

    Attributes:
        name: Template identifier from the manifest (e.g.,
            ``"nightly_summary_attachment"``).
        version: Template version from the manifest. Free-form string
            to permit either semver (``"1.2.3"``) or date-stamped
            (``"2026.03.15"``) versioning schemes.
    """

    name: str
    version: str

    def __post_init__(self) -> None:
        """Validate non-empty fields.

        Raises:
            ValueError: If either field is empty.
        """
        if not self.name:
            raise ValueError("TemplateRef.name must be non-empty")
        if not self.version:
            raise ValueError("TemplateRef.version must be non-empty")


__all__ = ["TemplateRef"]
