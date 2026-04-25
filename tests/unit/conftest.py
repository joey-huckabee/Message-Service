"""Unit-test conftest.

Unit tests MUST NOT perform I/O, network calls, database access, or
subprocess execution. Fixtures exposed here are pure in-memory fakes.

Fixtures provided:

* ``fake_clock`` — a ``FakeClock`` with ``tick()`` and ``advance()`` methods
  for deterministic time-based tests.
* ``frozen_uuid`` — monkeypatches ``uuid.uuid4`` to return a predictable
  sequence, for tests that assert on minted run_ids.
* ``null_logger`` — a structlog logger that discards records, for tests
  where logging output would clutter assertion diagnostics.

Enforcement: a session-scoped autouse fixture (``_forbid_io``) monkey-
patches ``socket.socket`` and ``aiosqlite.connect`` to raise
``RuntimeError`` during unit-test collection. Integration and e2e tests
live in their own sibling directories with their own conftests and are
unaffected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

# Re-export fixtures from tests.fixtures so they are discoverable under the
# ``unit`` test tree. As more fixture modules are populated, add their
# re-exports here.
from tests.fixtures.clocks import fake_clock, fake_clock_at_epoch  # noqa: F401
from tests.fixtures.io_guard import install_io_guard, set_io_forbidden

#     from tests.fixtures.uuids import frozen_uuid
#     from tests.fixtures.loggers import null_logger

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True, scope="session")
def _forbid_io() -> Iterator[None]:
    """Install the unit-tier I/O guard for the whole session.

    Patches ``socket.socket.__init__`` and ``aiosqlite.connect`` to
    raise :class:`RuntimeError` when called from any unit test that has
    not opted out via ``@pytest.mark.allow_io``. Originals are restored
    at session teardown.

    The per-test toggle below (``_toggle_io_guard``) flips the active
    flag based on the running test's markers; tests with
    ``@pytest.mark.allow_io`` pass through to the original
    implementations.
    """
    with install_io_guard():
        yield


@pytest.fixture(autouse=True)
def _toggle_io_guard(request: pytest.FixtureRequest) -> Iterator[None]:
    """Per-test toggle for the I/O guard based on ``allow_io`` marker.

    Tests carrying ``@pytest.mark.allow_io`` are exempt from the guard
    for the duration of their setup / call / teardown phases. All other
    unit tests have the guard active.
    """
    has_opt_out = request.node.get_closest_marker("allow_io") is not None
    set_io_forbidden(not has_opt_out)
    try:
        yield
    finally:
        set_io_forbidden(False)
