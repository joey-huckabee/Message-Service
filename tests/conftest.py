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

import asyncio
from typing import cast

import pytest

# -----------------------------------------------------------------------------
# Windows ProactorEventLoop cleanup
# -----------------------------------------------------------------------------
#
# pytest-asyncio's runner fixture snapshots the previous event loop with
# ``asyncio.get_event_loop()``. On Windows, that creates a default
# ProactorEventLoop when no loop is installed yet. The runner restores that
# bookkeeping loop after each async test but does not own or close it, so its
# socket pair can survive until pytest's final GC pass and become a
# PytestUnraisableExceptionWarning under ``filterwarnings = ["error"]``.


class _NoImplicitEventLoopPolicy(asyncio.DefaultEventLoopPolicy):
    """Default policy variant that never creates an event loop implicitly."""

    def get_event_loop(self) -> asyncio.AbstractEventLoop:
        local = object.__getattribute__(self, "_local")
        loop = cast(
            asyncio.AbstractEventLoop | None,
            local._loop,
        )
        if loop is None:
            raise RuntimeError("There is no current event loop")
        return loop


def pytest_configure(config: pytest.Config) -> None:
    """Install a pytest-only event-loop policy before pytest-asyncio runs tests."""
    asyncio.set_event_loop_policy(_NoImplicitEventLoopPolicy())


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    """Provide pytest-asyncio with the no-implicit-loop policy."""
    return _NoImplicitEventLoopPolicy()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Close pytest-asyncio's restored bookkeeping loop, if one exists."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    if loop.is_running() or loop.is_closed():
        return
    loop.close()
    asyncio.set_event_loop(None)


def pytest_collection_modifyitems(
    config: pytest.Config,
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


def pytest_report_header(config: pytest.Config) -> list[str]:
    """Add requirement-coverage hint to the pytest header."""
    return [
        "Message-Service test suite — tag tests with @pytest.mark.requirement('L<N>-<CAT>-<NNN>')",
        "                             to link them to requirements; see docs/TRACE-MATRIX.md.",
    ]
