"""Template-related fixtures: manifest builders, sandboxed environments.

Planned fixtures:

* ``minimal_manifest`` — a manifest with exactly one stage template and
  one aggregation template, backed by actual files under ``tmp_path``.
* ``sandboxed_template_env`` — a fully-configured ``SandboxedEnvironment``
  from the production templating module, loaded from ``minimal_manifest``.
* ``manifest_builder`` — a fluent builder for constructing custom
  manifests within a test.
"""

from __future__ import annotations

# TODO(L3-TMPL-001, L3-TMPL-013): implement once the manifest loader and
# renderer modules are in place.
