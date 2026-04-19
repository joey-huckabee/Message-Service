# Message-Service — Requirements Trace Matrix

## Purpose

This document provides a **forward trace** from Level 1 requirements to their Level 2 and Level 3 derivations, and onward to concrete verification artifacts (test functions, analysis documents, review records, and demonstration procedures). It is the authoritative link between SHALL statements and the code, tests, and documents that satisfy them.

Backward tracing (from artifact to requirement) is deferred on grounds of maintenance overhead; the forward trace combined with the pytest marker convention is sufficient for compliance purposes.

## Linking convention

### Requirement identifiers

Requirements are identified in the form `L<N>-<CATEGORY>-<NNN>`, where `<N>` is the level (1, 2, or 3), `<CATEGORY>` is one of the category codes defined in `L1-REQ.md`, and `<NNN>` is a zero-padded sequence number within that category.

### Verification artifact paths

All verification artifact references in this document are **repo-relative paths**, formatted according to the verification method:

- **Test (T)**: pytest discovery format — `tests/<subdir>/<file>.py::<test_function>`. Every test function that verifies a requirement SHALL be tagged with a pytest marker referencing the requirement identifier, for example:

  ```python
  @pytest.mark.requirement("L1-API-001")
  def test_service_exposes_message_service_protocol():
      ...
  ```

  A future tool (see `ROADMAP.md`) will auto-extract these markers and populate the verification columns in this matrix.

- **Analysis (A)**: `docs/analysis/<document>.md`
- **Inspection (I)**: `docs/reviews/<review-record>.md`
- **Demonstration (D)**: `docs/procedures/<procedure>.md`

### Status values

- **Draft** — requirement text is being refined
- **Approved** — requirement text is frozen and ready for decomposition or implementation
- **Implemented** — derivations exist and implementation is complete
- **Verified** — all verification artifacts pass and have been reviewed

---

## Forward trace

One table per category. Entries marked `(TBD)` will be populated as L2 and L3 decomposition proceeds and as tests and documents are created.

### L1-API: gRPC interface

| L1 ID      | L2 Children | L3 Children | Verification Artifacts | Status |
|------------|-------------|-------------|------------------------|--------|
| L1-API-001 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-API-002 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-API-003 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-API-004 | (TBD)       | (TBD)       | (TBD)                  | Draft  |

### L1-RUN: Run lifecycle

| L1 ID      | L2 Children | L3 Children | Verification Artifacts | Status |
|------------|-------------|-------------|------------------------|--------|
| L1-RUN-001 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-RUN-002 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-RUN-003 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-RUN-004 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-RUN-005 | (TBD)       | (TBD)       | (TBD)                  | Draft  |

### L1-STAGE: Stage lifecycle and idempotency

| L1 ID        | L2 Children | L3 Children | Verification Artifacts | Status |
|--------------|-------------|-------------|------------------------|--------|
| L1-STAGE-001 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-STAGE-002 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-STAGE-003 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-STAGE-004 | (TBD)       | (TBD)       | (TBD)                  | Draft  |

### L1-TMPL: Template governance and sandboxing

| L1 ID       | L2 Children | L3 Children | Verification Artifacts | Status |
|-------------|-------------|-------------|------------------------|--------|
| L1-TMPL-001 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-TMPL-002 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-TMPL-003 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-TMPL-004 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-TMPL-005 | (TBD)       | (TBD)       | (TBD)                  | Draft  |

### L1-AGGR: Aggregation and composition

| L1 ID       | L2 Children | L3 Children | Verification Artifacts | Status |
|-------------|-------------|-------------|------------------------|--------|
| L1-AGGR-001 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-AGGR-002 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-AGGR-003 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-AGGR-004 | (TBD)       | (TBD)       | (TBD)                  | Draft  |

### L1-SWEEP: Orphan detection and disposition

| L1 ID        | L2 Children | L3 Children | Verification Artifacts | Status |
|--------------|-------------|-------------|------------------------|--------|
| L1-SWEEP-001 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-SWEEP-002 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-SWEEP-003 | (TBD)       | (TBD)       | (TBD)                  | Draft  |

### L1-SUB: Subscriptions and tags

| L1 ID      | L2 Children | L3 Children | Verification Artifacts | Status |
|------------|-------------|-------------|------------------------|--------|
| L1-SUB-001 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-SUB-002 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-SUB-003 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-SUB-004 | (TBD)       | (TBD)       | (TBD)                  | Draft  |

### L1-AUTH: Authentication

