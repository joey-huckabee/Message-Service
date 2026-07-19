# Message-Service — Template Author Guide

How to add, validate, version, and test a Jinja2 template for Message-Service.
This complements the [Pipeline Integration Guide](pipeline-integration-guide.md)
(which covers the gRPC contract that *references* templates) and the normative
requirements in `docs/L*-REQ.md` (`L1-TMPL-001`…`L1-TMPL-005`).

## 1. The three template kinds

Every template has a `kind` that fixes where it runs and what context it
receives (`TemplateKind` in `domain/aggregates/template_metadata.py`):

| `kind` | Rendered… | Produces | Context is supplied by |
|--------|-----------|----------|------------------------|
| `REPORT_FRAGMENT` | per stage, from that stage's `SubmitStageReport` | one attachment fragment | the **pipeline** (the stage's `context` map) |
| `AGGREGATION` | once per run, `SINGLE_AGGREGATED` mode | the single composite attachment | the **service** (rendered fragments in `stage_order`) |
| `EMAIL_BODY` | once per finalized run | the email body | the **service** (run metadata + per-stage body contributions) |

The distinction matters for schema validation (§4): a `REPORT_FRAGMENT`'s
context is pipeline-supplied and *should* carry a JSON Schema; `AGGREGATION` /
`EMAIL_BODY` contexts are service-produced and typically omit one.

## 2. Register it in the manifest

Templates are never loaded by filesystem scan — each must be declared in the
manifest TOML at the `templates.manifest_path` config key. The manifest is
loaded and fully validated at startup; a malformed entry fails the service
start (`L2-TMPL-003`). See `config/templates.manifest.example.toml` for a
copy-ready example.

```toml
[[template]]
name                = "nightly-summary"    # required — referenced by pipelines
version             = "1.2.0"              # required — semver (packaging.version)
kind                = "REPORT_FRAGMENT"    # required — REPORT_FRAGMENT | AGGREGATION | EMAIL_BODY
source_path         = "./templates/nightly_summary.html.j2"   # required
context_schema_path = "./templates/nightly_summary.schema.json"  # optional
description         = "Nightly ETL per-stage summary."          # optional
```

Rules the loader enforces (`infrastructure/templating/manifest_loader.py`):

- **Required:** `name`, `version`, `kind`, `source_path`. **Optional:**
  `context_schema_path`, `description`. Any other key is rejected.
- All `*_path` values resolve **relative to the manifest file's directory**.
- `(name, version)` must be unique across the manifest.
- `source_path` and `context_schema_path` (if given) must exist and be readable.
- `kind` must be one of the three enum values.

## 3. Reference it from a pipeline

A pipeline names a template by `(name, version)` in a `BeginRun` /
`SubmitStageReport` template ref. `version` may be an explicit semver **or** the
literal `"latest"`:

- `"latest"` resolves to the **highest** semver among all manifest entries
  sharing that `name`, and is resolved **once at `BeginRun` initiation**, not at
  render time (`L2-TMPL-007`). A run therefore renders against a frozen version
  even if you add a newer one mid-run.
- An unknown `(name, version)` ref is rejected at initiation with error code
  `UNKNOWN_TEMPLATE` (`L2-RUN-010`).

Because `"latest"` freezes at initiation, publishing a new version is safe:
in-flight runs keep the version they started with; new runs pick up the bump.

## 4. Write the context JSON Schema (`REPORT_FRAGMENT`)

