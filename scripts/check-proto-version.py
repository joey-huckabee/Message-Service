#!/usr/bin/env python3
"""Assert the installed proto version matches the pinned tag (L3-API-004).

The ``message-service-proto`` dependency is pinned in ``pyproject.toml`` as a
tag-pinned git URL (``tag = "vX.Y.Z"``, per L3-API-003). This gate asserts that
the *installed* ``message_service_proto.__version__`` equals that pinned tag
(leading ``v`` stripped), catching the case where the lockfile resolves — or the
environment has installed — a different proto version than the manifest pins.

Exit-code contract (L3-API-004):

* **0** — match: installed version equals the pinned tag.
* **1** — mismatch: the two disagree (both are named in the diagnostic).
* **2** — undeterminable: the dependency, its tag, or ``__version__`` is missing.

The comparison helpers (:func:`pinned_proto_tag`, :func:`normalize_tag`,
:func:`installed_proto_version`, :func:`evaluate`) are importable so the
conformance test can exercise the match / mismatch / undeterminable outcomes
without shelling out.

Run from the project root::

    poetry run python scripts/check-proto-version.py
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = ROOT / "pyproject.toml"

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_UNDETERMINABLE = 2

_DEP_NAME = "message-service-proto"


def pinned_proto_tag(pyproject_text: str) -> str | None:
    """Return the ``tag`` pinned for the proto dependency, or ``None``.

    Args:
        pyproject_text: Contents of a ``pyproject.toml`` file.

    Returns:
        The tag string (e.g. ``"v0.1.1"``), or ``None`` if the dependency or its
        ``tag`` key is absent (or the dependency is not a git+tag table).
    """
    data = tomllib.loads(pyproject_text)
    dep = data.get("tool", {}).get("poetry", {}).get("dependencies", {}).get(_DEP_NAME)
    if not isinstance(dep, dict):
        return None
    tag = dep.get("tag")
    return tag if isinstance(tag, str) else None


def normalize_tag(tag: str) -> str:
    """Strip a single leading ``v`` from a version tag (``v0.1.1`` → ``0.1.1``)."""
    return tag[1:] if tag.startswith("v") else tag


def installed_proto_version() -> str | None:
    """Return the installed ``message_service_proto.__version__``, or ``None``."""
    try:
        import message_service_proto
    except ImportError:
        return None
    version = getattr(message_service_proto, "__version__", None)
    return version if isinstance(version, str) else None


def evaluate(pinned_tag: str | None, installed_version: str | None) -> tuple[int, str]:
    """Compare a pinned tag against an installed version.

    Args:
        pinned_tag: The tag from ``pyproject.toml`` (e.g. ``"v0.1.1"``), or None.
        installed_version: The installed ``__version__`` (e.g. ``"0.1.1"``), or None.

    Returns:
        ``(exit_code, message)`` per the L3-API-004 contract.
    """
    if pinned_tag is None or installed_version is None:
        return (
            EXIT_UNDETERMINABLE,
            "cannot determine proto version: "
            f"pinned_tag={pinned_tag!r}, installed_version={installed_version!r}",
        )
    expected = normalize_tag(pinned_tag)
    if installed_version == expected:
        return (
            EXIT_OK,
            f"proto version OK: pinned {pinned_tag!r} == installed {installed_version!r}",
        )
    return (
        EXIT_MISMATCH,
        f"proto version MISMATCH: pyproject pins {pinned_tag!r} (expected "
        f"{expected!r}) but installed message_service_proto.__version__ is "
        f"{installed_version!r}",
    )


def main(argv: list[str] | None = None) -> int:
    """Run the proto-version check and return the process exit code.

    Args:
        argv: Unused; accepted for argparse-style symmetry.

    Returns:
        One of ``EXIT_OK`` / ``EXIT_MISMATCH`` / ``EXIT_UNDETERMINABLE``.
    """
    try:
        pyproject_text = PYPROJECT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read {PYPROJECT_PATH}: {exc}", file=sys.stderr)
        return EXIT_UNDETERMINABLE

    exit_code, message = evaluate(pinned_proto_tag(pyproject_text), installed_proto_version())
    print(message, file=sys.stderr if exit_code else sys.stdout)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
