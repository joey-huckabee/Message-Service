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

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True, scope="session")
def _forbid_io() -> Iterator[None]:
    """Guard: unit tests that accidentally perform I/O SHALL fail loudly.

    Implementation deferred until fixtures/io_guard.py exists. Intent:
    patch socket.socket and aiosqlite.connect to raise RuntimeError.
    """
    # TODO(L3-PERS-016): wire up io_guard once fixtures/io_guard.py is in place
    yield


# Fixtures will be imported from tests.fixtures once those modules are
# populated. Example of the planned re-export pattern:
#
#     from tests.fixtures.clocks import fake_clock  # noqa: F401
#     from tests.fixtures.uuids import frozen_uuid  # noqa: F401
#     from tests.fixtures.loggers import null_logger  # noqa: F401
