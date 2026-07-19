"""gRPC server interceptor that rejects RPCs over a concurrency limit (L3-API-020).

``maximum_concurrent_rpcs`` on the server only *queues* excess work; under a
burst the queue grows without a fast-fail signal. This interceptor is the
*rejecting* limit (L1-API-005 / L2-API-012): it maintains an in-flight counter
and, once the configured ``limit`` is reached, aborts further RPCs with
``RESOURCE_EXHAUSTED`` before the handler runs, so a saturated server sheds load
instead of degrading silently.

The saturation cause is not one of the proto ``ErrorCode`` enum values, so it is
conveyed additively through the R-ERR-001 ``grpc-status-details-bin`` envelope
(``L3-ERR-023``): an ``ErrorInfo`` with ``reason = "RESOURCE_EXHAUSTED_CONCURRENCY"``
and ``metadata = {limit, in_flight}``. No ``x-message-service-error-code`` key is
emitted for it — clients read the standard ``RESOURCE_EXHAUSTED`` status code to
back off, and operators read the ``reason`` for the specific cause.

This interceptor SHALL be ordered *after* the correlation-id interceptor so a
rejection log record carries the RPC's ``correlation_id``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import grpc
import structlog

from message_service.interfaces.grpc.error_mapping import _STATUS_DETAILS_KEY, _status_details_bin

logger = structlog.get_logger(__name__)

# google.rpc.ErrorInfo.reason for a concurrency-saturation rejection (L3-API-020).
# Deliberately NOT a proto ErrorCode enum value — see module docstring.
_CONCURRENCY_REASON = "RESOURCE_EXHAUSTED_CONCURRENCY"


class ConcurrencyLimitInterceptor(grpc.aio.ServerInterceptor):  # type: ignore[misc]
    """Rejects RPCs that would exceed a global in-flight concurrency limit."""

    def __init__(self, limit: int) -> None:
        """Initialize the interceptor.

        Args:
            limit: The maximum number of concurrently-executing RPCs. Must be
                positive; a disabled limit is expressed by not installing this
                interceptor at all (``grpc.max_in_flight_rpcs == 0``).

        Raises:
            ValueError: If ``limit`` is not positive.
        """
        if limit < 1:
            raise ValueError(f"concurrency limit must be positive, got {limit}")
        self._limit = limit
        self._in_flight = 0

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler | None]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler | None:
        """Wrap the resolved handler so it counts against the in-flight limit.

        Args:
            continuation: Resolves the next handler in the chain.
            handler_call_details: Metadata for the incoming call.

        Returns:
            The handler, wrapped to enforce the limit. Non-unary or unresolved
            handlers are returned unchanged.
        """
        handler = await continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler

        inner = handler.unary_unary

        async def _wrapped(request: object, context: grpc.aio.ServicerContext) -> object:
            # Check-and-increment is atomic w.r.t. the event loop: there is no
            # ``await`` between the comparison and the increment, so two RPCs
            # cannot both observe an under-limit counter and both proceed.
            if self._in_flight >= self._limit:
                await self._reject(context)  # aborts (raises); never returns
            self._in_flight += 1
            try:
                return await inner(request, context)
            finally:
                self._in_flight -= 1

        return grpc.unary_unary_rpc_method_handler(
            _wrapped,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )

    async def _reject(self, context: grpc.aio.ServicerContext) -> None:
        """Abort the RPC with RESOURCE_EXHAUSTED + the R-ERR-001 rich envelope.

        Args:
            context: The servicer context for the RPC being rejected.
        """
        details = {"limit": self._limit, "in_flight": self._in_flight}
        message = (
            f"concurrency limit reached ({self._in_flight}/{self._limit} in-flight); retry later"
        )
        logger.warning(
            "rpc_rejected_concurrency_limit",
            limit=self._limit,
            in_flight=self._in_flight,
        )
        trailing_metadata: tuple[tuple[str, str | bytes], ...] = (
            (
                _STATUS_DETAILS_KEY,
                _status_details_bin(
                    grpc_code=grpc.StatusCode.RESOURCE_EXHAUSTED,
                    message=message,
                    error_code=_CONCURRENCY_REASON,
                    details=details,
                ),
            ),
        )
        await context.abort(
            grpc.StatusCode.RESOURCE_EXHAUSTED,
            details=message,
            trailing_metadata=trailing_metadata,
        )


__all__ = ["ConcurrencyLimitInterceptor"]
