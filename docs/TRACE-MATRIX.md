# Message-Service — Requirements Trace Matrix

## Purpose

This document provides a **forward trace** from L1 requirements through L2 and L3 to concrete verification artifacts. It is the authoritative link between SHALL statements and the code, tests, and documents that satisfy them.

Backward tracing is deferred as a maintenance-overhead choice; the forward trace plus the pytest marker convention is sufficient for compliance purposes.

## Linking convention

### Requirement identifiers

`L<N>-<CATEGORY>-<NNN>` where `<N>` is 1, 2, or 3 and `<CATEGORY>` is one of the 14 category codes from `L1-REQ.md`.

### Verification artifact paths

- **Test (T)**: pytest discovery format `tests/<subdir>/<file>.py::<test_function>`. Test functions SHALL be tagged with `@pytest.mark.requirement("L3-XXX-NNN")` markers; a future `scripts/build-trace-matrix.py` tool will auto-populate the verification columns from these markers (see `ROADMAP.md`).
- **Analysis (A)**: `docs/analysis/<document>.md`
- **Inspection (I)**: `docs/reviews/<review-record>.md`
- **Demonstration (D)**: `docs/procedures/<procedure>.md`

### Status values

`Draft` → `Approved` → `Implemented` → `Verified`.

---

## L1 → L2 → L3 forward trace

Per category, showing each L1 with its L2 children, and each L2 with its L3 children. Verification artifacts are `(TBD)` until populated by the marker-extraction tool.

### LAPI: gRPC interface

**L1 → L2**

| L1 ID | L2 Children | Status |
|-------|-------------|--------|
| L1-API-001 | L2-API-001, L2-API-002, L2-API-003 | Draft |
| L1-API-002 | L2-API-004, L2-API-005 | Draft |
| L1-API-003 | L2-API-006, L2-API-007 | Draft |
| L1-API-004 | L2-API-008, L2-API-009, L2-API-010, L2-API-011 | Draft |

**L2 → L3**

| L2 ID | L3 Children | Verification Artifacts | Status |
|-------|-------------|------------------------|--------|
| L2-API-001 | L3-API-001, L3-API-002 | (TBD) | Draft |
| L2-API-002 | L3-API-003, L3-API-004 | (TBD) | Draft |
| L2-API-003 | L3-API-005 | (TBD) | Draft |
| L2-API-004 | L3-API-006 | (TBD) | Draft |
| L2-API-005 | L3-API-007 | (TBD) | Draft |
| L2-API-006 | L3-API-008 | (TBD) | Draft |
| L2-API-007 | L3-API-009, L3-API-010 | (TBD) | Draft |
| L2-API-008 | L3-API-011, L3-API-012 | (TBD) | Draft |
| L2-API-009 | L3-API-013 | (TBD) | Draft |
| L2-API-010 | L3-API-014, L3-API-015, L3-API-016 | (TBD) | Draft |
| L2-API-011 | L3-API-017, L3-API-018 | (TBD) | Draft |

### LRUN: Run lifecycle

**L1 → L2**

| L1 ID | L2 Children | Status |
|-------|-------------|--------|
| L1-RUN-001 | L2-RUN-001, L2-RUN-002, L2-RUN-003 | Draft |
| L1-RUN-002 | L2-RUN-004, L2-RUN-005, L2-RUN-006 | Draft |
| L1-RUN-003 | L2-RUN-007, L2-RUN-008, L2-RUN-009, L2-RUN-010, L2-RUN-011 | Draft |
| L1-RUN-004 | L2-RUN-012, L2-RUN-013 | Draft |
| L1-RUN-005 | L2-RUN-014, L2-RUN-015 | Draft |

**L2 → L3**

