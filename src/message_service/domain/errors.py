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
* L2-RUN-005: invalid transition → InvalidStateTransition
"""

from __future__ import annotations

from typing import Any, ClassVar


# =============================================================================
# Root
# =============================================================================


class MessageServiceError(Exception):
    """Root of the Message-Service exception hierarchy.

    Attributes:
        error_code: Machine-readable code matching the proto ``ErrorCode`` enum.
            Subclasses override this at class level.
        details: Structured diagnostic details. Safe to include in client-facing
            error responses.
    """

    error_code: ClassVar[str] = "ERROR_CODE_UNSPECIFIED"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(error_code={self.error_code!r}, message={self.message!r})"


# =============================================================================
# Validation errors (mapped to gRPC INVALID_ARGUMENT)
# =============================================================================


class ValidationError(MessageServiceError):
    """Base for input validation failures. Maps to gRPC INVALID_ARGUMENT."""

    error_code: ClassVar[str] = "ERROR_CODE_UNSPECIFIED"


class UnknownPipelineType(ValidationError):
    """Pipeline type not in configured registry. See L2-RUN-007."""

    error_code: ClassVar[str] = "ERROR_CODE_UNKNOWN_PIPELINE_TYPE"


class UnknownTag(ValidationError):
    """Tag not present in configured vocabulary. See L2-RUN-008, L2-SUB-008."""

    error_code: ClassVar[str] = "ERROR_CODE_UNKNOWN_TAG"


class DuplicateStageId(ValidationError):
    """Two or more declared stages share the same stage_id. See L2-RUN-009."""

    error_code: ClassVar[str] = "ERROR_CODE_DUPLICATE_STAGE_ID"


class UnknownTemplate(ValidationError):
    """Template reference not present in manifest. See L2-RUN-010."""

    error_code: ClassVar[str] = "ERROR_CODE_UNKNOWN_TEMPLATE"


class MissingAggregationTemplate(ValidationError):
    """SINGLE_AGGREGATED mode declared without an aggregation_template.

    See L2-RUN-011, L2-AGGR-009.
    """

    error_code: ClassVar[str] = "ERROR_CODE_MISSING_AGGREGATION_TEMPLATE"


class UnknownStage(ValidationError):
    """Submission references a stage_id not declared in the run. See L2-STAGE-008."""

    error_code: ClassVar[str] = "ERROR_CODE_UNKNOWN_STAGE"


class ContextSchemaViolation(ValidationError):
    """Stage context failed JSON Schema validation. See L2-TMPL-011.

    The ``details`` dict SHOULD include a ``schema_path`` (JSON Pointer) key
    identifying the failing element.
    """

    error_code: ClassVar[str] = "ERROR_CODE_CONTEXT_SCHEMA_VIOLATION"


class ContextSizeExceeded(ValidationError):
    """Submitted context exceeded ``templates.max_context_bytes``. See L2-TMPL-012."""

    error_code: ClassVar[str] = "ERROR_CODE_CONTEXT_SIZE_EXCEEDED"


class RenderedSizeExceeded(ValidationError):
    """Rendered output exceeded ``templates.max_rendered_bytes``. See L2-TMPL-013."""

    error_code: ClassVar[str] = "ERROR_CODE_RENDERED_SIZE_EXCEEDED"


class MalformedRequest(ValidationError):
    """Request failed protobuf-level or syntactic validation."""

    error_code: ClassVar[str] = "ERROR_CODE_MALFORMED_REQUEST"


# =============================================================================
# Resource lookup errors (mapped to gRPC NOT_FOUND)
# =============================================================================


class NotFoundError(MessageServiceError):
    """Base for missing-resource failures. Maps to gRPC NOT_FOUND."""

    error_code: ClassVar[str] = "ERROR_CODE_UNSPECIFIED"


class RunNotFound(NotFoundError):
    """Referenced run_id does not exist. See L2-STAGE-009."""

    error_code: ClassVar[str] = "ERROR_CODE_RUN_NOT_FOUND"


# =============================================================================
# Precondition errors (mapped to gRPC FAILED_PRECONDITION)
# =============================================================================


class PreconditionError(MessageServiceError):
    """Base for state-based precondition failures. Maps to FAILED_PRECONDITION."""

    error_code: ClassVar[str] = "ERROR_CODE_UNSPECIFIED"


class InvalidRunState(PreconditionError):
    """Operation attempted against a run in an incompatible state. See L2-RUN-012.

    Typical usage: ``FinalizeRun`` called against a run not in ``AGGREGATING``.
    """

    error_code: ClassVar[str] = "ERROR_CODE_INVALID_RUN_STATE"


class InvalidStateTransition(PreconditionError):
    """Attempt to perform a transition not in the permitted transition table.

    Raised by :mod:`message_service.domain.state_machines`. See L2-RUN-005,
    L2-RUN-006, L2-STAGE-002.
    """

    error_code: ClassVar[str] = "ERROR_CODE_INVALID_RUN_STATE"


class InvalidStageState(PreconditionError):
    """Operation attempted against a stage in an incompatible state."""

    error_code: ClassVar[str] = "ERROR_CODE_INVALID_STAGE_STATE"


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


class PersistenceError(InfrastructureError):
    """SQLite or filesystem persistence operation failed unexpectedly."""


class TemplateRenderError(InfrastructureError):
    """Jinja2 rendering failed for a reason other than schema or size violation.

    Contrast with :class:`ContextSchemaViolation` and :class:`RenderedSizeExceeded`
    which are validation errors surfaced to the client.
    """


class EmailDeliveryError(InfrastructureError):
    """SMTP delivery failed. Detail ``retriable`` (bool) is set by the retry logic."""


class ConfigurationError(MessageServiceError):
    """Configuration could not be loaded or validated. See L2-CFG-005.

    Raised at startup before any service component is instantiated, causing
    the process to exit with a nonzero status (L2-CFG-006).
    """

    error_code: ClassVar[str] = "ERROR_CODE_INTERNAL"


__all__ = [
    # Root
    "MessageServiceError",
    # Validation
    "ValidationError",
    "UnknownPipelineType",
    "UnknownTag",
    "DuplicateStageId",
    "UnknownTemplate",
    "MissingAggregationTemplate",
    "UnknownStage",
    "ContextSchemaViolation",
    "ContextSizeExceeded",
    "RenderedSizeExceeded",
    "MalformedRequest",
    # Not found
    "NotFoundError",
    "RunNotFound",
    # Precondition
    "PreconditionError",
    "InvalidRunState",
    "InvalidStateTransition",
    "InvalidStageState",
    # Infrastructure
    "InfrastructureError",
    "PersistenceError",
    "TemplateRenderError",
    "EmailDeliveryError",
    # Configuration
    "ConfigurationError",
]
