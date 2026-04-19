"""Password hashing benchmark (L3-AUTH-003).

Asserts that Argon2id password hashing completes in the 50-500 ms band on
CI hardware. Outside this band, the configured ``memory_cost``,
``time_cost``, and ``parallelism`` parameters SHALL be reviewed.

Excluded from the default pytest run; invoke explicitly with:

    poetry run pytest tests/benchmarks/ -m benchmark
"""

from __future__ import annotations

# TODO(L3-AUTH-003): implement once password hasher is in place.
