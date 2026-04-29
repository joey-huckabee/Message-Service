# ADR-002 — Hexagonal architecture boundary enforced via static AST conformance test

- **Status**: Accepted
- **Date**: 2026-04-29 (initial v1 release)
- **Deciders**: Project lead
- **Related requirements**: L1-PERS-003 (port abstraction), L2-PERS-010 (hexagonal boundary), L3-PERS-015 / L3-PERS-016 / L3-PERS-017 (architecture-boundary discipline)
- **Supersedes**: N/A
- **Superseded by**: N/A

## Context

Message-Service follows the hexagonal / ports-and-adapters pattern. Dependencies flow inward only:

```
interfaces/  → application/  ← infrastructure/
                  ↑
                domain/
```

Concretely:

- `domain/` — pure business logic. State machines, aggregates, value objects, the `MessageServiceError` hierarchy. No I/O. No framework imports.
- `application/` — use cases and the port interfaces they depend on. `BeginRunUseCase`, `SubmitStageReportUseCase`, `FinalizeRunUseCase`, `AssembleAndDeliverUseCase`, the various pruner / sweeper use cases. Plus port ABCs under `application/ports/` (`Clock`, `Mailer`, `RunRepository`, etc.).
- `infrastructure/` — concrete adapters implementing the ports. SQLite repositories, the `aiosmtplib` mailer, the Jinja2 sandboxed renderer, the asyncio scheduler and sweeper loop.
- `interfaces/` — inbound adapters: gRPC servicer + error translator, FastAPI routes + auth + HTML, the CLI entrypoint.
- `bootstrap/` — composition root. Wires the adapters into a `Service` dataclass.

The boundary rule is: **`domain/` and `application/` MAY NOT import from `infrastructure/` or `interfaces/`.** A `domain/` file that imports `aiosqlite` or `grpc` has crossed a layer boundary and broken the dependency-inversion guarantee that lets us swap adapters and run pure unit tests.

The question is: **how do we enforce that boundary?**

## Decision

**Enforce the hexagonal boundary via a static AST-walk conformance test in `tests/conformance/test_architecture_boundaries.py`.**

The test walks every `.py` file under `src/message_service/domain/` and `src/message_service/application/`, parses each via `ast.parse`, and inspects every `Import` / `ImportFrom` node. Any import whose module name starts with `message_service.infrastructure.` or `message_service.interfaces.` records a violation (file path + line number + the offending import). The test fails the build with the full violation list if any exist.

The same file holds two test functions, one per direction (`test_domain_does_not_import_infrastructure_or_interfaces`, `test_application_does_not_import_infrastructure_or_interfaces`). Both run on every PR.

## Alternatives considered

### A. Separate Python packages with import-mocked boundaries

Split the codebase into multiple top-level packages (`message_service_domain`, `message_service_application`, etc.), each with its own `pyproject.toml`, and let Python's import resolver enforce the boundary by simply not making the forbidden modules available.

**Why rejected.** Adds substantial packaging overhead — three or four `pyproject.toml` files, intra-monorepo workspace tooling, and a release process that updates them in lockstep. The benefit is real (the boundary becomes "structurally impossible to violate") but disproportionate: a single AST scan delivers the same enforcement guarantee at PR time without the packaging machinery. Reasonable tradeoff for a much larger codebase; overkill for v1's scale.

### B. mypy plugin

Author a mypy plugin that recognizes the layer-prefix convention and rejects forbidden imports during type-checking.

