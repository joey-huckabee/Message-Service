"""Full-service fixtures for e2e tests.

The :func:`running_service` fixture is the centerpiece. It builds a
fully-composed :class:`Service` via the production composition root
(:func:`message_service.bootstrap.build_service`), starts a real
``grpc.aio`` server on an ephemeral port, builds the FastAPI app via
:func:`create_app`, and yields a :class:`RunningService` handle
giving tests three driving surfaces:

* ``grpc_stub`` — a ``MessageServiceStub`` connected to the
  in-process server, used to drive the pipeline-side flows
  (BeginRun, SubmitStageReport, FinalizeRun).
* ``dashboard_client`` — an ``httpx.AsyncClient`` over an
  ``ASGITransport`` against the FastAPI app. No real port — the
  ASGI transport is sufficient for HTTP correctness assertions and
  avoids a second listening socket.
* ``smtp_capture`` — the :class:`SmtpCapture` from
  :mod:`tests.fixtures.email`, configured into the service's
  ``mail.smtp.{host,port}`` so end-of-pipeline emails actually hit
  an in-process aiosmtpd. Tests assert on
  ``smtp_capture.messages`` to confirm delivery.

The sweeper loop is constructed but NOT started by default — that
matches the production bootstrap. Tests that exercise the orphan
disposition path call ``service.sweeper_loop.start()`` explicitly
after configuring a short ``sweeper.run_timeout_seconds`` (see
:func:`tests.fixtures.config.write_e2e_config`).

Lifecycle ordering on teardown:

1. Close the dashboard client (releases the ASGI transport).
2. Close the gRPC channel and stop the server.
3. Call :func:`shutdown_service` — drains background tasks, closes
   the SQLite connection.
4. Brief ``asyncio.sleep(0)`` so Windows ProactorEventLoop's socket
   cleanup runs before the test event loop closes (mirrors the
   pattern used in ``tests/integration/grpc/test_servicer.py``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import grpc
import httpx
import pytest
from httpx import ASGITransport
from message_service_proto.v1 import message_service_pb2_grpc as pb_grpc

from message_service.bootstrap import Service, build_service, shutdown_service
from message_service.config.loader import load_config
from message_service.interfaces.grpc.servicer import register
from message_service.interfaces.rest.app import create_app
from tests.fixtures.config import write_e2e_config
from tests.fixtures.email import SmtpCapture


@dataclass
class RunningService:
    """Handle yielded by the :func:`running_service` fixture.

    Attributes:
        service: The fully-composed :class:`Service` from
            :func:`build_service`. Tests can reach into it for
            UoW-scoped repo access (e.g., asserting an audit row was
            written) or to start ``sweeper_loop`` for orphan-path
            tests.
        grpc_stub: gRPC stub bound to the in-process server.
        dashboard_client: ``httpx.AsyncClient`` against the FastAPI
            app via ``ASGITransport``.
        smtp_capture: In-process SMTP capture used by the service's
            mailer.
    """

    service: Service
    grpc_stub: pb_grpc.MessageServiceStub
    dashboard_client: httpx.AsyncClient
    smtp_capture: SmtpCapture


@asynccontextmanager
async def build_running_service(
    tmp_path: Path,
    smtp_capture: SmtpCapture,
    *,
    sweeper_run_timeout_seconds: int = 60,
    sweeper_poll_interval_seconds: int = 30,
) -> AsyncIterator[RunningService]:
    """Construct a fully-composed service plus its harness surfaces.

    Default sweeper timings (60s / 30s) are loose so the sweeper does
    NOT fire during a normal happy-path test. Orphan-path tests
    override to ~2s / 0.1s and start ``service.sweeper_loop`` to
    exercise the orphan transition.
    """
    config_path = write_e2e_config(
        tmp_path,
        smtp_host=smtp_capture.host,
        smtp_port=smtp_capture.port,
        sweeper_run_timeout_seconds=sweeper_run_timeout_seconds,
        sweeper_poll_interval_seconds=sweeper_poll_interval_seconds,
    )
    config = load_config(config_path)
    service = await build_service(config)

    # gRPC server: real port + real channel so the pipeline-side
    # flows exercise wire serialization end-to-end.
    server = grpc.aio.server()
    register(server, service)
    grpc_port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{grpc_port}")
    grpc_stub = pb_grpc.MessageServiceStub(channel)

    # Dashboard: ASGI transport against the same Service.
    app = create_app(service)
    transport = ASGITransport(app=app)
    dashboard_client = httpx.AsyncClient(transport=transport, base_url="http://testserver")

    handle = RunningService(
        service=service,
        grpc_stub=grpc_stub,
        dashboard_client=dashboard_client,
        smtp_capture=smtp_capture,
    )
    try:
        yield handle
    finally:
        # 1. Close the dashboard client first.
        await dashboard_client.aclose()
        # 2. Close the gRPC channel + stop the server.
        await channel.close()
        await server.stop(grace=0)
        # 3. Drain background tasks + close the SQLite connection.
        await shutdown_service(service, timeout=2.0)
        # 4. Yield control so Windows ProactorEventLoop GCs sockets.
        await asyncio.sleep(0)


@pytest.fixture
async def running_service(
    tmp_path: Path,
    smtp_capture: SmtpCapture,
) -> AsyncIterator[RunningService]:
    """Default fully-composed service with loose sweeper timing."""
    async with build_running_service(tmp_path, smtp_capture) as handle:
        yield handle


__all__ = ["RunningService", "build_running_service", "running_service"]
