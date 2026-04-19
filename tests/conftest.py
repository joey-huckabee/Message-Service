"""Root pytest configuration for Message-Service.

Fixture scoping rules (see ``tests/fixtures/`` for implementations):

* **Function scope** — default. Fresh fixture per test. Use for anything
  mutable (SQLite databases, filesystem temp dirs, fake clocks).
* **Module scope** — shared across tests in one file. Use for read-only
  resources that are expensive to build (loaded template manifest, parsed
  config schema).
* **Session scope** — shared across the entire pytest run. Use sparingly;
  limited to truly immutable resources (compiled proto stubs).

Markers registered in ``pyproject.toml``:

* ``unit`` / ``integration`` / ``e2e`` — auto-applied by directory via
  ``pytest_collection_modifyitems`` below.
* ``slow`` — tests taking more than 1 second; skip in fast dev loops with
  ``pytest -m 'not slow'``.
* ``requirement(id)`` — link a test to an L1/L2/L3 requirement identifier.
  Example: ``@pytest.mark.requirement("L3-RUN-007")``.

This root conftest intentionally stays small. Fixture definitions live in
``tests/fixtures/`` and are re-exported by level-specific conftests at
``tests/unit/conftest.py``, ``tests/integration/conftest.py``, etc.
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(
    config: pytest.Config,  # noqa: ARG001
    items: list[pytest.Item],
) -> None:
    """Auto-apply layer markers based on test path.

    This lets developers write ``pytest -m unit`` without having to decorate
    every test function individually. Explicit markers on specific tests
    still work and take precedence.
    """
    for item in items:
        path_parts = item.path.parts
        if "unit" in path_parts:
            item.add_marker(pytest.mark.unit)
        elif "integration" in path_parts:
            item.add_marker(pytest.mark.integration)
        elif "e2e" in path_parts:
            item.add_marker(pytest.mark.e2e)
        elif "conformance" in path_parts:
            item.add_marker(pytest.mark.conformance)
        elif "benchmarks" in path_parts:
            item.add_marker(pytest.mark.benchmark)


def pytest_report_header(config: pytest.Config) -> list[str]:  # noqa: ARG001
    """Add requirement-coverage hint to the pytest header."""
    return [
        "Message-Service test suite — tag tests with @pytest.mark.requirement('L<N>-<CAT>-<NNN>')",
        "                             to link them to requirements; see docs/TRACE-MATRIX.md.",
    ]
