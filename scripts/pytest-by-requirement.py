#!/usr/bin/env python3
"""Run pytest filtered by requirement marker value.

Pytest's ``-m`` expression language does not natively support matching
the string argument of a parameterised marker, so this wrapper walks the
collected items and selects those carrying a ``requirement`` marker whose
first argument matches the requested id.

Usage:
    ./scripts/pytest-by-requirement.py L3-RUN-007
    ./scripts/pytest-by-requirement.py L3-STAGE-      # substring prefix match
    ./scripts/pytest-by-requirement.py L3-RUN-007 -- -v --tb=short

Everything after ``--`` is forwarded to pytest. When no ``--`` is given,
``-v`` is added by default.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    """Collect and run only the tests whose requirement marker matches the filter."""
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    req_filter = sys.argv[1]

    # split args on "--"
    extra_args: list[str] = []
    if "--" in sys.argv:
        idx = sys.argv.index("--")
        extra_args = sys.argv[idx + 1 :]
    else:
        extra_args = ["-v"]

    # Collect test ids whose requirement marker matches req_filter.
    # Strategy: run pytest --collect-only -q, then filter via grep -l on the
    # source lines that carry the marker. This keeps the script dependency-free.
    collect = subprocess.run(
        ["python3", "-m", "pytest", "--collect-only", "-q", "tests/"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"},
    )
    test_ids = [
        line.strip()
        for line in collect.stdout.splitlines()
        if "::" in line and not line.startswith("=")
    ]

    # For each test id, read the file and check whether the function has the
    # requirement marker. This is approximate (doesn't parse the AST), but
    # good enough for the common case of one marker per test.
    selected: list[str] = []
    for test_id in test_ids:
        file_part = test_id.split("::")[0]
        func_part = test_id.rsplit("::", 1)[-1].split("[")[0]
        try:
            source = (ROOT / file_part).read_text()
        except OSError:
            continue
        # Look for requirement marker within 10 lines before the function def.
        lines = source.splitlines()
        for idx, line in enumerate(lines):
            if f"def {func_part}(" in line:
                window = "\n".join(lines[max(0, idx - 10) : idx])
                if f'requirement("{req_filter}' in window:
                    selected.append(test_id)
                break

    if not selected:
        print(f"No tests found matching requirement filter {req_filter!r}")
        return 1

    print(f"Selected {len(selected)} tests matching {req_filter!r}:")
    for t in selected:
        print(f"  {t}")
    print()

    result = subprocess.run(
        ["python3", "-m", "pytest", *selected, *extra_args],
        cwd=ROOT,
        check=False,
        env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"},
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
