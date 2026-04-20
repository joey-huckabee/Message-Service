"""Unit tests for :mod:`message_service.domain.aggregates.template_metadata`."""

from __future__ import annotations

from pathlib import Path

import pytest

from message_service.domain.aggregates.template_metadata import TemplateKind, TemplateMetadata


def _meta(**overrides: object) -> TemplateMetadata:
    fields: dict[str, object] = {
        "name": "nightly_summary",
        "version": "1.0.0",
        "kind": TemplateKind.REPORT_FRAGMENT,
        "source_path": Path("/templates/nightly_summary.j2"),
    }
    fields.update(overrides)
    return TemplateMetadata(**fields)  # type: ignore[arg-type]


@pytest.mark.requirement("L2-TMPL-001")
def test_template_metadata_constructs_with_valid_values() -> None:
    m = _meta()
    assert m.name == "nightly_summary"
    assert m.kind == TemplateKind.REPORT_FRAGMENT


@pytest.mark.requirement("L2-TMPL-001")
def test_template_metadata_is_frozen() -> None:
    m = _meta()
    with pytest.raises((AttributeError, TypeError)):
        m.name = "other"  # type: ignore[misc]


@pytest.mark.requirement("L2-TMPL-001")
def test_optional_fields_default_to_none() -> None:
    m = _meta()
    assert m.context_schema_path is None
    assert m.description is None


@pytest.mark.requirement("L2-TMPL-001")
@pytest.mark.parametrize(
    ("name", "version"),
    [("", "1.0"), ("foo", ""), ("", "")],
)
def test_rejects_empty_name_or_version(name: str, version: str) -> None:
    with pytest.raises(ValueError):
        _meta(name=name, version=version)


@pytest.mark.requirement("L2-TMPL-002")
@pytest.mark.parametrize(
    "kind",
    [TemplateKind.REPORT_FRAGMENT, TemplateKind.AGGREGATION, TemplateKind.EMAIL_BODY],
)
def test_all_template_kinds_construct(kind: TemplateKind) -> None:
    m = _meta(kind=kind)
    assert kind == m.kind
