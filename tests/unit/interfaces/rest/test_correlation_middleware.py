"""Tests for the FastAPI per-request correlation middleware (L3-OBS-004)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse
from structlog.contextvars import clear_contextvars, get_contextvars

from message_service.interfaces.rest.app import CorrelationIdMiddleware


@pytest.mark.requirement("L3-OBS-004")
def test_middleware_binds_fresh_correlation_id_per_request() -> None:
    """Each request sees a fresh 32-hex correlation_id bound in its context."""
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/probe")
    def probe() -> dict[str, Any]:
        return {"correlation_id": get_contextvars().get("correlation_id")}

    with TestClient(app) as client:
        first = client.get("/probe").json()["correlation_id"]
        second = client.get("/probe").json()["correlation_id"]

    assert isinstance(first, str) and len(first) == 32
    assert all(c in "0123456789abcdef" for c in first)
    assert first != second  # a distinct id per request


@pytest.mark.asyncio
@pytest.mark.requirement("L3-OBS-004")
async def test_middleware_clears_context_after_dispatch() -> None:
    """The request context is cleared in the finally after dispatch."""
    captured: dict[str, Any] = {}

    async def call_next(request: object) -> PlainTextResponse:
        captured.update(get_contextvars())
        return PlainTextResponse("ok")

    async def _asgi(scope: object, receive: object, send: object) -> None:  # unused
        return None

    middleware = CorrelationIdMiddleware(_asgi)
    clear_contextvars()

    response = await middleware.dispatch(object(), call_next)  # type: ignore[arg-type]

    assert response.status_code == 200
    assert "correlation_id" in captured  # bound during dispatch
    assert get_contextvars() == {}  # cleared afterward
