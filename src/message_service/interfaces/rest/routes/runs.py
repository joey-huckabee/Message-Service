"""Past-runs, run-detail, resend, and report-viewer dashboard routes.

Read + action routes under ``/runs``:

* ``GET /runs?limit=&offset=&states=`` — paginated list per
  L3-DASH-022/023/024 (Increment 19a).
* ``GET /runs/{run_id}`` — run detail with ordered stage list per
  L3-DASH-025/026 (Increment 19a).
* ``POST /runs/{run_id}/resend`` — manual resend per
  L3-DASH-012/013/027/028 (Increment 19b).
* ``GET /runs/{run_id}/report`` — saved email body per L3-DASH-029
  (Increment 19c).
* ``GET /runs/{run_id}/stages/{stage_id}/fragment`` — saved per-stage
  fragment per L3-DASH-030 (Increment 19c).

Requirement references
----------------------
L1-DASH-003 (past-runs view, resend, report viewer)
L2-DASH-012, L2-DASH-013, L2-DASH-014
L3-DASH-022..L3-DASH-030
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict

from message_service.domain.aggregates.run import AttachmentMode
from message_service.domain.errors import InvalidRunStateError, RunNotFoundError
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.run_states import TERMINAL_STATES, RunState
from message_service.domain.state_machines.stage_states import StageState
from message_service.interfaces.rest.app import require_session

if TYPE_CHECKING:
    from message_service.bootstrap import Service
    from message_service.domain.aggregates.run import Run
    from message_service.domain.aggregates.stage import Stage

# Permitted run-id pattern: UUID4 (L3-DASH-025). FastAPI's Path with a
# regex returns 422 for non-matching values, satisfying the validator
# obligation.
_UUID4_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"


# -----------------------------------------------------------------------------
# Response models
# -----------------------------------------------------------------------------


def _iso_z(value: datetime) -> str:
    """Render a UTC datetime as an ISO-8601 string with the ``Z`` suffix."""
    return value.isoformat().replace("+00:00", "Z")


class RunSummaryResponse(BaseModel):
    """L3-DASH-026 + L3-DASH-024 list-item shape."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    pipeline_type: str
    state: RunState
    attachment_mode: AttachmentMode
    tags: list[str]
    created_at: str
    updated_at: str


class StageInDetailResponse(BaseModel):
    """L3-DASH-026 stage projection (no JSON context payloads)."""

    model_config = ConfigDict(extra="forbid")

    stage_id: str
    state: StageState
    submitted_at: str | None


class RunDetailResponse(BaseModel):
    """L3-DASH-026 detail-response payload."""

    model_config = ConfigDict(extra="forbid")

    run: RunSummaryResponse
    stages: list[StageInDetailResponse]


# -----------------------------------------------------------------------------
# Projections
# -----------------------------------------------------------------------------


def _project_run_summary(run: Run) -> RunSummaryResponse:
    return RunSummaryResponse(
        run_id=str(run.run_id),
        pipeline_type=run.pipeline_type,
        state=run.state,
        attachment_mode=run.attachment_mode,
        tags=sorted(run.tags),
        created_at=_iso_z(run.created_at),
        updated_at=_iso_z(run.updated_at),
    )


def _project_stage(stage: Stage) -> StageInDetailResponse:
    return StageInDetailResponse(
        stage_id=str(stage.stage_id),
        state=stage.state,
        submitted_at=_iso_z(stage.submitted_at) if stage.submitted_at else None,
    )


# -----------------------------------------------------------------------------
# Router factory
# -----------------------------------------------------------------------------