**Why rejected.** mypy plugins are powerful but have a substantial maintenance tail (compatibility with mypy releases, the plugin API's instability, debugging plugin-emitted diagnostics). The conformance test we ship is roughly 50 lines of stdlib `ast` walking, which any contributor can read and modify. A plugin would also require adopting it across the project's dependent tooling (pre-commit, IDE integrations), some of which don't easily accept custom plugins.

### C. Runtime locks (e.g., `__getattr__`-based import sentinels)

At process startup, install an import hook that raises if a forbidden module is imported from a forbidden path. The check happens at runtime; production traffic is the enforcer.

**Why rejected.** Defers detection from PR-time to runtime, which is the wrong direction for a structural invariant. Bugs that should fail the build instead reach production, where the cost of catching them is higher. Also adds a runtime cost (import-time overhead on every Python process startup) that the static check has zero of.

### D. Code review + naming convention only

Trust contributors to follow the layering convention. No automated check.

**Why rejected.** The convention is exactly the kind of rule that erodes silently — one well-intentioned import-shortcut sneaks past review, the next contributor sees the precedent, and within a quarter the boundary is decorative. The static check is cheap insurance against social drift.

## Decision criteria

The four alternatives differ along three axes:

| Approach | When does it catch the violation? | What does it cost to maintain? | Runtime cost? |
|---|---|---|---|
| Separate packages (A) | At import resolution (PR / runtime) | High — multi-package tooling | Zero |
| mypy plugin (B) | At type-check time (PR) | Medium — plugin API churn | Zero |
| Runtime locks (C) | At process startup / runtime | Low | Non-zero (import-hook overhead) |
| **Static AST scan (chosen)** | **At PR time (CI)** | **Low — 50-line script** | **Zero** |
| Review-only (D) | When a reviewer notices | None | Zero |

The static scan wins on the *catch-at-PR-time + low maintenance + zero runtime cost* combination. The check is fast (<100 ms across the entire src/ tree), the diagnostics include file path and line number for every violation, and the test code itself is short enough to be self-documenting.

## Consequences

### Positive

- **Fail-fast on PRs.** A boundary violation in a PR fails CI immediately, before a reviewer needs to notice. The cost of catching the violation is bounded to the cost of running the existing pytest invocation.
- **Self-documenting.** A new contributor reading `tests/conformance/test_architecture_boundaries.py` sees exactly which directories are which side of the boundary, and exactly what import patterns are forbidden. No abstract architecture diagram required.
- **Survives refactoring.** Renaming a file or moving code between layers automatically changes what the conformance test sees on the next run. Drift is impossible without the test going red.
- **Generalizes.** The same AST-walk pattern enforces L3-PERS-035 (only the report pruner deletes report files), L3-OBS-039 (only the audit pruner deletes audit rows), L3-RUN-031 (only `system_clock.py` and `migration_runner.py` read the wall clock). Each is a specialized variant of "X is the sole caller of Y", with one allow-list. The pattern is repeatable.

### Negative

- **Won't catch dynamically-imported violations.** Code that constructs a module name as a string and imports it via `importlib.import_module(...)` flies under the AST radar. v1 has no such dynamic imports in domain/application, but a future "smart factory" pattern that loads adapters by config name would need additional review discipline. The conformance test's docstring documents this gap; the practical mitigation is: don't dynamically import adapters from domain/application code — that's what the bootstrap composition root is for.
- **Won't catch transitive violations through dynamic dispatch.** A domain function that takes a `Callable` and the caller passes a function from `infrastructure/` is structurally legal (the static scan only sees the import graph, not the value flow). This is fine — it's not actually a boundary violation; it's the dependency-inversion pattern working correctly. But a reviewer reading domain code that calls an opaque `Callable` may not realize the call ultimately reaches infrastructure. The compensating control is: ports are ABCs declared in `application/ports/`, so domain/application code that wants to invoke an adapter does so through a port (which is itself in the allowed-import set).
- **Allow-list maintenance.** The L3-PERS-035 / L3-OBS-039 / L3-RUN-031 specialized variants ship explicit allow-lists (e.g., "only `report_pruner.py` and `bootstrap/service.py::_ensure_report_directory` may call `Path.unlink`"). Each new permitted call site requires editing the allow-list in the same commit. This is the cost of the strict-default discipline; it's small in practice (the allow-lists rarely change) and the explicit-edit-with-rationale pattern is the same shape we use for `# noqa` reason comments elsewhere.

## Re-evaluation triggers

The decision should be revisited if any of the following hold:

1. **The codebase grows past ~50 modules per layer.** At that scale, separate packages (Alternative A) start paying off — the multi-package boundary becomes structurally enforced rather than test-enforced, and the import-graph clarity helps onboarding.
2. **A dynamic-loading feature lands.** If, for example, the v2 plugin system loads adapter modules by name, the AST-only check is insufficient and a runtime hook becomes necessary.
3. **Multiple boundary violations slip past CI in the same release cycle.** A signal that the static check is ineffective for some reason (test was disabled? AST walk has a gap?). Diagnose root cause before defaulting to the next alternative.

## References

- `tests/conformance/test_architecture_boundaries.py` — the conformance test itself (~110 lines)
- `tests/conformance/test_pathlib_enforcement.py` — same pattern enforcing pathlib usage
- `tests/conformance/test_clock_chokepoint.py` — same pattern enforcing the `Clock` port chokepoint (L3-RUN-031)
- `tests/conformance/test_report_pruner_sole_deleter.py` — same pattern enforcing the L3-PERS-035 sole-deleter invariant
- `tests/conformance/test_audit_log_sole_deleter.py` — same pattern enforcing the L3-OBS-039 sole-deleter invariant
- `docs/L3-REQ.md` — L3-PERS-015 / L3-PERS-016 / L3-PERS-017 (the boundary-discipline obligations)
- ADR-001 — the SQLite decision that depends on the port-abstraction discipline this ADR enforces (a future RDBMS swap is feasible because this boundary is held)
