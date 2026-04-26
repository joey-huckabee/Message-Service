"""Template registry inspection routes (Increment 20a).

Two read-only routes under ``/templates`` give administrators a view
into the template manifest without granting filesystem access:

* ``GET /templates`` — list every registered template with its
  metadata projection plus the parsed JSON-schema content (L3-DASH-031).
* ``GET /templates/{name}/{version}`` — detail view of a single
  template; same projection as the list element (L3-DASH-032).

Both routes are gated by ``require_admin`` (per L3-DASH-007 / L3-DASH-011 /
L3-DASH-021). Write methods (POST / PATCH / DELETE) on the
``/templates/*`` prefix surface as HTTP 405 per L3-DASH-014;
templates are git-managed (L2-DASH-009 / L3-DASH-014's rationale)
and SHALL NOT be writable through the dashboard.

The Jinja2 source body itself is deliberately excluded from every
response shape — operators can read source files directly from the
filesystem if they need to. Exposing source bodies through an
authenticated API would re-introduce a content-disclosure surface
that L3-DASH-015 explicitly forecloses.

Requirement references
----------------------
L1-DASH-003 (clause 3 — template inspection)
L2-DASH-007 (admin gate), L2-DASH-009 (read-only inspection)
L3-DASH-006, L3-DASH-007, L3-DASH-011, L3-DASH-021 (admin gate)
L3-DASH-014 (HTTP method allow-list)
L3-DASH-015 (response projection)
L3-DASH-031, L3-DASH-032 (route paths + 404 semantics)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict

from message_service.domain.aggregates.template_metadata import (
    TemplateKind,
    TemplateMetadata,
)
from message_service.interfaces.rest.app import require_admin_factory

if TYPE_CHECKING:
    from message_service.bootstrap import Service

_log = structlog.get_logger(__name__)


# -----------------------------------------------------------------------------
# Response model
# -----------------------------------------------------------------------------


class TemplateResponse(BaseModel):
    """L3-DASH-031 / L3-DASH-032 per-template projection."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    kind: TemplateKind
    source_path: str
    context_schema_path: str | None
    context_schema: dict[str, Any] | None
    description: str | None


# -----------------------------------------------------------------------------
# Projection
# -----------------------------------------------------------------------------


def _read_schema(meta: TemplateMetadata) -> dict[str, Any] | None:
    """Load and parse the JSON schema for a template, or ``None``.

    Returns ``None`` when the template has no schema declared, when
    the schema file is missing, or when the file is not parseable
    JSON. Failures log at WARNING and do not propagate — a degraded
    listing is preferred over a 500 that hides the rest of the
    registry from the operator.
    """
    if meta.context_schema_path is None:
        return None
    try:
        text = meta.context_schema_path.read_text(encoding="utf-8")
        parsed = json.loads(text)
    except OSError as exc:
        _log.warning(
            "template_schema_read_failed",
            name=meta.name,
            version=meta.version,
            path=str(meta.context_schema_path),
            error=str(exc),
        )
        return None
    except json.JSONDecodeError as exc:
        _log.warning(
            "template_schema_parse_failed",
            name=meta.name,
            version=meta.version,
            path=str(meta.context_schema_path),
            error=str(exc),
        )
        return None
    if not isinstance(parsed, dict):
        # JSON Schema documents are always objects at the root; if the
        # file parses to a list or scalar it isn't a valid schema and
        # we surface ``None`` rather than passing it through.
        _log.warning(
            "template_schema_not_object",
            name=meta.name,
            version=meta.version,
            path=str(meta.context_schema_path),
        )
        return None
    return parsed


def _project_template(meta: TemplateMetadata) -> TemplateResponse:
    return TemplateResponse(
        name=meta.name,
        version=meta.version,
        kind=meta.kind,
        source_path=str(meta.source_path),
        context_schema_path=(str(meta.context_schema_path) if meta.context_schema_path else None),
        context_schema=_read_schema(meta),
        description=meta.description,
    )


# -----------------------------------------------------------------------------
# Router factory
# -----------------------------------------------------------------------------


def build_templates_router(service: Service) -> APIRouter:
    """Construct the templates inspection router bound to ``service``.

    Mounts ``GET /templates`` and ``GET /templates/{name}/{version}``.
    POST / PATCH / DELETE on either path return 405 by virtue of
    FastAPI's default method handling — the L3-DASH-014 obligation
    is structural rather than requiring an explicit handler.

    Args:
        service: The constructed ``Service`` whose ``template_repo``
            the routes close over.

    Returns:
        An :class:`APIRouter` mounted under ``/templates``.
    """
    router = APIRouter(prefix="/templates", tags=["templates"])
    require_admin = require_admin_factory(service)

    @router.get("")
    async def list_templates(
        _admin_id: int = Depends(require_admin),
    ) -> list[TemplateResponse]:
        """L3-DASH-031: list every registered template.

        Response is ordered by ``(name, version)`` ascending per the
        adapter's ``list_all`` contract. Each element carries the full
        metadata projection — including parsed JSON-schema content
        when a `context_schema_path` is declared and the file is
        readable. The Jinja2 source body itself is NOT exposed.
        """
        del _admin_id
        return [_project_template(m) for m in service.template_repo.list_all()]

    @router.get("/{name}/{version}")
    async def get_template(
        name: Annotated[str, Path(min_length=1, max_length=128)],
        version: Annotated[str, Path(min_length=1, max_length=64)],
        _admin_id: int = Depends(require_admin),
    ) -> TemplateResponse:
        """L3-DASH-032: detail view of a single registered template.

        Looks up the template by its full ``(name, version)`` key.
        Unknown pairs SHALL return HTTP 404 with a generic detail
        string — no information disclosure about whether ``name``,
        ``version``, or both were unmatched.
        """
        del _admin_id
        for m in service.template_repo.list_all():
            if m.name == name and m.version == version:
                return _project_template(m)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="template not found",
        )

    return router


__all__ = ["TemplateResponse", "build_templates_router"]