| L1 ID       | L2 Children | L3 Children | Verification Artifacts | Status |
|-------------|-------------|-------------|------------------------|--------|
| L1-AUTH-001 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-AUTH-002 | (TBD)       | (TBD)       | (TBD)                  | Draft  |

### L1-MAIL: Email delivery

| L1 ID       | L2 Children | L3 Children | Verification Artifacts | Status |
|-------------|-------------|-------------|------------------------|--------|
| L1-MAIL-001 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-MAIL-002 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-MAIL-003 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-MAIL-004 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-MAIL-005 | (TBD)       | (TBD)       | (TBD)                  | Draft  |

### L1-DASH: Dashboard

| L1 ID       | L2 Children | L3 Children | Verification Artifacts | Status |
|-------------|-------------|-------------|------------------------|--------|
| L1-DASH-001 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-DASH-002 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-DASH-003 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-DASH-004 | (TBD)       | (TBD)       | (TBD)                  | Draft  |

### L1-PERS: Persistence

| L1 ID       | L2 Children | L3 Children | Verification Artifacts | Status |
|-------------|-------------|-------------|------------------------|--------|
| L1-PERS-001 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-PERS-002 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-PERS-003 | (TBD)       | (TBD)       | (TBD)                  | Draft  |

### L1-OBS: Observability

| L1 ID      | L2 Children | L3 Children | Verification Artifacts | Status |
|------------|-------------|-------------|------------------------|--------|
| L1-OBS-001 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-OBS-002 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-OBS-003 | (TBD)       | (TBD)       | (TBD)                  | Draft  |

### L1-CFG: Configuration

| L1 ID      | L2 Children | L3 Children | Verification Artifacts | Status |
|------------|-------------|-------------|------------------------|--------|
| L1-CFG-001 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-CFG-002 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-CFG-003 | (TBD)       | (TBD)       | (TBD)                  | Draft  |

### L1-DEP: Deployment

| L1 ID      | L2 Children | L3 Children | Verification Artifacts | Status |
|------------|-------------|-------------|------------------------|--------|
| L1-DEP-001 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-DEP-002 | (TBD)       | (TBD)       | (TBD)                  | Draft  |
| L1-DEP-003 | (TBD)       | (TBD)       | (TBD)                  | Draft  |

---

## Coverage summary

### By category

| Category | L1 Count | L2 Count | L3 Count | L1s with no L2 children | L1s with no verification artifact |
|----------|----------|----------|----------|-------------------------|-----------------------------------|
| API      | 4        | 0        | 0        | 4                       | 4                                 |
| RUN      | 5        | 0        | 0        | 5                       | 5                                 |
| STAGE    | 4        | 0        | 0        | 4                       | 4                                 |
| TMPL     | 5        | 0        | 0        | 5                       | 5                                 |
| AGGR     | 4        | 0        | 0        | 4                       | 4                                 |
| SWEEP    | 3        | 0        | 0        | 3                       | 3                                 |
| SUB      | 4        | 0        | 0        | 4                       | 4                                 |
| AUTH     | 2        | 0        | 0        | 2                       | 2                                 |
| MAIL     | 5        | 0        | 0        | 5                       | 5                                 |
| DASH     | 4        | 0        | 0        | 4                       | 4                                 |
| PERS     | 3        | 0        | 0        | 3                       | 3                                 |
| OBS      | 3        | 0        | 0        | 3                       | 3                                 |
| CFG      | 3        | 0        | 0        | 3                       | 3                                 |
| DEP      | 3        | 0        | 0        | 3                       | 3                                 |
| **Total**| **52**   | **0**    | **0**    | **52**                  | **52**                            |

### By verification method

At L1 stage, each requirement declares one or more verification methods; artifact paths are populated during implementation.

| Method             | L1 Count | Artifacts Linked |
|--------------------|----------|------------------|
| Test (T)           | 46       | 0                |
| Analysis (A)       | 2        | 0                |
| Inspection (I)     | 16       | 0                |
| Demonstration (D)  | 5        | 0                |

(Methods are not exclusive — a single L1 may list more than one method; the counts above sum to more than 52.)

### Orphans and strays

- **Orphan L2s** (L2 requirements with no L1 parent): 0 — L2 decomposition has not begun.
- **Orphan L3s** (L3 requirements with no L2 parent): 0 — L3 decomposition has not begun.
- **L1s with no decomposition**: 52 (all) — expected at this stage.

---

## Document change history

| Date       | Author | Change                                           |
|------------|--------|--------------------------------------------------|
| 2026-04-18 | Joey   | Initial trace matrix, populated with L1 entries  |
