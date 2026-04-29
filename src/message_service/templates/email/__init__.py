"""Service-internal email templates (L3-MAIL-015).

Distinct from the user-supplied templates loaded via
``templates.manifest_path``: those go through the sandboxed
:class:`~message_service.application.ports.template_renderer.TemplateRenderer`
port and may be operator-modified. The templates in this directory
ship with the codebase, are loaded via :mod:`importlib.resources`,
and SHALL NOT accept user-supplied content (per L3-MAIL-015 +
L3-MAIL-031).
"""
