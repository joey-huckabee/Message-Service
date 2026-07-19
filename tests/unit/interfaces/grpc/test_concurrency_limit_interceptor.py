"""Unit tests for the rejecting concurrency-limit interceptor (L3-API-020).

The interceptor is exercised without a real gRPC server: a fake continuation
supplies a controllable inner handler and a fake servicer context captures the
``abort`` call. Concurrency is simulated by holding inner handlers open on an
``asyncio.Event`` while extra calls attempt to enter.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import grpc
import pytest

from message_service.interfaces.grpc.concurrency_limit_interceptor import (
    ConcurrencyLimitInterceptor,
)


class _AbortRaisedError(Exception):
    """Sentinel raised by the fake context's ``abort`` to mimic real gRPC."""


@dataclass
class _AbortCall:
    code: grpc.StatusCode
    details: str
    trailing_metadata: tuple[tuple[str, Any], ...]


@dataclass
class _FakeServicerContext:
    aborts: list[_AbortCall] = field(default_factory=list)

    async def abort(
        self,
        code: grpc.StatusCode,
        *,
        details: str = "",
        trailing_metadata: tuple[tuple[str, Any], ...] = (),
    ) -> None:
        self.aborts.append(
            _AbortCall(code=code, details=details, trailing_metadata=trailing_metadata)
        )
        raise _AbortRaisedError()


async def _wrapped_handler(
    interceptor: ConcurrencyLimitInterceptor,
    inner: Any,
) -> Any:
    """Run ``inner`` through the interceptor and return the wrapped callable."""

    async def continuation(_details: object) -> grpc.RpcMethodHandler:
        return grpc.unary_unary_rpc_method_handler(inner)

    handler = await interceptor.intercept_service(continuation, object())
    assert handler is not None
    return handler.unary_unary


def _parse_reason(trailing_metadata: tuple[tuple[str, Any], ...]) -> tuple[str, dict[str, str]]:
    """Extract (ErrorInfo.reason, metadata) from grpc-status-details-bin."""
    from google.rpc import error_details_pb2, status_pb2

    blob = dict(trailing_metadata)["grpc-status-details-bin"]
    assert isinstance(blob, bytes)
    status = status_pb2.Status.FromString(blob)
    info = error_details_pb2.ErrorInfo()
    status.details[0].Unpack(info)
    return info.reason, dict(info.metadata)


@pytest.mark.asyncio
@pytest.mark.requirement("L3-API-020")
async def test_rejects_rpc_over_limit_with_resource_exhausted() -> None:
    """The (limit+1)-th concurrent RPC is rejected with RESOURCE_EXHAUSTED."""
    interceptor = ConcurrencyLimitInterceptor(limit=2)
    release = asyncio.Event()

    async def inner(_request: object, _context: object) -> str:
        await release.wait()
        return "ok"

    wrapped = await _wrapped_handler(interceptor, inner)

    # Fill the two slots and hold them open.
    held = [
        asyncio.create_task(wrapped(object(), _FakeServicerContext())),
        asyncio.create_task(wrapped(object(), _FakeServicerContext())),
    ]
    # The increment happens synchronously before the first await inside the
    # wrapper, so once both tasks have reached ``release.wait()`` the counter
    # is at the limit. Poll the counter (white-box) to avoid a fixed sleep.
    while interceptor._in_flight < 2:
        await asyncio.sleep(0)

    # The third concurrent call is rejected without invoking the handler.
    ctx = _FakeServicerContext()
    with pytest.raises(_AbortRaisedError):
        await wrapped(object(), ctx)

    assert ctx.aborts[0].code is grpc.StatusCode.RESOURCE_EXHAUSTED
    reason, metadata = _parse_reason(ctx.aborts[0].trailing_metadata)
    assert reason == "RESOURCE_EXHAUSTED_CONCURRENCY"
    assert metadata["limit"] == "2"
    assert metadata["in_flight"] == "2"
    # A rejection carries NO legacy proto-code key (Option A: no enum bump).
    assert "x-message-service-error-code" not in dict(ctx.aborts[0].trailing_metadata)

    # Draining the held RPCs releases their slots.
    release.set()
    assert await asyncio.gather(*held) == ["ok", "ok"]
    assert interceptor._in_flight == 0


@pytest.mark.asyncio
@pytest.mark.requirement("L3-API-020")
async def test_slot_released_after_completion_allows_next_rpc() -> None:
    """A slot freed by a completed RPC is reusable by a later RPC."""
    interceptor = ConcurrencyLimitInterceptor(limit=1)

    async def inner(_request: object, _context: object) -> str:
        return "ok"

    wrapped = await _wrapped_handler(interceptor, inner)

    assert await wrapped(object(), _FakeServicerContext()) == "ok"
    assert interceptor._in_flight == 0
    # The single slot is free again, so a second sequential call succeeds.
    assert await wrapped(object(), _FakeServicerContext()) == "ok"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-API-020")
async def test_slot_released_even_when_handler_raises() -> None:
    """A handler that raises still releases its slot (decrement in finally)."""
    interceptor = ConcurrencyLimitInterceptor(limit=1)

    async def inner(_request: object, _context: object) -> str:
        raise RuntimeError("boom")

    wrapped = await _wrapped_handler(interceptor, inner)

    with pytest.raises(RuntimeError):
        await wrapped(object(), _FakeServicerContext())
    assert interceptor._in_flight == 0


@pytest.mark.requirement("L3-API-020")
def test_non_positive_limit_rejected() -> None:
    """A non-positive limit is a construction error (disabled == not installed)."""
    with pytest.raises(ValueError, match="must be positive"):
        ConcurrencyLimitInterceptor(limit=0)
