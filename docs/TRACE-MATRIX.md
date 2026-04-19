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

## L1 to L2 forward trace

One table per category. The "L2 Children" column lists the L2 requirements that decompose each L1. L3 children and Verification Artifacts remain `(TBD)` until those artifacts are produced.

### L1-API: gRPC interface

| L1 ID      | L2 Children                          | Status |
|------------|--------------------------------------|--------|
| L1-API-001 | L2-API-001, L2-API-002, L2-API-003   | Draft  |
| L1-API-002 | L2-API-004, L2-API-005               | Draft  |
| L1-API-003 | L2-API-006, L2-API-007               | Draft  |
| L1-API-004 | L2-API-008, L2-API-009, L2-API-010, L2-API-011 | Draft |

### L1-RUN: Run lifecycle

| L1 ID      | L2 Children                                                     | Status |
|------------|-----------------------------------------------------------------|--------|
| L1-RUN-001 | L2-RUN-001, L2-RUN-002, L2-RUN-003                              | Draft  |
| L1-RUN-002 | L2-RUN-004, L2-RUN-005, L2-RUN-006                              | Draft  |
| L1-RUN-003 | L2-RUN-007, L2-RUN-008, L2-RUN-009, L2-RUN-010, L2-RUN-011      | Draft  |
| L1-RUN-004 | L2-RUN-012, L2-RUN-013                                          | Draft  |
| L1-RUN-005 | L2-RUN-014, L2-RUN-015                                          | Draft  |

### L1-STAGE: Stage lifecycle and idempotency

| L1 ID        | L2 Children                                      | Status |
|--------------|--------------------------------------------------|--------|
| L1-STAGE-001 | L2-STAGE-001, L2-STAGE-002                       | Draft  |
| L1-STAGE-002 | L2-STAGE-003, L2-STAGE-004, L2-STAGE-005         | Draft  |
| L1-STAGE-003 | L2-STAGE-006, L2-STAGE-007                       | Draft  |
| L1-STAGE-004 | L2-STAGE-008, L2-STAGE-009                       | Draft  |

### L1-TMPL: Template governance and sandboxing

| L1 ID       | L2 Children                                            | Status |
|-------------|--------------------------------------------------------|--------|
| L1-TMPL-001 | L2-TMPL-001, L2-TMPL-002, L2-TMPL-003                  | Draft  |
| L1-TMPL-002 | L2-TMPL-004, L2-TMPL-005, L2-TMPL-006                  | Draft  |
| L1-TMPL-003 | L2-TMPL-007, L2-TMPL-008, L2-TMPL-009                  | Draft  |
| L1-TMPL-004 | L2-TMPL-010, L2-TMPL-011                               | Draft  |
| L1-TMPL-005 | L2-TMPL-012, L2-TMPL-013, L2-TMPL-014                  | Draft  |

### L1-AGGR: Aggregation and composition

| L1 ID       | L2 Children                                        | Status |
|-------------|----------------------------------------------------|--------|
| L1-AGGR-001 | L2-AGGR-001, L2-AGGR-002, L2-AGGR-003              | Draft  |
| L1-AGGR-002 | L2-AGGR-004, L2-AGGR-005, L2-AGGR-006              | Draft  |
| L1-AGGR-003 | L2-AGGR-007, L2-AGGR-008                           | Draft  |
| L1-AGGR-004 | L2-AGGR-009, L2-AGGR-010                           | Draft  |

### L1-SWEEP: Orphan detection and disposition

| L1 ID        | L2 Children                                        | Status |
|--------------|----------------------------------------------------|--------|
| L1-SWEEP-001 | L2-SWEEP-001, L2-SWEEP-002, L2-SWEEP-003           | Draft  |
| L1-SWEEP-002 | L2-SWEEP-004, L2-SWEEP-005, L2-SWEEP-006           | Draft  |
| L1-SWEEP-003 | L2-SWEEP-007, L2-SWEEP-008, L2-SWEEP-009           | Draft  |

### L1-SUB: Subscriptions and tags

| L1 ID      | L2 Children                                    | Status |
|------------|------------------------------------------------|--------|
| L1-SUB-001 | L2-SUB-001, L2-SUB-002, L2-SUB-003             | Draft  |
| L1-SUB-002 | L2-SUB-004, L2-SUB-005                         | Draft  |
| L1-SUB-003 | L2-SUB-006, L2-SUB-007, L2-SUB-008             | Draft  |
| L1-SUB-004 | L2-SUB-009, L2-SUB-010                         | Draft  |

