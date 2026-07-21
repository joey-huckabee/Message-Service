"""Unit tests for :mod:`message_service.infrastructure.templating.manifest_loader`."""

from __future__ import annotations

from pathlib import Path

import pytest

from message_service.domain.aggregates.template_metadata import TemplateKind, TemplateMetadata
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import ConfigurationError, UnknownTemplateError
from message_service.infrastructure.templating.manifest_loader import (
    InMemoryTemplateRepository,
    load_template_manifest,
)

# -----------------------------------------------------------------------------
# Test helpers
# -----------------------------------------------------------------------------


def _write_manifest(tmp_path: Path, body: str) -> Path:
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(body)
    return manifest


# -----------------------------------------------------------------------------
# InMemoryTemplateRepository direct construction
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-TMPL-001")
def test_repository_get_raises_on_unknown() -> None:
    repo = InMemoryTemplateRepository({})
    with pytest.raises(UnknownTemplateError) as exc_info:
        repo.get(TemplateRef(name="missing", version="1.0"))
    assert exc_info.value.details["name"] == "missing"
    assert exc_info.value.details["version"] == "1.0"


@pytest.mark.requirement("L2-TMPL-001")
def test_repository_exists_returns_false_on_unknown() -> None:
    repo = InMemoryTemplateRepository({})
    assert repo.exists(TemplateRef(name="missing", version="1.0")) is False


# -----------------------------------------------------------------------------
# resolve_latest (L3-TMPL-009 / L3-TMPL-010)
# -----------------------------------------------------------------------------


def _meta(name: str, version: str) -> TemplateMetadata:
    """Build a TemplateMetadata for resolve_latest tests."""
    return TemplateMetadata(
        name=name,
        version=version,
        kind=TemplateKind.REPORT_FRAGMENT,
        source_path=Path(f"/tmp/{name}-{version}.j2"),
        context_schema_path=None,
        description=None,
    )


@pytest.mark.requirement("L3-TMPL-010")
def test_resolve_latest_raises_unknown_template_error_for_missing_name() -> None:
    """L3-TMPL-010: empty manifest entries for the name SHALL raise."""
    repo = InMemoryTemplateRepository({})
    with pytest.raises(UnknownTemplateError) as exc_info:
        repo.resolve_latest("missing")
    assert exc_info.value.details["template_name"] == "missing"


@pytest.mark.requirement("L3-TMPL-009")
@pytest.mark.requirement("L3-TMPL-010")
def test_resolve_latest_returns_single_entry_when_only_one_version() -> None:
    """A single matching entry SHALL be returned as the resolved ref."""
    entries = {("nightly", "1.0.0"): _meta("nightly", "1.0.0")}
    repo = InMemoryTemplateRepository(entries)

    ref = repo.resolve_latest("nightly")

    assert ref.name == "nightly"
    assert ref.version == "1.0.0"


@pytest.mark.requirement("L3-TMPL-007")
@pytest.mark.requirement("L3-TMPL-009")
@pytest.mark.requirement("L3-TMPL-010")
def test_resolve_latest_picks_highest_semver_among_multiple() -> None:
    """L3-TMPL-010: multiple entries SHALL resolve to the highest semver."""
    entries = {
        ("nightly", "1.0.0"): _meta("nightly", "1.0.0"),
        ("nightly", "2.0.0"): _meta("nightly", "2.0.0"),
        ("nightly", "1.5.3"): _meta("nightly", "1.5.3"),
    }
    repo = InMemoryTemplateRepository(entries)

    ref = repo.resolve_latest("nightly")

    assert ref.version == "2.0.0"


@pytest.mark.requirement("L2-TMPL-004")
@pytest.mark.requirement("L3-TMPL-007")
@pytest.mark.requirement("L3-TMPL-010")
def test_resolve_latest_orders_pre_release_below_final() -> None:
    """Pre-release versions SHALL order below their corresponding final (PEP 440).

    L2-TMPL-004: version strings are parsed and compared via
    ``packaging.version.Version`` — a lexical compare would order ``1.0.0rc1``
    *after* ``1.0.0``; PEP 440 semantics put the pre-release below the final.

    Both ``1.0.0rc1`` and ``1.0.0`` are valid manifest entries; the
    final version SHALL win regardless of insertion order.
    """
    entries = {
        ("nightly", "1.0.0rc1"): _meta("nightly", "1.0.0rc1"),
        ("nightly", "1.0.0"): _meta("nightly", "1.0.0"),
    }
    repo = InMemoryTemplateRepository(entries)

    ref = repo.resolve_latest("nightly")

    assert ref.version == "1.0.0"