| L2 ID | L3 Children | Verification Artifacts | Status |
|-------|-------------|------------------------|--------|
| L2-RUN-001 | L3-RUN-001 | (TBD) | Draft |
| L2-RUN-002 | L3-RUN-002, L3-RUN-003, L3-RUN-030 | (TBD) | Draft |
| L2-RUN-003 | L3-RUN-004, L3-RUN-005 | (TBD) | Draft |
| L2-RUN-004 | L3-RUN-006, L3-RUN-007, L3-RUN-028, L3-RUN-029 | (TBD) | Draft |
| L2-RUN-005 | L3-RUN-008 | (TBD) | Draft |
| L2-RUN-006 | L3-RUN-009 | (TBD) | Draft |
| L2-RUN-007 | L3-RUN-010, L3-RUN-011 | (TBD) | Draft |
| L2-RUN-008 | L3-RUN-012, L3-RUN-013 | (TBD) | Draft |
| L2-RUN-009 | L3-RUN-014, L3-RUN-015 | (TBD) | Draft |
| L2-RUN-010 | L3-RUN-016, L3-RUN-017 | (TBD) | Draft |
| L2-RUN-011 | L3-RUN-018, L3-RUN-019 | (TBD) | Draft |
| L2-RUN-012 | L3-RUN-020, L3-RUN-021 | (TBD) | Draft |
| L2-RUN-013 | L3-RUN-022, L3-RUN-023 | (TBD) | Draft |
| L2-RUN-014 | L3-RUN-024, L3-RUN-025 | (TBD) | Draft |
| L2-RUN-015 | L3-RUN-026, L3-RUN-027 | (TBD) | Draft |

### LSTAGE: Stage lifecycle and idempotency

**L1 → L2**

| L1 ID | L2 Children | Status |
|-------|-------------|--------|
| L1-STAGE-001 | L2-STAGE-001, L2-STAGE-002 | Draft |
| L1-STAGE-002 | L2-STAGE-003, L2-STAGE-004, L2-STAGE-005 | Draft |
| L1-STAGE-003 | L2-STAGE-006, L2-STAGE-007 | Draft |
| L1-STAGE-004 | L2-STAGE-008, L2-STAGE-009 | Draft |

**L2 → L3**

| L2 ID | L3 Children | Verification Artifacts | Status |
|-------|-------------|------------------------|--------|
| L2-STAGE-001 | L3-STAGE-001, L3-STAGE-002, L3-STAGE-017 | (TBD) | Draft |
| L2-STAGE-002 | L3-STAGE-003, L3-STAGE-004, L3-STAGE-018 | (TBD) | Draft |
| L2-STAGE-003 | L3-STAGE-005 | (TBD) | Draft |
| L2-STAGE-004 | L3-STAGE-006, L3-STAGE-007 | (TBD) | Draft |
| L2-STAGE-005 | L3-STAGE-008, L3-STAGE-009 | (TBD) | Draft |
| L2-STAGE-006 | L3-STAGE-010, L3-STAGE-011 | (TBD) | Draft |
| L2-STAGE-007 | L3-STAGE-012, L3-STAGE-013 | (TBD) | Draft |
| L2-STAGE-008 | L3-STAGE-014, L3-STAGE-015 | (TBD) | Draft |
| L2-STAGE-009 | L3-STAGE-016 | (TBD) | Draft |

### LTMPL: Template governance and sandboxing

**L1 → L2**

| L1 ID | L2 Children | Status |
|-------|-------------|--------|
| L1-TMPL-001 | L2-TMPL-001, L2-TMPL-002, L2-TMPL-003 | Draft |
| L1-TMPL-002 | L2-TMPL-004, L2-TMPL-005, L2-TMPL-006 | Draft |
| L1-TMPL-003 | L2-TMPL-007, L2-TMPL-008, L2-TMPL-009 | Draft |
| L1-TMPL-004 | L2-TMPL-010, L2-TMPL-011 | Draft |
| L1-TMPL-005 | L2-TMPL-012, L2-TMPL-013, L2-TMPL-014 | Draft |

**L2 → L3**

