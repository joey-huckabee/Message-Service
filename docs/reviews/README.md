# Security reviews

This directory is the destination for **security-review records** produced after
any change to the Jinja2 template **sandbox configuration** (per **L3-TMPL-028**).

It is intentionally (near-)empty in v1: no sandbox-configuration changes have
occurred since initial authoring, which is the correct state. When such a change
lands, record the review here as a dated Markdown file.

This `README.md` exists so the directory is tracked in git (git does not track
empty directories) and so a fresh checkout — including CI — satisfies the
`L3-TMPL-028` conformance check in
`tests/unit/infrastructure/test_v1_design_inspections.py`.

> Note: a `docs/reviews/filesystem-access-points.md` prose registry deliberately
> does **not** live here — v1 maintains filesystem-access conformance in code, via
> `tests/conformance/test_pathlib_enforcement.py` and the report-pruner
> sole-deleter test (`L3-PERS-023` / `L3-PERS-035`).
