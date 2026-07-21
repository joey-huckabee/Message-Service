"""Domain exception hierarchy for Message-Service.

All exceptions raised by domain, application, or infrastructure layers SHALL
derive from :class:`MessageServiceError`. This gives a single root for layer-
boundary translation (e.g., mapping to gRPC status codes at the servicer
boundary per L2-API-008 through L2-API-011).

Design notes
------------
* Each exception class carries a class-level ``error_code`` attribute whose
  value mirrors the ``ErrorCode`` enum in ``message_service.proto``. This
  lets the servicer boundary map exceptions to proto error codes mechanically
  without an explicit lookup table.
* Exceptions carry structured ``details`` in addition to a free-form message.
  Details are a dict of JSON-serializable values that get attached to the
  gRPC error response and emitted as structured log fields.
* Exceptions are NOT used for control flow in the happy path. They signal
  validation failures, precondition violations, and unexpected internal
  errors only.

Requirement references
----------------------
* L2-API-008: validation → INVALID_ARGUMENT
* L2-API-009: not-found → NOT_FOUND
* L2-API-010: internal → INTERNAL with correlation id, no stack trace
* L2-RUN-005: invalid transition → InvalidStateTransitionError
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

# =============================================================================
# Root
# =============================================================================


class MessageServiceError(Exception):
    """Root of the Message-Service exception hierarchy.

    Attributes:
        error_code: Machine-readable code matching the proto ``ErrorCode`` enum.
            Subclasses override this at class level (per `L3-ERR-001`).
        http_status: HTTP status code surfaced by the FastAPI dashboard's
            error-translation layer when this exception class is raised
            from a route. Each intermediate category sets a sensible
            default; specific leaf classes override (e.g.,
            :class:`DuplicateEmailError` returns 409 rather than the
            422 default of its :class:`ValidationError` parent).
        log_level: ``logging`` level the gRPC + REST translators emit
            the boundary log record at. ``ERROR`` is the conservative
            default for the root; intermediate categories override
            (e.g., :class:`ValidationError` → INFO, :class:`DomainError`
            → INFO, :class:`InfrastructureError` → WARNING).
        details: Structured diagnostic details. Safe to include in
            client-facing error responses subject to the L3-ERR-016
            redaction filter.
    """

    error_code: ClassVar[str] = "ERROR_CODE_UNSPECIFIED"
    http_status: ClassVar[int] = 500
    log_level: ClassVar[int] = logging.ERROR

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        """Initialize the error with a message and optional structured details.

        Args:
            message: Human-readable message. Appears in logs and (for
                validation-category errors) in the client-facing gRPC response.
            details: Machine-parseable diagnostic fields. Attached to the
                gRPC error response metadata and included in log records.
        """
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def __repr__(self) -> str:
        """Return a debugging representation including error_code and message."""
        return f"{type(self).__name__}(error_code={self.error_code!r}, message={self.message!r})"


# =============================================================================
# Validation errors (mapped to gRPC INVALID_ARGUMENT)
# =============================================================================


class ValidationError(MessageServiceError):
    """Base for input validation failures. Maps to gRPC INVALID_ARGUMENT."""

    http_status: ClassVar[int] = 422
    log_level: ClassVar[int] = logging.INFO

    error_code: ClassVar[str] = "ERROR_CODE_UNSPECIFIED"


class UnknownPipelineTypeError(ValidationError):
    """Pipeline type not in configured registry. See L2-RUN-007."""

    error_code: ClassVar[str] = "ERROR_CODE_UNKNOWN_PIPELINE_TYPE"


class UnknownTagError(ValidationError):
    """Tag not present in configured vocabulary. See L2-RUN-008, L2-SUB-008."""

    error_code: ClassVar[str] = "ERROR_CODE_UNKNOWN_TAG"


class DuplicateStageIdError(ValidationError):
    """Two or more declared stages share the same stage_id. See L2-RUN-009."""

    error_code: ClassVar[str] = "ERROR_CODE_DUPLICATE_STAGE_ID"


class UnknownTemplateError(ValidationError):
    """Template reference not present in manifest. See L2-RUN-010."""

    error_code: ClassVar[str] = "ERROR_CODE_UNKNOWN_TEMPLATE"


class MissingAggregationTemplateError(ValidationError):
    """SINGLE_AGGREGATED mode declared without an aggregation_template.

    See L2-RUN-011, L2-AGGR-009.
    """

    error_code: ClassVar[str] = "ERROR_CODE_MISSING_AGGREGATION_TEMPLATE"


class UnknownStageError(ValidationError):
    """Submission references a stage_id not declared in the run. See L2-STAGE-008."""

    error_code: ClassVar[str] = "ERROR_CODE_UNKNOWN_STAGE"


class ContextSchemaViolationError(ValidationError):
    """Stage context failed JSON Schema validation. See L2-TMPL-011.

    The ``details`` dict SHOULD include a ``json_pointer`` (RFC 6901) key
    identifying the failing element.
    """

    error_code: ClassVar[str] = "ERROR_CODE_CONTEXT_SCHEMA_VIOLATION"


class ContextSizeExceededError(ValidationError):
    """Submitted context exceeded ``templates.max_context_bytes``. See L2-TMPL-012."""

    error_code: ClassVar[str] = "ERROR_CODE_CONTEXT_SIZE_EXCEEDED"


class RenderedSizeExceededError(ValidationError):
    """Rendered output exceeded ``templates.max_rendered_bytes``. See L2-TMPL-013."""

    error_code: ClassVar[str] = "ERROR_CODE_RENDERED_SIZE_EXCEEDED"


class DuplicateEmailError(ValidationError):
    """Admin-initiated user creation collided with an existing email.

    Raised by ``CreateUserUseCase`` when the persistence layer's UNIQUE
    constraint on ``users.email`` rejects the insert. Surfaced as HTTP
    409 by the dashboard route per L3-AUTH-015. The HTTP-409 mapping
    is route-layer (validation errors normally map to gRPC INVALID_ARGUMENT;
    409 is the right HTTP code for "the request is well-formed but
    conflicts with current state").
    """

    error_code: ClassVar[str] = "ERROR_CODE_UNSPECIFIED"


class InvalidEmailError(ValidationError):
    """Admin-initiated request supplied a malformed email address.

    Raised by ``CreateUserUseCase`` (and any other admin path that
    accepts an email) when the syntactic format check fails. Surfaced
    as HTTP 422 by the dashboard route per L3-AUTH-015.
    """

    error_code: ClassVar[str] = "ERROR_CODE_UNSPECIFIED"


class MalformedRequestError(ValidationError):
    """Request failed protobuf-level or syntactic validation."""

    error_code: ClassVar[str] = "ERROR_CODE_MALFORMED_REQUEST"


# =============================================================================
# Resource lookup errors (mapped to gRPC NOT_FOUND)
# =============================================================================


class DomainError(MessageServiceError):
    """Intermediate category for domain-layer rule violations.

    Per `L3-ERR-002`/`L3-ERR-004`, the four direct subclasses of
    :class:`MessageServiceError` are :class:`DomainError`,
    :class:`ValidationError`, :class:`InfrastructureError`, and
    :class:`ConfigurationError`. ``DomainError`` clusters
    "the request is well-formed but conflicts with current state":
    not-found, forbidden, and precondition violations all sit under
    it. The dashboard / gRPC translators dispatch on ``DomainError``
    subclasses' specific types (NotFound → 404, Forbidden → 403,
    Precondition → 409) rather than on ``DomainError`` directly,
    matching the existing isinstance-based dispatch in
    `interfaces/grpc/error_mapping.py`.

    Domain errors log at INFO because they reflect normal client-
    visible business outcomes (someone asked for a missing resource,
    a precondition wasn't met) rather than service malfunctions.
    """

    log_level: ClassVar[int] = logging.INFO


class NotFoundError(DomainError):
    """Base for missing-resource failures. Maps to gRPC NOT_FOUND."""

    error_code: ClassVar[str] = "ERROR_CODE_UNSPECIFIED"
    http_status: ClassVar[int] = 404


class RunNotFoundError(NotFoundError):
    """Referenced run_id does not exist. See L2-STAGE-009."""

    error_code: ClassVar[str] = "ERROR_CODE_RUN_NOT_FOUND"


class SubscriptionNotFoundError(NotFoundError):
    """Referenced subscription_id does not exist. See L3-DASH-019."""

    error_code: ClassVar[str] = "ERROR_CODE_UNSPECIFIED"


class UserNotFoundError(NotFoundError):
    """Referenced ``user_id`` does not exist. See L3-AUTH-014.

    Raised by the admin user-management routes (PATCH and password
    reset) when the path-parameter ``user_id`` does not match a row.
    Surfaced as HTTP 404 by the dashboard route.
    """

    error_code: ClassVar[str] = "ERROR_CODE_UNSPECIFIED"


# =============================================================================
# Authorization errors (mapped to gRPC PERMISSION_DENIED / HTTP 403)
# =============================================================================


class ForbiddenError(DomainError):
    """Base for cross-user / unauthorized access failures.

    Distinct from :class:`NotFoundError`: the resource exists but the
    caller does not own it. Per L2-DASH-004, dashboard CRUD routes
    SHALL return HTTP 403 for cross-user attempts (rather than masking
    them as 404), so the route layer can distinguish the two cases.
    """

    error_code: ClassVar[str] = "ERROR_CODE_UNSPECIFIED"
    http_status: ClassVar[int] = 403


class SubscriptionForbiddenError(ForbiddenError):
    """Subscription exists but the caller is not its owner. See L3-DASH-007."""

    error_code: ClassVar[str] = "ERROR_CODE_UNSPECIFIED"


# =============================================================================
# Precondition errors (mapped to gRPC FAILED_PRECONDITION)
# =============================================================================


class PreconditionError(DomainError):
    """Base for state-based precondition failures. Maps to FAILED_PRECONDITION."""

    error_code: ClassVar[str] = "ERROR_CODE_UNSPECIFIED"
    http_status: ClassVar[int] = 409


class InvalidRunStateError(PreconditionError):
    """Operation attempted against a run in an incompatible state. See L2-RUN-012.

    Typical usage: ``FinalizeRun`` called against a run not in ``AGGREGATING``.
    """

    error_code: ClassVar[str] = "ERROR_CODE_INVALID_RUN_STATE"


class InvalidStateTransitionError(PreconditionError):
    """Attempt to perform a transition not in the permitted transition table.

    Raised by :mod:`message_service.domain.state_machines`. See L2-RUN-005,
    L2-RUN-006, L2-STAGE-002.
    """

    error_code: ClassVar[str] = "ERROR_CODE_INVALID_RUN_STATE"


class InvalidStageStateError(PreconditionError):
    """Operation attempted against a stage in an incompatible state."""

    error_code: ClassVar[str] = "ERROR_CODE_INVALID_STAGE_STATE"


class SelfProtectionError(PreconditionError):
    """Admin attempted a self-deadmin or self-disable operation. See L2-AUTH-009.

    Raised by ``UpdateUserUseCase`` when the requesting administrator
    targets their own ``user_id`` with ``is_admin=False`` or
    ``disabled=True``. Surfaced as HTTP 409 by the dashboard route
    (per L3-AUTH-017). No audit record is emitted because no
    successful action occurred — the rejected attempt is captured by
    a structured-log WARNING line.
    """

    error_code: ClassVar[str] = "ERROR_CODE_UNSPECIFIED"


# =============================================================================
# Infrastructure errors
# =============================================================================
# These surface unexpected conditions in adapter layers. They are never mapped
# directly to client-facing gRPC errors; instead the gRPC servicer catches them,
# generates a correlation id, logs the full exception with stack trace, and
# returns INTERNAL with a sanitized message carrying only the correlation id
# (L2-API-010).


class InfrastructureError(MessageServiceError):
    """Base for infrastructure-layer failures (persistence, SMTP, templating)."""

    error_code: ClassVar[str] = "ERROR_CODE_INTERNAL"
    http_status: ClassVar[int] = 500
    log_level: ClassVar[int] = logging.WARNING


class PersistenceError(InfrastructureError):
    """SQLite or filesystem persistence operation failed unexpectedly."""


class TemplateRenderError(InfrastructureError):
    """Jinja2 rendering failed for a reason other than schema or size violation.

    Contrast with :class:`ContextSchemaViolationError`,
    :class:`ContextSizeExceededError`, and :class:`RenderedSizeExceededError`,
    which are validation errors surfaced to the client.
    """


class EmailDeliveryError(InfrastructureError):
    """SMTP delivery failed.

    The mailer classifies the failure in ``details["failure_reason"]``:
    ``PERMANENT_SMTP_FAILURE`` (fail-fast, not retried) or ``RETRIES_EXHAUSTED``
    (a transient failure retried to exhaustion). Callers distinguishing
    transient-vs-permanent (e.g. the delivery-outcome metric) key on that.
    """


class EmailSizeExceededError(EmailDeliveryError):
    """The MIME-encoded email exceeded ``mail.max_email_size_bytes``.

    Raised by the mailer's pre-transmission size check (L2-MAIL-008)
    before any SMTP traffic is emitted. Subclassing
    :class:`EmailDeliveryError` keeps the existing generic
    ``except EmailDeliveryError`` handlers working while letting
    callers that care about size-exceeded specifically (e.g.,
    :class:`AssembleAndDeliverUseCase`'s L3-MAIL-030 admin-notification
    path) catch this subtype first. ``details`` carries
    ``failure_reason="EMAIL_SIZE_EXCEEDED"``, ``measured_bytes``,
    ``limit_bytes``, and ``recipient_count`` per L3-MAIL-014.
    """


class ConfigurationError(MessageServiceError):
    """Configuration could not be loaded or validated. See L2-CFG-005.

    Raised at startup before any service component is instantiated, causing
    the process to exit with a nonzero status (L2-CFG-006). Unlike
    :class:`InfrastructureError`, this is rarely client-visible — by
    the time the service is serving requests, configuration has been
    successfully validated.
    """

    error_code: ClassVar[str] = "ERROR_CODE_INTERNAL"
    http_status: ClassVar[int] = 500
    log_level: ClassVar[int] = logging.ERROR


# =============================================================================
# Self-check helpers (L3-ERR-008, L3-ERR-009)
# =============================================================================


def _iter_leaf_error_classes() -> list[type[MessageServiceError]]:
    """Walk the :class:`MessageServiceError` subclass tree; return leaves only.

    A "leaf" is a class with no further :class:`MessageServiceError`
    subclasses — the concrete classes the codebase actually raises.
    Intermediate classes (``ValidationError``, ``DomainError``,
    ``NotFoundError``, ``ForbiddenError``, ``PreconditionError``,
    ``InfrastructureError``) are filtered out.

    Used by :func:`assert_error_codes_match_proto_enum` at bootstrap
    and by the test suite's hierarchy-shape assertions.
    """
    leaves: list[type[MessageServiceError]] = []
    seen: set[type[MessageServiceError]] = set()

    def _visit(cls: type[MessageServiceError]) -> None:
        if cls in seen:
            return
        seen.add(cls)
        subs = cls.__subclasses__()
        if not subs:
            leaves.append(cls)
            return
        for sub in subs:
            _visit(sub)

    _visit(MessageServiceError)
    return leaves


def assert_error_codes_match_proto_enum(
    proto_error_code_names: set[str],
) -> list[str]:
    """Verify every leaf exception's `error_code` is in the proto enum (L3-ERR-008).

    Per `L3-ERR-008`, the bootstrap self-check imports the proto
    ``ErrorCode`` enum, collects every declared enum value, and
    asserts each concrete exception's ``error_code`` is present in
    that set. Mismatch raises :class:`ConfigurationError` before any
    RPC is served.

    Per `L3-ERR-009`, proto enum values that no exception class
    exposes are NOT a fatal error — the bootstrap may legitimately
    declare codes ahead of their Python counterparts during phased
    rollouts. The function returns the list of orphan proto values
    so the caller can emit a WARNING log.

    Args:
        proto_error_code_names: The set of error-code names declared
            by the proto enum (typically
            ``set(message_service_pb2.ErrorCode.keys())``).

    Returns:
        Sorted list of proto enum values that no leaf exception
        class declares (`L3-ERR-009`'s WARNING-level orphan list).

    Raises:
        ConfigurationError: A leaf exception class has an
            ``error_code`` not present in the proto enum. The
            details dict carries ``{exception_class, error_code,
            proto_codes}`` so the operator can spot the drift.
    """
    used_codes: dict[str, str] = {}
    for cls in _iter_leaf_error_classes():
        code = cls.error_code
        if code not in proto_error_code_names:
            raise ConfigurationError(
                f"exception class {cls.__name__!r} declares error_code "
                f"{code!r} which is not in the proto ErrorCode enum",
                details={
                    "exception_class": cls.__name__,
                    "error_code": code,
                    "proto_codes": sorted(proto_error_code_names),
                },
            )
        used_codes[code] = cls.__name__
    orphans = sorted(proto_error_code_names - used_codes.keys())
    return orphans


__all__ = [  # noqa: RUF022 — grouped by exception category, mirrors hierarchy
    # Root
    "MessageServiceError",
    # Validation
    "ValidationError",
    "UnknownPipelineTypeError",
    "UnknownTagError",
    "DuplicateStageIdError",
    "UnknownTemplateError",
    "MissingAggregationTemplateError",
    "UnknownStageError",
    "ContextSchemaViolationError",
    "ContextSizeExceededError",
    "RenderedSizeExceededError",
    "MalformedRequestError",
    "DuplicateEmailError",
    "InvalidEmailError",
    # Domain (intermediate; raised only via subcategories below)
    "DomainError",
    # Not found
    "NotFoundError",
    "RunNotFoundError",
    "SubscriptionNotFoundError",
    "UserNotFoundError",
    # Forbidden
    "ForbiddenError",
    "SubscriptionForbiddenError",
    # Precondition
    "PreconditionError",
    "InvalidRunStateError",
    "InvalidStateTransitionError",
    "InvalidStageStateError",
    "SelfProtectionError",
    # Infrastructure
    "InfrastructureError",
    "PersistenceError",
    "TemplateRenderError",
    "EmailDeliveryError",
    "EmailSizeExceededError",
    # Configuration
    "ConfigurationError",
    # Self-check helpers (L3-ERR-008, L3-ERR-009)
    "assert_error_codes_match_proto_enum",
]