| L2 ID | L3 Children | Verification Artifacts | Status |
|-------|-------------|------------------------|--------|
| L2-TMPL-001 | L3-TMPL-001, L3-TMPL-002 | (TBD) | Draft |
| L2-TMPL-002 | L3-TMPL-003, L3-TMPL-004 | (TBD) | Draft |
| L2-TMPL-003 | L3-TMPL-005, L3-TMPL-006, L3-TMPL-027 | (TBD) | Draft |
| L2-TMPL-004 | L3-TMPL-007, L3-TMPL-008 | (TBD) | Draft |
| L2-TMPL-005 | L3-TMPL-009, L3-TMPL-010 | (TBD) | Draft |
| L2-TMPL-006 | L3-TMPL-011, L3-TMPL-012 | (TBD) | Draft |
| L2-TMPL-007 | L3-TMPL-013, L3-TMPL-014, L3-TMPL-028 | (TBD) | Draft |
| L2-TMPL-008 | L3-TMPL-015, L3-TMPL-016 | (TBD) | Draft |
| L2-TMPL-009 | L3-TMPL-017 | (TBD) | Draft |
| L2-TMPL-010 | L3-TMPL-018, L3-TMPL-019 | (TBD) | Draft |
| L2-TMPL-011 | L3-TMPL-020 | (TBD) | Draft |
| L2-TMPL-012 | L3-TMPL-021, L3-TMPL-022 | (TBD) | Draft |
| L2-TMPL-013 | L3-TMPL-023, L3-TMPL-024 | (TBD) | Draft |
| L2-TMPL-014 | L3-TMPL-025, L3-TMPL-026 | (TBD) | Draft |

### LAGGR: Aggregation and composition

**L1 → L2**

| L1 ID | L2 Children | Status |
|-------|-------------|--------|
| L1-AGGR-001 | L2-AGGR-001, L2-AGGR-002, L2-AGGR-003 | Draft |
| L1-AGGR-002 | L2-AGGR-004, L2-AGGR-005, L2-AGGR-006 | Draft |
| L1-AGGR-003 | L2-AGGR-007, L2-AGGR-008 | Draft |
| L1-AGGR-004 | L2-AGGR-009, L2-AGGR-010 | Draft |

**L2 → L3**

| L2 ID | L3 Children | Verification Artifacts | Status |
|-------|-------------|------------------------|--------|
| L2-AGGR-001 | L3-AGGR-001, L3-AGGR-002, L3-AGGR-017 | (TBD) | Draft |
| L2-AGGR-002 | L3-AGGR-003, L3-AGGR-018 | (TBD) | Draft |
| L2-AGGR-003 | L3-AGGR-004, L3-AGGR-005 | (TBD) | Draft |
| L2-AGGR-004 | L3-AGGR-006, L3-AGGR-007, L3-AGGR-019 | (TBD) | Draft |
| L2-AGGR-005 | L3-AGGR-008, L3-AGGR-009, L3-AGGR-020 | (TBD) | Draft |
| L2-AGGR-006 | L3-AGGR-010, L3-AGGR-011 | (TBD) | Draft |
| L2-AGGR-007 | L3-AGGR-012 | (TBD) | Draft |
| L2-AGGR-008 | L3-AGGR-013, L3-AGGR-014 | (TBD) | Draft |
| L2-AGGR-009 | L3-AGGR-015 | (TBD) | Draft |
| L2-AGGR-010 | L3-AGGR-016 | (TBD) | Draft |

### LSWEEP: Orphan detection and disposition

**L1 → L2**

| L1 ID | L2 Children | Status |
|-------|-------------|--------|
| L1-SWEEP-001 | L2-SWEEP-001, L2-SWEEP-002, L2-SWEEP-003 | Draft |
| L1-SWEEP-002 | L2-SWEEP-004, L2-SWEEP-005, L2-SWEEP-006 | Draft |
| L1-SWEEP-003 | L2-SWEEP-007, L2-SWEEP-008, L2-SWEEP-009 | Draft |

**L2 → L3**

