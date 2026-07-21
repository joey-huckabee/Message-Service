"""Filesystem-backed :class:`ReportStore` adapter.

Implements the layout pinned in L3-PERS-025::

    <root>/
      <run_id>/
        email.html
        fragments/
          <stage_id>.html

Writes use the atomic-rename mechanic pinned in L2-PERS-005 /
L3-PERS-026: write the rendered bytes to a sibling file with the same
name plus a ``.tmp`` suffix, then call :meth:`pathlib.Path.replace` to
atomically promote it. Same-directory rename is atomic on both POSIX
and Windows.

Reads return ``None`` when the requested file is absent — the dashboard
report-viewer routes translate that into HTTP 404 with uniform privacy
semantics (see L3-DASH-029 / L3-DASH-030).

Requirement references
----------------------
L1-PERS-002, L2-PERS-005, L2-PERS-006, L2-PERS-007
L3-PERS-024, L3-PERS-025, L3-PERS-026
"""

from __future__ import annotations

from pathlib import Path

from message_service.application.ports.report_store import ReportStore
from message_service.domain.errors import PersistenceError
from message_service.domain.ids import RunId, StageId

_EMAIL_BODY_FILENAME = "email.html"
_FRAGMENTS_DIRNAME = "fragments"


class FilesystemReportStore(ReportStore):
    """Concrete :class:`ReportStore` writing under a configured root.

    The constructor does NOT create the root directory; bootstrap is
    responsible for ``mkdir`` + the writable-test (L3-PERS-010 /
    L3-PERS-011) before constructing the adapter. That keeps the
    adapter a pure value object and lets startup fail loudly on
    misconfiguration before any use case runs.
    """

    def __init__(self, *, root: Path) -> None:
        """Bind the adapter to the configured report-directory root.

        Args:
            root: The configured ``persistence.filesystem.report_directory``.
                MUST already exist and be writable; bootstrap performs
                that check.
        """
        self._root = root

    # ------------------------------------------------------------------
    # Email body
    # ------------------------------------------------------------------

    def save_email_body(self, run_id: RunId, html: str) -> None:
        """Atomically save the assembled email body for ``run_id``."""
        path = self._email_body_path(run_id)
        self._atomic_write_text(path, html)

    def read_email_body(self, run_id: RunId) -> str | None:
        """Return the saved email body or ``None`` if absent."""
        path = self._email_body_path(run_id)
        return self._read_text_if_exists(path)

    # ------------------------------------------------------------------
    # Per-stage fragments
    # ------------------------------------------------------------------

    def save_fragment(self, run_id: RunId, stage_id: StageId, html: str) -> None:
        """Atomically save the per-stage rendered fragment."""
        path = self._fragment_path(run_id, stage_id)
        self._atomic_write_text(path, html)

    def read_fragment(self, run_id: RunId, stage_id: StageId) -> str | None:
        """Return the saved fragment or ``None`` if absent."""
        path = self._fragment_path(run_id, stage_id)
        return self._read_text_if_exists(path)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _email_body_path(self, run_id: RunId) -> Path:
        return self._contained(self._root / str(run_id) / _EMAIL_BODY_FILENAME)

    def _fragment_path(self, run_id: RunId, stage_id: StageId) -> Path:
        return self._contained(self._root / str(run_id) / _FRAGMENTS_DIRNAME / f"{stage_id}.html")

    def _contained(self, path: Path) -> Path:
        """Resolve ``path`` and reject anything that escapes the report root.

        ``run_id`` / ``stage_id`` flow in from the wire and become path
        components (``stage_id`` is a bare ``NewType(str)`` with no charset
        validation), so a value like ``../../evil`` would otherwise write or
        read outside the report tree — arbitrary-file access. Resolving and
        requiring containment defeats ``..`` segments, absolute paths, and
        Windows backslash separators regardless of the caller.

        Raises:
            PersistenceError: The resolved path is not inside the report root.
        """
        resolved = path.resolve()
        if not resolved.is_relative_to(self._root.resolve()):
            raise PersistenceError(
                "report path escapes the report root",
                details={"path": str(path)},
            )
        return resolved

    @staticmethod
    def _atomic_write_text(path: Path, html: str) -> None:
        """Write ``html`` to ``path`` via tmp-then-replace (L3-PERS-026).

        Creates intermediate directories on demand per L3-PERS-025.
        Wraps :class:`OSError` from any of the three filesystem
        operations into :class:`PersistenceError` so call sites have a
        single exception type to catch.
        """
        tmp_path = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(html, encoding="utf-8")
            tmp_path.replace(path)
        except OSError as exc:
            raise PersistenceError(
                f"failed to save report file: {path}",
                details={"path": str(path), "os_error": str(exc)},
            ) from exc

    @staticmethod
    def _read_text_if_exists(path: Path) -> str | None:
        """Return decoded UTF-8 contents or ``None`` if the file is missing."""
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")


__all__ = ["FilesystemReportStore"]