### L1-AUTH: Authentication

| L1 ID       | L2 Children                                    | Status |
|-------------|------------------------------------------------|--------|
| L1-AUTH-001 | L2-AUTH-001, L2-AUTH-002, L2-AUTH-003          | Draft  |
| L1-AUTH-002 | L2-AUTH-004, L2-AUTH-005, L2-AUTH-006          | Draft  |

### L1-MAIL: Email delivery

| L1 ID       | L2 Children                                             | Status |
|-------------|---------------------------------------------------------|--------|
| L1-MAIL-001 | L2-MAIL-001, L2-MAIL-002, L2-MAIL-003                   | Draft  |
| L1-MAIL-002 | L2-MAIL-004, L2-MAIL-005, L2-MAIL-006                   | Draft  |
| L1-MAIL-003 | L2-MAIL-007, L2-MAIL-008                                | Draft  |
| L1-MAIL-004 | L2-MAIL-009, L2-MAIL-010, L2-MAIL-011                   | Draft  |
| L1-MAIL-005 | L2-MAIL-012, L2-MAIL-013                                | Draft  |

### L1-DASH: Dashboard

| L1 ID       | L2 Children                                             | Status |
|-------------|---------------------------------------------------------|--------|
| L1-DASH-001 | L2-DASH-001, L2-DASH-002, L2-DASH-003                   | Draft  |
| L1-DASH-002 | L2-DASH-004, L2-DASH-005, L2-DASH-006                   | Draft  |
| L1-DASH-003 | L2-DASH-007, L2-DASH-008, L2-DASH-009                   | Draft  |
| L1-DASH-004 | L2-DASH-010, L2-DASH-011                                | Draft  |

### L1-PERS: Persistence

| L1 ID       | L2 Children                                                | Status |
|-------------|------------------------------------------------------------|--------|
| L1-PERS-001 | L2-PERS-001, L2-PERS-002, L2-PERS-003, L2-PERS-004         | Draft  |
| L1-PERS-002 | L2-PERS-005, L2-PERS-006, L2-PERS-007                      | Draft  |
| L1-PERS-003 | L2-PERS-008, L2-PERS-009, L2-PERS-010                      | Draft  |

### L1-OBS: Observability

| L1 ID      | L2 Children                                    | Status |
|------------|------------------------------------------------|--------|
| L1-OBS-001 | L2-OBS-001, L2-OBS-002, L2-OBS-003             | Draft  |
| L1-OBS-002 | L2-OBS-004, L2-OBS-005, L2-OBS-006             | Draft  |
| L1-OBS-003 | L2-OBS-007, L2-OBS-008, L2-OBS-009             | Draft  |

### L1-CFG: Configuration

| L1 ID      | L2 Children                                    | Status |
|------------|------------------------------------------------|--------|
| L1-CFG-001 | L2-CFG-001, L2-CFG-002, L2-CFG-003             | Draft  |
| L1-CFG-002 | L2-CFG-004, L2-CFG-005, L2-CFG-006             | Draft  |
| L1-CFG-003 | L2-CFG-007, L2-CFG-008                         | Draft  |

### L1-DEP: Deployment

| L1 ID      | L2 Children                                    | Status |
|------------|------------------------------------------------|--------|
| L1-DEP-001 | L2-DEP-001, L2-DEP-002, L2-DEP-003             | Draft  |
| L1-DEP-002 | L2-DEP-004, L2-DEP-005, L2-DEP-006             | Draft  |
| L1-DEP-003 | L2-DEP-007, L2-DEP-008, L2-DEP-009             | Draft  |

---

## L2 to L3 forward trace (pending L3 decomposition)

L3 children and Verification Artifacts will be populated when L3 requirements are drafted. The table below is the skeleton for that future work.

