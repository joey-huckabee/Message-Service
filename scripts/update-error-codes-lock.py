#!/usr/bin/env python3
"""Regenerate ``docs/error-codes.lock`` from the current proto enum (L3-ERR-011).

Deterministically rewrites the error-code stability lockfile to mirror the
current proto ``ErrorCode`` enum. Run this whenever a new error code is added
(the ``check-error-code-stability.py`` gate exits ``2`` until the lockfile is
regenerated and committed); the diff on ``docs/error-codes.lock`` then makes the
addition — or any accidental removal/rename — visible at PR review.

The lockfile format and the current-enum read live in
``check-error-code-stability.py``; this script loads that module by file path
(its hyphenated CLI name blocks a normal import) so the two stay in lockstep.

Run from the project root::

    poetry run python scripts/update-error-codes-lock.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_CHECK_SCRIPT = _SCRIPT_DIR / "check-error-code-stability.py"


def _load_check_module() -> object:
    """Load the sibling check script as a module by file path.

    Returns:
        The imported ``check-error-code-stability`` module object.

    Raises:
        ImportError: The check script could not be located or executed.
    """
    spec = importlib.util.spec_from_file_location("check_error_code_stability", _CHECK_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {_CHECK_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the module's @dataclass can resolve its own
    # annotations via sys.modules (dataclasses looks the owning module up there).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    """Regenerate the lockfile from the current proto enum.

    Returns:
        Process exit code (always ``0`` on success).
    """
    check = _load_check_module()
    codes = check.current_error_codes()  # type: ignore[attr-defined]
    text = check.render_lockfile(codes)  # type: ignore[attr-defined]
    lock_path: Path = check.LOCK_PATH  # type: ignore[attr-defined]
    # newline="\n" keeps the file LF on every platform so a Windows regenerate
    # produces byte-identical output to the committed (LF) lockfile.
    lock_path.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {lock_path} ({len(codes)} codes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
