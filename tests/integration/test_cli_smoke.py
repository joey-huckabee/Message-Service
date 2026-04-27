"""CLI smoke + cross-platform line-ending tests.

`L3-DEP-016` requires the installed ``message-service`` console
script to respond to ``--help`` with exit code 0 and help text
containing "config". `L3-DEP-017` requires that templates and
configs load identically when their on-disk line endings are
LF (Linux) or CRLF (Windows) — a real concern because
``poetry-built`` artifacts may pass through git-attribute
transformation between platforms.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from message_service.config.loader import load_config

# -----------------------------------------------------------------------------
# CLI --help smoke (L3-DEP-016)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-DEP-016")
def test_message_service_cli_help_exits_zero_with_config_in_output() -> None:
    """L3-DEP-016: ``poetry run message-service --help`` SHALL exit 0
    and the output SHALL contain "config".

    Uses ``python -m message_service`` rather than the installed
    console script; both bind to the same ``main`` function (per
    the cli/main.py re-export in `interfaces.cli`), and the module
    invocation does not depend on the virtualenv's ``Scripts/``
    directory being on PATH for the test process. The CI workflow
    additionally exercises the installed-script path per L1-CICD-001.
    """
    result = subprocess.run(
        [sys.executable, "-m", "message_service", "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, f"--help exit code = {result.returncode}; stderr={result.stderr}"
    assert "config" in result.stdout.lower(), f"'config' not in --help output: {result.stdout}"


# -----------------------------------------------------------------------------
# Cross-platform line-ending compatibility (L3-DEP-017)
# -----------------------------------------------------------------------------


def _make_config_text() -> str:
    """The same minimally-valid config used by other integration tests."""
    return """\
[grpc]
host = "127.0.0.1"
port = 50051

[dashboard]
host = "127.0.0.1"
port = 8080

[persistence]
sqlite_path = "PLACEHOLDER_SQLITE"

[persistence.filesystem]
report_directory = "PLACEHOLDER_REPORTS"

[templates]
manifest_path = "PLACEHOLDER_TEMPLATES"
max_context_bytes = 524288
max_rendered_bytes = 5242880

[templates.email_body_template_ref]
name = "email_body"
version = "1.0"

[tags]
vocabulary_path = "PLACEHOLDER_TAGS"

[pipelines]
registered = ["etl-nightly"]

[mail]
from_address = "svc@example.com"
max_email_size_bytes = 10485760

[mail.smtp]
host = "smtp.example.com"
port = 587
username = "u"
password = "p"

[mail.retry]
max_retries = 1
initial_interval_seconds = 1
max_interval_seconds = 1
"""


def _write_supporting_files(tmp_path: Path, line_ending: str) -> Path:
    """Write the supporting templates/tags TOMLs + Jinja sources, with the
    requested line ending. Returns the top-level config path.
    """
    template_text = """\
[[template]]
name = "email_body"
version = "1.0"
kind = "EMAIL_BODY"
source_path = "body.html.j2"
"""
    tags_text = """\
[[tag]]
name = "production"
"""
    body_text = "<p>{{ run_id }}</p>\n"

    # Write supporting files with the chosen line ending.
    (tmp_path / "templates.toml").write_bytes(
        template_text.replace("\n", line_ending).encode("utf-8")
    )
    (tmp_path / "tags.toml").write_bytes(tags_text.replace("\n", line_ending).encode("utf-8"))
    (tmp_path / "body.html.j2").write_bytes(body_text.replace("\n", line_ending).encode("utf-8"))

    config_text = (
        _make_config_text()
        .replace("PLACEHOLDER_SQLITE", (tmp_path / "svc.db").as_posix())
        .replace("PLACEHOLDER_REPORTS", (tmp_path / "reports").as_posix())
        .replace("PLACEHOLDER_TEMPLATES", (tmp_path / "templates.toml").as_posix())
        .replace("PLACEHOLDER_TAGS", (tmp_path / "tags.toml").as_posix())
    )
    config_path = tmp_path / "config.toml"
    config_path.write_bytes(config_text.replace("\n", line_ending).encode("utf-8"))
    return config_path


@pytest.mark.requirement("L3-DEP-017")
def test_config_loads_identically_with_lf_line_endings(tmp_path: Path) -> None:
    """LF-line-ending config + supporting files SHALL load successfully."""
    config_path = _write_supporting_files(tmp_path, line_ending="\n")
    config = load_config(config_path)
    assert config.grpc.port == 50051
    assert list(config.pipelines.registered) == ["etl-nightly"]


@pytest.mark.requirement("L3-DEP-017")
def test_config_loads_identically_with_crlf_line_endings(tmp_path: Path) -> None:
    """CRLF-line-ending config + supporting files SHALL load successfully.

    This is the Windows case — git-attribute transformation may
    convert LF→CRLF on checkout. Pydantic + tomllib both handle
    CRLF natively; this test pins that expectation.
    """
    config_path = _write_supporting_files(tmp_path, line_ending="\r\n")
    config = load_config(config_path)
    assert config.grpc.port == 50051
    assert list(config.pipelines.registered) == ["etl-nightly"]


@pytest.mark.requirement("L3-DEP-017")
def test_config_lf_and_crlf_produce_equivalent_objects(tmp_path: Path) -> None:
    """Same content with different line endings SHALL produce identical Config objects.

    Verifies the loader does not surface line-ending bytes anywhere
    in the parsed object — every string value stripped of trailing
    `\\r` / `\\n` characters by the TOML parser, every numeric value
    parsed identically.
    """
    lf_dir = tmp_path / "lf"
    crlf_dir = tmp_path / "crlf"
    lf_dir.mkdir()
    crlf_dir.mkdir()

    lf_config = load_config(_write_supporting_files(lf_dir, "\n"))
    crlf_config = load_config(_write_supporting_files(crlf_dir, "\r\n"))

    # Compare the structurally meaningful fields. Filesystem paths
    # differ between the two tmp-subdirs but everything else SHALL
    # match.
    assert lf_config.grpc == crlf_config.grpc
    assert lf_config.pipelines == crlf_config.pipelines
    assert lf_config.mail == crlf_config.mail
    assert lf_config.templates.email_body_template_ref == (
        crlf_config.templates.email_body_template_ref
    )
