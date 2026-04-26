"""Configuration fixtures: TOML config writers for tests.

The :func:`write_e2e_config` helper writes a minimally-valid config
tree (top-level TOML, tag vocabulary TOML, template manifest TOML,
plus three Jinja2 source files) under a per-test ``tmp_path`` and
returns the top-level config path. e2e tests pass that path to
:func:`message_service.config.loader.load_config`, exercising the
real loader/parser/validator chain.

Why TOML on disk rather than constructing :class:`Config` directly:
the e2e tier's purpose is to exercise the full bootstrap path,
including config loading. Direct construction skips that surface and
hides loader regressions.
"""

from __future__ import annotations

from pathlib import Path

# Minimal Jinja2 templates that produce non-empty, grep-friendly HTML
# so e2e tests can assert on rendered output (e.g., `run_id in body`).

_EMAIL_BODY_TEMPLATE = """\
<html>
<body>
<h1>Run {{ run_id }} — {{ pipeline_type }}</h1>
<p>Tags: {{ run_metadata.tags | join(", ") }}</p>
<p>Created: {{ run_metadata.created_at }}</p>
<p>Attachment mode: {{ attachment_mode }}</p>
<ul>
{% for s in stages %}
<li>{{ s.stage_id }} (order {{ s.stage_order }}, content: {{ s.had_content }})</li>
{% endfor %}
</ul>
</body>
</html>
"""

_FRAGMENT_TEMPLATE = """\
<section data-stage="{{ metric_name }}">
<p>{{ metric_name }} = {{ metric_value }}</p>
</section>
"""

_AGGREGATION_TEMPLATE = """\
<html>
<body>
<h1>Aggregated report — {{ pipeline_type }} run {{ run_id }}</h1>
{% for s in stages %}
{{ s.rendered_html }}
{% endfor %}
</body>
</html>
"""

_TAGS_TOML = """\
[[tag]]
name = "production"

[[tag]]
name = "critical"

[[tag]]
name = "nightly"
"""

_TEMPLATES_TOML = """\
[[template]]
name = "email_body"
version = "1.0"
kind = "EMAIL_BODY"
source_path = "email_body.html.j2"

[[template]]
name = "fragment"
version = "1.0"
kind = "REPORT_FRAGMENT"
source_path = "fragment.html.j2"

[[template]]
name = "aggregation"
version = "1.0"
kind = "AGGREGATION"
source_path = "aggregation.html.j2"
"""


def write_e2e_config(
    tmp_path: Path,
    *,
    smtp_host: str,
    smtp_port: int,
    sweeper_run_timeout_seconds: int = 60,
    sweeper_poll_interval_seconds: int = 30,
) -> Path:
    """Write the e2e TOML tree under ``tmp_path``; return the top-level path.

    Args:
        tmp_path: Per-test temporary directory (pytest ``tmp_path``).
        smtp_host: SMTP host the service should connect to (the
            ``smtp_capture`` fixture's bound host).
        smtp_port: SMTP port the service should connect to.
        sweeper_run_timeout_seconds: Default 60. Orphan-path tests
            override to a small value (~2s) so they run fast.
        sweeper_poll_interval_seconds: Default 30 — long enough that
            the sweeper does NOT fire during a normal happy-path
            test. Orphan-path tests override to ~0.1s.

    Returns:
        Path to the top-level ``config.toml``.
    """
    (tmp_path / "email_body.html.j2").write_text(_EMAIL_BODY_TEMPLATE, encoding="utf-8")
    (tmp_path / "fragment.html.j2").write_text(_FRAGMENT_TEMPLATE, encoding="utf-8")
    (tmp_path / "aggregation.html.j2").write_text(_AGGREGATION_TEMPLATE, encoding="utf-8")
    (tmp_path / "templates.toml").write_text(_TEMPLATES_TOML, encoding="utf-8")
    (tmp_path / "tags.toml").write_text(_TAGS_TOML, encoding="utf-8")

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[grpc]
host = "127.0.0.1"
port = 50051

[dashboard]
host = "127.0.0.1"
port = 8080
https_only = false

[persistence]
sqlite_path = "{(tmp_path / "svc.db").as_posix()}"

[persistence.filesystem]
report_directory = "{(tmp_path / "reports").as_posix()}"

[templates]
manifest_path = "{(tmp_path / "templates.toml").as_posix()}"
max_context_bytes = 524288
max_rendered_bytes = 5242880

[templates.email_body_template_ref]
name = "email_body"
version = "1.0"

[tags]
vocabulary_path = "{(tmp_path / "tags.toml").as_posix()}"

[pipelines]
registered = ["etl-nightly", "backup-daily"]

[mail]
from_address = "svc@example.com"
max_email_size_bytes = 10485760

[mail.smtp]
host = "{smtp_host}"
port = {smtp_port}
use_starttls = false

[mail.retry]
max_retries = 1
initial_interval_seconds = 1
max_interval_seconds = 1

[sweeper]
run_timeout_seconds = {sweeper_run_timeout_seconds}
poll_interval_seconds = {sweeper_poll_interval_seconds}
""",
        encoding="utf-8",
    )
    return config_path


__all__ = ["write_e2e_config"]