If a template declares a `context_schema_path`, the submitted context is
validated against that JSON Schema **before** the template renders
(`L1-TMPL-004`). A violation is rejected with `CONTEXT_SCHEMA_VIOLATION`, whose
`details` include a `json_pointer` to the offending element — so pipelines get a
precise, machine-parseable error instead of a render blow-up.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["record_count", "source"],
  "properties": {
    "record_count": { "type": "integer", "minimum": 0 },
    "source":       { "type": "string", "minLength": 1 },
    "duration_seconds": { "type": "number" }
  },
  "additionalProperties": false
}
```

Prefer `additionalProperties: false` and explicit `required` — the schema is the
contract between the pipeline and the template, and a tight schema turns a typo
into a clear rejection rather than a silently-missing value.

## 5. What context each kind receives

Read the reference templates in `config/dev-templates/` for working examples.

- **`REPORT_FRAGMENT`** receives the stage's submitted `context` map verbatim
  (validated per §4). Example (`extract_report.html.j2`): `record_count`,
  `source`, `duration_seconds`.
- **`AGGREGATION`** receives `pipeline_type`, `run_id`, `tags`, and `stages` —
  an ordered (`stage_order`) list whose entries carry the pre-rendered fragment
  HTML (`stage.rendered_html`). See `aggregation.html.j2`.
- **`EMAIL_BODY`** receives `run_id`, `pipeline_type`, `run_metadata`
  (`tags`, `created_at`), `stages` (the per-stage summary), `attachment_mode`,
  and the two per-stage email-body contribution buckets `before_contributions`
  / `after_contributions` (each entry `{stage_id, stage_order, context}`, sorted
  by `(stage_order, stage_id)`, per `L3-AGGR-005`). See `email_body.html.j2`.

## 6. Rendering rules — the sandbox

Templates render in a Jinja2 `SandboxedEnvironment` (`L1-TMPL-003`). Author
within these constraints:

- **`autoescape=True`** — string values are HTML-escaped by default. Only pass a
  value through `| safe` when it is already trusted HTML (e.g. the aggregation
  template emitting an already-rendered, already-escaped fragment).
- **`StrictUndefined`** — referencing a context key that isn't present raises
  rather than rendering blank. Use `| default(...)` for genuinely optional
  fields (see `extract_report.html.j2`).
- **No filesystem, network, or import access**, and only a whitelist of filters
  and globals. Do not reach for `{% include %}` of arbitrary paths or custom
  Python — it will not be available.
- **Size limits** — the submitted context may not exceed
  `templates.max_context_bytes` (default 1 MiB → `CONTEXT_SIZE_EXCEEDED`), and
  rendered output may not exceed `templates.max_rendered_bytes` (default 10 MiB
  → `RENDERED_SIZE_EXCEEDED`). Keep contexts lean; push large payloads into
  attachments, not the email body.

Any other render failure surfaces as `TEMPLATE_RENDER_ERROR`.

## 7. Per-pipeline overrides

Two optional `[pipelines.*]` config maps let a specific pipeline diverge from
the service defaults without touching the template contract:

- **`pipelines.subject_templates`** (`L2-MAIL-014`) — a per-pipeline email
  `Subject:` override (a `str.format` string over `{pipeline_type}` / `{run_id}`,
  *not* a manifest template).
- **`pipelines.email_body_template_overrides`** (`L2-TMPL-015`) — a per-pipeline
  `EMAIL_BODY` template ref (`{name, version}`) overriding the service-wide
  `templates.email_body_template_ref`. The referenced template must exist in the
  manifest — this is validated at startup.

Both are keyed by a **registered** `pipeline_type`; an empty/absent map is the
default and preserves the service-wide behavior. See `config/config.toml.example`.

## 8. Test a template in isolation

The renderer is a plain adapter, so a template can be exercised directly without
standing up the service. The unit tests under
`tests/unit/infrastructure/templating/` show the pattern: build an
`InMemoryTemplateRepository` (or `load_template_manifest` over a fixture
manifest), construct the `Jinja2SandboxedTemplateRenderer` with the size limits,
and call `render(TemplateRef(name, version), context)`. Assert on the rendered
HTML, and — for a schema-bearing template — assert that a bad context raises
`ContextSchemaViolationError` with the expected `json_pointer`.

A good template PR adds, at minimum:

1. the manifest entry + source file (+ schema for a `REPORT_FRAGMENT`),
2. a render test over a representative context, and
3. for schema-bearing templates, a rejection test proving the schema catches a
   malformed context.

## 9. Checklist

- [ ] Manifest entry has `name`, `version`, `kind`, `source_path` (+ schema for
      a `REPORT_FRAGMENT`); no stray keys; `(name, version)` unique.
- [ ] Paths resolve relative to the manifest and the files exist.
- [ ] Schema uses `required` + `additionalProperties: false`.
- [ ] Template renders under `autoescape` + `StrictUndefined`; optional fields
      use `| default(...)`; only trusted HTML uses `| safe`.
- [ ] Context stays well under `max_context_bytes`; output under
      `max_rendered_bytes`.
- [ ] A render test and (schema-bearing) a rejection test are added.
- [ ] The service starts cleanly with the new manifest (startup validates it).
