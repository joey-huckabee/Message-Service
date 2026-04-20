"""Descriptor for a registered Jinja2 template loaded from the manifest.

The manifest (at ``templates.manifest_path``) enumerates every template
the service knows about. Each manifest entry becomes a
:class:`TemplateMetadata` instance. The Jinja2 source itself is loaded
lazily by the template-rendering adapter.

Requirement references
----------------------
L2-TMPL-001, L2-TMPL-002, L2-TMPL-003
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class TemplateKind(StrEnum):
    """What the template produces.

    ``REPORT_FRAGMENT``: stage-level report contribution (becomes an
    HTML attachment fragment, either composite via the aggregation
    template or standalone in PER_STAGE mode).

    ``AGGREGATION``: assembles stage report fragments into a single
    composite attachment in ``SINGLE_AGGREGATED`` mode.

    ``EMAIL_BODY``: the email body itself. Stages contribute per-stage
    email-body context; the body template renders with the
    concatenated/merged context at assembly time.
    """

    REPORT_FRAGMENT = "REPORT_FRAGMENT"
    AGGREGATION = "AGGREGATION"
    EMAIL_BODY = "EMAIL_BODY"


@dataclass(frozen=True, slots=True)
class TemplateMetadata:
    """A manifest entry describing a registered template.

    Attributes:
        name: Manifest identifier referenced by submitters.
        version: Manifest-declared version.
        kind: What the template produces.
        source_path: Filesystem path to the Jinja2 source, resolved
            against the manifest's directory at load time.
        context_schema_path: Filesystem path to the JSON Schema document
            validating this template's context, resolved at load time.
            ``None`` if the template accepts any context.
        description: Optional human-readable blurb from the manifest.
    """

    name: str
    version: str
    kind: TemplateKind
    source_path: Path
    context_schema_path: Path | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        """Validate non-empty name/version.

        Raises:
            ValueError: If ``name`` or ``version`` is empty.
        """
        if not self.name:
            raise ValueError("TemplateMetadata.name must be non-empty")
        if not self.version:
            raise ValueError("TemplateMetadata.version must be non-empty")


__all__ = ["TemplateKind", "TemplateMetadata"]
