"""Integration tests for :class:`FilesystemReportStore`.

The adapter writes real files under ``tmp_path``. Tests cover:

* L3-PERS-025 directory layout — ``<root>/<run_id>/email.html`` and
  ``<root>/<run_id>/fragments/<stage_id>.html``.
* L3-PERS-026 atomic-write mechanic — every write goes through
  ``<final>.tmp`` + ``Path.replace()``; the tmp file SHALL NOT remain
  on success, and an interrupted write SHALL leave the final filename
  absent.
* :class:`ReportStore` read methods return ``None`` for absent
  artifacts (per L3-PERS-024).
* :class:`PersistenceError` wraps OSError so call sites have one
  exception type to catch.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from message_service.domain.errors import PersistenceError
from message_service.domain.ids import RunId, StageId
from message_service.infrastructure.persistence.filesystem.report_store import (
    FilesystemReportStore,
)

_RID = RunId("00000000-0000-4000-8000-000000000001")
_SID_A = StageId("extract")
_SID_B = StageId("transform")


@pytest.fixture
def store(tmp_path: Path) -> FilesystemReportStore:
    """Adapter rooted at a fresh per-test temp directory.

    Bootstrap is normally responsible for creating the root + the
    write-probe; the tests here pre-create it via the ``tmp_path``
    fixture so the adapter starts in the post-bootstrap state.
    """
    root = tmp_path / "reports"
    root.mkdir()
    return FilesystemReportStore(root=root)


# -----------------------------------------------------------------------------
# Layout (L3-PERS-025)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-PERS-025")
def test_save_email_body_writes_to_run_id_email_html(
    store: FilesystemReportStore, tmp_path: Path
) -> None:
    """Body SHALL land at ``<root>/<run_id>/email.html``."""
    store.save_email_body(_RID, "<html>body</html>")
    expected = tmp_path / "reports" / _RID / "email.html"
    assert expected.is_file()
    assert expected.read_text(encoding="utf-8") == "<html>body</html>"


@pytest.mark.requirement("L3-PERS-025")
def test_save_fragment_writes_to_run_id_fragments_stage_id_html(
    store: FilesystemReportStore, tmp_path: Path
) -> None:
    """Fragment SHALL land at ``<root>/<run_id>/fragments/<stage_id>.html``."""
    store.save_fragment(_RID, _SID_A, "<p>frag</p>")
    expected = tmp_path / "reports" / _RID / "fragments" / "extract.html"
    assert expected.is_file()
    assert expected.read_text(encoding="utf-8") == "<p>frag</p>"


@pytest.mark.requirement("L3-PERS-025")
def test_save_creates_intermediate_directories_on_demand(
    store: FilesystemReportStore, tmp_path: Path
) -> None:
    """``mkdir(parents=True, exist_ok=True)`` SHALL be applied per L3-PERS-025."""
    # The run-id and fragments subdirectories don't exist yet.
    assert not (tmp_path / "reports" / _RID).exists()
    store.save_fragment(_RID, _SID_A, "<p>frag</p>")
    assert (tmp_path / "reports" / _RID / "fragments").is_dir()


@pytest.mark.requirement("L3-PERS-025")
def test_multiple_fragments_share_one_run_directory(
    store: FilesystemReportStore, tmp_path: Path
) -> None:
    store.save_fragment(_RID, _SID_A, "A")
    store.save_fragment(_RID, _SID_B, "B")
    fragments_dir = tmp_path / "reports" / _RID / "fragments"
    saved = sorted(p.name for p in fragments_dir.iterdir())
    assert saved == ["extract.html", "transform.html"]


# -----------------------------------------------------------------------------
# Atomic-write mechanic (L3-PERS-026)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-PERS-026")
def test_save_email_body_uses_tmp_then_replace_and_leaves_no_tmp(
    store: FilesystemReportStore, tmp_path: Path
) -> None:
    """On success the ``.tmp`` sibling SHALL NOT remain."""
    store.save_email_body(_RID, "<html>body</html>")
    parent = tmp_path / "reports" / _RID
    siblings = sorted(p.name for p in parent.iterdir())
    assert siblings == ["email.html"]


@pytest.mark.requirement("L3-PERS-026")
def test_save_email_body_overwrites_existing_atomically(
    store: FilesystemReportStore, tmp_path: Path
) -> None:
    """A second save SHALL replace the prior bytes (idempotent re-render)."""
    store.save_email_body(_RID, "first")
    store.save_email_body(_RID, "second")
    final = tmp_path / "reports" / _RID / "email.html"
    assert final.read_text(encoding="utf-8") == "second"


@pytest.mark.requirement("L3-PERS-026")
def test_interrupted_write_leaves_final_file_absent(
    store: FilesystemReportStore, tmp_path: Path
) -> None:
    """If ``Path.replace`` fails, the final filename SHALL NOT exist.

    Simulates the "kill between write and rename" condition pinned in
    L3-PERS-026 by patching ``Path.replace`` to raise OSError.
    """
    final = tmp_path / "reports" / _RID / "email.html"

    with (
        patch.object(Path, "replace", side_effect=OSError("simulated crash")),
        pytest.raises(PersistenceError),
    ):
        store.save_email_body(_RID, "<html>body</html>")

    assert not final.exists()


# -----------------------------------------------------------------------------
# Read semantics (L3-PERS-024)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-PERS-024")
def test_read_email_body_round_trips_saved_bytes(store: FilesystemReportStore) -> None:
    store.save_email_body(_RID, "<html>π</html>")  # multi-byte UTF-8 included
    assert store.read_email_body(_RID) == "<html>π</html>"


@pytest.mark.requirement("L3-PERS-024")
def test_read_email_body_returns_none_for_absent_run(
    store: FilesystemReportStore,
) -> None:
    """Missing artifact SHALL surface as ``None`` rather than raise."""
    assert store.read_email_body(_RID) is None


@pytest.mark.requirement("L3-PERS-024")
def test_read_fragment_round_trips_saved_bytes(store: FilesystemReportStore) -> None:
    store.save_fragment(_RID, _SID_A, "<p>frag</p>")
    assert store.read_fragment(_RID, _SID_A) == "<p>frag</p>"


@pytest.mark.requirement("L3-PERS-024")
def test_read_fragment_returns_none_for_absent_stage(
    store: FilesystemReportStore,
) -> None:
    assert store.read_fragment(_RID, _SID_A) is None


@pytest.mark.requirement("L3-PERS-024")
def test_read_fragment_returns_none_for_unrelated_run(
    store: FilesystemReportStore,
) -> None:
    """Saved fragment under one run_id SHALL NOT leak to another."""
    store.save_fragment(_RID, _SID_A, "<p>frag</p>")
    other = RunId("00000000-0000-4000-8000-deadbeef0000")
    assert store.read_fragment(other, _SID_A) is None


# -----------------------------------------------------------------------------
# Error handling
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-PERS-024")
def test_save_wraps_oserror_in_persistence_error(
    store: FilesystemReportStore,
) -> None:
    """Underlying OSError SHALL surface as :class:`PersistenceError`."""
    with (
        patch.object(Path, "write_text", side_effect=OSError("disk full")),
        pytest.raises(PersistenceError) as excinfo,
    ):
        store.save_email_body(_RID, "<html>body</html>")
    assert "report file" in str(excinfo.value)
    assert excinfo.value.details.get("os_error") == "disk full"
