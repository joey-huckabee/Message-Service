"""Tests for :mod:`message_service.__main__`.

What's tested
-------------
* Argument parsing — ``--config`` wins over ``$MESSAGE_SERVICE_CONFIG``;
  absence of both fails; env-var fallback works.
* ``_run`` lifecycle — starts the gRPC server, accepts an RPC, and
  cleanly shuts down on event trigger.
* ``_async_main`` — bad config path returns exit code 2;
  missing config returns non-zero via SystemExit from argparse.

What's not tested here
----------------------
Signal-handler installation — the platform dispatch in
``_install_signal_handlers`` is exercised indirectly by tests that
go through ``_async_main``. Testing real OS-signal delivery is
flaky and platform-specific, so those paths rely on manual
verification (see the smoke test in the increment log).
"""

from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path
from unittest.mock import patch

import grpc
import pytest
from message_service_proto.v1 import message_service_pb2 as pb
from message_service_proto.v1 import message_service_pb2_grpc as pb_grpc

from message_service.__main__ import (
    _async_main,
    _resolve_config_path,
    _run,
    main,
)
from message_service.config.loader import load_config

# -----------------------------------------------------------------------------
# Fixtures — minimal valid config on disk
# -----------------------------------------------------------------------------


def _find_free_port() -> int:
    """Return an OS-assigned free TCP port on the loopback interface.

    Binding to port 0 lets the kernel choose a port that is guaranteed
    free AND outside any reserved range. This matters on Windows, where
    Hyper-V/WSL/Docker reserve blocks of ports (visible via ``netsh
    interface ipv4 show excludedportrange protocol=tcp``); a hard-coded
    port that lands inside such a block cannot be bound by any process
    and fails with ``WinError 10013`` — surfacing as gRPC's
    ``Failed to bind to address ...; port == 0``. The kernel never hands
    out a reserved port for an ephemeral bind, so this avoids the whole
    class of collision. The socket is closed immediately; a bind-only
    socket (never listened/connected) leaves no ``TIME_WAIT`` behind.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def _write_config(
    tmp_path: Path,
    grpc_port: int = 55082,
    dashboard_port: int = 58082,
) -> Path:
    """Write a minimal valid Config TOML + backing files; return its path."""
    (tmp_path / "body.html.j2").write_text("<p>{{ run_id }}</p>")
    (tmp_path / "frag.html.j2").write_text("<p>{{ v }}</p>")
    (tmp_path / "agg.html.j2").write_text(
        "<html>{% for s in stages %}{{ s.rendered_html | safe }}{% endfor %}</html>"
    )
    (tmp_path / "templates.toml").write_text(
        """
[[template]]
name = "email_body"
version = "1.0"
kind = "EMAIL_BODY"
source_path = "body.html.j2"

[[template]]
name = "frag"
version = "1.0"
kind = "REPORT_FRAGMENT"
source_path = "frag.html.j2"

[[template]]
name = "agg"
version = "1.0"
kind = "AGGREGATION"
source_path = "agg.html.j2"
"""
    )
    (tmp_path / "tags.toml").write_text('[[tag]]\nname = "production"\n')

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f"""
[grpc]
host = "127.0.0.1"
port = {grpc_port}

[dashboard]
host = "127.0.0.1"
port = {dashboard_port}
https_only = false

[persistence]
sqlite_path = "{(tmp_path / "svc.db").as_posix()}"

[persistence.filesystem]
report_directory = "{(tmp_path / "reports").as_posix()}"

[templates]
manifest_path = "{(tmp_path / "templates.toml").as_posix()}"

[templates.email_body_template_ref]
name = "email_body"
version = "1.0"

[tags]
vocabulary_path = "{(tmp_path / "tags.toml").as_posix()}"

[pipelines]
registered = ["etl-nightly"]

[mail]
from_address = "svc@example.com"

[mail.smtp]
host = "smtp.example.com"
port = 587
username = "u"
password = "p"