@pytest.mark.requirement("L3-TMPL-009")
def test_resolve_latest_returns_original_manifest_version_string() -> None:
    """The resolved ref SHALL carry the *original* manifest version string.

    ``packaging.Version`` canonicalizes on parse, but the manifest is
    keyed by the exact stored string. resolve_latest compares by parsed
    Version yet must return the stored key so a subsequent
    ``exists()`` / ``get()`` lookup succeeds.
    """
    entries = {("nightly", "v2.0.0"): _meta("nightly", "v2.0.0")}
    repo = InMemoryTemplateRepository(entries)

    ref = repo.resolve_latest("nightly")

    # Canonical form would be "2.0.0"; the stored key is "v2.0.0".
    assert ref.version == "v2.0.0"
    assert repo.exists(ref)


@pytest.mark.requirement("L3-TMPL-009")
@pytest.mark.requirement("L3-TMPL-010")
def test_resolve_latest_result_is_a_valid_manifest_key_when_version_non_canonical() -> None:
    """A non-canonical winning version SHALL still round-trip through get().

    ``"1.0.0-alpha"`` canonicalizes to ``"1.0.0a0"`` — returning the
    canonical form would raise ``UnknownTemplateError`` for a template
    that exists. The highest here is the final ``"1.0.0"``; but when the
    only entry is the pre-release, the stored key must come back intact.
    """
    entries = {("nightly", "1.0.0-alpha"): _meta("nightly", "1.0.0-alpha")}
    repo = InMemoryTemplateRepository(entries)

    ref = repo.resolve_latest("nightly")

    assert ref.version == "1.0.0-alpha"
    # The returned ref is a real manifest key: get() must not raise.
    assert repo.get(ref).version == "1.0.0-alpha"


@pytest.mark.requirement("L3-TMPL-010")
def test_resolve_latest_filters_by_name_only_other_names_ignored() -> None:
    """Only manifest entries with the matching name SHALL be considered."""
    entries = {
        ("nightly", "1.0.0"): _meta("nightly", "1.0.0"),
        ("daily", "9.9.9"): _meta("daily", "9.9.9"),  # different name; SHALL be ignored
    }
    repo = InMemoryTemplateRepository(entries)

    ref = repo.resolve_latest("nightly")

    assert ref.name == "nightly"
    assert ref.version == "1.0.0"  # not 9.9.9


# -----------------------------------------------------------------------------
# Loader happy path
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-TMPL-001")
@pytest.mark.requirement("L3-TMPL-003")
def test_loader_reads_minimal_manifest(tmp_path: Path) -> None:
    (tmp_path / "nightly.html.j2").write_text("<html></html>")
    manifest = _write_manifest(
        tmp_path,
        """
[[template]]
name = "nightly"
version = "1.0"
kind = "AGGREGATION"
source_path = "nightly.html.j2"
""",
    )
    repo = load_template_manifest(manifest)
    ref = TemplateRef(name="nightly", version="1.0")
    assert repo.exists(ref) is True
    meta = repo.get(ref)
    assert meta.kind == TemplateKind.AGGREGATION
    assert meta.source_path == tmp_path / "nightly.html.j2"
    assert meta.description is None


@pytest.mark.requirement("L3-TMPL-001")
def test_loader_accepts_all_template_kinds(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        """
[[template]]
name = "frag"
version = "1.0"
kind = "REPORT_FRAGMENT"
source_path = "frag.html.j2"

[[template]]
name = "agg"
version = "1.0"
kind = "AGGREGATION"
source_path = "agg.html.j2"

[[template]]
name = "body"
version = "1.0"
kind = "EMAIL_BODY"
source_path = "body.html.j2"
""",
    )
    repo = load_template_manifest(manifest)
    assert len(repo.list_by_kind(TemplateKind.REPORT_FRAGMENT)) == 1
    assert len(repo.list_by_kind(TemplateKind.AGGREGATION)) == 1
    assert len(repo.list_by_kind(TemplateKind.EMAIL_BODY)) == 1


@pytest.mark.requirement("L2-TMPL-001")
def test_loader_resolves_relative_source_path(tmp_path: Path) -> None:
    subdir = tmp_path / "fragments"
    subdir.mkdir()
    manifest = _write_manifest(
        tmp_path,
        """
[[template]]
name = "x"
version = "1.0"
kind = "REPORT_FRAGMENT"
source_path = "fragments/x.html.j2"
""",
    )
    repo = load_template_manifest(manifest)
    meta = repo.get(TemplateRef(name="x", version="1.0"))
    # Relative path resolved against manifest's directory.
    assert meta.source_path == tmp_path / "fragments" / "x.html.j2"