| L2 ID | L3 Children | Verification Artifacts | Status |
|-------|-------------|------------------------|--------|
| L2-SWEEP-001 | L3-SWEEP-001, L3-SWEEP-002, L3-SWEEP-018 | (TBD) | Draft |
| L2-SWEEP-002 | L3-SWEEP-003, L3-SWEEP-017 | (TBD) | Draft |
| L2-SWEEP-003 | L3-SWEEP-004, L3-SWEEP-005, L3-SWEEP-016 | (TBD) | Draft |
| L2-SWEEP-004 | L3-SWEEP-006 | (TBD) | Draft |
| L2-SWEEP-005 | L3-SWEEP-007, L3-SWEEP-008 | (TBD) | Draft |
| L2-SWEEP-006 | L3-SWEEP-009, L3-SWEEP-010 | (TBD) | Draft |
| L2-SWEEP-007 | L3-SWEEP-011, L3-SWEEP-012 | (TBD) | Draft |
| L2-SWEEP-008 | L3-SWEEP-013, L3-SWEEP-014 | (TBD) | Draft |
| L2-SWEEP-009 | L3-SWEEP-015 | (TBD) | Draft |

### LSUB: Subscriptions and tags

**L1 → L2**

| L1 ID | L2 Children | Status |
|-------|-------------|--------|
| L1-SUB-001 | L2-SUB-001, L2-SUB-002, L2-SUB-003 | Draft |
| L1-SUB-002 | L2-SUB-004, L2-SUB-005 | Draft |
| L1-SUB-003 | L2-SUB-006, L2-SUB-007, L2-SUB-008 | Draft |
| L1-SUB-004 | L2-SUB-009, L2-SUB-010 | Draft |

**L2 → L3**

| L2 ID | L3 Children | Verification Artifacts | Status |
|-------|-------------|------------------------|--------|
| L2-SUB-001 | L3-SUB-001, L3-SUB-002, L3-SUB-019 | (TBD) | Draft |
| L2-SUB-002 | L3-SUB-003, L3-SUB-004 | (TBD) | Draft |
| L2-SUB-003 | L3-SUB-005, L3-SUB-006, L3-SUB-020 | (TBD) | Draft |
| L2-SUB-004 | L3-SUB-007 | (TBD) | Draft |
| L2-SUB-005 | L3-SUB-008 | (TBD) | Draft |
| L2-SUB-006 | L3-SUB-009, L3-SUB-010 | (TBD) | Draft |
| L2-SUB-007 | L3-SUB-011, L3-SUB-012 | (TBD) | Draft |
| L2-SUB-008 | L3-SUB-013, L3-SUB-014 | (TBD) | Draft |
| L2-SUB-009 | L3-SUB-015, L3-SUB-016 | (TBD) | Draft |
| L2-SUB-010 | L3-SUB-017, L3-SUB-018 | (TBD) | Draft |

### LAUTH: Authentication

**L1 → L2**

| L1 ID | L2 Children | Status |
|-------|-------------|--------|
| L1-AUTH-001 | L2-AUTH-001, L2-AUTH-002, L2-AUTH-003 | Draft |
| L1-AUTH-002 | L2-AUTH-004, L2-AUTH-005, L2-AUTH-006 | Draft |

**L2 → L3**

| L2 ID | L3 Children | Verification Artifacts | Status |
|-------|-------------|------------------------|--------|
| L2-AUTH-001 | L3-AUTH-001, L3-AUTH-013 | (TBD) | Draft |
| L2-AUTH-002 | L3-AUTH-002, L3-AUTH-003 | (TBD) | Draft |
| L2-AUTH-003 | L3-AUTH-004, L3-AUTH-005 | (TBD) | Draft |
| L2-AUTH-004 | L3-AUTH-006, L3-AUTH-007 | (TBD) | Draft |
| L2-AUTH-005 | L3-AUTH-008, L3-AUTH-009 | (TBD) | Draft |
| L2-AUTH-006 | L3-AUTH-010, L3-AUTH-011, L3-AUTH-012 | (TBD) | Draft |

### LMAIL: Email delivery

**L1 → L2**

