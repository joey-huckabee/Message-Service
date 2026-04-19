# Contributing to Message-Service

This guide documents the commands to run before every commit so pre-commit hooks pass on the first try.

## TL;DR — before every commit

Run these in order from the repo root. Each one must succeed before moving to the next.

```bash
# 1. Fast formatting — auto-fixes most style issues
poetry run ruff format .

# 2. Lint with auto-fix — catches rule violations, auto-fixes the safe ones
poetry run ruff check . --fix

# 3. Type check — mypy in strict mode
poetry run mypy src tests

# 4. Run the test suite
poetry run pytest

# 5. Regenerate the trace matrix (if you added requirement markers)
poetry run python scripts/build-trace-matrix.py

# 6. Final sanity: run pre-commit against every file
poetry run pre-commit run --all-files
```

If steps 1–5 succeed, step 6 should be a no-op. If step 6 changes any file, `git add` those changes and amend.

---

## First-time setup

Clone the repo and install dev dependencies:

```bash
git clone <repo-url> Message-Service
cd Message-Service
poetry install
poetry run pre-commit install
```

`pre-commit install` wires the hooks into `.git/hooks/pre-commit` so they fire automatically on `git commit`. You can run them manually with `poetry run pre-commit run --all-files` at any time.

Verify everything works:

```bash
poetry run pytest
```

Expect: all tests passing (currently 99).

---

## The pre-commit hooks, explained

`.pre-commit-config.yaml` runs these in order. Understanding what each one does tells you which of the TL;DR commands to re-run when one fails.

### From `pre-commit-hooks`

| Hook                        | What it checks                                     | Manual fix                                           |
|-----------------------------|----------------------------------------------------|------------------------------------------------------|
| `trailing-whitespace`       | Lines ending with spaces/tabs                      | `poetry run ruff format .` (catches most)            |
| `end-of-file-fixer`         | Every file ends with exactly one newline           | Run pre-commit; it auto-fixes                        |
| `check-yaml`                | `.yaml` / `.yml` files parse                       | Manually inspect the file                            |
| `check-toml`                | `.toml` files parse                                | Manually inspect the file                            |
| `check-added-large-files`   | No files >500 KB accidentally committed            | Remove the file; use git-lfs if it's legitimate      |
| `check-merge-conflict`      | No `<<<<<<<` markers left behind                   | Finish the merge                                     |
| `mixed-line-ending`         | All files use LF line endings                      | `poetry run ruff format .` normalizes                |

### From `ruff-pre-commit`

| Hook          | What it checks              | Manual fix                      |
|---------------|-----------------------------|---------------------------------|
| `ruff`        | Lint rules (with `--fix`)   | `poetry run ruff check . --fix` |
| `ruff-format` | Code formatting             | `poetry run ruff format .`      |

### From `mirrors-mypy`

| Hook   | What it checks                                  | Manual fix                     |
|--------|-------------------------------------------------|--------------------------------|
| `mypy` | Strict type checking against `pyproject.toml`   | `poetry run mypy src tests`    |

---

## Writing new code — checklist

Before you open a PR, walk through this list:

### 1. Does every new test have a requirement marker?

Every test function SHALL carry `@pytest.mark.requirement("L<N>-<CAT>-<NNN>")` tying it to a Level 1, 2, or 3 SHALL statement in `docs/L1-REQ.md`, `docs/L2-REQ.md`, or `docs/L3-REQ.md`. The `scripts/build-trace-matrix.py` tool uses these markers to populate the verification artifact columns.

```python
import pytest

@pytest.mark.requirement("L3-RUN-007")
def test_every_state_pair_respects_transition_table():
    ...
```

Multiple markers are permitted when one test verifies multiple requirements.

### 2. Do exceptions carry structured details?

Every exception raised in the codebase SHALL derive from `MessageServiceError` and SHOULD carry a `details` dict with machine-parseable diagnostic fields:

```python
# Good
raise UnknownTag(
    f"tag {tag!r} not in vocabulary",
    details={"tag": tag, "allowed": sorted(vocabulary)},
)

# Avoid — no structured detail
raise UnknownTag(f"tag {tag!r} not in vocabulary ({vocabulary})")
```

See `docs/LOGGING-AND-EXCEPTIONS.md` for the exception philosophy.

### 3. Does every log call use structured events?

Events are snake_case nouns or verb phrases, followed by keyword fields:

```python
# Good
logger.info("run_finalized", run_id=run_id, stage_count=len(stages))

# Avoid — free-form text, hard to search
logger.info(f"Finalized run {run_id} with {len(stages)} stages")
```

### 4. Is time injected, not fetched directly?

Domain and application code SHALL accept a `Clock` parameter. Only the production `SystemClock` in `infrastructure/time/` calls `datetime.now`.

```python
# Good
class FinalizeRun:
    def __init__(self, clock: Clock, ...):
        self._clock = clock

    def execute(self):
        now = self._clock.now()
        ...

# Avoid — untestable
def execute():
    now = datetime.now(tz=UTC)
    ...
```

