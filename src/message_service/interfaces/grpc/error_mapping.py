"""gRPC error translation — maps domain exceptions to gRPC status codes.

The servicer catches every exception that escapes a use case and converts it
to an appropriate gRPC status response. This module is the single source of
truth for that mapping, so that the servicer itself stays thin.

Design notes
------------
* Validation errors → ``INVALID_ARGUMENT`` with ``ErrorCode`` in details.
* Not-found errors → ``NOT_FOUND``.
* Precondition errors → ``FAILED_PRECONDITION``.
* Infrastructure errors and unexpected exceptions → ``INTERNAL`` with a
  correlation id. The full exception (including stack trace) is logged at
  ERROR level; only the correlation id and a generic message reach the client.
  This is the L2-API-010 contract: no stack traces leave the server.

Requirement references
----------------------
* L2-API-008, L2-API-009, L2-API-010, L2-API-011
"""

from __future__ import annotations

import uuid

import grpc
import structlog

from message_service.domain.errors import (
    MessageServiceError,
    NotFoundError,
    PreconditionError,
    ValidationError,
)
from message_service.observability.logging_setup import redact_sensitive_keys

logger = structlog.get_logger(__name__)


def _status_code_for(exc: MessageServiceError) -> grpc.StatusCode:
    """Map a domain exception to the gRPC status code category."""
    if isinstance(exc, ValidationError):
        return grpc.StatusCode.INVALID_ARGUMENT
    if isinstance(exc, NotFoundError):
        return grpc.StatusCode.NOT_FOUND
    if isinstance(exc, PreconditionError):
        return grpc.StatusCode.FAILED_PRECONDITION
    # InfrastructureError and any MessageServiceError not matching above
    return grpc.StatusCode.INTERNAL


async def translate_to_grpc_status(
    context: grpc.aio.ServicerContext,
    exc: BaseException,
) -> None:
    """Translate an exception into a gRPC status response.

    Call this from the servicer's except block::

        try:
            return await use_case.execute(cmd)
        except BaseException as exc:  # noqa: BLE001 — boundary translation
            await translate_to_grpc_status(context, exc)
            return None  # unreachable; context.abort raises

    After calling this function, the RPC has been aborted — no response
    message SHALL be returned.

    Args:
        context: The servicer context for the in-flight RPC.
        exc: The exception to translate.
    """
    if isinstance(exc, MessageServiceError):
        await _translate_known(context, exc)
    else:
        await _translate_unexpected(context, exc)


async def _translate_known(
    context: grpc.aio.ServicerContext,
    exc: MessageServiceError,
) -> None:
    """Translate an expected MessageServiceError.

    The boundary log level comes from the exception's class-level
    ``log_level`` ClassVar (added in Step 2 of Increment 22). The
    exception's ``details`` dict is run through
    :func:`redact_sensitive_keys` (per `L3-ERR-016`) before being
    logged or — if the wire-format upgrade in `R-ERR-001` ever
    lands — flowed into trailing metadata. The ``details`` dict is
    NOT currently serialized to the response (only ``error_code``
    is, in trailing metadata) so the redacted copy is currently only
    used in the log record; pre-redacting still matters because the
    structlog processor only redacts top-level event keys, not
    values nested inside a ``details=`` argument.
    """
    status = _status_code_for(exc)
    safe_details = redact_sensitive_keys(exc.details)

    logger.log(
        exc.log_level,
        "request_rejected",
        error_code=exc.error_code,
        message=exc.message,
        details=safe_details,
        grpc_status=status.name,
    )

    # Trailing metadata: machine-readable error code only. Per
    # `L3-ERR-015` (reworded in Step 1 of 22), v1 keeps the simpler
    # context.abort + trailing-metadata shape; the richer
    # google.rpc.Status + ErrorInfo envelope (which would also carry
    # safe_details) is deferred to ROADMAP `R-ERR-001`.
    trailing_metadata: tuple[tuple[str, str], ...] = (
        ("x-message-service-error-code", exc.error_code),
    )
    await context.abort(
        status,
        details=exc.message,
        trailing_metadata=trailing_metadata,
    )


async def _translate_unexpected(
    context: grpc.aio.ServicerContext,
    exc: BaseException,
) -> None:
    """Translate an unexpected exception. Logged at ERROR with stack trace.

    Client receives ``INTERNAL`` with only a correlation id; the stack trace
    never leaves the server (L2-API-010).

    The correlation id reuses the per-RPC id bound by
    :class:`~message_service.interfaces.grpc.correlation_interceptor.CorrelationIdInterceptor`
    when one is present (so the client-facing id matches this RPC's log
    records, L3-API-002); a fresh id is minted only when none is bound — e.g.
    a unit test calling this translator directly, outside the interceptor.
    """
    bound_correlation_id = structlog.contextvars.get_contextvars().get("correlation_id")
    correlation_id = (
        bound_correlation_id if isinstance(bound_correlation_id, str) else uuid.uuid4().hex
    )
    logger.error(
        "unexpected_internal_error",
        correlation_id=correlation_id,
        exc_type=type(exc).__name__,
        exc_info=exc,
    )
    trailing_metadata: tuple[tuple[str, str], ...] = (
        ("x-message-service-error-code", "ERROR_CODE_INTERNAL"),
        ("x-message-service-correlation-id", correlation_id),
    )
    await context.abort(
        grpc.StatusCode.INTERNAL,
        details=f"internal error (correlation id: {correlation_id})",
        trailing_metadata=trailing_metadata,
    )