| L1 ID | L2 Children | Status |
|-------|-------------|--------|
| L1-MAIL-001 | L2-MAIL-001, L2-MAIL-002, L2-MAIL-003 | Draft |
| L1-MAIL-002 | L2-MAIL-004, L2-MAIL-005, L2-MAIL-006 | Draft |
| L1-MAIL-003 | L2-MAIL-007, L2-MAIL-008 | Draft |
| L1-MAIL-004 | L2-MAIL-009, L2-MAIL-010, L2-MAIL-011 | Draft |
| L1-MAIL-005 | L2-MAIL-012, L2-MAIL-013 | Draft |

**L2 → L3**

| L2 ID | L3 Children | Verification Artifacts | Status |
|-------|-------------|------------------------|--------|
| L2-MAIL-001 | L3-MAIL-001, L3-MAIL-020 | (TBD) | Draft |
| L2-MAIL-002 | L3-MAIL-002, L3-MAIL-003, L3-MAIL-022 | (TBD) | Draft |
| L2-MAIL-003 | L3-MAIL-004 | (TBD) | Draft |
| L2-MAIL-004 | L3-MAIL-005, L3-MAIL-006 | (TBD) | Draft |
| L2-MAIL-005 | L3-MAIL-007, L3-MAIL-008, L3-MAIL-023 | (TBD) | Draft |
| L2-MAIL-006 | L3-MAIL-009, L3-MAIL-010, L3-MAIL-011 | (TBD) | Draft |
| L2-MAIL-007 | L3-MAIL-012, L3-MAIL-021 | (TBD) | Draft |
| L2-MAIL-008 | L3-MAIL-013 | (TBD) | Draft |
| L2-MAIL-009 | L3-MAIL-014 | (TBD) | Draft |
| L2-MAIL-010 | L3-MAIL-015, L3-MAIL-016 | (TBD) | Draft |
| L2-MAIL-011 | L3-MAIL-017, L3-MAIL-024 | (TBD) | Draft |
| L2-MAIL-012 | L3-MAIL-018, L3-MAIL-025 | (TBD) | Draft |
| L2-MAIL-013 | L3-MAIL-019, L3-MAIL-026 | (TBD) | Draft |

### LDASH: Dashboard

**L1 → L2**

| L1 ID | L2 Children | Status |
|-------|-------------|--------|
| L1-DASH-001 | L2-DASH-001, L2-DASH-002, L2-DASH-003 | Draft |
| L1-DASH-002 | L2-DASH-004, L2-DASH-005, L2-DASH-006 | Draft |
| L1-DASH-003 | L2-DASH-007, L2-DASH-008, L2-DASH-009 | Draft |
| L1-DASH-004 | L2-DASH-010, L2-DASH-011 | Draft |

**L2 → L3**

| L2 ID | L3 Children | Verification Artifacts | Status |
|-------|-------------|------------------------|--------|
| L2-DASH-001 | L3-DASH-001, L3-DASH-002, L3-DASH-018 | (TBD) | Draft |
| L2-DASH-002 | L3-DASH-003, L3-DASH-004, L3-DASH-019 | (TBD) | Draft |
| L2-DASH-003 | L3-DASH-005, L3-DASH-006, L3-DASH-020 | (TBD) | Draft |
| L2-DASH-004 | L3-DASH-007, L3-DASH-008 | (TBD) | Draft |
| L2-DASH-005 | L3-DASH-009 | (TBD) | Draft |
| L2-DASH-006 | L3-DASH-010 | (TBD) | Draft |
| L2-DASH-007 | L3-DASH-011, L3-DASH-021 | (TBD) | Draft |
| L2-DASH-008 | L3-DASH-012, L3-DASH-013 | (TBD) | Draft |
| L2-DASH-009 | L3-DASH-014, L3-DASH-015 | (TBD) | Draft |
| L2-DASH-010 | L3-DASH-016 | (TBD) | Draft |
| L2-DASH-011 | L3-DASH-017 | (TBD) | Draft |

### LPERS: Persistence

**L1 → L2**