[service]
shutdown_grace_period_seconds = 2
"""
    )
    return cfg_path


# -----------------------------------------------------------------------------
# Arg parsing
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-DEP-006")
@pytest.mark.requirement("L3-CFG-001")
@pytest.mark.requirement("L3-CFG-002")
def test_resolve_config_path_uses_cli_arg(tmp_path: Path) -> None:
    cfg = tmp_path / "my.toml"
    cfg.write_text("")
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MESSAGE_SERVICE_CONFIG", None)
        assert _resolve_config_path(["--config", str(cfg)]) == cfg


@pytest.mark.requirement("L3-CFG-003")
def test_resolve_config_path_prefers_cli_over_env(tmp_path: Path) -> None:
    cli_cfg = tmp_path / "cli.toml"
    cli_cfg.write_text("")
    env_cfg = tmp_path / "env.toml"
    env_cfg.write_text("")
    with patch.dict(os.environ, {"MESSAGE_SERVICE_CONFIG": str(env_cfg)}):
        assert _resolve_config_path(["--config", str(cli_cfg)]) == cli_cfg


def test_resolve_config_path_falls_back_to_env(tmp_path: Path) -> None:
    env_cfg = tmp_path / "env.toml"
    env_cfg.write_text("")
    with patch.dict(os.environ, {"MESSAGE_SERVICE_CONFIG": str(env_cfg)}):
        assert _resolve_config_path([]) == env_cfg


def test_resolve_config_path_errors_when_both_missing() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MESSAGE_SERVICE_CONFIG", None)
        with pytest.raises(SystemExit):
            _resolve_config_path([])


# -----------------------------------------------------------------------------
# _run lifecycle — real gRPC server, real config
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-DEP-001")
@pytest.mark.requirement("L3-DEP-011")
@pytest.mark.allow_io
async def test_run_starts_server_and_shuts_down_on_event(tmp_path: Path) -> None:
    """L3-DEP-011: shutdown SHALL be driven by an `asyncio.Event` that
    `_run` blocks on; setting it returns control cleanly to the caller.
    Also exercises the L1-DEP-001 dual-platform startup invariant.
    """
    # OS-assigned free ports avoid collisions with other tests and with
    # Windows reserved port ranges (see _find_free_port).
    cfg_path = _write_config(
        tmp_path, grpc_port=_find_free_port(), dashboard_port=_find_free_port()
    )
    config = load_config(cfg_path)
    shutdown = asyncio.Event()

    async def trigger() -> None:
        # Give the server a moment to bind before we signal shutdown.
        await asyncio.sleep(0.2)
        shutdown.set()

    trigger_task = asyncio.create_task(trigger())
    try:
        await _run(config, shutdown_event=shutdown)
    finally:
        if not trigger_task.done():
            trigger_task.cancel()


@pytest.mark.asyncio
@pytest.mark.allow_io
async def test_run_server_accepts_rpc_while_listening(tmp_path: Path) -> None:
    """While ``_run`` is active the server SHALL answer a real BeginRun RPC."""
    grpc_port = _find_free_port()
    cfg_path = _write_config(tmp_path, grpc_port=grpc_port, dashboard_port=_find_free_port())
    config = load_config(cfg_path)
    shutdown = asyncio.Event()

    async def exercise() -> bool:
        # Wait for the listener.
        await asyncio.sleep(0.3)
        try:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{grpc_port}") as channel:
                stub = pb_grpc.MessageServiceStub(channel)
                response = await stub.BeginRun(
                    pb.BeginRunRequest(
                        pipeline_type="etl-nightly",
                        declared_stages=[
                            pb.DeclaredStage(
                                stage_id="extract",
                                stage_order=0,
                                report_template=pb.TemplateRef(name="frag", version="1.0"),
                            ),
                        ],
                        attachment_mode=pb.ATTACHMENT_MODE_PER_STAGE,
                    )
                )
                return bool(response.run_id)
        finally:
            shutdown.set()

    exercise_task = asyncio.create_task(exercise())
    try:
        await _run(config, shutdown_event=shutdown)
        accepted = await exercise_task
    finally:
        if not exercise_task.done():
            exercise_task.cancel()
        # Let the Windows ProactorEventLoop release socket-level
        # resources before the test loop closes, preventing
        # PytestUnraisableExceptionWarning at session cleanup.
        await asyncio.sleep(0)

    assert accepted is True


# -----------------------------------------------------------------------------
# _async_main — exit codes
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_main_returns_2_on_bad_config_path(tmp_path: Path) -> None:
    """A nonexistent config path SHALL yield exit code 2."""
    bogus = tmp_path / "does-not-exist.toml"
    exit_code = await _async_main(["--config", str(bogus)])
    assert exit_code == 2


# -----------------------------------------------------------------------------
# Sync main() wrapper
# -----------------------------------------------------------------------------


def test_main_wraps_async_main_with_asyncio_run(tmp_path: Path) -> None:
    """``main`` SHALL invoke the async entrypoint via ``asyncio.run`` and
    propagate its exit code."""
    bogus = tmp_path / "does-not-exist.toml"
    exit_code = main(["--config", str(bogus)])
    assert exit_code == 2


def test_main_with_missing_config_arg_exits_via_argparse() -> None:
    """Missing ``--config`` with no env var SHALL SystemExit via argparse."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MESSAGE_SERVICE_CONFIG", None)
        with pytest.raises(SystemExit):
            main([])


