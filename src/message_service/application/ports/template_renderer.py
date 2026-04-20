"""Port: render a template referenced by :class:`TemplateRef` with a context.

Separates *looking up templates* (:class:`TemplateRepository`) from
*executing templates* (:class:`TemplateRenderer`). The repository knows
what templates exist and where their source files live; the renderer
actually produces HTML.

The implementation lives in ``infrastructure/`` and uses a Jinja2
``SandboxedEnvironment`` per L1-TMPL-002: template authors cannot
access Python builtins, import modules, or escape into host state.

Requirement references
----------------------
L1-TMPL-001 (manifest-based discovery)
L1-TMPL-002 (sandboxed execution)
L2-TMPL-004, L2-TMPL-005, L2-TMPL-006 (sandboxing, context size, rendered size)
L3-TMPL-028 (enforce max_rendered_bytes)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from message_service.domain.aggregates.template_ref import TemplateRef


class TemplateRenderer(ABC):
    """Abstract sandboxed template executor.

    Implementations MUST:

    * Render in a sandbox (Jinja2 ``SandboxedEnvironment`` or
      equivalent). Template authors MUST NOT be able to import Python
      modules, access ``__builtins__``, or read the filesystem.
    * Reject contexts whose serialized form exceeds
      ``templates.max_context_bytes`` with
      :class:`~message_service.domain.errors.ContextSizeExceededError`
      *before* invoking the template engine (L2-TMPL-005).
    * Reject rendered output exceeding
      ``templates.max_rendered_bytes`` with
      :class:`~message_service.domain.errors.RenderedSizeExceededError`
      after rendering (L3-TMPL-028).
    * Raise
      :class:`~message_service.domain.errors.TemplateRenderError` on
      any other rendering failure (syntax error, undefined variable in
      strict mode, sandbox violation). The original exception chains
      via ``__cause__``.
    * Raise
      :class:`~message_service.domain.errors.UnknownTemplateError` if
      the referenced template is not in the manifest.
      Implementations MAY choose to consult
      :class:`TemplateRepository` internally or to cache manifest
      lookups; the port guarantees the error contract either way.
    """

    @abstractmethod
    def render(self, ref: TemplateRef, context: dict[str, Any]) -> str:
        """Render ``template_ref`` with ``context`` and return HTML.

        Synchronous by design: Jinja2 rendering is CPU-bound; wrapping
        it in ``async`` would add overhead without concurrency
        benefit. Use cases that need to render many templates do so
        sequentially inside an already-async context.

        Args:
            ref: The template to render.
            context: Template variables. The dict is not mutated; any
                rendering-time mutation by the template is contained
                inside the sandbox.

        Returns:
            Rendered HTML as a string. Callers needing bytes (e.g.,
            :class:`~message_service.application.ports.mailer.EmailAttachment`)
            encode via ``.encode("utf-8")``.

        Raises:
            UnknownTemplateError: ``ref`` is not in the manifest.
            ContextSizeExceededError: Context too large.
            RenderedSizeExceededError: Rendered output too large.
            TemplateRenderError: Any other rendering failure.
        """


__all__ = ["TemplateRenderer"]