| L1 ID | L2 Children | Status |
|-------|-------------|--------|
| L1-PERS-001 | L2-PERS-001, L2-PERS-002, L2-PERS-003, L2-PERS-004 | Draft |
| L1-PERS-002 | L2-PERS-005, L2-PERS-006, L2-PERS-007 | Draft |
| L1-PERS-003 | L2-PERS-008, L2-PERS-009, L2-PERS-010 | Draft |

**L2 → L3**

| L2 ID | L3 Children | Verification Artifacts | Status |
|-------|-------------|------------------------|--------|
| L2-PERS-001 | L3-PERS-001, L3-PERS-018 | (TBD) | Draft |
| L2-PERS-002 | L3-PERS-002, L3-PERS-003, L3-PERS-019 | (TBD) | Draft |
| L2-PERS-003 | L3-PERS-004, L3-PERS-005, L3-PERS-020 | (TBD) | Draft |
| L2-PERS-004 | L3-PERS-006, L3-PERS-007, L3-PERS-021 | (TBD) | Draft |
| L2-PERS-005 | L3-PERS-008, L3-PERS-009, L3-PERS-022 | (TBD) | Draft |
| L2-PERS-006 | L3-PERS-010, L3-PERS-011 | (TBD) | Draft |
| L2-PERS-007 | L3-PERS-012, L3-PERS-023 | (TBD) | Draft |
| L2-PERS-008 | L3-PERS-013, L3-PERS-014 | (TBD) | Draft |
| L2-PERS-009 | L3-PERS-015 | (TBD) | Draft |
| L2-PERS-010 | L3-PERS-016, L3-PERS-017 | (TBD) | Draft |

### LOBS: Observability

**L1 → L2**

| L1 ID | L2 Children | Status |
|-------|-------------|--------|
| L1-OBS-001 | L2-OBS-001, L2-OBS-002, L2-OBS-003 | Draft |
| L1-OBS-002 | L2-OBS-004, L2-OBS-005, L2-OBS-006 | Draft |
| L1-OBS-003 | L2-OBS-007, L2-OBS-008, L2-OBS-009 | Draft |

**L2 → L3**

| L2 ID | L3 Children | Verification Artifacts | Status |
|-------|-------------|------------------------|--------|
| L2-OBS-001 | L3-OBS-001, L3-OBS-002, L3-OBS-018 | (TBD) | Draft |
| L2-OBS-002 | L3-OBS-003, L3-OBS-004 | (TBD) | Draft |
| L2-OBS-003 | L3-OBS-005, L3-OBS-006 | (TBD) | Draft |
| L2-OBS-004 | L3-OBS-007 | (TBD) | Draft |
| L2-OBS-005 | L3-OBS-008 | (TBD) | Draft |
| L2-OBS-006 | L3-OBS-009, L3-OBS-010, L3-OBS-011 | (TBD) | Draft |
| L2-OBS-007 | L3-OBS-012, L3-OBS-013 | (TBD) | Draft |
| L2-OBS-008 | L3-OBS-014, L3-OBS-015, L3-OBS-016 | (TBD) | Draft |
| L2-OBS-009 | L3-OBS-017 | (TBD) | Draft |

### LCFG: Configuration

**L1 → L2**

| L1 ID | L2 Children | Status |
|-------|-------------|--------|
| L1-CFG-001 | L2-CFG-001, L2-CFG-002, L2-CFG-003 | Draft |
| L1-CFG-002 | L2-CFG-004, L2-CFG-005, L2-CFG-006 | Draft |
| L1-CFG-003 | L2-CFG-007, L2-CFG-008 | Draft |

**L2 → L3**

