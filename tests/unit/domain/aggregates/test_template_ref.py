"""Unit tests for :mod:`message_service.domain.aggregates.template_ref`."""

from __future__ import annotations

import pytest

from message_service.domain.aggregates.template_ref import TemplateRef


@pytest.mark.requirement("L2-TMPL-001")
def test_template_ref_constructs_with_valid_values() -> None:
    ref = TemplateRef(name="nightly_summary", version="1.0.0")
    assert ref.name == "nightly_summary"
    assert ref.version == "1.0.0"


@pytest.mark.requirement("L2-TMPL-001")
def test_template_ref_is_frozen() -> None:
    ref = TemplateRef(name="x", version="1")
    with pytest.raises((AttributeError, TypeError)):
        ref.name = "y"  # type: ignore[misc]


@pytest.mark.requirement("L2-TMPL-001")
def test_template_ref_equality_is_value_based() -> None:
    a = TemplateRef(name="foo", version="2.0.0")
    b = TemplateRef(name="foo", version="2.0.0")
    c = TemplateRef(name="foo", version="2.0.1")
    assert a == b
    assert a != c
    assert hash(a) == hash(b)


@pytest.mark.requirement("L2-TMPL-001")
@pytest.mark.parametrize(
    ("name", "version"),
    [("", "1.0.0"), ("foo", ""), ("", "")],
)
def test_template_ref_rejects_empty_fields(name: str, version: str) -> None:
    with pytest.raises(ValueError):
        TemplateRef(name=name, version=version)
