"""Unit tests for JSON Schema context validation in the Jinja2 renderer.

Covers L1-TMPL-004 / L2-TMPL-010 / L2-TMPL-011 and L3 children
L3-TMPL-018, 020, 029, 030, 031, 032.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from jsonschema import Draft202012Validator

from message_service.application.ports.template_repository import TemplateRepository
from message_service.domain.aggregates.template_metadata import (
    TemplateKind,
    TemplateMetadata,
)
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import (
    ConfigurationError,
    ContextSchemaViolationError,
)
from message_service.infrastructure.templating.renderer import (
    Jinja2SandboxedTemplateRenderer,
    _to_json_pointer,
)

_REF = TemplateRef(name="sample", version="1.0")
_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "items": {
            "type": "array",
            "items": {"type": "object", "properties": {"id": {"type": "integer"}}},
        },
    },
    "required": ["name"],
}


def _make_repo(meta: TemplateMetadata) -> MagicMock:
    r = MagicMock(spec=TemplateRepository)
    r.get.return_value = meta
    r.list_all.return_value = (meta,)
    return r


def _write_template(tmp_path: Path) -> Path:
    src = tmp_path / "tpl.html.j2"
    src.write_text("<html>{{ name }}</html>", encoding="utf-8")
    return src


def _write_schema(tmp_path: Path, doc: dict[str, object] | str) -> Path:
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return schema_path


def _meta(tmp_path: Path, schema_path: Path | None) -> TemplateMetadata:
    return TemplateMetadata(
        name=_REF.name,
        version=_REF.version,
        kind=TemplateKind.REPORT_FRAGMENT,
        source_path=_write_template(tmp_path),
        context_schema_path=schema_path,
    )


# -----------------------------------------------------------------------------
# L3-TMPL-018: validators built eagerly at construction, one per template
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-TMPL-018")
def test_validator_constructed_eagerly_at_renderer_init(tmp_path: Path) -> None:
    """Renderer SHALL build a Draft202012Validator per schemed template at init."""
    schema_path = _write_schema(tmp_path, _SCHEMA)
    repo = _make_repo(_meta(tmp_path, schema_path))

    renderer = Jinja2SandboxedTemplateRenderer(
        repository=repo, max_context_bytes=10_000, max_rendered_bytes=10_000
    )

    cached = renderer._validators[(_REF.name, _REF.version)]
    assert isinstance(cached, Draft202012Validator)
    # list_all queried exactly once at construction.
    assert repo.list_all.call_count == 1


@pytest.mark.requirement("L3-TMPL-018")
def test_same_validator_reused_across_renders(tmp_path: Path) -> None:
    """A single validator instance SHALL serve every render of the same ref."""
    schema_path = _write_schema(tmp_path, _SCHEMA)
    repo = _make_repo(_meta(tmp_path, schema_path))
    renderer = Jinja2SandboxedTemplateRenderer(
        repository=repo, max_context_bytes=10_000, max_rendered_bytes=10_000
    )
    first = renderer._validators[(_REF.name, _REF.version)]
    renderer.render(_REF, {"name": "alice"})
    renderer.render(_REF, {"name": "bob"})
    second = renderer._validators[(_REF.name, _REF.version)]
    assert first is second
    # list_all called once at construction; subsequent renders SHALL NOT
    # re-scan the manifest.
    assert repo.list_all.call_count == 1


# -----------------------------------------------------------------------------
# L3-TMPL-029: validation runs at render time, not at construction
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-TMPL-029")
def test_validation_runs_at_render_not_at_construction(tmp_path: Path) -> None:
    """Construction SHALL succeed even when a future render would violate."""
    schema_path = _write_schema(tmp_path, _SCHEMA)
    repo = _make_repo(_meta(tmp_path, schema_path))

    # Construction does NOT raise — it only compiles the schema.
    renderer = Jinja2SandboxedTemplateRenderer(
        repository=repo, max_context_bytes=10_000, max_rendered_bytes=10_000
    )

    # Bad context is rejected only when render is called.
    with pytest.raises(ContextSchemaViolationError):
        renderer.render(_REF, {})  # missing required "name"


# -----------------------------------------------------------------------------
# L3-TMPL-030: templates without context_schema_path skip validation
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-TMPL-030")
def test_template_without_schema_skips_validation(tmp_path: Path) -> None:
    """Manifest entry with context_schema_path=None SHALL bypass validation."""
    repo = _make_repo(_meta(tmp_path, schema_path=None))
    renderer = Jinja2SandboxedTemplateRenderer(
        repository=repo, max_context_bytes=10_000, max_rendered_bytes=10_000
    )
    # No validator was registered for this ref.
    assert (_REF.name, _REF.version) not in renderer._validators
    # Render with arbitrary context — schema would have rejected this if
    # one were registered, but the renderer SHALL pass it through.
    out = renderer.render(_REF, {"name": "alice", "junk": [1, 2, 3]})
    assert "alice" in out


# -----------------------------------------------------------------------------
# L3-TMPL-031: bad schemas raise ConfigurationError at construction
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-TMPL-005")
@pytest.mark.requirement("L3-TMPL-031")
def test_missing_schema_file_raises_configuration_error(tmp_path: Path) -> None:
    repo = _make_repo(_meta(tmp_path, schema_path=tmp_path / "missing.json"))
    with pytest.raises(ConfigurationError) as exc_info:
        Jinja2SandboxedTemplateRenderer(
            repository=repo, max_context_bytes=10_000, max_rendered_bytes=10_000
        )
    assert exc_info.value.details["name"] == _REF.name
    assert exc_info.value.details["version"] == _REF.version
    assert "missing.json" in exc_info.value.details["schema_path"]


@pytest.mark.requirement("L3-TMPL-031")
def test_malformed_json_schema_raises_configuration_error(tmp_path: Path) -> None:
    schema_path = _write_schema(tmp_path, "{not: valid json")
    repo = _make_repo(_meta(tmp_path, schema_path))
    with pytest.raises(ConfigurationError) as exc_info:
        Jinja2SandboxedTemplateRenderer(
            repository=repo, max_context_bytes=10_000, max_rendered_bytes=10_000
        )
    assert "valid JSON" in str(exc_info.value)
    assert exc_info.value.details["name"] == _REF.name


@pytest.mark.requirement("L3-TMPL-031")
def test_schema_failing_meta_schema_raises_configuration_error(tmp_path: Path) -> None:
    # `type: "imaginary"` is not a valid Draft 2020-12 type keyword.
    schema_path = _write_schema(tmp_path, {"type": "imaginary"})
    repo = _make_repo(_meta(tmp_path, schema_path))
    with pytest.raises(ConfigurationError) as exc_info:
        Jinja2SandboxedTemplateRenderer(
            repository=repo, max_context_bytes=10_000, max_rendered_bytes=10_000
        )
    assert "meta-schema" in str(exc_info.value)
    assert exc_info.value.details["name"] == _REF.name


# -----------------------------------------------------------------------------
# L3-TMPL-032: JSON Pointer derivation
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-TMPL-032")
def test_json_pointer_helper_root_violation_renders_empty() -> None:
    assert _to_json_pointer([]) == ""


@pytest.mark.requirement("L3-TMPL-032")
def test_json_pointer_helper_nested_path() -> None:
    assert _to_json_pointer(["foo", "bar", 0]) == "/foo/bar/0"


@pytest.mark.requirement("L3-TMPL-032")
def test_json_pointer_helper_escapes_special_chars() -> None:
    """RFC 6901: '~' → '~0', '/' → '~1' inside segments."""
    assert _to_json_pointer(["a/b", "c~d"]) == "/a~1b/c~0d"


@pytest.mark.requirement("L3-TMPL-032")
def test_validation_error_carries_json_pointer_for_nested_violation(
    tmp_path: Path,
) -> None:
    schema_path = _write_schema(tmp_path, _SCHEMA)
    repo = _make_repo(_meta(tmp_path, schema_path))
    renderer = Jinja2SandboxedTemplateRenderer(
        repository=repo, max_context_bytes=10_000, max_rendered_bytes=10_000
    )

    # `items[1].id` is a string instead of an integer — the path should
    # render as "/items/1/id".
    bad = {"name": "alice", "items": [{"id": 1}, {"id": "oops"}]}
    with pytest.raises(ContextSchemaViolationError) as exc_info:
        renderer.render(_REF, bad)

    details = exc_info.value.details
    assert details["json_pointer"] == "/items/1/id"
    assert details["validator"] == "type"
    assert details["instance_value"] == "oops"
    assert "message" in details


# -----------------------------------------------------------------------------
# L3-TMPL-020: details fields complete
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-TMPL-020")
def test_details_contains_all_required_fields(tmp_path: Path) -> None:
    schema_path = _write_schema(tmp_path, _SCHEMA)
    repo = _make_repo(_meta(tmp_path, schema_path))
    renderer = Jinja2SandboxedTemplateRenderer(
        repository=repo, max_context_bytes=10_000, max_rendered_bytes=10_000
    )
    with pytest.raises(ContextSchemaViolationError) as exc_info:
        renderer.render(_REF, {})  # missing "name"
    details = exc_info.value.details
    for required in ("name", "version", "json_pointer", "validator", "instance_value", "message"):
        assert required in details, f"missing details field: {required}"


# -----------------------------------------------------------------------------
# Ordering invariant: size check runs BEFORE schema validation (L3-TMPL-022)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-TMPL-022")
def test_size_check_runs_before_schema_validation(tmp_path: Path) -> None:
    """An oversized context that ALSO violates the schema SHALL raise size first."""
    from message_service.domain.errors import ContextSizeExceededError

    schema_path = _write_schema(tmp_path, _SCHEMA)
    repo = _make_repo(_meta(tmp_path, schema_path))
    # Tiny context limit guaranteed to trigger size first.
    renderer = Jinja2SandboxedTemplateRenderer(
        repository=repo, max_context_bytes=10, max_rendered_bytes=10_000
    )
    # This context would also fail schema (missing "name"), but size
    # check runs first.
    with pytest.raises(ContextSizeExceededError):
        renderer.render(_REF, {"junk": "x" * 100})


# -----------------------------------------------------------------------------
# L3-TMPL-019: $ref resolution
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-TMPL-019")
def test_self_referencing_internal_defs_resolve(tmp_path: Path) -> None:
    """`$ref` to an internal `$defs` SHALL resolve and validate.

    Note: external-file `$ref` resolution is jsonschema-library territory
    and would require a configured retrieval handler; v1 supports only
    internal `$defs` references which is what real templates use.
    """
    schema_with_ref: dict[str, object] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"item": {"$ref": "#/$defs/item"}, "name": {"type": "string"}},
        "required": ["name", "item"],
        "$defs": {
            "item": {
                "type": "object",
                "properties": {"id": {"type": "integer"}},
                "required": ["id"],
            }
        },
    }
    schema_path = _write_schema(tmp_path, schema_with_ref)
    repo = _make_repo(_meta(tmp_path, schema_path))
    renderer = Jinja2SandboxedTemplateRenderer(
        repository=repo, max_context_bytes=10_000, max_rendered_bytes=10_000
    )

    # Valid case — both fields and the ref-resolved sub-object validate.
    out = renderer.render(_REF, {"name": "alice", "item": {"id": 7}})
    assert "alice" in out

    # Invalid sub-object (id is a string) — pointer points into the ref.
    with pytest.raises(ContextSchemaViolationError) as exc_info:
        renderer.render(_REF, {"name": "alice", "item": {"id": "x"}})
    assert exc_info.value.details["json_pointer"] == "/item/id"


# -----------------------------------------------------------------------------
# Happy path: valid context renders
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L1-TMPL-004")
def test_valid_context_renders_successfully(tmp_path: Path) -> None:
    schema_path = _write_schema(tmp_path, _SCHEMA)
    repo = _make_repo(_meta(tmp_path, schema_path))
    renderer = Jinja2SandboxedTemplateRenderer(
        repository=repo, max_context_bytes=10_000, max_rendered_bytes=10_000
    )
    result = renderer.render(_REF, {"name": "alice", "items": [{"id": 1}]})
    assert "alice" in result
