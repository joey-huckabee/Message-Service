"""Helpers shared across scenarios for one-shot config / data setup.

Each scenario keeps its own ``config.toml`` for clarity (so a reader
can see exactly what differs from the project default), but the
boilerplate of writing template files and tag vocabularies — which
several scenarios reuse — lives here.
"""

from __future__ import annotations

import json
from pathlib import Path

_DEFAULT_BODY_TEMPLATE = """\
<html>
  <body>
    <h2>Run {{ run_id }}</h2>
    <p>Pipeline: {{ pipeline_type }}</p>
    <p>Stages reported:</p>
    <ul>
    {% for stage in stages %}
      <li>{{ stage.stage_id }} (order {{ stage.stage_order }})</li>
    {% endfor %}
    </ul>
  </body>
</html>
"""

_DEFAULT_FRAGMENT_TEMPLATE = """\
<section>
  <h3>{{ stage_id | default('stage') }}</h3>
  <pre>{{ payload | default('(no data)') }}</pre>
</section>
"""

_DEFAULT_AGGREGATION_TEMPLATE = """\
<html>
  <body>
    <h2>Aggregated report — run {{ run_id }}</h2>
    <p>Pipeline: {{ pipeline_type }}</p>
    {% for stage in stages %}
      {{ stage.rendered_html | safe }}
    {% endfor %}
  </body>
</html>
"""


def write_default_templates(directory: Path) -> dict[str, Path]:
    """Drop the three default Jinja2 sources into ``directory``.

    Returns a dict mapping logical names to written file paths so the
    scenario can wire them into the manifest or pass them around.
    """
    directory.mkdir(parents=True, exist_ok=True)
    body_path = directory / "email_body.html.j2"
    fragment_path = directory / "fragment.html.j2"
    aggregation_path = directory / "aggregation.html.j2"
    # newline="\n" keeps the generated templates LF-only on Windows so
    # repeated runs don't churn line endings vs what's checked in.
    body_path.write_text(_DEFAULT_BODY_TEMPLATE, encoding="utf-8", newline="\n")
    fragment_path.write_text(_DEFAULT_FRAGMENT_TEMPLATE, encoding="utf-8", newline="\n")
    aggregation_path.write_text(_DEFAULT_AGGREGATION_TEMPLATE, encoding="utf-8", newline="\n")
    return {
        "email_body": body_path,
        "fragment": fragment_path,
        "aggregation": aggregation_path,
    }


def write_template_manifest(
    manifest_path: Path,
    template_paths: dict[str, Path],
) -> None:
    """Write a TOML manifest pointing at ``template_paths``.

    Manifest entries: ``email_body`` (EMAIL_BODY), ``fragment``
    (REPORT_FRAGMENT), ``aggregation`` (AGGREGATION). All at version
    ``1.0``.
    """
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    body = template_paths["email_body"].as_posix()
    fragment = template_paths["fragment"].as_posix()
    aggregation = template_paths["aggregation"].as_posix()
    manifest_path.write_text(
        f"""\
[[template]]
name = "email_body"
version = "1.0"
kind = "EMAIL_BODY"
source_path = "{body}"

[[template]]
name = "fragment"
version = "1.0"
kind = "REPORT_FRAGMENT"
source_path = "{fragment}"

[[template]]
name = "aggregation"
version = "1.0"
kind = "AGGREGATION"
source_path = "{aggregation}"
""",
        encoding="utf-8",
        newline="\n",
    )


def write_tag_vocabulary(path: Path, names: list[str]) -> None:
    """Write a TOML tag vocabulary listing every name in ``names``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for name in names:
        lines.append(f'[[tag]]\nname = "{name}"\n')
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def reset_state_dirs(*dirs: Path) -> None:
    """Delete every directory in ``dirs`` (idempotent demo cleanup).

    Demos call this on entry so a fresh run produces consistent
    output regardless of prior state.
    """
    import shutil

    for d in dirs:
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)


def reset_sqlite_files(*paths: Path) -> None:
    """Delete a SQLite database and any sidecar files (WAL / SHM)."""
    for p in paths:
        for sidecar in (p, Path(f"{p}-wal"), Path(f"{p}-shm")):
            if sidecar.exists():
                sidecar.unlink()


def stage_struct(payload: dict[str, object]) -> object:
    """Wrap a Python dict as a ``google.protobuf.Struct``.

    Returns the proto Struct ready to attach to a
    ``ReportContribution.context`` field.
    """
    from google.protobuf.struct_pb2 import Struct

    s = Struct()
    s.update(payload)
    return s


def dump_json(payload: object) -> str:
    """Pretty-print a payload (helper for demo output)."""
    return json.dumps(payload, indent=2, default=str)
