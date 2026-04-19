# Logging and Exception Conventions

This document defines how the service emits log records and how exceptions propagate across layer boundaries. The rules here are **mandatory** — they are what the verification artifacts for L1-OBS-001 and L2-API-010 will check.

If you need to deviate from these conventions, document the deviation in an ADR.

## Exception philosophy

### The hierarchy

All exceptions raised inside this service derive from `message_service.domain.errors.MessageServiceError`. The hierarchy lives in one file (`src/message_service/domain/errors.py`) and is grouped by gRPC-status category to make boundary translation mechanical.

```
MessageServiceError
├── ValidationError                  → gRPC INVALID_ARGUMENT
│   ├── UnknownPipelineTypeError
│   ├── UnknownTagError
│   ├── DuplicateStageIdError
│   ├── UnknownTemplateError
│   ├── MissingAggregationTemplateError
│   ├── UnknownStageError
│   ├── ContextSchemaViolationError
│   ├── ContextSizeExceededError
│   ├── RenderedSizeExceededError
│   └── MalformedRequestError
├── NotFoundError                    → gRPC NOT_FOUND
│   └── RunNotFoundError
├── PreconditionError                → gRPC FAILED_PRECONDITION
│   ├── InvalidRunStateError
│   ├── InvalidStateTransitionError
│   └── InvalidStageStateError
├── InfrastructureError              → gRPC INTERNAL (logged, sanitized to client)
│   ├── PersistenceError
│   ├── TemplateRenderError
│   └── EmailDeliveryError
└── ConfigurationError               → startup failure, process exits
```

### Every exception carries a proto error code

Each leaf exception class has a class-level `error_code` attribute that exactly matches a value from the `ErrorCode` enum in `message_service.proto`. This is how domain code communicates the machine-readable code to the servicer boundary without the domain layer depending on proto types.

```python
class UnknownTagError(ValidationError):
    error_code: ClassVar[str] = "ERROR_CODE_UNKNOWN_TAG"
```

### Raising exceptions: include structured details

```python
# GOOD — structured, machine-parseable details alongside the message
raise UnknownTagError(
    f"tag {tag!r} not in configured vocabulary",
    details={"tag": tag, "allowed_tags": sorted(vocabulary)},
)

# BAD — unstructured string, detail only available via parsing
raise UnknownTagError(f"tag {tag!r} not allowed; allowed: {vocabulary}")
```

The `details` dict flows through to:
- gRPC error trailing metadata (via `error_mapping.py`)
- log records at the catch point (as structured fields)
- the dashboard's error display

### Never use exceptions for control flow

Exceptions signal validation failures, precondition violations, and unexpected internal errors. They are **not** a substitute for returning success/failure. Specifically:

- A run sweeper finding orphans is **not** an exception — it is the expected result of the scan.
- A stage being retried is **not** an exception — it is a normal transition.
- An idempotent submission on an already-submitted stage is **not** an exception — it is the idempotency contract.

### Catching exceptions

Three acceptable patterns, in order of preference:

1. **Let it propagate.** The default. Domain and application code should raise cleanly and trust the servicer boundary to translate.
2. **Catch to add context, then re-raise.** Use `raise ... from exc`:
   ```python
   try:
       row = await conn.fetchone(...)
   except aiosqlite.Error as exc:
       raise PersistenceError(
           "failed to load run",
           details={"run_id": run_id},
       ) from exc
   ```
3. **Catch at the boundary.** The servicer wraps every RPC in a translation block — see `error_mapping.py`. No other layer should have broad `except Exception` handlers.

**Never** silently swallow exceptions:

```python
# FORBIDDEN
try:
    do_thing()
except Exception:
    pass  # NO
```

### Boundary translation: what the client sees

The gRPC servicer calls `translate_to_grpc_status()` from `interfaces/grpc/error_mapping.py`. The translation rules:

| Exception type         | gRPC status          | What the client sees              |
|------------------------|----------------------|-----------------------------------|
| `ValidationError`      | `INVALID_ARGUMENT`   | message + error_code metadata     |
| `NotFoundError`        | `NOT_FOUND`          | message + error_code metadata     |
| `PreconditionError`    | `FAILED_PRECONDITION`| message + error_code metadata     |
| `InfrastructureError`  | `INTERNAL`           | correlation_id only               |
| Any other exception    | `INTERNAL`           | correlation_id only               |

For `INTERNAL` errors, the correlation id is logged server-side alongside the full stack trace. Operators reconcile client-reported correlation ids against server logs — **the client never sees internal exception types or traces** (L2-API-010).

## Logging philosophy

### The library: structlog

All application code uses `structlog` via `message_service.observability.logging_setup.get_logger`:

```python
from message_service.observability.logging_setup import get_logger

logger = get_logger(__name__)

logger.info("run_finalized", run_id=run_id, stage_count=len(stages))
```

### Event names are snake_case verbs or noun phrases

The first positional argument to each log call is the **event name**, not a free-form sentence. Event names are searchable and stable across versions.

```python
# GOOD
logger.info("run_begun", run_id=run_id, pipeline_type=pipeline_type)
logger.info("stage_submission_superseded", run_id=run_id, stage_id=stage_id)
logger.warning("smtp_transient_failure", retry_count=n, backoff_seconds=backoff)

# BAD — hard to search, inconsistent
logger.info(f"Run {run_id} began with pipeline {pipeline_type}")
logger.info("Successfully saved the thing")
```

