"""Port: filesystem-backed storage for rendered reports.

The ``ReportStore`` port persists the assembled email body and the
per-stage rendered fragments produced by
:class:`~message_service.application.use_cases.assemble_and_deliver.AssembleAndDeliverUseCase`.
The dashboard's report-viewer routes read these saved bytes back so
operators see the exact HTML that was delivered (rather than a fresh
re-render that may differ if templates have since changed).

The resend path (Increment 19b) deliberately does NOT consult this
store; per L3-DASH-027 it re-renders against the persisted
:attr:`Stage.report_context_json`. The store is read-only from the
viewer's perspective and write-only from the assemble path's
perspective.

Read methods SHALL return ``None`` when the requested artifact is
absent (run pre-dates the store, render-stage failed before the
fragment was written, etc.) — callers translate that into HTTP 404
with uniform privacy semantics rather than disclosing whether the run
or stage is the missing piece.

Requirement references
----------------------
L1-PERS-002 (filesystem storage of rendered reports)
L2-PERS-005 (atomic-rename writes)
L2-PERS-008 (port = abc.ABC with full type hints)
L2-DASH-014 (report-viewer routes consume this port)
L3-PERS-024, L3-PERS-025, L3-PERS-026
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from message_service.domain.ids import RunId, StageId


class ReportStore(ABC):
    """Abstract port for the filesystem-backed rendered-report store.

    Concrete adapters live under ``infrastructure/persistence/filesystem/``
    and use the atomic-rename mechanic pinned in ``L2-PERS-005`` /
    ``L3-PERS-026``: write to ``<final>.tmp``, then ``Path.replace()``.
    """

    @abstractmethod
    def save_email_body(self, run_id: RunId, html: str) -> None:
        """Persist the assembled email body for a delivered run.

        Args:
            run_id: The run whose body is being saved.
            html: Rendered email-body HTML, encoded to UTF-8 by the
                adapter.

        Raises:
            PersistenceError: Underlying I/O failed. Use cases that
                survive without the saved snapshot (the assemble path
                completes the run regardless) MAY catch and log.
        """

    @abstractmethod
    def read_email_body(self, run_id: RunId) -> str | None:
        """Return the previously saved email body or ``None`` if absent.

        Args:
            run_id: Target run.

        Returns:
            The decoded HTML if a saved body exists; ``None`` otherwise.
            ``None`` covers all of: missing run, run that pre-dates the
            store, and runs whose delivery failed before the body was
            saved.
        """

    @abstractmethod
    def save_fragment(self, run_id: RunId, stage_id: StageId, html: str) -> None:
        """Persist a per-stage rendered fragment.

        Called once per stage during the assemble path. Each fragment
        is saved as it is rendered so that partial render output is
        viewable even when a later stage's render fails.

        Args:
            run_id: The run that owns the stage.
            stage_id: Target stage identifier.
            html: Rendered fragment HTML.

        Raises:
            PersistenceError: Underlying I/O failed.
        """

    @abstractmethod
    def read_fragment(self, run_id: RunId, stage_id: StageId) -> str | None:
        """Return the previously saved fragment or ``None`` if absent.

        Args:
            run_id: Target run.
            stage_id: Target stage.

        Returns:
            The decoded HTML if a saved fragment exists; ``None``
            otherwise. ``None`` covers missing run, missing stage,
            stages that never produced a fragment (empty render), and
            runs that pre-date the store.
        """


class NoOpReportStore(ReportStore):
    """``ReportStore`` that drops writes and returns ``None`` on read.

    Used by tests that exercise the assemble path without caring about
    saved-snapshot behavior, mirroring the
    :class:`~message_service.application.ports.metrics_recorder.NoOpMetricsRecorder`
    pattern. Production wiring uses the filesystem adapter.
    """

    def save_email_body(self, run_id: RunId, html: str) -> None:
        """Drop the write."""
        del run_id, html

    def read_email_body(self, run_id: RunId) -> str | None:
        """Always return ``None``."""
        del run_id
        return None

    def save_fragment(self, run_id: RunId, stage_id: StageId, html: str) -> None:
        """Drop the write."""
        del run_id, stage_id, html

    def read_fragment(self, run_id: RunId, stage_id: StageId) -> str | None:
        """Always return ``None``."""
        del run_id, stage_id
        return None


__all__ = ["NoOpReportStore", "ReportStore"]
