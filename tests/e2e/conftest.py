"""End-to-end test conftest.

E2E tests treat the service as a black box: start the real gRPC server
and FastAPI dashboard in subprocess or in-process mode, drive them via
real client stubs and HTTP requests, and assert externally-observable
outcomes (email sent, report file on disk, audit records written).

E2E tests are the slowest tier. They are marked ``slow`` automatically
and SHOULD cover only the critical end-to-end paths:

* Happy path: BeginRun → submissions → FinalizeRun → email delivered
* Orphan path: BeginRun → submissions stop → sweeper fires → disposition applied
* Resend: past report resent to current subscriber list
* Admin operations: user disabled mid-run, tag added, template reviewed

Fixtures provided (to be implemented in ``tests/fixtures/``):

* ``running_service`` — lifespan-managed service process with all ports
  bound to ephemeral local addresses; teardown drains in-flight work.
* ``grpc_stub`` — a ``MessageServiceStub`` connected to ``running_service``.
* ``dashboard_client`` — authenticated ``httpx.AsyncClient`` against the
  dashboard, pre-seeded with a test user and session cookie.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _mark_slow(request: pytest.FixtureRequest) -> None:
    """Auto-apply the ``slow`` marker to every e2e test."""
    request.node.add_marker(pytest.mark.slow)


# from tests.fixtures.service import running_service  # noqa: F401
# from tests.fixtures.service import grpc_stub, dashboard_client  # noqa: F401