| L2 ID         | L3 Children | Verification Artifacts | Status |
|---------------|-------------|------------------------|--------|
| L2-API-001    | (TBD)       | (TBD)                  | Draft  |
| L2-API-002    | (TBD)       | (TBD)                  | Draft  |
| L2-API-003    | (TBD)       | (TBD)                  | Draft  |
| L2-API-004    | (TBD)       | (TBD)                  | Draft  |
| L2-API-005    | (TBD)       | (TBD)                  | Draft  |
| L2-API-006    | (TBD)       | (TBD)                  | Draft  |
| L2-API-007    | (TBD)       | (TBD)                  | Draft  |
| L2-API-008    | (TBD)       | (TBD)                  | Draft  |
| L2-API-009    | (TBD)       | (TBD)                  | Draft  |
| L2-API-010    | (TBD)       | (TBD)                  | Draft  |
| L2-API-011    | (TBD)       | (TBD)                  | Draft  |
| L2-RUN-001 through L2-RUN-015    | (TBD)       | (TBD)                  | Draft  |
| L2-STAGE-001 through L2-STAGE-009 | (TBD)       | (TBD)                  | Draft  |
| L2-TMPL-001 through L2-TMPL-014  | (TBD)       | (TBD)                  | Draft  |
| L2-AGGR-001 through L2-AGGR-010  | (TBD)       | (TBD)                  | Draft  |
| L2-SWEEP-001 through L2-SWEEP-009 | (TBD)       | (TBD)                  | Draft  |
| L2-SUB-001 through L2-SUB-010    | (TBD)       | (TBD)                  | Draft  |
| L2-AUTH-001 through L2-AUTH-006  | (TBD)       | (TBD)                  | Draft  |
| L2-MAIL-001 through L2-MAIL-013  | (TBD)       | (TBD)                  | Draft  |
| L2-DASH-001 through L2-DASH-011  | (TBD)       | (TBD)                  | Draft  |
| L2-PERS-001 through L2-PERS-010  | (TBD)       | (TBD)                  | Draft  |
| L2-OBS-001 through L2-OBS-009    | (TBD)       | (TBD)                  | Draft  |
| L2-CFG-001 through L2-CFG-008    | (TBD)       | (TBD)                  | Draft  |
| L2-DEP-001 through L2-DEP-009    | (TBD)       | (TBD)                  | Draft  |

The per-L2 rows are collapsed above as ranges for brevity. When L3 decomposition begins, this table will be expanded to one row per L2.

---

## Coverage summary

### By category

| Category | L1 Count | L2 Count | L3 Count | L1s without L2 children | L2s without L3 children |
|----------|----------|----------|----------|-------------------------|-------------------------|
| API      | 4        | 11       | 0        | 0                       | 11                      |
| RUN      | 5        | 15       | 0        | 0                       | 15                      |
| STAGE    | 4        | 9        | 0        | 0                       | 9                       |
| TMPL     | 5        | 14       | 0        | 0                       | 14                      |
| AGGR     | 4        | 10       | 0        | 0                       | 10                      |
| SWEEP    | 3        | 9        | 0        | 0                       | 9                       |
| SUB      | 4        | 10       | 0        | 0                       | 10                      |
| AUTH     | 2        | 6        | 0        | 0                       | 6                       |
| MAIL     | 5        | 13       | 0        | 0                       | 13                      |
| DASH     | 4        | 11       | 0        | 0                       | 11                      |
| PERS     | 3        | 10       | 0        | 0                       | 10                      |
| OBS      | 3        | 9        | 0        | 0                       | 9                       |
| CFG      | 3        | 8        | 0        | 0                       | 8                       |
| DEP      | 3        | 9        | 0        | 0                       | 9                       |
| **Total**| **52**   | **144**  | **0**    | **0**                   | **144**                 |

### By verification method at L1

| Method             | L1 Count | Artifacts Linked |
|--------------------|----------|------------------|
| Test (T)           | 46       | 0                |
| Analysis (A)       | 2        | 0                |
| Inspection (I)     | 16       | 0                |
| Demonstration (D)  | 5        | 0                |

(Methods are not exclusive — a single L1 may list more than one method.)

### Orphans and strays

- **Orphan L2s** (L2 requirements with no L1 parent): 0 — every L2 traces to an L1 via its explicit `Parent` field.
- **Orphan L3s**: N/A — L3 decomposition has not begun.
- **L1s without L2 decomposition**: 0 — all 52 L1s have been decomposed.
- **L2s without L3 decomposition**: 144 — expected at this stage.

---

## Document change history

| Date       | Author | Change                                      |
|------------|--------|---------------------------------------------|
| 2026-04-18 | Joey   | Initial matrix populated with L1 entries    |
| 2026-04-18 | Joey   | L2 decomposition complete; 144 L2 entries   |
