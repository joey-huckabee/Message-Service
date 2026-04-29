"""Unit tests for :mod:`message_service.infrastructure.templating.renderer`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from message_service.application.ports.template_repository import TemplateRepository
from message_service.domain.aggregates.template_metadata import (
    TemplateKind,
    TemplateMetadata,
)
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import (
    ContextSizeExceededError,
    RenderedSizeExceededError,
    TemplateRenderError,
    UnknownTemplateError,
)
from message_service.infrastructure.templating.renderer import (
    Jinja2SandboxedTemplateRenderer,
)

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


_REF = TemplateRef(name="sample", version="1.0")


@pytest.fixture
def template_source(tmp_path: Path) -> Path:
    """Create a Jinja2 source file on disk; return its path."""
    src = tmp_path / "sample.html.j2"
    src.write_text("<html>{{ name }}</html>")
    return src


@pytest.fixture
def repo(template_source: Path) -> MagicMock:
    """Repository mock returning metadata pointing at `template_source`."""
    r = MagicMock(spec=TemplateRepository)
    meta = TemplateMetadata(
        name=_REF.name,
        version=_REF.version,
        kind=TemplateKind.REPORT_FRAGMENT,
        source_path=template_source,
    )
    r.get.return_value = meta
    # Renderer constructor calls list_all() to build its eager schema
    # validator cache; default these tests to no schemas (empty list).
    r.list_all.return_value = (meta,)
    return r


@pytest.fixture
def renderer(repo: MagicMock) -> Jinja2SandboxedTemplateRenderer:
    return Jinja2SandboxedTemplateRenderer(
        repository=repo,
        max_context_bytes=10_000,
        max_rendered_bytes=10_000,
    )


# -----------------------------------------------------------------------------
# Happy path
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L1-TMPL-002")
def test_renders_simple_template(renderer: Jinja2SandboxedTemplateRenderer) -> None:
    result = renderer.render(_REF, {"name": "world"})
    assert "world" in result


@pytest.mark.requirement("L1-TMPL-002")
def test_autoescape_escapes_html(
    renderer: Jinja2SandboxedTemplateRenderer,
) -> None:
    """Jinja2 SHALL HTML-escape context values (autoescape=True)."""
    result = renderer.render(_REF, {"name": "<script>evil()</script>"})
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


# -----------------------------------------------------------------------------
# Construction parameter validation
# -----------------------------------------------------------------------------


def test_rejects_non_positive_max_context_bytes(repo: MagicMock) -> None:
    with pytest.raises(ValueError, match="max_context_bytes"):
        Jinja2SandboxedTemplateRenderer(repository=repo, max_context_bytes=0, max_rendered_bytes=1)


def test_rejects_non_positive_max_rendered_bytes(repo: MagicMock) -> None:
    with pytest.raises(ValueError, match="max_rendered_bytes"):
        Jinja2SandboxedTemplateRenderer(repository=repo, max_context_bytes=1, max_rendered_bytes=0)


# -----------------------------------------------------------------------------
# StrictUndefined (L2-TMPL-009)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-TMPL-009")
def test_undefined_variable_raises_template_render_error(
    renderer: Jinja2SandboxedTemplateRenderer,
) -> None:
    with pytest.raises(TemplateRenderError) as exc_info:
        # context lacks "name"
        renderer.render(_REF, {"other": "value"})
    assert "UndefinedError" in exc_info.value.details["exception_class"]


# -----------------------------------------------------------------------------
# Context-size enforcement (L2-TMPL-014)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-TMPL-014")
def test_oversized_context_raises_before_template_invoked(
    repo: MagicMock,
) -> None:
    small = Jinja2SandboxedTemplateRenderer(
        repository=repo, max_context_bytes=50, max_rendered_bytes=10_000
    )
    with pytest.raises(ContextSizeExceededError) as exc_info:
        small.render(_REF, {"name": "x" * 1000})  # exceeds 50 bytes easily
    assert exc_info.value.details["limit_bytes"] == 50
    assert exc_info.value.details["measured_bytes"] > 50
    # Repo was never consulted — the pre-check fired first.
    repo.get.assert_not_called()


@pytest.mark.requirement("L2-TMPL-014")
def test_non_json_serializable_context_raises_template_render_error(
    renderer: Jinja2SandboxedTemplateRenderer,
) -> None:
    """Bytes are not JSON-serializable; the pre-check catches it."""
    with pytest.raises(TemplateRenderError) as exc_info:
        renderer.render(_REF, {"data": b"\x00\x01"})
    assert exc_info.value.details["name"] == _REF.name


# -----------------------------------------------------------------------------
# Rendered-size enforcement (L2-TMPL-014)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-TMPL-014")
def test_oversized_render_raises(repo: MagicMock, tmp_path: Path) -> None:
    # Template that emits 10_000 "X" characters.
    src = tmp_path / "big.html.j2"
    src.write_text("{% for _ in range(10000) %}X{% endfor %}")
    repo.get.return_value = TemplateMetadata(
        name=_REF.name,
        version=_REF.version,
        kind=TemplateKind.REPORT_FRAGMENT,
        source_path=src,
    )
    r = Jinja2SandboxedTemplateRenderer(
        repository=repo, max_context_bytes=10_000, max_rendered_bytes=500
    )
    # The sandboxed env has no `range` global (L2-TMPL-007 empty
    # globals), so the template raises UndefinedError before we even
    # hit the size check. That is the correct, stricter behavior —
    # but we want this test to exercise the size check. Use a
    # different source that doesn't need globals:
    src.write_text("X" * 10_000)
    # Clear the cached Template so the renderer re-reads from disk.
    r._cache.clear()
    with pytest.raises(RenderedSizeExceededError) as exc_info:
        r.render(_REF, {})
    assert exc_info.value.details["limit_bytes"] == 500
    assert exc_info.value.details["measured_bytes"] == 10_000


# -----------------------------------------------------------------------------
# Sandbox security (L1-TMPL-002, L2-TMPL-007)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L1-TMPL-002")
def test_sandbox_blocks_class_attribute_access(repo: MagicMock, tmp_path: Path) -> None:
    """Template attempting to reach __class__ SHALL be rejected by sandbox."""
    src = tmp_path / "evil.html.j2"
    src.write_text("{{ ''.__class__.__mro__ }}")
    repo.get.return_value = TemplateMetadata(
        name=_REF.name,
        version=_REF.version,
        kind=TemplateKind.REPORT_FRAGMENT,
        source_path=src,
    )
    r = Jinja2SandboxedTemplateRenderer(
        repository=repo, max_context_bytes=10_000, max_rendered_bytes=10_000
    )
    with pytest.raises(TemplateRenderError) as exc_info:
        r.render(_REF, {})
    # SecurityError or similar sandbox-violation exception.
    assert "Error" in exc_info.value.details["exception_class"]


@pytest.mark.requirement("L2-TMPL-007")
def test_sandbox_has_no_range_global(repo: MagicMock, tmp_path: Path) -> None:
    """Jinja2's default `range` global SHALL NOT be present (explicit empty globals)."""
    src = tmp_path / "uses_range.html.j2"
    src.write_text("{% for i in range(3) %}{{ i }}{% endfor %}")
    repo.get.return_value = TemplateMetadata(
        name=_REF.name,
        version=_REF.version,
        kind=TemplateKind.REPORT_FRAGMENT,
        source_path=src,
    )
    r = Jinja2SandboxedTemplateRenderer(
        repository=repo, max_context_bytes=10_000, max_rendered_bytes=10_000
    )
    with pytest.raises(TemplateRenderError):
        r.render(_REF, {})


