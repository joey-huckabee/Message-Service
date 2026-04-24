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

import sys

import pytest

# -----------------------------------------------------------------------------
# Windows ProactorEventLoop + gRPC socket cleanup noise
# -----------------------------------------------------------------------------
#
# On Windows, the ProactorEventLoop and gRPC-managed sockets have
# finalization timing that differs from Linux's SelectorEventLoop.
# Their __del__ methods emit ResourceWarning entries during Python's
# GC pass, which pytest's unraisable-exception collector promotes to
# PytestUnraisableExceptionWarning and raises as an ExceptionGroup
# during session cleanup -- after tests have already passed.
#
# These warnings represent cleanup-ordering noise in Windows' gRPC +
# asyncio interplay, NOT a test correctness issue. The fixtures call
# channel.close() / server.stop() / asyncio.sleep(0) to yield to the
# loop; this hook catches whatever the platform still emits by
# wrapping sys.unraisablehook to pre-filter those specific patterns
# before pytest's collector ever sees them.

_original_unraisablehook = sys.unraisablehook


def _filtering_unraisablehook(unraisable: sys.UnraisableHookArgs) -> None:
    """Drop known-benign Windows resource warnings; delegate all others."""
    exc = unraisable.exc_value
    if isinstance(exc, ResourceWarning):
        obj_repr = repr(unraisable.object)
        msg = str(exc) if exc.args else ""
        # gRPC C-core socket finalizer (Windows completion-port timing).
        if "socket.socket" in obj_repr:
            return
        # ProactorEventLoop finalizer (Windows-only event loop class).
        if "event loop" in msg or "ProactorEventLoop" in obj_repr:
            return
    _original_unraisablehook(unraisable)


sys.unraisablehook = _filtering_unraisablehook


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
