"""CLI entrypoint for the Message-Service.

Usage::

    python -m message_service --config /etc/message-service/config.toml

Or, via environment variable::

    MESSAGE_SERVICE_CONFIG=/path/config.toml python -m message_service

Behavior
--------
The entrypoint:

1. Parses ``--config`` (or falls back to ``MESSAGE_SERVICE_CONFIG``).
2. Loads and validates the config.
3. Calls :func:`message_service.bootstrap.build_service` to construct
   every adapter and use case.
4. Constructs a ``grpc.aio.server`` and a uvicorn-driven FastAPI app,
   registers the gRPC servicer via
   :func:`message_service.interfaces.grpc.servicer.register`, and
   binds the two listeners from ``config.grpc`` and
   ``config.dashboard``.
5. Installs SIGTERM and SIGINT handlers that set an internal
   :class:`asyncio.Event`. On platforms where
   ``loop.add_signal_handler`` is unsupported (Windows), falls back
   to ``signal.signal`` with a thread-safe event trigger.
6. Starts both servers and awaits the shutdown event.
7. On shutdown: signals uvicorn to exit and gracefully stops the
   gRPC server (both bounded by
   ``config.service.shutdown_grace_period_seconds``), then calls
   :func:`message_service.bootstrap.shutdown_service` to drain
   background tasks and close the SQLite connection.

Requirement references
----------------------
L1-DEP-001 (single-process service)
L1-API-001, L1-API-003 (gRPC plaintext listener)
L1-DASH-001 (FastAPI dashboard listener)
L2-DEP-006 (graceful shutdown on SIGTERM)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

import grpc
import structlog
import uvicorn

from message_service.bootstrap import Service, build_service, shutdown_service
from message_service.config.loader import load_config
from message_service.config.schema import Config
from message_service.interfaces.grpc.concurrency_limit_interceptor import (
    ConcurrencyLimitInterceptor,
)
from message_service.interfaces.grpc.correlation_interceptor import CorrelationIdInterceptor
from message_service.interfaces.grpc.servicer import register
from message_service.interfaces.rest.app import create_app

_log = structlog.get_logger(__name__)

_ENV_CONFIG = "MESSAGE_SERVICE_CONFIG"


# -----------------------------------------------------------------------------
# Arg parsing
# -----------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="message_service",
        description=("Message-Service: gRPC ingest + aggregated-report mail delivery."),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Path to the service config TOML file. If omitted, falls back to "
            f"the ${_ENV_CONFIG} environment variable."
        ),
    )
    return parser


def _resolve_config_path(argv: list[str] | None = None) -> Path:
    """Resolve the config-file path from CLI args, then env var.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns:
        The resolved :class:`pathlib.Path`.

    Raises:
        SystemExit: Neither ``--config`` nor ``$MESSAGE_SERVICE_CONFIG`` is set.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.config is not None:
        return args.config  # type: ignore[no-any-return]

    env_value = os.environ.get(_ENV_CONFIG)
    if env_value:
        return Path(env_value)

    parser.error(f"no config path provided: pass --config or set ${_ENV_CONFIG}")


# -----------------------------------------------------------------------------
# Server lifecycle
# -----------------------------------------------------------------------------


async def _build_grpc_server(service: Service) -> tuple[grpc.aio.Server, str]:
    """Create a gRPC server, register the servicer, bind the port, and start.

    Returns the server plus the ``host:port`` string that was bound.
    Separated from :func:`_run` so tests can exercise construction
    independently of the blocking main loop.
    """
    # L3-API-002 / L3-OBS-003: bind a fresh correlation_id per RPC so every log
    # record (success + failure) carries it and the error translator can surface
    # the same id to the client. The concurrency limiter (L3-API-020) is ordered
    # AFTER correlation so a rejection log record carries the RPC's id; it is
    # installed only when grpc.max_in_flight_rpcs > 0 (0 = disabled).
    interceptors: list[grpc.aio.ServerInterceptor] = [CorrelationIdInterceptor()]
    if service.config.grpc.max_in_flight_rpcs > 0:
        interceptors.append(ConcurrencyLimitInterceptor(service.config.grpc.max_in_flight_rpcs))
    server = grpc.aio.server(
        maximum_concurrent_rpcs=service.config.grpc.max_concurrent_rpcs,
        interceptors=interceptors,
    )
    register(server, service)

    bind_address = f"{service.config.grpc.host}:{service.config.grpc.port}"
    server.add_insecure_port(bind_address)
    await server.start()

    _log.info(
        "grpc_server_listening",
        address=bind_address,
    )
    return server, bind_address


def _build_uvicorn_server(service: Service) -> uvicorn.Server:
    """Construct the uvicorn server hosting the FastAPI dashboard.

    The server is configured but not started; the caller drives its
    lifecycle via :meth:`uvicorn.Server.serve` so we can interleave
    its shutdown with the gRPC server under one shared event. The
    asyncio loop is the one already running (``loop="asyncio"``) — we
    do not let uvicorn install its own.

    Args:
        service: The constructed service (provides config + the
            FastAPI app).

    Returns:
        An unstarted :class:`uvicorn.Server`.
    """
    app = create_app(service)
    config = uvicorn.Config(
        app=app,
        host=service.config.dashboard.host,
        port=service.config.dashboard.port,
        log_config=None,  # structlog owns logging; don't let uvicorn replace it
        loop="asyncio",
        lifespan="on",
        access_log=False,
    )
    return uvicorn.Server(config)