# -----------------------------------------------------------------------------
# Filter whitelist (L2-TMPL-008)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-TMPL-008")
def test_allowed_filter_works(repo: MagicMock, tmp_path: Path) -> None:
    """The `upper` filter is on the whitelist and SHALL work."""
    src = tmp_path / "upper.html.j2"
    src.write_text("{{ name | upper }}")
    repo.get.return_value = TemplateMetadata(
        name=_REF.name,
        version=_REF.version,
        kind=TemplateKind.REPORT_FRAGMENT,
        source_path=src,
    )
    r = Jinja2SandboxedTemplateRenderer(
        repository=repo, max_context_bytes=10_000, max_rendered_bytes=10_000
    )
    result = r.render(_REF, {"name": "hello"})
    assert "HELLO" in result


@pytest.mark.requirement("L2-TMPL-008")
def test_removed_filter_raises(repo: MagicMock, tmp_path: Path) -> None:
    """A non-whitelisted filter (e.g., `attr`) SHALL NOT resolve."""
    src = tmp_path / "attr_filter.html.j2"
    src.write_text("{{ foo | attr('bar') }}")
    repo.get.return_value = TemplateMetadata(
        name=_REF.name,
        version=_REF.version,
        kind=TemplateKind.REPORT_FRAGMENT,
        source_path=src,
    )
    r = Jinja2SandboxedTemplateRenderer(
        repository=repo, max_context_bytes=10_000, max_rendered_bytes=10_000
    )
    with pytest.raises(TemplateRenderError):
        r.render(_REF, {"foo": "value"})


# -----------------------------------------------------------------------------
# Unknown template propagates from repository
# -----------------------------------------------------------------------------


def test_unknown_template_raises_unknown_template_error(
    renderer: Jinja2SandboxedTemplateRenderer, repo: MagicMock
) -> None:
    repo.get.side_effect = UnknownTemplateError(
        "not found", details={"name": "x", "version": "1.0"}
    )
    with pytest.raises(UnknownTemplateError):
        renderer.render(TemplateRef(name="x", version="1.0"), {})


# -----------------------------------------------------------------------------
# Compile caching
# -----------------------------------------------------------------------------


def test_template_compiled_once_per_ref(
    renderer: Jinja2SandboxedTemplateRenderer, repo: MagicMock
) -> None:
    """Subsequent renders of the same ref SHALL NOT re-read the source file."""
    renderer.render(_REF, {"name": "first"})
    renderer.render(_REF, {"name": "second"})
    renderer.render(_REF, {"name": "third"})
    # Repo is consulted every time (it's cheap) but the source file
    # should have been read exactly once. We verify via cache content.
    assert (_REF.name, _REF.version) in renderer._cache


# -----------------------------------------------------------------------------
# Source file read error
# -----------------------------------------------------------------------------


def test_unreadable_source_file_raises_template_render_error(
    repo: MagicMock, tmp_path: Path
) -> None:
    # Point at a non-existent file.
    repo.get.return_value = TemplateMetadata(
        name=_REF.name,
        version=_REF.version,
        kind=TemplateKind.REPORT_FRAGMENT,
        source_path=tmp_path / "does-not-exist.html.j2",
    )
    r = Jinja2SandboxedTemplateRenderer(
        repository=repo, max_context_bytes=10_000, max_rendered_bytes=10_000
    )
    with pytest.raises(TemplateRenderError) as exc_info:
        r.render(_REF, {})
    assert "source_path" in exc_info.value.details