### 5. Is filesystem access via pathlib?

```python
# Good
from pathlib import Path
config_path = Path(config_dir) / "default.toml"

# Avoid — ruff PTH rule will reject this
config_path = os.path.join(config_dir, "default.toml")
```

### 6. Does the trace matrix build cleanly?

After adding test markers or requirement statements:

```bash
poetry run python scripts/build-trace-matrix.py
```

Check the "Marker reference check" section of the output — any markers pointing at non-existent requirements are flagged there.

---

## Running subsets of tests

### By layer

```bash
poetry run pytest -m unit                    # fast, no I/O
poetry run pytest -m integration             # real SQLite, filesystem, SMTP
poetry run pytest -m e2e                     # full service black-box
poetry run pytest -m "not slow"              # skip long-running tests
```

### By requirement id

Use the helper script (substring prefix match works):

```bash
poetry run python scripts/pytest-by-requirement.py L3-RUN-007
poetry run python scripts/pytest-by-requirement.py L3-STAGE-    # all L3-STAGE-*
poetry run python scripts/pytest-by-requirement.py L2-MAIL-006 -- -v --tb=short
```

### By path

```bash
poetry run pytest tests/unit/domain/         # just the domain layer
poetry run pytest tests/unit/application/ports/test_clock.py
```

### By test name substring

```bash
poetry run pytest -k "state_machine"
poetry run pytest -k "in_progress and not reserved"
```

---

## Common failure modes and fixes

### `ruff format` changes files during pre-commit

**Cause**: You skipped step 1 of the TL;DR.
**Fix**: `poetry run ruff format .` then `git add -u && git commit --amend --no-edit`.

### `mypy` fails on strict mode

**Cause**: Missing type hint, `Any` leak, unreachable branch, unhandled `None`.
**Fix**: Add the missing annotation. Use `cast()` sparingly and only with a comment justifying why.
**Nuclear option**: `# type: ignore[error-code]` is acceptable only with a comment explaining the tradeoff.

### `pytest` collected no tests

**Cause**: New test file not named `test_*.py`, or missing `__init__.py` in the test directory.
**Fix**: Rename the file; add the `__init__.py` with a one-line docstring.

### CI fails on trace matrix but local is fine

**Cause**: You didn't regenerate and commit `docs/TRACE-MATRIX.md` after adding markers.
**Fix**: `poetry run python scripts/build-trace-matrix.py && git add docs/TRACE-MATRIX.md`.

### Import fails at test time but works in editor

**Cause**: The `message_service` package isn't installed in editable mode.
**Fix**: `poetry install` re-runs the install with the src-layout discovery rules from `pyproject.toml`.

### `pre-commit` hook takes forever on first run

**Cause**: First invocation clones the hook repositories and builds virtualenvs.
**Fix**: Wait it out; subsequent runs are fast. Manually prime with `poetry run pre-commit install --install-hooks`.

---

## Regenerating derived artifacts

Some files in the repo are generated from other sources and SHOULD NOT be hand-edited:

| File                      | Regenerate with                                            |
|---------------------------|------------------------------------------------------------|
| `docs/TRACE-MATRIX.md`    | `poetry run python scripts/build-trace-matrix.py`          |
| `poetry.lock`             | `poetry lock` (or `poetry add/remove` flows)               |

The trace matrix in particular: any time you add a `@pytest.mark.requirement` marker, regenerate the matrix before committing so the forward-trace columns stay up to date.

---

## Coding conventions quick reference

Conventions established by prior increments; keep new code consistent.

| Topic                             | Rule                                                                           |
|-----------------------------------|--------------------------------------------------------------------------------|
| Python version                    | 3.10+, so no `match` over complex patterns that need 3.11, no `StrEnum`        |
| Enum style                        | `class Foo(str, Enum):` — not `StrEnum` (3.11+)                                |
| Filesystem paths                  | `pathlib.Path` only; ruff PTH rule enforces                                    |
| Timestamps                        | Injected `Clock`; persist via `iso_z()` with `"Z"` suffix, not `+00:00`        |
| Dependencies                      | Flow inward; `domain/` imports nothing from `infrastructure/` or `interfaces/` |
| Exception class attributes        | `ClassVar[str]` `error_code`; structured `details` dict on instances           |
| Log event names                   | snake_case nouns/verbs; fields as kwargs                                       |
| Docstring style                   | Google format (args, returns, raises sections)                                 |
| Line length                       | Follow ruff-format default; do not hand-wrap what it would keep on one line    |

---

## Getting help

- `docs/L1-REQ.md`, `L2-REQ.md`, `L3-REQ.md` — the authoritative requirement statements
- `docs/LOGGING-AND-EXCEPTIONS.md` — exception and logging philosophy
- `docs/TRACE-MATRIX.md` — which tests verify which requirements
- `tests/README.md` — test tree layout and fixture conventions
- `ROADMAP.md` — deferred items; if you find yourself wanting to implement one, mention it
