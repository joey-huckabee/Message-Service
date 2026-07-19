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

import json
import uuid
from collections.abc import Mapping
from typing import Any

import grpc
import structlog
from google.protobuf import any_pb2
from google.rpc import error_details_pb2, status_pb2

from message_service.domain.errors import (
    MessageServiceError,
    NotFoundError,
    PreconditionError,
    ValidationError,
)
from message_service.observability.logging_setup import redact_sensitive_keys

logger = structlog.get_logger(__name__)

# google.rpc.ErrorInfo.domain for every error this service emits (L3-ERR-023).
_ERROR_INFO_DOMAIN = "message-service"
# Standard trailing-metadata key gRPC uses to carry a serialized google.rpc.Status.
_STATUS_DETAILS_KEY = "grpc-status-details-bin"


def _stringify(details: Mapping[str, Any]) -> dict[str, str]:
    """Coerce a details dict to the ``map<string, string>`` ErrorInfo.metadata shape."""
    return {
        key: value if isinstance(value, str) else json.dumps(value, default=str)
        for key, value in details.items()
    }


def _status_details_bin(
    *,
    grpc_code: grpc.StatusCode,
    message: str,
    error_code: str,
    details: Mapping[str, Any],
) -> bytes:
    """Serialize a google.rpc.Status + ErrorInfo for grpc-status-details-bin (L3-ERR-023).

    Additive to the trailing-metadata shape: a client reading only
    ``x-message-service-error-code`` is unaffected; a client using
    ``grpc_status.from_call`` receives this structured envelope.
    """
    info = error_details_pb2.ErrorInfo(
        reason=error_code,
        domain=_ERROR_INFO_DOMAIN,
        metadata=_stringify(details),
    )
    any_detail = any_pb2.Any()
    any_detail.Pack(info)
    status_proto = status_pb2.Status(
        code=grpc_code.value[0],
        message=message,
        details=[any_detail],
    )
    return bytes(status_proto.SerializeToString())


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

    # Trailing metadata: the legacy machine-readable error code (retained for
    # backward compatibility, L3-ERR-015) plus the additive google.rpc.Status +
    # ErrorInfo envelope in grpc-status-details-bin (L3-ERR-023).
    trailing_metadata: tuple[tuple[str, str | bytes], ...] = (
        ("x-message-service-error-code", exc.error_code),
        (
            _STATUS_DETAILS_KEY,
            _status_details_bin(
                grpc_code=status,
                message=exc.message,
                error_code=exc.error_code,
                details=safe_details,
            ),
        ),
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
    public_message = f"internal error (correlation id: {correlation_id})"
    trailing_metadata: tuple[tuple[str, str | bytes], ...] = (
        ("x-message-service-error-code", "ERROR_CODE_INTERNAL"),
        ("x-message-service-correlation-id", correlation_id),
        (
            _STATUS_DETAILS_KEY,
            _status_details_bin(
                grpc_code=grpc.StatusCode.INTERNAL,
                message=public_message,
                error_code="ERROR_CODE_INTERNAL",
                details={"correlation_id": correlation_id},
            ),
        ),
    )
    await context.abort(
        grpc.StatusCode.INTERNAL,
        details=public_message,
        trailing_metadata=trailing_metadata,
    )
