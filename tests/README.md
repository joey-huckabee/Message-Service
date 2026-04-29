# Message-Service — Test Suite

The formal test-strategy document for this project lives at [`docs/test-strategy.md`](../docs/test-strategy.md). Start there.

This file remains as a brief in-repo orientation. For the conventions binding on contributors — tier definitions, fixture scoping, the `@pytest.mark.requirement` marker, the I/O guard, the SMTP capture, the Windows event-loop quirks, how to run subsets, and how to add a new test — read `docs/test-strategy.md`.

## Quick reference

```bash
# Fast dev loop — unit only, skip slow
poetry run pytest -m "unit and not slow"

# What CI runs on PRs
poetry run pytest -m "not e2e"

# Everything (what CI runs on main)
poetry run pytest

# Skip the coverage gate during iteration
poetry run pytest --no-cov

# Tests for a specific requirement
poetry run python scripts/pytest-by-requirement.py L3-RUN-007
```

## When you change a test

After adding / removing / re-marking a test, regenerate the trace matrix:

```bash
poetry run python scripts/build-trace-matrix.py
```

Commit `docs/TRACE-MATRIX.md` alongside the test change. The conformance test
`tests/conformance/test_trace_matrix_check_mode.py` fails the build if the
matrix is stale.
