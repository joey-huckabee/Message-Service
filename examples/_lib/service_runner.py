"""Subprocess wrapper for the Message-Service.

Each scenario starts the service as a subprocess so the demo
exercises the same boot path as production (`python -m
message_service --config <path>`). The runner polls until both
listeners (gRPC + dashboard) are bound, then yields control to the
demo. On exit it sends SIGTERM (Windows: terminates) and waits for
clean shutdown.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO


@contextmanager
def running_service(
    config_path: Path,
    *,
    grpc_port: int,
    dashboard_port: int,
    boot_timeout: float = 15.0,
    log_prefix: str = "[service]",
) -> Iterator[subprocess.Popen[str]]:
    """Start the service as a subprocess; tear it down on context exit.

    The subprocess inherits the demo's environment (notably
    ``MESSAGE_SERVICE_*`` env vars used by config substitution). Its
    stdout + stderr are streamed to the demo's stdout with a prefix so
    structured-log lines appear interleaved with the demo's own
    output.

    Args:
        config_path: TOML config file the service will load.
        grpc_port: Expected gRPC bind port (probed for readiness).
        dashboard_port: Expected dashboard bind port (probed for readiness).
        boot_timeout: Seconds to wait for both ports to bind before
            giving up.
        log_prefix: Prefix attached to every line streamed from the
            service's stdout.

    Yields:
        The :class:`subprocess.Popen` handle (exposed for tests that
        want to read return codes).
    """
    env = dict(os.environ)
    proc = subprocess.Popen(
        [sys.executable, "-m", "message_service", "--config", str(config_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        env=env,
        bufsize=1,  # line-buffered
    )
    streamer = threading.Thread(
        target=_stream_output,
        args=(proc.stdout, log_prefix),
        daemon=True,
    )
    streamer.start()
    try:
        _wait_for_port("127.0.0.1", grpc_port, boot_timeout, "gRPC")
        _wait_for_port("127.0.0.1", dashboard_port, boot_timeout, "dashboard")
        yield proc
    finally:
        if proc.poll() is None:
            if sys.platform == "win32":
                proc.terminate()
            else:
                proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5.0)


def _wait_for_port(host: str, port: int, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect((host, port))
                return
            except OSError:
                time.sleep(0.2)
    raise TimeoutError(f"{label} port {host}:{port} did not bind within {timeout}s")


def _stream_output(stream: IO[str] | None, prefix: str) -> None:
    if stream is None:
        return
    try:
        for line in stream:
            line = line.rstrip("\n")
            print(f"{prefix} {line}", flush=True)
    except Exception:  # noqa: BLE001 — best-effort streaming; subprocess pipe close is normal at shutdown
        return
