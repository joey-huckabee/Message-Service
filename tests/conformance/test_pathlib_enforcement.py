"""Pathlib-enforcement conformance test (L2-DEP-002, L3-PERS-012, L3-DEP-003).

Enforces that all filesystem path manipulation uses ``pathlib.Path``.
Specifically:

* No ``os.path.join`` calls in ``src/``.
* No string concatenation of paths using ``"/"`` or ``"\\"`` literals.
* No ``os.sep`` or ``os.pathsep`` in ``src/`` (pathlib exposes these
  implicitly via ``Path`` behavior).

Ruff's ``PTH`` ruleset covers most of this at lint time. This test is a
belt-and-braces check that the lint rules remain enabled and that any
``# noqa: PTH`` suppressions are documented in ``docs/reviews/``.
"""

from __future__ import annotations

# TODO: implement after the first few source modules exist.
