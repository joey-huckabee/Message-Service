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
4. Constructs a ``grpc.aio.server``, registers the servicer via
   :func:`message_service.interfaces.grpc.servicer.register`, and
   binds an insecure TCP port from ``config.grpc``.
5. Installs SIGTERM and SIGINT handlers that set an internal
   :class:`asyncio.Event`. On platforms where
   ``loop.add_signal_handler`` is unsupported (Windows), falls back
   to ``signal.signal`` with a thread-safe event trigger.
6. Starts the server and awaits the shutdown event.
7. On shutdown: gracefully stops the gRPC server (bounded by
   ``config.service.shutdown_grace_period_seconds``), then calls
   :func:`message_service.bootstrap.shutdown_service` to drain
   background tasks and close the SQLite connection.

Requirement references
----------------------
L1-DEP-001 (single-process service)
L1-API-001, L1-API-003 (gRPC plaintext listener)
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

from message_service.bootstrap import Service, build_service, shutdown_service
from message_service.config.loader import load_config
from message_service.config.schema import Config
from message_service.interfaces.grpc.servicer import register

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
    server = grpc.aio.server()
    register(server, service)

    bind_address = f"{service.config.grpc.host}:{service.config.grpc.port}"
    server.add_insecure_port(bind_address)
    await server.start()

    _log.info(
        "grpc_server_listening",
        address=bind_address,
    )
    return server, bind_address


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
    """Build the service, start the gRPC server, and await shutdown.

    Testable core: pass an externally-owned ``shutdown_event`` and
    trigger it from a timer to drive the shutdown path without
    delivering real OS signals.
    """
    if shutdown_event is None:
        shutdown_event = asyncio.Event()

    _log.info("service_starting", config_grpc_port=config.grpc.port)

    service = await build_service(config)
    server: grpc.aio.Server | None = None

    try:
        server, _bind_address = await _build_grpc_server(service)

        # Kick off the orphan sweeper. Must happen AFTER the gRPC
        # server is accepting connections (so any accidental
        # transaction collisions during startup surface as RPC
        # errors rather than crashing the service) and BEFORE we
        # block on ``shutdown_event.wait`` (otherwise the sweeper
        # never ticks in production).
        service.sweeper_loop.start()

        _log.info("service_running")

        await shutdown_event.wait()

        _log.info("service_stopping")

        # Grace period for in-flight RPCs to finish. ``stop(grace=None)``
        # is hard abort; ``stop(grace=N)`` waits up to N seconds, then
        # cancels stragglers.
        await server.stop(grace=float(config.service.shutdown_grace_period_seconds))
    finally:
        # Always drain the scheduler + close the DB, even if the gRPC
        # server failed to come up. ``shutdown_service`` is idempotent.
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
    except Exception as exc:
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
    except Exception:
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