| L2 ID | L3 Children | Verification Artifacts | Status |
|-------|-------------|------------------------|--------|
| L2-CFG-001 | L3-CFG-001, L3-CFG-002 | (TBD) | Draft |
| L2-CFG-002 | L3-CFG-003, L3-CFG-016 | (TBD) | Draft |
| L2-CFG-003 | L3-CFG-004, L3-CFG-015 | (TBD) | Draft |
| L2-CFG-004 | L3-CFG-005, L3-CFG-006 | (TBD) | Draft |
| L2-CFG-005 | L3-CFG-007, L3-CFG-008 | (TBD) | Draft |
| L2-CFG-006 | L3-CFG-009 | (TBD) | Draft |
| L2-CFG-007 | L3-CFG-010, L3-CFG-011 | (TBD) | Draft |
| L2-CFG-008 | L3-CFG-012, L3-CFG-013, L3-CFG-014 | (TBD) | Draft |

### LDEP: Deployment

**L1 → L2**

| L1 ID | L2 Children | Status |
|-------|-------------|--------|
| L1-DEP-001 | L2-DEP-001, L2-DEP-002, L2-DEP-003 | Draft |
| L1-DEP-002 | L2-DEP-004, L2-DEP-005, L2-DEP-006 | Draft |
| L1-DEP-003 | L2-DEP-007, L2-DEP-008, L2-DEP-009 | Draft |

**L2 → L3**

| L2 ID | L3 Children | Verification Artifacts | Status |
|-------|-------------|------------------------|--------|
| L2-DEP-001 | L3-DEP-001, L3-DEP-002, L3-DEP-017 | (TBD) | Draft |
| L2-DEP-002 | L3-DEP-003, L3-DEP-004 | (TBD) | Draft |
| L2-DEP-003 | L3-DEP-005, L3-DEP-018 | (TBD) | Draft |
| L2-DEP-004 | L3-DEP-006, L3-DEP-007 | (TBD) | Draft |
| L2-DEP-005 | L3-DEP-008, L3-DEP-009 | (TBD) | Draft |
| L2-DEP-006 | L3-DEP-010, L3-DEP-011, L3-DEP-012 | (TBD) | Draft |
| L2-DEP-007 | L3-DEP-013 | (TBD) | Draft |
| L2-DEP-008 | L3-DEP-014 | (TBD) | Draft |
| L2-DEP-009 | L3-DEP-015, L3-DEP-016 | (TBD) | Draft |

---

## Coverage summary

### By category

| Category | L1 | L2 | L3 | L2s without L3 children | L3s without verification artifact |
|----------|----|----|----|--|--|
| API | 4 | 11 | 18 | 0 | 18 |
| RUN | 5 | 15 | 30 | 0 | 30 |
| STAGE | 4 | 9 | 18 | 0 | 18 |
| TMPL | 5 | 14 | 28 | 0 | 28 |
| AGGR | 4 | 10 | 20 | 0 | 20 |
| SWEEP | 3 | 9 | 18 | 0 | 18 |
| SUB | 4 | 10 | 20 | 0 | 20 |
| AUTH | 2 | 6 | 13 | 0 | 13 |
| MAIL | 5 | 13 | 26 | 0 | 26 |
| DASH | 4 | 11 | 21 | 0 | 21 |
| PERS | 3 | 10 | 23 | 0 | 23 |
| OBS | 3 | 9 | 18 | 0 | 18 |
| CFG | 3 | 8 | 16 | 0 | 16 |
| DEP | 3 | 9 | 18 | 0 | 18 |
| **Total** | **52** | **144** | **287** | **0** | **287** |

### Orphans and strays

- **Orphan L2s** (no L1 parent): 0 — every L2 declares a `Parent:` field referencing an L1.
- **Orphan L3s** (no L2 parent): 0 — every L3 declares a `Parent:` field referencing an L2.
- **L1s without L2 children**: 0 — full decomposition complete.
- **L2s without L3 children**: 0.
- **L3s without verification artifact**: 287 — expected at this stage; populated by implementation.

---

## Document change history

| Date       | Author | Change                                            |
|------------|--------|---------------------------------------------------|
| 2026-04-18 | Joey   | Initial matrix with L1 entries                    |
| 2026-04-18 | Joey   | L2 decomposition; 144 L2 entries                  |
| 2026-04-18 | Joey   | L3 decomposition; 287 L3 entries; full trace live |
