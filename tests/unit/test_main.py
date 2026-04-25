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


def _write_config(tmp_path: Path, grpc_port: int = 55082) -> Path:
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
port = 8080

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
def test_resolve_config_path_uses_cli_arg(tmp_path: Path) -> None:
    cfg = tmp_path / "my.toml"
    cfg.write_text("")
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MESSAGE_SERVICE_CONFIG", None)
        assert _resolve_config_path(["--config", str(cfg)]) == cfg


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
@pytest.mark.allow_io
async def test_run_starts_server_and_shuts_down_on_event(tmp_path: Path) -> None:
    """``_run`` SHALL start a gRPC server, await the shutdown event, and
    tear everything down cleanly."""
    # Choose a port unlikely to collide with other tests.
    cfg_path = _write_config(tmp_path, grpc_port=55090)
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
    cfg_path = _write_config(tmp_path, grpc_port=55091)
    config = load_config(cfg_path)
    shutdown = asyncio.Event()

    async def exercise() -> bool:
        # Wait for the listener.
        await asyncio.sleep(0.3)
        try:
            async with grpc.aio.insecure_channel("127.0.0.1:55091") as channel:
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