@pytest.mark.requirement("L2-TMPL-001")
def test_loader_preserves_absolute_source_path(tmp_path: Path) -> None:
    absolute = tmp_path / "absolute.html.j2"
    manifest = _write_manifest(
        tmp_path,
        f"""
[[template]]
name = "abs"
version = "1.0"
kind = "REPORT_FRAGMENT"
source_path = "{absolute.as_posix()}"
""",
    )
    repo = load_template_manifest(manifest)
    meta = repo.get(TemplateRef(name="abs", version="1.0"))
    assert meta.source_path == absolute


@pytest.mark.requirement("L2-TMPL-001")
def test_loader_accepts_empty_manifest(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, "# empty\n")
    repo = load_template_manifest(manifest)
    assert repo.list_by_kind(TemplateKind.REPORT_FRAGMENT) == ()


# -----------------------------------------------------------------------------
# Loader error paths
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-TMPL-002")
def test_loader_raises_when_file_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    with pytest.raises(ConfigurationError, match="not found"):
        load_template_manifest(missing)


@pytest.mark.requirement("L3-TMPL-002")
def test_loader_raises_on_malformed_toml(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, "this is [[[ not valid")
    with pytest.raises(ConfigurationError) as exc_info:
        load_template_manifest(manifest)
    assert "parser_error" in exc_info.value.details


@pytest.mark.requirement("L3-TMPL-001")
def test_loader_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        """
[[template]]
name = "x"
version = "1.0"
kind = "AGGREGATION"
source_path = "x.html.j2"

[other_section]
key = "value"
""",
    )
    with pytest.raises(ConfigurationError, match="unknown top-level key"):
        load_template_manifest(manifest)


@pytest.mark.requirement("L3-TMPL-001")
def test_loader_rejects_unknown_template_key(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        """
[[template]]
name = "x"
version = "1.0"
kind = "AGGREGATION"
source_path = "x.html.j2"
category = "not-allowed"
""",
    )
    with pytest.raises(ConfigurationError) as exc_info:
        load_template_manifest(manifest)
    assert "category" in exc_info.value.details["unknown_keys"]


@pytest.mark.requirement("L3-TMPL-001")
@pytest.mark.parametrize("missing_field", ["name", "version", "kind", "source_path"])
def test_loader_rejects_missing_required_field(tmp_path: Path, missing_field: str) -> None:
    fields = {
        "name": '"x"',
        "version": '"1.0"',
        "kind": '"AGGREGATION"',
        "source_path": '"x.html.j2"',
    }
    fields.pop(missing_field)
    body = "[[template]]\n" + "\n".join(f"{k} = {v}" for k, v in fields.items())
    manifest = _write_manifest(tmp_path, body)
    with pytest.raises(ConfigurationError) as exc_info:
        load_template_manifest(manifest)
    assert exc_info.value.details["missing_field"] == missing_field


@pytest.mark.requirement("L3-TMPL-001")
def test_loader_rejects_unknown_kind(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        """
[[template]]
name = "x"
version = "1.0"
kind = "INVALID_KIND"
source_path = "x.html.j2"
""",
    )
    with pytest.raises(ConfigurationError) as exc_info:
        load_template_manifest(manifest)
    assert exc_info.value.details["kind"] == "INVALID_KIND"


@pytest.mark.requirement("L2-TMPL-001")
@pytest.mark.requirement("L3-TMPL-006")
@pytest.mark.requirement("L3-TMPL-008")
def test_loader_rejects_duplicate_name_version(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        """
[[template]]
name = "nightly"
version = "1.0"
kind = "AGGREGATION"
source_path = "nightly1.html.j2"

[[template]]
name = "nightly"
version = "1.0"
kind = "REPORT_FRAGMENT"
source_path = "nightly2.html.j2"
""",
    )
    with pytest.raises(ConfigurationError, match="duplicate"):
        load_template_manifest(manifest)


@pytest.mark.requirement("L2-TMPL-001")
def test_loader_allows_multiple_versions_of_same_name(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        """
[[template]]
name = "nightly"
version = "1.0"
kind = "AGGREGATION"
source_path = "nightly_v1.html.j2"

[[template]]
name = "nightly"
version = "2.0"
kind = "AGGREGATION"
source_path = "nightly_v2.html.j2"
""",
    )
    repo = load_template_manifest(manifest)
    assert repo.exists(TemplateRef(name="nightly", version="1.0"))
    assert repo.exists(TemplateRef(name="nightly", version="2.0"))
