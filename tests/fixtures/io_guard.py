"""I/O guard for the unit-test tier.

Unit tests SHALL NOT open real database connections.
``tests/unit/conftest.py`` installs this guard at session scope, which
patches ``aiosqlite.connect`` so that calling it from a unit test
raises :class:`RuntimeError` with a pointer to the unit/integration
boundary documented in ``tests/README.md``.

Tests that legitimately need real ``aiosqlite`` (the application-layer
use cases that drive real :class:`SqliteUnitOfWork` to verify
transactional atomicity, the bootstrap composition root, etc.) opt out
per-test or per-module via ``@pytest.mark.allow_io``. Tests that span
multiple components against real local resources belong in
``tests/integration/`` and never see this guard at all (the unit
conftest's session fixture does not apply outside ``tests/unit/``).

Mechanism
---------
The guard maintains a single module-level flag, ``_io_forbidden``. The
patch always checks the flag at call time; a per-test autouse fixture
in ``tests/unit/conftest.py`` flips the flag based on the
``allow_io`` marker. This keeps the patching cost paid once at session
start; per-test cost is one attribute read.

Why only aiosqlite, not sockets
-------------------------------
An earlier draft also patched ``socket.socket.__init__``, but the
asyncio event-loop construction path calls ``socket.socketpair()``
internally for its self-signaling pipe (mandatory on Windows
``ProactorEventLoop``; common on POSIX selectors too). Patching the
constructor breaks ``pytest-asyncio`` for every async unit test. The
real risk worth blocking is database I/O — accidental ``aiosqlite``
connections in unit tests have a track record in this codebase; raw
socket creation in unit tests does not. Network-layer I/O via
``aiosmtplib``, ``grpc.aio``, etc. is caught further up the stack at
test review time and by the architecture-boundary conformance.

Bypassing the guard from production code is impossible because the
patch is installed inside the test process only. The original
implementation is restored at session teardown via
:meth:`pytest.MonkeyPatch.undo`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import aiosqlite
import pytest

_GUARD_MESSAGE = (
    "unit tests forbid real aiosqlite connections. If this test "
    "legitimately needs one, either move it to tests/integration/ or "
    "annotate it with @pytest.mark.allow_io. See tests/README.md for "
    "the unit/integration boundary."
)

_io_forbidden: bool = False

_orig_aiosqlite_connect = aiosqlite.connect


def set_io_forbidden(forbidden: bool) -> None:
    """Set the per-test guard flag.

    Called from ``tests/unit/conftest.py``'s per-test autouse fixture.
    The flag is flipped to ``True`` for tests without
    ``@pytest.mark.allow_io`` and ``False`` for tests that carry it.

    Args:
        forbidden: ``True`` to enable raising on aiosqlite calls;
            ``False`` to pass through to the original implementation.
    """
    global _io_forbidden
    _io_forbidden = forbidden


def _guarded_aiosqlite_connect(*args: Any, **kwargs: Any) -> Any:
    """Replacement for :func:`aiosqlite.connect`."""
    if _io_forbidden:
        raise RuntimeError(_GUARD_MESSAGE)
    return _orig_aiosqlite_connect(*args, **kwargs)


@contextmanager
def install_io_guard() -> Iterator[None]:
    """Install the I/O guard for the duration of the context.

    Used as a session-scoped context inside the
    ``tests/unit/conftest.py::_forbid_io`` autouse fixture. Installation
    is idempotent within a process; nested installations are a no-op
    because :class:`pytest.MonkeyPatch` records the original each time.

    Yields:
        ``None``. The original implementation is restored on exit via
        :meth:`pytest.MonkeyPatch.undo`.
    """
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(aiosqlite, "connect", _guarded_aiosqlite_connect)
    try:
        yield
    finally:
        monkeypatch.undo()
        # Defensive reset so a leaked flag from a crashed test cannot
        # outlive the guard installation.
        set_io_forbidden(False)


__all__ = ["install_io_guard", "set_io_forbidden"]
