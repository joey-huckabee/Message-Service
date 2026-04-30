"""Stdlib-only colored / timestamped output for demo scripts.

No external dependencies. Color is on by default for TTY stdout;
respects ``NO_COLOR`` env var (https://no-color.org/) and disables
itself when stdout is not a TTY.
"""

from __future__ import annotations

import contextlib
import os
import sys
from datetime import datetime

# Force UTF-8 on stdout/stderr so the demos render the same on Windows
# (cp1252 default), Linux, and macOS. Without this the `✓` / `✗`
# characters and any arrows in scenario messages raise UnicodeEncodeError
# under the legacy Windows console codepage. Wrapped in a guard because
# `reconfigure` only exists on `io.TextIOWrapper`-shaped streams.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if callable(_reconfigure):
        with contextlib.suppress(OSError, ValueError):
            _reconfigure(encoding="utf-8", errors="replace")

_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"


def _color_enabled() -> bool:
    if "NO_COLOR" in os.environ:
        return False
    return sys.stdout.isatty()


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _color(text: str, code: str) -> str:
    if not _color_enabled():
        return text
    return f"{code}{text}{_RESET}"


def step(n: int, msg: str) -> None:
    """Print a numbered ``Step N: …`` line."""
    label = _color(f"Step {n}", _BOLD + _CYAN)
    print(f"[{_ts()}] {label}: {msg}", flush=True)


def info(msg: str) -> None:
    print(f"[{_ts()}] {msg}", flush=True)


def success(msg: str) -> None:
    print(f"[{_ts()}] {_color('✓', _GREEN)} {msg}", flush=True)


def failure(msg: str) -> None:
    print(f"[{_ts()}] {_color('✗', _RED)} {msg}", flush=True, file=sys.stderr)


def warn(msg: str) -> None:
    print(f"[{_ts()}] {_color('!', _YELLOW)} {msg}", flush=True)


def header(title: str) -> None:
    bar = "-" * len(title)
    print(f"\n{_color(title, _BOLD)}\n{_color(bar, _DIM)}", flush=True)


def detail(msg: str) -> None:
    """Indented, dim line for verbose detail."""
    print(f"   {_color(msg, _DIM)}", flush=True)
