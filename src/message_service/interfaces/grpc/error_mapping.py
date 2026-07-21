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
from pydantic import ValidationError as PydanticValidationError

from message_service.domain.errors import (
    MalformedRequestError,
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

# Size bounds on the ErrorInfo.metadata packed into grpc-status-details-bin
# (L3-ERR-024). Some `details` values are influenced by client input (e.g. an
# oversized stage_id, template name, or a long list of invalid tags/validation
# errors). Without a bound the serialized google.rpc.Status can exceed gRPC's
# default ~8 KiB trailing-metadata limit, which makes the whole abort fail and
# LOSES the structured error the client needed. We cap each value and the total,
# leaving generous headroom under 8 KiB for the message + framing overhead.
_MAX_METADATA_VALUE_BYTES = 1024
_MAX_METADATA_TOTAL_BYTES = 4096
_TRUNCATION_MARKER = "…[truncated]"

# The gRPC status ``message`` (and the ``context.abort(details=…)`` string) is
# also client-influenced — an exception message can embed caller input (e.g.
# ``UnknownTagError`` interpolates the full submitted tag list). It rides in the
# serialized ``google.rpc.Status`` and in ``grpc-message``, so it too must be
# bounded or a large message would blow gRPC's ~8 KiB trailing-metadata limit and
# lose the whole aborted status — the same failure the metadata bounding prevents.
_MAX_MESSAGE_BYTES = 2048


def _truncate_message(message: str) -> str:
    """Bound a client-facing status message to ``_MAX_MESSAGE_BYTES`` (L3-ERR-024)."""
    encoded = message.encode("utf-8")
    if len(encoded) <= _MAX_MESSAGE_BYTES:
        return message
    return encoded[:_MAX_MESSAGE_BYTES].decode("utf-8", errors="ignore") + _TRUNCATION_MARKER


def _stringify(details: Mapping[str, Any]) -> dict[str, str]:
    """Coerce a details dict to a size-bounded ``map<string, string>`` (L3-ERR-024).

    Each value is stringified, then truncated to ``_MAX_METADATA_VALUE_BYTES``;
    once the running total reaches ``_MAX_METADATA_TOTAL_BYTES`` the remaining
    fields are dropped. Any truncation or drop adds a ``_truncated`` marker so a
    client can tell the metadata is not complete. Keeping the packed
    ``google.rpc.Status`` small guarantees the abort's trailing metadata fits
    under gRPC's limit and the structured error actually reaches the client.
    """
    result: dict[str, str] = {}
    total = 0
    truncated = False
    # Deterministic order so truncation drops the same fields across replays.
    for key in sorted(details):
        value = details[key]
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        encoded = text.encode("utf-8")
        if len(encoded) > _MAX_METADATA_VALUE_BYTES:
            text = encoded[:_MAX_METADATA_VALUE_BYTES].decode("utf-8", errors="ignore")
            text += _TRUNCATION_MARKER
            truncated = True
        field_bytes = len(text.encode("utf-8"))
        if total + field_bytes > _MAX_METADATA_TOTAL_BYTES:
            truncated = True
            continue  # drop this (and, effectively, larger later) field
        result[key] = text
        total += field_bytes
    if truncated:
        result["_truncated"] = "true"
    return result


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
        message=_truncate_message(message),
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

    Raises:
        BaseException: Re-raises any ``BaseException`` that is not an
            ``Exception`` (``asyncio.CancelledError``, ``KeyboardInterrupt``,
            ``SystemExit``) rather than translating it, so cooperative
            cancellation and interpreter shutdown propagate unchanged.
    """
    # Control-flow exceptions (cancellation, shutdown) must never be turned into
    # a gRPC status — re-raise so grpc.aio's cancellation machinery sees them.
    if not isinstance(exc, Exception):
        raise exc
    if isinstance(exc, MessageServiceError):
        await _translate_known(context, exc)
    elif isinstance(exc, PydanticValidationError):
        # A request-adaptation validation failure is caller-supplied bad input,
        # not a server fault: translate it to INVALID_ARGUMENT (not INTERNAL).
        await _translate_known(context, _malformed_from_pydantic(exc))
    else:
        await _translate_unexpected(context, exc)


def _malformed_from_pydantic(exc: PydanticValidationError) -> MalformedRequestError:
    """Build a :class:`MalformedRequestError` from a pydantic validation failure.

    The per-error ``loc`` (field path) and ``type`` (rule name) are surfaced so
    the client can locate the problem; the offending input value is deliberately
    NOT included, so no caller-supplied data is echoed back in the error.
    """
    problems = [
        {"field": ".".join(str(p) for p in err["loc"]), "error": err["type"]}
        for err in exc.errors()
    ]
    return MalformedRequestError(
        "request validation failed",
        details={"validation_errors": problems},
    )


async def _translate_known(
    context: grpc.aio.ServicerContext,
    exc: MessageServiceError,
) -> None:
    """Translate an expected MessageServiceError.

    The boundary log level comes from the exception's class-level
    ``log_level`` ClassVar (added in Step 2 of Increment 22). The
    exception's ``details`` dict is run through
    :func:`redact_sensitive_keys` (per `L3-ERR-016`) before being both
    logged AND flowed into the ``grpc-status-details-bin`` envelope
    (the `R-ERR-001` wire upgrade landed — see `L3-ERR-023`). Pre-redacting
    matters because the structlog processor only redacts top-level event
    keys, not values nested inside a ``details=`` argument, and the same
    redacted copy is what reaches the client. The envelope's metadata is
    additionally size-bounded (`L3-ERR-024`) so a client-influenced field
    cannot overflow gRPC's trailing-metadata limit and lose the error.
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
        details=_truncate_message(exc.message),
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
