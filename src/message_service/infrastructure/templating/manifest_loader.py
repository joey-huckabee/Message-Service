"""Template manifest loading and :class:`TemplateRepository` adapter.

The template manifest file format (derived from L3-TMPL-001 and the
:class:`TemplateMetadata` schema):

.. code-block:: toml

    [[template]]
    name = "nightly_summary"
    version = "1.0.0"
    kind = "AGGREGATION"
    source_path = "aggregation/nightly_summary.html.j2"
    # optional:
    # context_schema_path = "schemas/nightly_summary.json"
    # description = "Aggregates all stage reports for nightly ETL"

All ``source_path`` and ``context_schema_path`` values are resolved
relative to the manifest file's parent directory unless absolute.

Duplicate ``(name, version)`` pairs raise :class:`ConfigurationError`.

The manifest is loaded once at service start (L2-TMPL-001). No
hot-reload — a restart is required to pick up changes.

Requirement references
----------------------
L2-TMPL-001, L2-TMPL-002, L2-TMPL-003
L3-TMPL-001 (tomllib)
L3-TMPL-002 (parse-failure error shape)
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from message_service.application.ports.template_repository import TemplateRepository
from message_service.domain.aggregates.template_metadata import (
    TemplateKind,
    TemplateMetadata,
)
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import ConfigurationError, UnknownTemplateError

# Permitted keys on a [[template]] table.
_PERMITTED_TEMPLATE_KEYS: frozenset[str] = frozenset(
    {"name", "version", "kind", "source_path", "context_schema_path", "description"}
)

_PERMITTED_TOP_LEVEL_KEYS: frozenset[str] = frozenset({"template"})


class InMemoryTemplateRepository(TemplateRepository):
    """:class:`TemplateRepository` backed by an in-memory dict.

    Instances are typically built via :func:`load_template_manifest`.
    The constructor is available for tests and alternative sources.

    Lookup complexity: O(1) amortized via ``dict``-keyed ``(name, version)``
    tuples.
    """

    def __init__(self, entries: dict[tuple[str, str], TemplateMetadata]) -> None:
        """Construct from a pre-indexed dict of ``(name, version) -> metadata``.

        Args:
            entries: Pre-validated manifest entries.
        """
        self._entries = entries
        # Secondary index by kind for :meth:`list_by_kind`. Built once
        # at construction to avoid scanning on every call.
        by_kind: dict[TemplateKind, list[TemplateMetadata]] = {k: [] for k in TemplateKind}
        for meta in entries.values():
            by_kind[meta.kind].append(meta)
        self._by_kind: dict[TemplateKind, tuple[TemplateMetadata, ...]] = {
            k: tuple(v) for k, v in by_kind.items()
        }

    def get(self, ref: TemplateRef) -> TemplateMetadata:  # noqa: D102
        meta = self._entries.get((ref.name, ref.version))
        if meta is None:
            raise UnknownTemplateError(
                f"template not found: {ref.name!r} @ {ref.version!r}",
                details={"name": ref.name, "version": ref.version},
            )
        return meta

    def exists(self, ref: TemplateRef) -> bool:  # noqa: D102
        return (ref.name, ref.version) in self._entries

    def list_by_kind(self, kind: TemplateKind) -> Sequence[TemplateMetadata]:  # noqa: D102
        return self._by_kind.get(kind, ())

    def list_all(self) -> Sequence[TemplateMetadata]:  # noqa: D102
        # Sort deterministically by (name, version) per L3-DASH-031.
        return tuple(sorted(self._entries.values(), key=lambda m: (m.name, m.version)))


def load_template_manifest(manifest_path: Path) -> InMemoryTemplateRepository:
    """Load and validate a template manifest TOML file.

    Args:
        manifest_path: Absolute or pre-resolved path to the manifest
            file (typically ``config.templates.manifest_path``).

    Returns:
        An :class:`InMemoryTemplateRepository` ready for use.

    Raises:
        ConfigurationError: Any of the following:

            * File does not exist or cannot be parsed as TOML.
            * Top-level key other than ``template`` present.
            * ``[[template]]`` entry missing a required field, or
              containing an unknown key.
            * ``kind`` value is not a recognized
              :class:`TemplateKind`.
            * Duplicate ``(name, version)`` entries.
    """
    if not manifest_path.exists():
        raise ConfigurationError(
            f"template manifest file not found: {manifest_path}",
            details={"path": str(manifest_path)},
        )

    try:
        with manifest_path.open("rb") as f:
            data: dict[str, Any] = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        # L3-TMPL-002: include parse error location in details.
        raise ConfigurationError(
            f"template manifest TOML parse failed: {exc}",
            details={"path": str(manifest_path), "parser_error": str(exc)},
        ) from exc

    unknown_top = set(data.keys()) - _PERMITTED_TOP_LEVEL_KEYS
    if unknown_top:
        raise ConfigurationError(
            f"template manifest has unknown top-level key(s): {sorted(unknown_top)}",
            details={
                "path": str(manifest_path),
                "unknown_keys": sorted(unknown_top),
                "permitted_keys": sorted(_PERMITTED_TOP_LEVEL_KEYS),
            },
        )

    template_entries: list[dict[str, Any]] = data.get("template", [])
    if not isinstance(template_entries, list):
        raise ConfigurationError(
            "template manifest 'template' key must be an array of tables ([[template]])",
            details={
                "path": str(manifest_path),
                "actual_type": type(template_entries).__name__,
            },
        )

    manifest_dir = manifest_path.parent
    entries: dict[tuple[str, str], TemplateMetadata] = {}
    for index, entry in enumerate(template_entries):
        if not isinstance(entry, dict):
            raise ConfigurationError(
                f"template manifest entry at index {index} is not a table",
                details={"path": str(manifest_path), "index": index},
            )

        unknown = set(entry.keys()) - _PERMITTED_TEMPLATE_KEYS
        if unknown:
            raise ConfigurationError(
                f"template manifest entry at index {index} has unknown key(s): {sorted(unknown)}",
                details={
                    "path": str(manifest_path),
                    "index": index,
                    "unknown_keys": sorted(unknown),
                    "permitted_keys": sorted(_PERMITTED_TEMPLATE_KEYS),
                },
            )

        # Required fields.
        for required in ("name", "version", "kind", "source_path"):
            if required not in entry:
                raise ConfigurationError(
                    f"template manifest entry at index {index} missing required field {required!r}",
                    details={
                        "path": str(manifest_path),
                        "index": index,
                        "missing_field": required,
                    },
                )

        # kind parsing
        kind_str = entry["kind"]
        try:
            kind = TemplateKind(kind_str)
        except ValueError as exc:
            raise ConfigurationError(
                f"template manifest entry at index {index} has unknown kind {kind_str!r}",
                details={
                    "path": str(manifest_path),
                    "index": index,
                    "kind": kind_str,
                    "permitted_kinds": sorted(k.value for k in TemplateKind),
                },
            ) from exc

        # Resolve paths relative to the manifest's directory.
        source_path = _resolve(manifest_dir, entry["source_path"])
        context_schema_path = (
            _resolve(manifest_dir, entry["context_schema_path"])
            if "context_schema_path" in entry
            else None
        )

        try:
            meta = TemplateMetadata(
                name=entry["name"],
                version=entry["version"],
                kind=kind,
                source_path=source_path,
                context_schema_path=context_schema_path,
                description=entry.get("description"),
            )
        except (ValueError, TypeError) as exc:
            raise ConfigurationError(
                f"template manifest entry at index {index} failed validation: {exc}",
                details={"path": str(manifest_path), "index": index, "reason": str(exc)},
            ) from exc

        key = (meta.name, meta.version)
        if key in entries:
            raise ConfigurationError(
                f"template manifest has duplicate entry for ({meta.name!r}, {meta.version!r})",
                details={
                    "path": str(manifest_path),
                    "index": index,
                    "name": meta.name,
                    "version": meta.version,
                },
            )
        entries[key] = meta

    return InMemoryTemplateRepository(entries)


def _resolve(base: Path, p: str) -> Path:
    """Resolve ``p`` relative to ``base`` if not already absolute."""
    path = Path(p)
    return path if path.is_absolute() else (base / path)


__all__ = ["InMemoryTemplateRepository", "load_template_manifest"]
