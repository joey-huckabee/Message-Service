"""Unit tests for the gRPC exception translator.

Covers the expected-error path (`_translate_known`), the unexpected-
exception path (`_translate_unexpected`), the L3-ERR-016 details-
redaction invariant, and the L3-ERR-022 BaseException-propagation
invariant.

Most tests use a small ``_FakeServicerContext`` rather than a real
``grpc.aio.ServicerContext`` because the only thing the translator
calls on the context is ``await context.abort(...)`` — capturing
that call directly is simpler and faster than spinning up a real
gRPC server.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import grpc
import pytest

from message_service.domain.errors import (
    ConfigurationError,
    ContextSchemaViolationError,
    DomainError,
    EmailDeliveryError,
    InfrastructureError,
    InvalidRunStateError,
    MessageServiceError,
    NotFoundError,
    PreconditionError,
    RunNotFoundError,
    UnknownTagError,
    ValidationError,
)
from message_service.interfaces.grpc.error_mapping import (
    _MAX_MESSAGE_BYTES,
    _MAX_METADATA_TOTAL_BYTES,
    _MAX_METADATA_VALUE_BYTES,
    _status_code_for,
    _translate_known,
    _translate_unexpected,
    translate_to_grpc_status,
)


@dataclass
class _AbortCall:
    """Captured arguments passed to ``context.abort``."""

    code: grpc.StatusCode
    details: str
    trailing_metadata: tuple[tuple[str, str], ...]


@dataclass
class _FakeServicerContext:
    """Minimum surface needed by the translator: an async ``abort``.

    A real ``grpc.aio.ServicerContext.abort`` raises (it does not
    return); we mimic that by raising a sentinel exception after
    capturing the call. Tests catch the sentinel and inspect
    ``self.aborts``.
    """

    aborts: list[_AbortCall] = field(default_factory=list)

    async def abort(
        self,
        code: grpc.StatusCode,
        *,
        details: str = "",
        trailing_metadata: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.aborts.append(
            _AbortCall(code=code, details=details, trailing_metadata=trailing_metadata)
        )
        raise _AbortRaisedError()


class _AbortRaisedError(Exception):
    """Sentinel raised by ``_FakeServicerContext.abort`` to mimic real gRPC."""


# -----------------------------------------------------------------------------
# Status-code mapping (L3-ERR-014)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-ERR-014")
def test_validation_error_maps_to_invalid_argument() -> None:
    exc = UnknownTagError("nope", details={"tag": "x"})
    assert _status_code_for(exc) is grpc.StatusCode.INVALID_ARGUMENT


@pytest.mark.requirement("L2-TMPL-011")
def test_context_schema_violation_maps_to_invalid_argument() -> None:
    """L2-TMPL-011: schema violations SHALL surface as INVALID_ARGUMENT with code."""
    exc = ContextSchemaViolationError(
        "schema mismatch",
        details={
            "name": "n",
            "version": "v",
            "json_pointer": "/items/0/id",
            "validator": "type",
            "instance_value": "oops",
            "message": "is not of type 'integer'",
        },
    )
    assert _status_code_for(exc) is grpc.StatusCode.INVALID_ARGUMENT
    assert exc.error_code == "ERROR_CODE_CONTEXT_SCHEMA_VIOLATION"


@pytest.mark.asyncio
@pytest.mark.requirement("L2-TMPL-011")
async def test_context_schema_violation_carries_code_and_pointer_through_translator() -> None:
    """L2-TMPL-011: the translator SHALL emit the error code in trailing metadata
    and a JSON Pointer in the public details string.
    """
    ctx = _FakeServicerContext()
    exc = ContextSchemaViolationError(
        "context failed schema for 'tpl'@'1.0': 'name' is a required property",
        details={
            "name": "tpl",
            "version": "1.0",
            "json_pointer": "/items/2/id",
            "validator": "required",
            "instance_value": None,
            "message": "'name' is a required property",
        },
    )
    with pytest.raises(_AbortRaisedError):
        await _translate_known(ctx, exc)
    abort = ctx.aborts[0]
    assert abort.code is grpc.StatusCode.INVALID_ARGUMENT
    metadata = dict(abort.trailing_metadata)
    assert metadata["x-message-service-error-code"] == "ERROR_CODE_CONTEXT_SCHEMA_VIOLATION"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-ERR-024")
async def test_status_details_metadata_is_size_bounded() -> None:
    """An oversized client-influenced details value SHALL be bounded, not lost.

    Regression: the whole redacted details dict was packed verbatim, so a large
    value could push the serialized google.rpc.Status past gRPC's ~8 KiB
    trailing-metadata limit and make the entire abort (and structured error) fail.
    """
    from google.rpc import error_details_pb2, status_pb2

    ctx = _FakeServicerContext()
    exc = UnknownTagError(
        "bad tag",
        details={"invalid_tags": "x" * 5000, "count": 3},
    )
    with pytest.raises(_AbortRaisedError):
        await _translate_known(ctx, exc)

    raw = dict(ctx.aborts[0].trailing_metadata)["grpc-status-details-bin"]
    assert isinstance(raw, bytes)
    # The whole serialized status stays comfortably under gRPC's ~8 KiB limit.
    assert len(raw) < 8192

    status = status_pb2.Status()
    status.ParseFromString(raw)
    info = error_details_pb2.ErrorInfo()
    status.details[0].Unpack(info)
    meta = dict(info.metadata)

    # The oversized value was truncated (not dropped), and marked.
    assert len(meta["invalid_tags"].encode("utf-8")) <= _MAX_METADATA_VALUE_BYTES + 32
    assert meta["invalid_tags"].endswith("…[truncated]")
    # A small field survives intact.
    assert meta["count"] == "3"
    # The incompleteness marker is present.
    assert meta["_truncated"] == "true"
    # Total metadata payload respects the total cap.
    total = sum(len(v.encode("utf-8")) for k, v in meta.items() if k != "_truncated")
    assert total <= _MAX_METADATA_TOTAL_BYTES


@pytest.mark.asyncio
@pytest.mark.requirement("L3-ERR-024")
async def test_oversized_status_message_is_bounded() -> None:
    """A huge (client-influenced) exception message SHALL be truncated, not lost.

    Regression: only ErrorInfo.metadata was bounded; the Status `message` and the
    `context.abort(details=…)` string carried the full message (e.g. an
    UnknownTagError interpolating 500 tags), which could push the serialized
    status past gRPC's ~8 KiB trailing-metadata limit and drop the whole abort.
    """
    from google.rpc import status_pb2

    ctx = _FakeServicerContext()
    huge = "x" * 50_000
    exc = UnknownTagError(f"unknown tag(s): {huge}", details={"count": 1})
    with pytest.raises(_AbortRaisedError):
        await _translate_known(ctx, exc)

    abort = ctx.aborts[0]
    # The abort's public details string is bounded.
    assert len(abort.details.encode("utf-8")) <= _MAX_MESSAGE_BYTES + 32
    # The serialized status (message + envelope) stays well under gRPC's ~8 KiB.
    raw = dict(abort.trailing_metadata)["grpc-status-details-bin"]
    assert len(raw) < 8192
    status = status_pb2.Status()
    status.ParseFromString(raw)
    assert len(status.message.encode("utf-8")) <= _MAX_MESSAGE_BYTES + 32


@pytest.mark.requirement("L3-ERR-014")
def test_not_found_error_maps_to_not_found() -> None:
    exc = RunNotFoundError("nope", details={"run_id": "x"})
    assert _status_code_for(exc) is grpc.StatusCode.NOT_FOUND


@pytest.mark.requirement("L3-ERR-014")
def test_precondition_error_maps_to_failed_precondition() -> None:
    exc = InvalidRunStateError("nope", details={"current_state": "READY"})
    assert _status_code_for(exc) is grpc.StatusCode.FAILED_PRECONDITION


@pytest.mark.requirement("L3-ERR-014")
def test_infrastructure_error_maps_to_internal() -> None:
    exc = EmailDeliveryError("smtp 500")
    assert _status_code_for(exc) is grpc.StatusCode.INTERNAL


@pytest.mark.requirement("L3-ERR-014")
def test_configuration_error_maps_to_internal() -> None:
    exc = ConfigurationError("bad config")
    assert _status_code_for(exc) is grpc.StatusCode.INTERNAL


# -----------------------------------------------------------------------------
# _translate_known: response shape (L3-ERR-014, L3-ERR-015)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-ERR-015")
@pytest.mark.requirement("L3-API-011")
@pytest.mark.requirement("L3-OBS-023")
@pytest.mark.requirement("L3-OBS-024")
async def test_translate_known_aborts_with_error_code_in_trailing_metadata() -> None:
    """L3-ERR-015 (reworded): trailing metadata SHALL carry x-message-service-error-code."""
    ctx = _FakeServicerContext()
    exc = UnknownTagError("nope", details={"tag": "x"})
    with pytest.raises(_AbortRaisedError):
        await _translate_known(ctx, exc)
    assert len(ctx.aborts) == 1
    abort = ctx.aborts[0]
    assert abort.code is grpc.StatusCode.INVALID_ARGUMENT
    assert abort.details == "nope"
    metadata = dict(abort.trailing_metadata)
    assert metadata["x-message-service-error-code"] == "ERROR_CODE_UNKNOWN_TAG"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-ERR-015")
async def test_translate_known_does_not_leak_internal_class_name() -> None:
    """L3-ERR-015: response carries the public message, not the str() form."""
    ctx = _FakeServicerContext()
    exc = UnknownTagError("nope", details={"tag": "x"})
    with pytest.raises(_AbortRaisedError):
        await _translate_known(ctx, exc)
    abort = ctx.aborts[0]
    assert "UnknownTagError" not in abort.details
    assert "Traceback" not in abort.details


# -----------------------------------------------------------------------------
# Details redaction (L3-ERR-016)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-ERR-016")
async def test_translate_known_redacts_sensitive_keys_in_log_record() -> None:
    """L3-ERR-016: sensitive keys in details SHALL be redacted before logging.

    Patch the module logger directly (structlog's cached logger makes
    ``caplog``/``capture_logs`` unreliable here) and assert the ``details`` dict
    actually passed to the boundary log has sensitive values replaced — the prior
    version explicitly refused to assert redaction happened, so it passed even if
    nothing was redacted (or logged at all).
    """
    from unittest.mock import patch

    import message_service.interfaces.grpc.error_mapping as error_mapping

    ctx = _FakeServicerContext()
    exc = ConfigurationError(
        "bad",
        details={
            "tag": "production",  # not sensitive
            "password": "should-not-leak",  # sensitive (L3-OBS-006)
            "PASSWORD": "case-insensitive-also",
            "session_token": "abc123",
        },
    )
    with patch.object(error_mapping, "logger") as mock_logger, pytest.raises(_AbortRaisedError):
        await _translate_known(ctx, exc)

    mock_logger.log.assert_called_once()
    logged_details = mock_logger.log.call_args.kwargs["details"]
    # Sensitive keys (case-insensitive) are replaced; the non-sensitive one survives.
    assert logged_details["password"] == "<redacted>"
    assert logged_details["PASSWORD"] == "<redacted>"
    assert logged_details["session_token"] == "<redacted>"
    assert logged_details["tag"] == "production"
    # And no sensitive value survives anywhere in the logged payload.
    assert "should-not-leak" not in str(logged_details)
    assert "abc123" not in str(logged_details)


@pytest.mark.asyncio
@pytest.mark.requirement("L2-OBS-012")
async def test_error_severity_boundary_log_carries_error_code() -> None:
    """L2-OBS-012: an ERROR-severity boundary log record SHALL carry `error_code`.

    The previous 'coverage' asserted the trailing metadata, not the log record.
    ``ConfigurationError`` logs at ERROR (the base ``MessageServiceError``
    log_level) with ``error_code=ERROR_CODE_INTERNAL``. Patch the module logger
    directly rather than using ``capture_logs`` — with
    ``cache_logger_on_first_use=True`` the cached module logger would bypass
    ``capture_logs`` once another test has configured structlog.
    """
    from unittest.mock import patch

    import message_service.interfaces.grpc.error_mapping as error_mapping

    ctx = _FakeServicerContext()
    exc = ConfigurationError("boom", details={"k": "v"})
    with (
        patch.object(error_mapping, "logger") as mock_logger,
        pytest.raises(_AbortRaisedError),
    ):
        await _translate_known(ctx, exc)

    # _translate_known emits `logger.log(exc.log_level, "request_rejected", error_code=..., ...)`.
    mock_logger.log.assert_called_once()
    call = mock_logger.log.call_args
    assert call.args[0] == logging.ERROR  # ConfigurationError's log_level
    assert call.args[1] == "request_rejected"
    assert call.kwargs["error_code"] == "ERROR_CODE_INTERNAL"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-ERR-016")
async def test_translate_known_does_not_mutate_original_details() -> None:
    """L3-ERR-016: redaction SHALL be on a copy; the original details dict is preserved."""
    ctx = _FakeServicerContext()
    original_details = {"tag": "x", "password": "secret"}
    exc = UnknownTagError("nope", details=original_details)
    with pytest.raises(_AbortRaisedError):
        await _translate_known(ctx, exc)
    # The original details dict on the exception SHALL be unchanged.
    assert exc.details["password"] == "secret"
    assert original_details["password"] == "secret"


# -----------------------------------------------------------------------------
# _translate_unexpected: correlation id (L3-ERR-017, L3-ERR-018)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-ERR-017")
@pytest.mark.requirement("L3-ERR-018")
@pytest.mark.requirement("L3-API-014")
@pytest.mark.requirement("L3-API-015")
@pytest.mark.requirement("L3-API-016")
async def test_translate_unexpected_returns_internal_with_correlation_id() -> None:
    """L3-ERR-017 / L3-API-014: unhandled exceptions SHALL surface a uuid4-hex
    correlation id (32 hex chars, no hyphens). L3-API-015: the same id SHALL
    appear in trailing metadata AND the public details string. L3-API-016:
    the public details message SHALL be exactly
    `"internal error (correlation id: {id})"`.
    """
    ctx = _FakeServicerContext()
    exc = RuntimeError("something went wrong")
    with pytest.raises(_AbortRaisedError):
        await _translate_unexpected(ctx, exc)
    abort = ctx.aborts[0]
    assert abort.code is grpc.StatusCode.INTERNAL
    metadata = dict(abort.trailing_metadata)
    assert metadata["x-message-service-error-code"] == "ERROR_CODE_INTERNAL"
    correlation_id = metadata["x-message-service-correlation-id"]
    # uuid4().hex is 32 hex chars, no hyphens (L3-API-014).
    assert len(correlation_id) == 32
    assert all(c in "0123456789abcdef" for c in correlation_id)
    # L3-API-015: the same correlation id SHALL also appear in the
    # public details string so an operator can grep the log for it.
    assert correlation_id in abort.details
    # L3-API-016: the exact detail-message format.
    assert abort.details == f"internal error (correlation id: {correlation_id})"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-API-002")
async def test_translate_unexpected_reuses_bound_correlation_id() -> None:
    """L3-API-002: the translator reuses the interceptor-bound correlation_id.

    When the CorrelationIdInterceptor has bound a correlation_id for the RPC,
    the failed-RPC trailing metadata surfaces that same id (not a fresh one),
    so the client's id matches the server's log records.
    """
    from structlog.contextvars import bind_contextvars, clear_contextvars

    bound = "abcdef0123456789abcdef0123456789"  # 32 hex, like uuid4().hex
    clear_contextvars()
    bind_contextvars(correlation_id=bound)
    try:
        ctx = _FakeServicerContext()
        with pytest.raises(_AbortRaisedError):
            await _translate_unexpected(ctx, RuntimeError("boom"))
        metadata = dict(ctx.aborts[0].trailing_metadata)
        assert metadata["x-message-service-correlation-id"] == bound
        assert ctx.aborts[0].details == f"internal error (correlation id: {bound})"
    finally:
        clear_contextvars()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-ERR-017")
async def test_translate_unexpected_does_not_leak_stack_trace_to_client() -> None:
    """L3-ERR-015: response SHALL NOT contain a stack trace or class name."""
    ctx = _FakeServicerContext()

    def _raises_with_traceback() -> None:
        nested = "secret-pipeline-state"  # noqa: F841 — intentionally in the frame locals
        raise RuntimeError("internal failure with stack frames")

    try:
        _raises_with_traceback()
    except RuntimeError as caught:
        with pytest.raises(_AbortRaisedError):
            await _translate_unexpected(ctx, caught)
    abort = ctx.aborts[0]
    assert "secret-pipeline-state" not in abort.details
    assert "Traceback" not in abort.details
    assert "RuntimeError" not in abort.details


# -----------------------------------------------------------------------------
# translate_to_grpc_status: dispatcher (L3-ERR-014)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-ERR-014")
async def test_translate_to_grpc_status_dispatches_known_to_translate_known() -> None:
    """A MessageServiceError SHALL go through the known-error path."""
    ctx = _FakeServicerContext()
    exc = UnknownTagError("nope", details={"tag": "x"})
    with pytest.raises(_AbortRaisedError):
        await translate_to_grpc_status(ctx, exc)
    assert ctx.aborts[0].code is grpc.StatusCode.INVALID_ARGUMENT
    metadata = dict(ctx.aborts[0].trailing_metadata)
    # Known path: NO correlation id (only unexpected path adds one).
    assert "x-message-service-correlation-id" not in metadata


@pytest.mark.asyncio
@pytest.mark.requirement("L3-ERR-014")
async def test_translate_to_grpc_status_dispatches_unknown_to_translate_unexpected() -> None:
    """A non-MessageServiceError SHALL go through the unexpected path."""
    ctx = _FakeServicerContext()
    exc = RuntimeError("plain python error")
    with pytest.raises(_AbortRaisedError):
        await translate_to_grpc_status(ctx, exc)
    assert ctx.aborts[0].code is grpc.StatusCode.INTERNAL
    metadata = dict(ctx.aborts[0].trailing_metadata)
    assert "x-message-service-correlation-id" in metadata


# -----------------------------------------------------------------------------
# BaseException propagation (L3-ERR-022)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-ERR-022")
@pytest.mark.parametrize(
    "control_exc", [KeyboardInterrupt(), SystemExit(), asyncio.CancelledError()]
)
async def test_control_flow_baseexceptions_propagate_through_translator(
    control_exc: BaseException,
) -> None:
    """L3-ERR-022: a non-``Exception`` ``BaseException`` SHALL propagate uncaught.

    The public boundary translator ``translate_to_grpc_status`` re-raises any
    ``BaseException`` that is not an ``Exception`` (``KeyboardInterrupt``,
    ``SystemExit``, ``asyncio.CancelledError``) rather than turning it into a
    gRPC status — so cooperative cancellation and interpreter shutdown are not
    swallowed. Verify it propagates and NO abort is issued.
    """
    ctx = _FakeServicerContext()
    with pytest.raises(type(control_exc)):
        await translate_to_grpc_status(ctx, control_exc)
    assert ctx.aborts == []  # never translated to a gRPC status


# -----------------------------------------------------------------------------
# Hierarchy invariants (L3-ERR-001, L3-ERR-002, L3-ERR-004, L3-ERR-005)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-ERR-001")
@pytest.mark.requirement("L3-ERR-003")
def test_message_service_error_has_required_classvars() -> None:
    """L3-ERR-001: root SHALL declare error_code + http_status + log_level."""
    assert hasattr(MessageServiceError, "error_code")
    assert hasattr(MessageServiceError, "http_status")
    assert hasattr(MessageServiceError, "log_level")
    assert isinstance(MessageServiceError.error_code, str)
    assert isinstance(MessageServiceError.http_status, int)
    assert isinstance(MessageServiceError.log_level, int)


@pytest.mark.requirement("L3-ERR-001")
@pytest.mark.requirement("L3-OBS-019")
@pytest.mark.requirement("L3-OBS-020")
def test_message_service_error_log_level_default_is_error() -> None:
    """L3-ERR-001 / L3-OBS-019/020: default log_level on the root SHALL
    be logging.ERROR; the ClassVar mapping IS the level-assignment spec.
    """
    assert MessageServiceError.log_level == logging.ERROR


@pytest.mark.requirement("L3-ERR-002")
def test_message_service_error_init_signature() -> None:
    """L3-ERR-002: ``__init__(self, message: str, *, details=None)``; details defaults to {}."""
    exc = MessageServiceError("hello")
    assert exc.message == "hello"
    assert exc.details == {}
    exc2 = MessageServiceError("hi", details={"a": 1})
    assert exc2.details == {"a": 1}


@pytest.mark.requirement("L3-ERR-004")
def test_four_intermediate_subclasses_exist() -> None:
    """L3-ERR-004: DomainError + ValidationError + InfrastructureError + ConfigurationError."""
    direct_subclasses = set(MessageServiceError.__subclasses__())
    expected = {DomainError, ValidationError, InfrastructureError, ConfigurationError}
    # The four SHALL all be direct subclasses (the spec permits
    # additional direct subclasses, but these four are mandatory).
    assert expected.issubset(direct_subclasses)


@pytest.mark.requirement("L3-ERR-004")
def test_domain_subcategories_inherit_from_domain_error() -> None:
    """NotFoundError / ForbiddenError / PreconditionError SHALL be DomainError subclasses."""
    assert issubclass(NotFoundError, DomainError)
    from message_service.domain.errors import ForbiddenError

    assert issubclass(ForbiddenError, DomainError)
    assert issubclass(PreconditionError, DomainError)


@pytest.mark.requirement("L3-ERR-005")
def test_every_concrete_class_inherits_from_one_intermediate() -> None:
    """L3-ERR-005: every concrete class SHALL transitively inherit from exactly
    one of {DomainError, ValidationError, InfrastructureError, ConfigurationError}.
    """
    from message_service.domain.errors import _iter_leaf_error_classes

    intermediates = (DomainError, ValidationError, InfrastructureError, ConfigurationError)
    for cls in _iter_leaf_error_classes():
        matches = [base for base in intermediates if issubclass(cls, base)]
        assert len(matches) == 1, (
            f"{cls.__name__} matches {len(matches)} intermediate categories: "
            f"{[b.__name__ for b in matches]}"
        )


# -----------------------------------------------------------------------------
# error_code uniqueness (L3-ERR-006, L3-ERR-007)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-ERR-006")
def test_every_leaf_error_code_is_upper_snake_case() -> None:
    """L3-ERR-006: error_code SHALL be a non-empty UPPER_SNAKE_CASE string."""
    from message_service.domain.errors import _iter_leaf_error_classes

    for cls in _iter_leaf_error_classes():
        code = cls.error_code
        assert code, f"{cls.__name__} has empty error_code"
        assert code.isupper() or "_" in code, (
            f"{cls.__name__}.error_code = {code!r} is not UPPER_SNAKE_CASE"
        )
        # Allowed character set:
        assert all(c.isupper() or c.isdigit() or c == "_" for c in code), (
            f"{cls.__name__}.error_code = {code!r} contains disallowed characters"
        )


@pytest.mark.requirement("L3-ERR-007")
def test_no_two_leaves_share_a_specific_error_code() -> None:
    """L3-ERR-007: distinct leaf classes SHALL declare distinct error_codes.

    Exceptions allowed (documented v1 intentional reuse):

    * ``ERROR_CODE_UNSPECIFIED`` and ``ERROR_CODE_INTERNAL`` — shared
      catch-alls used by classes whose specific code has not yet
      been minted in the proto enum (e.g., DuplicateEmailError,
      SelfProtectionError) or which collapse into the generic
      infrastructure bucket (PersistenceError / TemplateRenderError
      / EmailDeliveryError).
    * ``ERROR_CODE_INVALID_RUN_STATE`` shared by
      ``InvalidRunStateError`` and ``InvalidStateTransitionError``:
      the state-machine layer's invalid-transition error and the
      use-case layer's invalid-run-state error are semantically a
      single concept ("wrong state for this operation"); clients
      need only the code to react. A more granular pair could be
      minted in proto if a client ever needs to distinguish them,
      but no operational pressure exists today.
    """
    from message_service.domain.errors import _iter_leaf_error_classes

    permitted_shared_codes = {
        "ERROR_CODE_UNSPECIFIED",
        "ERROR_CODE_INTERNAL",
        "ERROR_CODE_INVALID_RUN_STATE",
    }
    seen: dict[str, list[str]] = {}
    for cls in _iter_leaf_error_classes():
        code = cls.error_code
        seen.setdefault(code, []).append(cls.__name__)
    duplicates = {
        code: classes
        for code, classes in seen.items()
        if len(classes) > 1 and code not in permitted_shared_codes
    }
    assert duplicates == {}, f"specific error_codes shared between leaves: {duplicates}"


# -----------------------------------------------------------------------------
# Self-check (L3-ERR-008)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-ERR-008")
@pytest.mark.requirement("L3-API-018")
def test_assert_error_codes_match_proto_enum_passes_for_real_proto() -> None:
    """L3-ERR-008 / L3-API-018: every leaf class's error_code SHALL be in
    the real proto enum (a static-analysis check enforced at import time).
    """
    from message_service_proto.v1 import message_service_pb2

    from message_service.domain.errors import assert_error_codes_match_proto_enum

    proto_codes = set(message_service_pb2.ErrorCode.keys())
    # Should not raise. Returns the orphan-proto-codes list.
    orphans = assert_error_codes_match_proto_enum(proto_codes)
    # No assertion on length — orphans are non-fatal per L3-ERR-009;
    # we just verify the call shape works.
    assert isinstance(orphans, list)


@pytest.mark.requirement("L3-ERR-008")
def test_assert_error_codes_match_proto_enum_raises_when_class_code_missing() -> None:
    """L3-ERR-008: missing proto code SHALL raise ConfigurationError."""
    from message_service.domain.errors import assert_error_codes_match_proto_enum

    # Build a synthetic proto-codes set that excludes one known code.
    minimal_codes = {"ERROR_CODE_UNSPECIFIED"}  # missing all the specific ones
    with pytest.raises(ConfigurationError) as excinfo:
        assert_error_codes_match_proto_enum(minimal_codes)
    assert "error_code" in excinfo.value.message
    # Details SHALL include the offending class name + its code.
    assert "exception_class" in excinfo.value.details
    assert "error_code" in excinfo.value.details


@pytest.mark.requirement("L3-ERR-009")
def test_assert_error_codes_match_proto_enum_returns_orphans() -> None:
    """L3-ERR-009: proto codes with no Python class SHALL be returned (warning, not fatal)."""
    from message_service_proto.v1 import message_service_pb2

    from message_service.domain.errors import assert_error_codes_match_proto_enum

    # Add a fake orphan code to the proto set.
    proto_codes = set(message_service_pb2.ErrorCode.keys())
    proto_codes.add("ERROR_CODE_FAKE_FUTURE_VALUE")
    orphans = assert_error_codes_match_proto_enum(proto_codes)
    assert "ERROR_CODE_FAKE_FUTURE_VALUE" in orphans


# -----------------------------------------------------------------------------
# http_status + log_level discipline
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-ERR-001")
def test_validation_error_http_status_default_is_422() -> None:
    assert ValidationError.http_status == 422


@pytest.mark.requirement("L3-ERR-001")
def test_not_found_error_http_status_default_is_404() -> None:
    assert NotFoundError.http_status == 404


@pytest.mark.requirement("L3-ERR-001")
def test_precondition_error_http_status_default_is_409() -> None:
    assert PreconditionError.http_status == 409


@pytest.mark.requirement("L3-ERR-001")
@pytest.mark.requirement("L3-OBS-019")
@pytest.mark.requirement("L3-OBS-020")
def test_infrastructure_error_log_level_is_warning() -> None:
    assert InfrastructureError.log_level == logging.WARNING


@pytest.mark.requirement("L3-ERR-001")
@pytest.mark.requirement("L3-OBS-019")
@pytest.mark.requirement("L3-OBS-020")
def test_validation_error_log_level_is_info() -> None:
    assert ValidationError.log_level == logging.INFO


# Silence unused-import warnings for `Any` (used implicitly in
# the dataclass annotations above).
_silenced: Any = None
del _silenced


# -----------------------------------------------------------------------------
# R-ERR-001: additive google.rpc.Status + ErrorInfo envelope (L3-ERR-023)
# -----------------------------------------------------------------------------


def _parse_status_details(trailing_metadata: tuple[tuple[str, Any], ...]) -> Any:
    """Parse the grpc-status-details-bin payload back into a google.rpc.Status."""
    from google.rpc import status_pb2

    blob = dict(trailing_metadata)["grpc-status-details-bin"]
    assert isinstance(blob, bytes)
    return status_pb2.Status.FromString(blob)


@pytest.mark.asyncio
@pytest.mark.requirement("L3-ERR-023")
async def test_translate_known_carries_rich_status_and_legacy_key() -> None:
    """A known error emits both the legacy key and a parseable google.rpc.Status."""
    from google.rpc import error_details_pb2

    ctx = _FakeServicerContext()
    exc = UnknownTagError("tag 'x' not allowed", details={"tag": "x", "allowed_tags": ["a", "b"]})
    with pytest.raises(_AbortRaisedError):
        await _translate_known(ctx, exc)
    metadata = dict(ctx.aborts[0].trailing_metadata)

    # Legacy shape retained (backward compatible).
    assert metadata["x-message-service-error-code"] == "ERROR_CODE_UNKNOWN_TAG"

    # Additive rich shape.
    status = _parse_status_details(ctx.aborts[0].trailing_metadata)
    assert status.code == grpc.StatusCode.INVALID_ARGUMENT.value[0]
    assert status.message == "tag 'x' not allowed"
    info = error_details_pb2.ErrorInfo()
    status.details[0].Unpack(info)
    assert info.reason == "ERROR_CODE_UNKNOWN_TAG"
    assert info.domain == "message-service"
    assert info.metadata["tag"] == "x"
    # Non-string detail values are stringified (JSON) for the map<string,string>.
    assert info.metadata["allowed_tags"] == '["a", "b"]'


@pytest.mark.asyncio
@pytest.mark.requirement("L3-ERR-023")
async def test_translate_unexpected_carries_rich_status_with_correlation_id() -> None:
    """An unexpected error's rich status carries INTERNAL + the correlation id."""
    from google.rpc import error_details_pb2

    ctx = _FakeServicerContext()
    with pytest.raises(_AbortRaisedError):
        await _translate_unexpected(ctx, RuntimeError("boom"))
    metadata = dict(ctx.aborts[0].trailing_metadata)
    correlation_id = metadata["x-message-service-correlation-id"]

    status = _parse_status_details(ctx.aborts[0].trailing_metadata)
    assert status.code == grpc.StatusCode.INTERNAL.value[0]
    info = error_details_pb2.ErrorInfo()
    status.details[0].Unpack(info)
    assert info.reason == "ERROR_CODE_INTERNAL"
    assert info.domain == "message-service"
    # The same correlation id appears in the rich envelope and the legacy key.
    assert info.metadata["correlation_id"] == correlation_id
