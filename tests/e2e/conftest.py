"""End-to-end test conftest.

E2E tests treat the service as a black box: build the production
:class:`~message_service.bootstrap.Service`, start a real
``grpc.aio`` server in-process, drive the FastAPI app via httpx
``ASGITransport``, capture outbound SMTP via in-process aiosmtpd,
and assert externally-observable outcomes (email actually sent, run
state transitions persisted, audit records written, report files
on disk).

E2E tests are the slowest tier. They are marked ``slow``
automatically and SHOULD cover only the critical end-to-end paths:

* Happy path: BeginRun → submissions → FinalizeRun → email delivered
* Orphan path: BeginRun → submissions stop → sweeper fires → disposition applied
* Resend: past report resent to current subscriber list
* Admin operations: user CRUD, audit-log viewer, template inspection

Fixtures provided (re-exported from :mod:`tests.fixtures`):

* :func:`smtp_capture` — aiosmtpd controller on an ephemeral port;
  every received message captured to a list.
* :func:`running_service` — fully-composed :class:`Service` plus
  in-process gRPC server, ASGI dashboard client, and SMTP capture.
"""

from __future__ import annotations

import pytest

from tests.fixtures.email import smtp_capture  # noqa: F401  (pytest fixture)
from tests.fixtures.service import running_service  # noqa: F401  (pytest fixture)


@pytest.fixture(autouse=True)
def _mark_slow(request: pytest.FixtureRequest) -> None:
    """Auto-apply the ``slow`` marker to every e2e test."""
    request.node.add_marker(pytest.mark.slow)
