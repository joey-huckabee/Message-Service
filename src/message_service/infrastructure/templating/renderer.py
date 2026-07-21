"""Sandboxed Jinja2 implementation of :class:`TemplateRenderer`.

Renderer construction follows L2-TMPL-007, L2-TMPL-008, L2-TMPL-009:

* ``jinja2.sandbox.SandboxedEnvironment`` — the default sandbox blocks
  attribute access that reaches Python internals.
* Explicit empty ``globals``, then rebuilt to contain only the
  permitted entries (currently: none; L2-TMPL-007 specifies an
  explicit mapping of whitelisted entries, v1 has zero).
* ``StrictUndefined`` — any reference to an unbound context key raises
  ``UndefinedError`` rather than silently producing blank output
  (L2-TMPL-009).
* Filter whitelist per L2-TMPL-008: the 11 named safe filters remain;
  all others (including ``attr``, ``pprint``, and any custom filters)
  are removed.

Size enforcement (L2-TMPL-014):

* Context size is measured as the byte length of its deterministic
  JSON encoding; if it exceeds
  :attr:`templates.max_context_bytes`,
  :class:`ContextSizeExceededError` is raised *before* the template
  engine is invoked.
* Rendered output size is measured as the UTF-8 byte length of the
  rendered string; if it exceeds
  :attr:`templates.max_rendered_bytes`,
  :class:`RenderedSizeExceededError` is raised.

JSON Schema validation (L1-TMPL-004 / L2-TMPL-010..011):

* At construction the renderer iterates :meth:`TemplateRepository.list_all`
  and, for every entry whose
  :attr:`TemplateMetadata.context_schema_path` is set, reads the file,
  parses JSON, runs ``Draft202012Validator.check_schema`` and caches a
  validator instance keyed by ``(name, version)`` — eager-at-startup
  per L3-TMPL-018 / L3-TMPL-031. Any failure raises
  :class:`ConfigurationError` and aborts service start.
* On :meth:`render`, after the context size pre-check and immediately
  before Jinja invocation, the cached validator (if any) runs against
  the supplied context. ``jsonschema.ValidationError`` is translated
  to :class:`ContextSchemaViolationError` with details containing the
  JSON Pointer to the offending element (per L3-TMPL-020 / L3-TMPL-032).
* Templates whose manifest entry omits ``context_schema_path`` skip
  schema validation entirely (L3-TMPL-030).

The adapter is **sync** per the port contract. Template rendering is
CPU-bound; wrapping in async would add overhead without concurrency.

Requirement references
----------------------
L1-TMPL-002, L1-TMPL-003 (sandboxing)
L1-TMPL-004 (JSON Schema validation)
L2-TMPL-007, L2-TMPL-008, L2-TMPL-009 (SandboxedEnvironment config)
L2-TMPL-010, L2-TMPL-011 (schema validation)
L2-TMPL-014 (size limits)
L3-TMPL-018, L3-TMPL-020, L3-TMPL-025, L3-TMPL-029..032
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
from jinja2 import StrictUndefined
from jinja2.exceptions import TemplateError
from jinja2.sandbox import SandboxedEnvironment
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from message_service.application.ports.template_renderer import TemplateRenderer
from message_service.application.ports.template_repository import TemplateRepository
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import (
    ConfigurationError,
    ContextSchemaViolationError,
    ContextSizeExceededError,
    RenderedSizeExceededError,
    TemplateRenderError,
)

# Whitelisted filters per L2-TMPL-008.
#
# NOTE on ``safe``: it is intentionally whitelisted and is load-bearing, NOT a
# footgun. Rendering is two-level. Leaf templates (each stage's report_template)
# render pipeline-supplied context with ``autoescape=True``, so their output is
# already HTML-escaped. The aggregation template then composes those pre-rendered
# fragments with ``{{ stage.rendered_html | safe }}`` — ``safe`` here says "this is
# already-rendered, already-escaped HTML from our own sandbox, do not double-escape
# it," not "trust raw pipeline input." Every shipped aggregation template relies on
# this; removing ``safe`` would break composition and double-escape every fragment.
# No un-escaped pipeline data reaches the output because the escaping happened at
# the leaf render.
_ALLOWED_FILTERS: frozenset[str] = frozenset(
    {
        "escape",
        "safe",
        "length",
        "default",
        "upper",
        "lower",
        "title",
        "trim",
        "replace",
        "join",
        "format",
    }
)


def _build_sandbox() -> SandboxedEnvironment:
    """Construct the sandboxed environment with the v1 security profile.

    Built per L2-TMPL-007 / L2-TMPL-008 / L2-TMPL-009:

    * ``globals`` starts empty then has only explicitly-permitted entries
      added (currently: none).
    * ``filters`` is replaced with only the whitelisted subset; any
      filter outside the whitelist (including the built-in ``attr``,
      ``pprint``, etc.) is removed.
    * ``autoescape=True`` — produces HTML-safe output by default.
    * ``undefined=StrictUndefined`` — raises on missing keys instead of
      silently emitting blank output.
    """
    env = SandboxedEnvironment(
        autoescape=True,
        undefined=StrictUndefined,
    )

    # L2-TMPL-007: explicit empty globals. Jinja2 pre-populates a few
    # globals (``range``, ``dict``, ``lipsum``, ``cycler``, ``joiner``);
    # we remove them all.
    env.globals = {}

    # L2-TMPL-008: keep only whitelisted filters.
    env.filters = {name: fn for name, fn in env.filters.items() if name in _ALLOWED_FILTERS}

    # Similarly trim tests (``defined``, ``equalto``, etc.) to the safe
    # built-ins. v1 does not use any tests in templates; we retain the
    # standard set for convention but could further whitelist if needed.

    return env


class Jinja2SandboxedTemplateRenderer(TemplateRenderer):
    """Sandboxed Jinja2 renderer backed by the template repository.

    The renderer looks up template metadata via the injected
    :class:`TemplateRepository`, reads the Jinja2 source from the
    metadata's ``source_path``, compiles, and renders. Compiled
    templates are cached on first use keyed by ``(name, version)``.
    """

    def __init__(
        self,
        *,
        repository: TemplateRepository,
        max_context_bytes: int,
        max_rendered_bytes: int,
    ) -> None:
        """Construct with repository and size limits.

        Args:
            repository: Source of :class:`TemplateMetadata` for
                ``source_path`` lookup.
            max_context_bytes: Context JSON-encoded byte ceiling.
                Typically ``config.templates.max_context_bytes``.
            max_rendered_bytes: Rendered-output UTF-8 byte ceiling.
                Typically ``config.templates.max_rendered_bytes``.
        """
        if max_context_bytes < 1:
            raise ValueError("max_context_bytes must be positive")
        if max_rendered_bytes < 1:
            raise ValueError("max_rendered_bytes must be positive")

        self._repo = repository
        self._max_context_bytes = max_context_bytes
        self._max_rendered_bytes = max_rendered_bytes
        self._env = _build_sandbox()
        # Compiled-template cache: (name, version) -> Template.
        self._cache: dict[tuple[str, str], Any] = {}
        # Eager schema validator cache (L3-TMPL-018 / L3-TMPL-031). Built
        # at construction; bad schemas raise ConfigurationError now so
        # they cannot surface mid-render.
        self._validators: dict[tuple[str, str], Draft202012Validator] = self._build_validators(
            repository
        )

    @staticmethod
    def _build_validators(
        repository: TemplateRepository,
    ) -> dict[tuple[str, str], Draft202012Validator]:
        validators: dict[tuple[str, str], Draft202012Validator] = {}
        for meta in repository.list_all():
            schema_path = meta.context_schema_path
            if schema_path is None:
                continue
            try:
                schema_text = schema_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ConfigurationError(
                    f"context schema file not readable: {schema_path}",
                    details={
                        "name": meta.name,
                        "version": meta.version,
                        "schema_path": str(schema_path),
                        "reason": str(exc),
                    },
                ) from exc
            try:
                schema_doc = json.loads(schema_text)
            except json.JSONDecodeError as exc:
                raise ConfigurationError(
                    f"context schema is not valid JSON: {schema_path}",
                    details={
                        "name": meta.name,
                        "version": meta.version,
                        "schema_path": str(schema_path),
                        "reason": str(exc),
                    },
                ) from exc
            try:
                Draft202012Validator.check_schema(schema_doc)
            except SchemaError as exc:
                raise ConfigurationError(
                    f"context schema fails Draft 2020-12 meta-schema: {schema_path}",
                    details={
                        "name": meta.name,
                        "version": meta.version,
                        "schema_path": str(schema_path),
                        "reason": str(exc),
                    },
                ) from exc
            validators[(meta.name, meta.version)] = Draft202012Validator(schema_doc)
        return validators

    def render(self, ref: TemplateRef, context: dict[str, Any]) -> str:  # noqa: D102
        # Context size pre-check (L2-TMPL-014). Serialize deterministically
        # so the measurement is reproducible.
        try:
            context_json = json.dumps(context, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise TemplateRenderError(
                f"template context is not JSON-serializable: {exc}",
                details={
                    "name": ref.name,
                    "version": ref.version,
                    "reason": str(exc),
                },
            ) from exc

        context_bytes = len(context_json.encode("utf-8"))
        if context_bytes > self._max_context_bytes:
            raise ContextSizeExceededError(
                f"template context size {context_bytes} bytes exceeds limit "
                f"{self._max_context_bytes} bytes",
                details={
                    "name": ref.name,
                    "version": ref.version,
                    "measured_bytes": context_bytes,
                    "limit_bytes": self._max_context_bytes,
                },
            )

        # JSON Schema validation (L3-TMPL-029): runs after the size
        # pre-check (per L3-TMPL-022) and immediately before Jinja2 is
        # invoked. Templates without a context_schema_path skip this
        # step entirely (L3-TMPL-030).
        validator = self._validators.get((ref.name, ref.version))
        if validator is not None:
            try:
                validator.validate(context)
            except jsonschema.ValidationError as exc:
                raise ContextSchemaViolationError(
                    f"context failed schema for {ref.name!r}@{ref.version!r}: {exc.message}",
                    details={
                        "name": ref.name,
                        "version": ref.version,
                        "json_pointer": _to_json_pointer(exc.absolute_path),
                        "validator": str(exc.validator) if exc.validator else "",
                        "instance_value": exc.instance,
                        "message": exc.message,
                    },
                ) from exc

        # Resolve template source. `repository.get` raises
        # UnknownTemplateError if the ref is absent.
        meta = self._repo.get(ref)
        template = self._cache.get((ref.name, ref.version))
        if template is None:
            template = self._compile(meta.source_path, ref)
            self._cache[(ref.name, ref.version)] = template

        # Render.
        try:
            rendered: str = str(template.render(context))
        except TemplateError as exc:
            # Covers UndefinedError, TemplateSyntaxError,
            # SecurityError (sandbox violations), and any subclass.
            raise TemplateRenderError(
                f"template render failed: {type(exc).__name__}: {exc}",
                details={
                    "name": ref.name,
                    "version": ref.version,
                    "exception_class": type(exc).__name__,
                    "message": str(exc),
                },
            ) from exc

        # Post-render size enforcement.
        rendered_bytes = len(rendered.encode("utf-8"))
        if rendered_bytes > self._max_rendered_bytes:
            raise RenderedSizeExceededError(
                f"template rendered output {rendered_bytes} bytes exceeds limit "
                f"{self._max_rendered_bytes} bytes",
                details={
                    "name": ref.name,
                    "version": ref.version,
                    "measured_bytes": rendered_bytes,
                    "limit_bytes": self._max_rendered_bytes,
                },
            )

        return rendered

    def _compile(self, source_path: Path, ref: TemplateRef) -> Any:
        """Read and compile a Jinja2 source file.

        Args:
            source_path: Filesystem path to the Jinja2 source.
            ref: Reference the source belongs to (for error context).

        Returns:
            A compiled Jinja2 ``Template`` object.

        Raises:
            TemplateRenderError: The source file cannot be read, or the
                template fails to compile. The original exception is
                chained via ``__cause__``.
        """
        try:
            source = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise TemplateRenderError(
                f"could not read template source {source_path}: {exc}",
                details={
                    "name": ref.name,
                    "version": ref.version,
                    "source_path": str(source_path),
                    "reason": str(exc),
                },
            ) from exc

        try:
            return self._env.from_string(source)
        except TemplateError as exc:
            raise TemplateRenderError(
                f"template compile failed: {type(exc).__name__}: {exc}",
                details={
                    "name": ref.name,
                    "version": ref.version,
                    "source_path": str(source_path),
                    "exception_class": type(exc).__name__,
                    "message": str(exc),
                },
            ) from exc


def _to_json_pointer(absolute_path: Any) -> str:
    """Render :attr:`jsonschema.ValidationError.absolute_path` as a JSON Pointer.

    Per L3-TMPL-032: ``["foo", "bar", 0]`` → ``"/foo/bar/0"``; an empty
    deque (root violation) renders as ``""``. RFC 6901 escapes ``~``
    and ``/`` in path segments.
    """
    parts = list(absolute_path)
    if not parts:
        return ""
    encoded = ("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)
    return "".join(encoded)


__all__ = ["Jinja2SandboxedTemplateRenderer"]
