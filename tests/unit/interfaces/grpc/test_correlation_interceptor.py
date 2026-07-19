"""Tests for the gRPC per-RPC correlation interceptor (L3-API-002 / L3-OBS-003)."""

from __future__ import annotations

from typing import Any

import grpc
import pytest
import structlog
from structlog.contextvars import clear_contextvars, get_contextvars

from message_service.interfaces.grpc.correlation_interceptor import (
    CORRELATION_ID_FIELD,
    CorrelationIdInterceptor,
)


async def _continuation_returning(
    handler: grpc.RpcMethodHandler,
) -> Any:
    """Build an interceptor continuation that always resolves to ``handler``."""

    async def _continuation(details: object) -> grpc.RpcMethodHandler:
        return handler

    return _continuation


@pytest.mark.asyncio
@pytest.mark.requirement("L3-API-002")
@pytest.mark.requirement("L3-OBS-003")
async def test_interceptor_binds_correlation_id_during_rpc_and_clears_after() -> None:
    """A fresh 32-hex correlation_id is bound during the RPC and cleared after."""
    captured: dict[str, Any] = {}

    async def behavior(request: object, context: object) -> str:
        captured.update(get_contextvars())
        return "response"

    handler = grpc.unary_unary_rpc_method_handler(behavior)
    continuation = await _continuation_returning(handler)

    clear_contextvars()
    wrapped = await CorrelationIdInterceptor().intercept_service(continuation, object())
    assert wrapped is not None

    result = await wrapped.unary_unary("req", object())

    assert result == "response"
    corr = captured.get(CORRELATION_ID_FIELD)
    assert isinstance(corr, str)
    assert len(corr) == 32
    assert all(c in "0123456789abcdef" for c in corr)
    # Cleared in a finally so it cannot leak to the next RPC on this task.
    assert CORRELATION_ID_FIELD not in get_contextvars()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-API-002")
async def test_interceptor_clears_context_even_when_handler_raises() -> None:
    """The request context is cleared in the finally even on an exception."""

    async def raising(request: object, context: object) -> str:
        raise RuntimeError("boom")

    handler = grpc.unary_unary_rpc_method_handler(raising)
    continuation = await _continuation_returning(handler)

    clear_contextvars()
    wrapped = await CorrelationIdInterceptor().intercept_service(continuation, object())
    assert wrapped is not None

    with pytest.raises(RuntimeError, match="boom"):
        await wrapped.unary_unary("req", object())

    assert get_contextvars() == {}


@pytest.mark.asyncio
@pytest.mark.requirement("L3-OBS-003")
async def test_log_record_during_rpc_carries_correlation_id() -> None:
    """A log emitted while handling the RPC carries the bound correlation_id.

    Uses ``structlog.testing.capture_logs`` with an explicit
    ``merge_contextvars`` step to mirror the configured processor chain.
    """
    events: list[Any] = []

    async def behavior(request: object, context: object) -> str:
        merged = structlog.contextvars.merge_contextvars(None, "info", {"event": "handling"})
        events.append(merged)
        return "ok"

    handler = grpc.unary_unary_rpc_method_handler(behavior)
    continuation = await _continuation_returning(handler)

    clear_contextvars()
    wrapped = await CorrelationIdInterceptor().intercept_service(continuation, object())
    assert wrapped is not None
    await wrapped.unary_unary("req", object())

    assert events and CORRELATION_ID_FIELD in events[0]
