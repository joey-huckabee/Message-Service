"""Structured logging setup using :mod:`structlog`.

This module is the single point of logging configuration for the service.
Call :func:`configure_logging` exactly once at startup, before any service
component is instantiated.

Design notes
------------
* Output is JSON to stdout, one record per line (L2-OBS-001).
* Context propagation uses ``structlog.contextvars`` so request-scoped fields
  (``run_id``, ``stage_id``, ``user_id``, ``correlation_id``) flow to every
  log record emitted during a request's handling without manual plumbing
  (L2-OBS-002).
* Sensitive field redaction runs as a processor in the pipeline, so log
  sites cannot accidentally leak them even if the call site does not know
  about the redaction rule (L2-OBS-003).
* The stdlib ``logging`` module is routed through structlog so library logs
  (grpcio, uvicorn, aiosqlite, jinja2) produce the same JSON shape.

Log level conventions — see ``docs/LOGGING-CONVENTIONS.md`` for details. In
brief:
* ``DEBUG``   — internal state, loop counters, SQL statements, not for prod
* ``INFO``    — lifecycle events, successful operations, state transitions
* ``WARNING`` — validation rejections, expected failures, retriable issues
* ``ERROR``   — unexpected exceptions, permanent failures needing attention
* ``CRITICAL`` — service-level failures requiring operator intervention now
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, unbind_contextvars
from structlog.typing import EventDict, Processor

# Fields that should never appear in log output even if a call site passes them.
# Extend this list if new sensitive fields are introduced.
_SENSITIVE_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "password_hash",
        "pwd",
        "secret",
        "smtp_password",
        "session_token",
        "cookie",
        "authorization",
        "email_body",  # full rendered email bodies should go to filesystem, not logs
        "rendered_output",
        "template_context",  # may contain arbitrary pipeline data
    }
)

_REDACTED = "<redacted>"


def _redact_sensitive_fields(
    logger: logging.Logger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Processor: replace values of sensitive keys with ``<redacted>``.

    Applied to every event dict before rendering, so no call site can leak
    sensitive data by accident (L2-OBS-003).
    """
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_FIELD_NAMES:
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(
    *,
    level: str = "INFO",
    development: bool = False,
) -> None:
    """Configure structlog and the stdlib logging module.

    Args:
        level: Minimum log level. One of ``DEBUG``, ``INFO``, ``WARNING``,
            ``ERROR``, ``CRITICAL``.
        development: If True, renders logs in a human-readable coloured
            format instead of JSON. Intended for local development only.
    """
    shared_processors: list[Processor] = [
        # Inject context vars (run_id, stage_id, user_id, etc.)
        structlog.contextvars.merge_contextvars,
        # Add standard fields
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        # Redact sensitive values. Run late so it catches everything.
        _redact_sensitive_fields,
    ]

    if development:
        renderer: Processor = structlog.dev.ConsoleRenderer(colors=True)
    else:
        shared_processors.append(structlog.processors.format_exc_info)
        shared_processors.append(structlog.processors.dict_tracebacks)
        renderer = structlog.processors.JSONRenderer()

    # Configure structlog itself (used by application code directly).
    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route the stdlib logging module through structlog so library logs
    # (grpc, uvicorn, aiosqlite, jinja2, ...) share the same output format.
    stdlib_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    stdlib_handler = logging.StreamHandler(sys.stdout)
    stdlib_handler.setFormatter(stdlib_formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(stdlib_handler)
    root.setLevel(level)

    # Tune chatty third-party loggers. Adjust as needed during tuning.
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.INFO)


# =============================================================================
# Context propagation helpers
# =============================================================================


def bind_request_context(**fields: Any) -> None:
    """Bind request-scoped fields for automatic inclusion in subsequent log records.

    Call this at the boundary of each inbound request (gRPC servicer method,
    FastAPI route handler) with the relevant identifiers::

        bind_request_context(run_id=run_id, correlation_id=uuid.uuid4().hex)

    Fields bound via this function automatically appear in every log record
    emitted by the current asyncio task until :func:`clear_request_context`
    is called.
    """
    bind_contextvars(**fields)


def clear_request_context(*keys: str) -> None:
    """Clear named context fields, or all fields if no keys supplied.

    Should be called in a ``finally`` block at the request handler boundary
    so that fields from one request do not leak into the next (a concern on
    long-lived worker tasks where the same task handles successive RPCs).
    """
    if keys:
        unbind_contextvars(*keys)
    else:
        clear_contextvars()


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a configured structlog logger.

    Usage::

        from message_service.observability.logging_setup import get_logger
        logger = get_logger(__name__)

        logger.info("run_finalized", run_id=run_id, stage_count=len(stages))
    """
    # structlog.get_logger returns Any; cast here to keep the downstream
    # signature strict without forcing every call site to assert.
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