def _install_signal_handlers(shutdown_event: asyncio.Event) -> None:
    """Install SIGTERM / SIGINT handlers that set ``shutdown_event``.

    On POSIX platforms we use :meth:`asyncio.AbstractEventLoop.add_signal_handler`.
    On Windows, that method raises :class:`NotImplementedError`, so we
    fall back to :func:`signal.signal` and schedule the event-set call
    thread-safely onto the loop.
    """
    loop = asyncio.get_running_loop()

    def _posix_handler(sig_name: str) -> None:
        _log.info("signal_received", signal=sig_name)
        shutdown_event.set()

    signals = [(signal.SIGTERM, "SIGTERM"), (signal.SIGINT, "SIGINT")]

    try:
        for sig, name in signals:
            loop.add_signal_handler(sig, _posix_handler, name)
    except NotImplementedError:
        # Windows fallback.
        def _win_handler(signum: int, _frame: object) -> None:
            name = signal.Signals(signum).name
            _log.info("signal_received", signal=name)
            loop.call_soon_threadsafe(shutdown_event.set)

        for sig, _name in signals:
            signal.signal(sig, _win_handler)


async def _run(
    config: Config,
    *,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Build the service, start gRPC + uvicorn, and await shutdown.

    Testable core: pass an externally-owned ``shutdown_event`` and
    trigger it from a timer to drive the shutdown path without
    delivering real OS signals.
    """
    if shutdown_event is None:
        shutdown_event = asyncio.Event()

    _log.info(
        "service_starting",
        config_grpc_port=config.grpc.port,
        config_dashboard_port=config.dashboard.port,
    )

    service = await build_service(config)
    grpc_server: grpc.aio.Server | None = None
    rest_server: uvicorn.Server | None = None
    rest_serve_task: asyncio.Task[None] | None = None

    try:
        grpc_server, _bind_address = await _build_grpc_server(service)

        rest_server = _build_uvicorn_server(service)
        rest_serve_task = asyncio.create_task(
            rest_server.serve(),
            name="rest-server",
        )

        def _on_rest_task_done(task: asyncio.Task[None]) -> None:
            """Surface an unexpected death of the REST serve task.

            Without this the task's exception would go unretrieved and the
            main coroutine would block on ``shutdown_event.wait()`` forever —
            the dashboard dead but the process apparently healthy. On any
            non-cancellation exit before shutdown was requested, log at ERROR
            and set ``shutdown_event`` so the whole service exits (and a
            process supervisor can restart it) rather than running half-dead.
            """
            if task.cancelled():
                return
            exc = task.exception()
            if shutdown_event.is_set():
                # Expected: the shutdown path awaits this task after signalling.
                return
            if exc is not None:
                _log.error("rest_server_task_failed", error=str(exc), exc_info=exc)
            else:
                _log.error("rest_server_task_exited_unexpectedly")
            shutdown_event.set()

        rest_serve_task.add_done_callback(_on_rest_task_done)
        _log.info(
            "rest_server_listening",
            address=f"{config.dashboard.host}:{config.dashboard.port}",
        )

        # Kick off the three periodic background loops: orphan sweeper,
        # rendered-report retention pruner, and audit-log retention
        # pruner. All started AFTER both listeners are accepting
        # connections (accidental transaction collisions during startup
        # surface as request errors rather than crashing the service)
        # and BEFORE we block on ``shutdown_event.wait`` (otherwise
        # they never tick in production). Order between the three does
        # not matter — they share the L2-PERS-004 mutex and serialize
        # cleanly through the UoW factory regardless of who ticks first.
        service.sweeper_loop.start()
        service.report_pruner_loop.start()
        service.audit_log_pruner_loop.start()

        _log.info("service_running")

        await shutdown_event.wait()

        _log.info("service_stopping")

        grace = float(config.service.shutdown_grace_period_seconds)

        # Signal uvicorn to drain in-flight requests and exit. Then
        # gracefully stop the gRPC server. Both run in parallel under
        # the same grace budget; we collect their completions afterward.
        # ``return_exceptions=True`` so a listener that already died (e.g. the
        # REST task failed pre-shutdown and tripped the done-callback, or its
        # graceful drain errors) cannot abort the teardown before the
        # ``finally`` drains the scheduler and closes the DB. Any exception is
        # logged; the REST failure was already surfaced by the done-callback.
        rest_server.should_exit = True
        results = await asyncio.gather(
            grpc_server.stop(grace=grace),
            rest_serve_task,
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                _log.error("shutdown_task_error", error=str(result), exc_info=result)
    finally:
        # Always drain the scheduler + close the DB, even if either
        # listener failed to come up. ``shutdown_service`` is idempotent.
        await shutdown_service(
            service,
            timeout=float(config.service.shutdown_grace_period_seconds),
        )
        _log.info("service_stopped")


async def _async_main(argv: list[str] | None = None) -> int:
    """Async entrypoint. Returns a UNIX-style exit code."""
    config_path = _resolve_config_path(argv)
    try:
        config = load_config(config_path)
    except Exception as exc:  # noqa: BLE001 — top-level catch: logged + nonzero exit
        _log.error(
            "config_load_failed",
            path=str(config_path),
            error=str(exc),
            exc_info=True,
        )
        return 2  # conventional "bad config" exit code

    shutdown_event = asyncio.Event()
    _install_signal_handlers(shutdown_event)

    try:
        await _run(config, shutdown_event=shutdown_event)
    except Exception:  # noqa: BLE001 — top-level catch: logged + nonzero exit
        _log.exception("service_crashed")
        return 1
    return 0


# -----------------------------------------------------------------------------
# Sync entrypoint (python -m message_service)
# -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Sync CLI entrypoint suitable for ``python -m message_service``."""
    return asyncio.run(_async_main(argv))


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main"]