def build_runs_router(service: Service) -> APIRouter:
    """Construct the runs router bound to a service.

    19a mounts ``GET /runs`` and ``GET /runs/{run_id}``. 19b will add
    ``POST /runs/{run_id}/resend``; 19c will add the report-viewer
    GET routes. Each future increment extends this router rather
    than introducing a new one, so the URL prefix and tags remain
    consistent.

    Args:
        service: The constructed service whose ``list_past_runs`` and
            ``get_run_detail`` use cases the routes close over.

    Returns:
        An :class:`APIRouter` mounted under ``/runs``.
    """
    router = APIRouter(prefix="/runs", tags=["runs"])

    @router.get("")
    async def list_runs(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        states: Annotated[list[RunState] | None, Query()] = None,
        _user_id: int = Depends(require_session),
    ) -> list[RunSummaryResponse]:
        """L3-DASH-022..024: paginated past-runs listing.

        ``limit`` defaults to 50, max 200; ``offset`` defaults to 0.
        ``states`` defaults to ``TERMINAL_STATES`` (per L3-DASH-023)
        when omitted; otherwise the supplied set is used. FastAPI's
        Query validation returns 422 for out-of-range / non-integer /
        unknown-enum values.
        """
        del _user_id  # auth gate only
        effective_states = frozenset(states) if states else TERMINAL_STATES
        runs = await service.list_past_runs.execute(
            limit=limit,
            offset=offset,
            states=effective_states,
        )
        return [_project_run_summary(r) for r in runs]

    @router.get("/{run_id}")
    async def get_run(
        run_id: Annotated[str, Path(pattern=_UUID4_PATTERN)],
        _user_id: int = Depends(require_session),
    ) -> RunDetailResponse:
        """L3-DASH-025/026: run detail with ordered stage list.

        ``run_id`` is validated as UUID4 (non-matching values yield
        HTTP 422). Missing runs yield HTTP 404 with a generic detail.
        """
        del _user_id
        try:
            detail = await service.get_run_detail.execute(run_id=RunId(run_id))
        except RunNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="run not found",
            ) from exc
        return RunDetailResponse(
            run=_project_run_summary(detail.run),
            stages=[_project_stage(s) for s in detail.stages],
        )

    @router.get(
        "/{run_id}/report",
        response_class=HTMLResponse,
    )
    async def get_report(
        run_id: Annotated[str, Path(pattern=_UUID4_PATTERN)],
        _user_id: int = Depends(require_session),
    ) -> HTMLResponse:
        """L3-DASH-029: saved email body for a run.

        Reads via :meth:`ReportStore.read_email_body`; ``None`` is
        translated to HTTP 404 with a generic detail string. The same
        404 fires for missing run, run that pre-dates the store, and
        runs that failed before delivery — so the route never
        discloses which of those happened (uniform privacy mirroring
        L3-DASH-025).
        """
        del _user_id
        html = service.report_store.read_email_body(RunId(run_id))
        if html is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="report not found",
            )
        return HTMLResponse(
            content=html,
            media_type="text/html; charset=utf-8",
        )

    @router.get(
        "/{run_id}/stages/{stage_id}/fragment",
        response_class=HTMLResponse,
    )
    async def get_fragment(
        run_id: Annotated[str, Path(pattern=_UUID4_PATTERN)],
        stage_id: Annotated[str, Path(min_length=1, max_length=128)],
        _user_id: int = Depends(require_session),
    ) -> HTMLResponse:
        """L3-DASH-030: saved per-stage rendered fragment.

        Reads via :meth:`ReportStore.read_fragment`; ``None`` is
        translated to HTTP 404 with the same uniform privacy
        semantics as :func:`get_report`.
        """
        del _user_id
        html = service.report_store.read_fragment(RunId(run_id), StageId(stage_id))
        if html is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="fragment not found",
            )
        return HTMLResponse(
            content=html,
            media_type="text/html; charset=utf-8",
        )

    @router.post("/{run_id}/resend", status_code=status.HTTP_202_ACCEPTED)
    async def resend_run(
        run_id: Annotated[str, Path(pattern=_UUID4_PATTERN)],
        user_id: int = Depends(require_session),
    ) -> dict[str, str]:
        """L1-DASH-003 / L3-DASH-012/013/027/028: manual resend.

        State preconditions per L3-DASH-028: SENT or FAILED only;
        any other state returns HTTP 409. Non-existent runs return
        HTTP 404. CSRF is enforced by the existing middleware.
        """
        try:
            await service.resend_run.execute(
                run_id=RunId(run_id),
                admin_user_id=user_id,
            )
        except RunNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="run not found",
            ) from exc
        except InvalidRunStateError as exc:
            current = exc.details.get("current_state", "unknown")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"run is in state {current}; resend requires SENT or FAILED",
            ) from exc
        return {"status": "ok"}

    return router


__all__ = [
    "RunDetailResponse",
    "RunSummaryResponse",
    "StageInDetailResponse",
    "build_runs_router",
]