# -----------------------------------------------------------------------------
# Signal handler installation (L3-DEP-010)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DEP-010")
async def test_install_signal_handlers_registers_sigterm_and_sigint() -> None:
    """L3-DEP-010: SIGTERM and SIGINT SHALL be installed, dispatching by
    platform between `loop.add_signal_handler` (POSIX) and
    `signal.signal` (Windows).

    On POSIX this SHALL succeed via `add_signal_handler`. On Windows
    `add_signal_handler` raises NotImplementedError; the fallback
    `signal.signal(...)` path is exercised. Either way, the call
    SHALL NOT raise.
    """
    from message_service.__main__ import _install_signal_handlers

    event = asyncio.Event()
    # Should not raise on either platform.
    _install_signal_handlers(event)
    # The event SHALL still be unset (handlers don't fire just from
    # registration).
    assert not event.is_set()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DEP-010")
async def test_install_signal_handlers_falls_back_when_add_signal_handler_unavailable() -> None:
    """L3-DEP-010: when `add_signal_handler` raises NotImplementedError
    (the Windows path), `signal.signal` SHALL be used instead.

    Mocks `loop.add_signal_handler` to always raise so the fallback
    fires regardless of host platform; verifies `signal.signal` was
    called for both SIGTERM and SIGINT.
    """
    import signal as _signal

    from message_service.__main__ import _install_signal_handlers

    event = asyncio.Event()
    loop = asyncio.get_running_loop()
    captured_signals: list[int] = []

    def _fake_signal(signum: int, _handler: object) -> object:
        captured_signals.append(signum)
        return None

    with (
        patch.object(loop, "add_signal_handler", side_effect=NotImplementedError),
        patch.object(_signal, "signal", side_effect=_fake_signal),
    ):
        _install_signal_handlers(event)

    # Both SIGTERM and SIGINT SHALL have been registered via the fallback.
    assert _signal.SIGTERM in captured_signals
    assert _signal.SIGINT in captured_signals


# -----------------------------------------------------------------------------
# Grace-period bounded shutdown (L3-DEP-012)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DEP-012")
@pytest.mark.allow_io
async def test_run_propagates_shutdown_grace_period_to_grpc_stop(
    tmp_path: Path,
) -> None:
    """L3-DEP-012: in-flight gRPC calls SHALL have
    `service.shutdown_grace_period_seconds` to complete before being
    force-cancelled.

    The grace value flows through to `grpc_server.stop(grace=...)`.
    We patch the actual ``grpc.aio._server.Server.stop`` (the
    runtime class, not the ``grpc.aio.Server`` re-export) to capture
    the argument and assert the propagation. The actual force-cancel
    timing on long-running RPCs is exercised by a separate
    Demonstration per the Verification Method on L2-DEP-006.
    """
    from grpc.aio import _server as _grpc_aio_server

    cfg_path = _write_config(
        tmp_path, grpc_port=_find_free_port(), dashboard_port=_find_free_port()
    )
    config = load_config(cfg_path)
    expected_grace = float(config.service.shutdown_grace_period_seconds)
    assert expected_grace > 0  # sanity

    captured_graces: list[float] = []
    real_stop = _grpc_aio_server.Server.stop

    async def _capturing_stop(self: _grpc_aio_server.Server, grace: float | None) -> None:
        captured_graces.append(grace if grace is not None else -1.0)
        # Pass through to the real stop so the server actually shuts down.
        await real_stop(self, grace=0)

    shutdown = asyncio.Event()

    async def trigger() -> None:
        await asyncio.sleep(0.2)
        shutdown.set()

    trigger_task = asyncio.create_task(trigger())
    try:
        with patch.object(_grpc_aio_server.Server, "stop", new=_capturing_stop):
            await _run(config, shutdown_event=shutdown)
    finally:
        if not trigger_task.done():
            trigger_task.cancel()
        await asyncio.sleep(0)

    # `grpc_server.stop` SHALL have been called with the configured grace.
    assert captured_graces, "grpc_server.stop was never invoked during shutdown"
    assert captured_graces[0] == expected_grace, (
        f"expected grace={expected_grace}, got {captured_graces[0]}"
    )