### Level conventions

| Level      | When to use                                                              |
|------------|--------------------------------------------------------------------------|
| `DEBUG`    | Loop counters, SQL statements, state variable traces. Disabled in prod.  |
| `INFO`     | Lifecycle events, successful operations, state transitions, RPC received. |
| `WARNING`  | Validation rejections, retriable errors, degraded conditions.            |
| `ERROR`    | Unexpected exceptions, permanent failures, delivery failures after retry. |
| `CRITICAL` | Service-level failures: SMTP relay completely unreachable, disk full, DB corruption. Indicates on-call attention needed. |

Specific guidance:

- **Starting/stopping the service** → `INFO` (e.g., `service_starting`, `service_shutdown_complete`).
- **An RPC handled successfully** → `INFO` once at completion, with the event name matching the RPC (e.g., `begin_run_handled`).
- **A validation error returned to the client** → `INFO` (client misuse is expected; it's not a warning for the operator).
- **An infrastructure error that was transparently retried** → `WARNING`.
- **An infrastructure error that failed permanently** → `ERROR`.
- **An unexpected exception caught by the servicer boundary** → `ERROR` with full stack trace and correlation id.
- **Orphan sweeper detects orphans** → `INFO` (this is the sweeper doing its job); the disposition action logs at its own level.
- **Email size exceeded** → `ERROR` (a failure that requires investigation).
- **Sweeper iteration itself fails** → `ERROR`; the sweeper continues and will retry next tick.

### Context propagation

The boundary of every inbound request (gRPC servicer method, FastAPI route handler) binds identifiers to the structlog context:

```python
from message_service.observability.logging_setup import (
    bind_request_context,
    clear_request_context,
)

async def BeginRun(self, request, context):
    correlation_id = uuid.uuid4().hex
    bind_request_context(
        correlation_id=correlation_id,
        pipeline_type=request.pipeline_type,
    )
    try:
        response = await self._begin_run_use_case.execute(request)
        bind_request_context(run_id=response.run_id)  # now known
        return response
    finally:
        clear_request_context()
```

Every log record emitted within the request automatically carries these fields — no manual threading through call chains.

### Sensitive field redaction

The logging pipeline runs a redaction processor that replaces values of sensitive keys with `<redacted>` before rendering. The keys are defined in `logging_setup.py`:

```
password, passwd, password_hash, pwd, secret, smtp_password,
session_token, cookie, authorization, email_body,
rendered_output, template_context
```

This is a **defence-in-depth** mechanism. Call sites should still avoid passing sensitive values, but the redaction catches slips.

To add a new sensitive field name, extend `_SENSITIVE_FIELD_NAMES` in `logging_setup.py` and add a test in `tests/unit/infrastructure/test_logging_setup.py` verifying the redaction.

### What not to log

- **Full email body content** — goes to the filesystem report store, not logs.
- **Full template context** — may contain arbitrary pipeline data, including customer data.
- **Session tokens and cookies** — use the user_id instead.
- **Passwords in any form** — never.
- **Full SMTP message bodies** — log the size and recipient count, not the content.
- **Stack traces for expected failures** — validation errors log a single INFO line, not a full trace.

### What to always log

- **run_id** — the single most useful identifier for correlating events across a run's lifetime.
- **stage_id** — whenever the event is stage-scoped.
- **correlation_id** — for the `INTERNAL` error path, and for any operator-facing event that might need support reconciliation.
- **pipeline_type** — for metric breakdowns.
- **outcome** — for operations that can succeed or fail (e.g., `outcome="sent"` vs `outcome="size_exceeded"`).
- **elapsed_ms** — for any operation that takes non-trivial time.

### Example: the complete happy path

```python
async def begin_run(cmd: BeginRunCommand) -> Run:
    logger.debug("begin_run_validating", pipeline_type=cmd.pipeline_type)
    _validate(cmd)  # may raise ValidationError
    run = Run.initiate(cmd)
    logger.debug("begin_run_persisting", run_id=run.id)
    await run_repo.insert(run)
    logger.info(
        "run_begun",
        run_id=run.id,
        pipeline_type=cmd.pipeline_type,
        declared_stage_count=len(cmd.declared_stages),
        attachment_mode=cmd.attachment_mode,
    )
    return run
```

That is all the logging the happy path needs. The DEBUG lines help during development and are quiet in production; the single INFO line captures the business event.

## Checklist for new code

When adding a new module, function, or use case, verify:

- [ ] Every raised exception derives from `MessageServiceError` or a subclass.
- [ ] Exception `details` dicts contain structured diagnostic data.
- [ ] No bare `except` or `except Exception` clauses (other than the servicer boundary).
- [ ] Every use case emits at least one INFO-level event at completion with the business-relevant fields.
- [ ] Error paths emit INFO (for client-visible rejections) or ERROR (for unexpected failures) — not WARNING for everything.
- [ ] Sensitive values (passwords, tokens, raw email bodies) are not passed to log calls.
- [ ] Long-running operations log `elapsed_ms`.
- [ ] State-transition events are logged at INFO with old and new state.
