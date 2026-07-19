"""gRPC server interceptor that binds a per-RPC correlation id (L3-API-002 / L3-OBS-003).

Every inbound RPC — success or failure — gets a fresh ``correlation_id`` bound
into the structlog contextvars at entry, so every log record emitted while the
RPC is handled carries the id automatically (via
``structlog.contextvars.merge_contextvars``, already in the processor chain).
The context is cleared in a ``finally`` so the id cannot leak to a later RPC
handled on the same worker task.

The unexpected-error translator (``error_mapping._translate_unexpected``) reads
the same bound id for its ``x-message-service-correlation-id`` trailing metadata,
so a failed RPC surfaces to the client the exact id its server-side logs carry.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import grpc

from message_service.observability.logging_setup import (
    bind_request_context,
    clear_request_context,
)

CORRELATION_ID_FIELD = "correlation_id"
"""structlog contextvars key under which the per-RPC id is bound."""


class CorrelationIdInterceptor(grpc.aio.ServerInterceptor):  # type: ignore[misc]
    """Binds a fresh ``correlation_id`` into the log context for each RPC."""

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler | None]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler | None:
        """Wrap the resolved handler so it runs inside a bound log context.

        Args:
            continuation: Resolves the next handler in the chain.
            handler_call_details: Metadata for the incoming call.

        Returns:
            The handler, wrapped to bind/clear the correlation id. Non-unary
            handlers (none exist in this service) and unresolved handlers are
            returned unchanged.
        """
        handler = await continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler

        inner = handler.unary_unary

        async def _wrapped(request: object, context: grpc.aio.ServicerContext) -> object:
            bind_request_context(**{CORRELATION_ID_FIELD: uuid.uuid4().hex})
            try:
                return await inner(request, context)
            finally:
                # Clear the whole request context (correlation id + any
                # per-handler fields such as run_id) at the RPC boundary so
                # nothing leaks to the next RPC on this task.
                clear_request_context()

        return grpc.aio.unary_unary_rpc_method_handler(
            _wrapped,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


__all__ = ["CORRELATION_ID_FIELD", "CorrelationIdInterceptor"]
