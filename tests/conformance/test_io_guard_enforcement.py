"""I/O-guard enforcement conformance test.

Verifies that :mod:`tests.fixtures.io_guard` actually raises on
``aiosqlite.connect`` when active. This complements the unit-tier
``_forbid_io`` fixture in ``tests/unit/conftest.py`` — the fixture
installs the guard for every unit test, but a conformance check is
worth carrying separately so a regression in the guard itself surfaces
distinctly from a unit-test misconfiguration.

The test installs the guard manually (rather than relying on the unit
fixture) so it can assert both the active and bypass paths in one
place.

This test verifies a property of the test infrastructure rather than
the service-under-test, so it carries no ``@pytest.mark.requirement``
marker (matching the precedent of
``test_pathlib_enforcement.py``); the unit/integration boundary is
documented in ``tests/README.md`` and ``tests/unit/conftest.py``.
"""

from __future__ import annotations

import aiosqlite
import pytest

from tests.fixtures.io_guard import install_io_guard, set_io_forbidden


def test_guard_blocks_aiosqlite_connect_when_active() -> None:
    """When the flag is set, ``aiosqlite.connect`` SHALL raise."""
    with install_io_guard():
        set_io_forbidden(True)
        with pytest.raises(RuntimeError, match="unit tests forbid"):
            aiosqlite.connect(":memory:")


def test_guard_passes_through_when_flag_is_unset() -> None:
    """When the flag is unset, ``aiosqlite.connect`` SHALL succeed."""
    with install_io_guard():
        set_io_forbidden(False)
        # The call itself must not raise. The Connection object is a
        # context manager; awaiting it would actually open the file —
        # we only need to confirm the guard didn't intercept.
        conn = aiosqlite.connect(":memory:")
        del conn


def test_guard_restores_original_on_context_exit() -> None:
    """The guard SHALL restore ``aiosqlite.connect`` on teardown."""
    original_connect = aiosqlite.connect

    with install_io_guard():
        # Inside the context, the patched implementation is installed.
        assert aiosqlite.connect is not original_connect

    # After exit, the original is back.
    assert aiosqlite.connect is original_connect
