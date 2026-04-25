"""Pydantic v2 configuration schema.

The schema is a tree of frozen models, one per TOML section, validated at
service startup by :mod:`message_service.config.loader`. Fields that
should accept ``${env:VAR}`` substitution are declared with the
:data:`SubstitutableStr` type alias rather than bare ``str`` (L3-CFG-014);
this explicit opt-in prevents accidental substitution in paths or
template strings that may legitimately contain dollar signs.

Design principles
-----------------
* Every model sets ``extra='forbid'`` so typos in config keys raise a
  validation error rather than silently being ignored (L3-CFG-006).
* Every model sets ``frozen=True`` so runtime mutation raises
  ``ValidationError`` (L3-CFG-016).
* Path fields are declared as ``Path``; the loader resolves relative
  paths against the config file's directory before validation
  (L3-CFG-010, L3-CFG-011).
* Constraints use Pydantic ``Field(...)`` with ``ge=`` / ``gt=`` / ``le=``
  rather than custom validators wherever possible; errors are more
  readable and the schema is self-documenting.

Requirement references
----------------------
L1-CFG-001, L1-CFG-002, L1-CFG-003
L2-CFG-002, L2-CFG-004, L2-CFG-005
L3-CFG-005, L3-CFG-006, L3-CFG-014, L3-CFG-016, L3-OBS-021
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# -----------------------------------------------------------------------------
# Type aliases
# -----------------------------------------------------------------------------

SubstitutableStr = Annotated[str, "substitutable"]
"""String type whose values pass through env-var substitution at load time.

Fields declared as :data:`SubstitutableStr` are scanned for the pattern
``${env:VAR_NAME}`` by the loader (L3-CFG-012). Any other ``str`` field
is treated literally. The marker is the string literal ``"substitutable"``
in the field's Pydantic metadata; :func:`loader._is_substitutable_field`
detects it.
"""

SUBSTITUTABLE_MARKER = "substitutable"
"""Literal marker appended by :data:`SubstitutableStr` to field metadata."""


# Canonical frozen-forbid base. Every config model in this module
# inherits its model_config from this class.
class _FrozenForbid(BaseModel):
    """Base class enforcing frozen + extra-forbid on every config model."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        # Make validation error messages more useful by keeping field paths
        # in JSON-Pointer-compatible form.
        populate_by_name=True,
    )


# -----------------------------------------------------------------------------
# Listener configs
# -----------------------------------------------------------------------------


class GrpcConfig(_FrozenForbid):
    """gRPC listener configuration (L2-API-007)."""

    host: str = Field(default="0.0.0.0", min_length=1)
    port: int = Field(ge=1, le=65_535)


class DashboardConfig(_FrozenForbid):
    """FastAPI dashboard listener configuration (L2-DASH-002)."""

    host: str = Field(default="0.0.0.0", min_length=1)
    port: int = Field(ge=1, le=65_535)


# -----------------------------------------------------------------------------
# Persistence
# -----------------------------------------------------------------------------


class FilesystemPersistenceConfig(_FrozenForbid):
    """Filesystem persistence for rendered reports (L1-PERS-002)."""

    report_directory: Path


class PersistenceConfig(_FrozenForbid):
    """SQLite persistence for metadata + in-flight state (L2-PERS-001)."""

    sqlite_path: Path
    connection_pool_size: int = Field(default=16, ge=1, le=256)
    filesystem: FilesystemPersistenceConfig


# -----------------------------------------------------------------------------
# Templates, tags, pipelines
# -----------------------------------------------------------------------------


class TemplateRefConfig(_FrozenForbid):
    """A ``(name, version)`` reference to a template declared in the manifest.

    Serialization-shape peer of
    :class:`message_service.domain.aggregates.template_ref.TemplateRef` —
    kept separate so the domain value object stays free of config
    framework dependencies. The config loader translates this into a
    :class:`TemplateRef` before handing to use cases.
    """

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class TemplatesConfig(_FrozenForbid):
    """Template manifest, size limits, and shared template refs.

    Requirement references
    ----------------------
    L2-TMPL-001, L2-TMPL-014

    Attributes:
        manifest_path: Path to the template manifest TOML.
        max_context_bytes: Reject contexts larger than this (L3-STAGE-014).
        max_rendered_bytes: Reject rendered output larger than this
            (L3-TMPL-028).
        email_body_template_ref: The template used to render the email
            body for every finalized run. Fixed service-wide in v1
            (ROADMAP: per-pipeline in a future increment; see
            ``docs/ROADMAP.md``).
    """

    manifest_path: Path
    max_context_bytes: int = Field(default=1_048_576, ge=1_024)
    max_rendered_bytes: int = Field(default=10_485_760, ge=1_024)
    email_body_template_ref: TemplateRefConfig


class TagsConfig(_FrozenForbid):
    """Controlled tag vocabulary location (L2-SUB-006)."""

    vocabulary_path: Path


class PipelinesConfig(_FrozenForbid):
    """Registry of known pipeline_type values (L2-RUN-007).

    BeginRun requests with a pipeline_type not in this list SHALL be
    rejected with ``UnknownPipelineTypeError``.
    """

    registered: list[str] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# Sweeper
# -----------------------------------------------------------------------------


DispositionAction = Literal[
    "SEND_PARTIAL_FLAGGED",
    "DISCARD_SILENTLY",
    "NOTIFY_SUBSCRIBERS",
    "NOTIFY_ADMINS",
]


class SweeperConfig(_FrozenForbid):
    """Orphan sweeper timing and disposition policy (L1-SWEEP-002, L2-SWEEP-007)."""

    run_timeout_seconds: int = Field(default=3_600, ge=1)
    poll_interval_seconds: int = Field(default=60, ge=1)
    # L2-SWEEP-010 / L3-SWEEP-008: per-tick cap so a backlog can't
    # monopolize the shared SQLite connection. Default 1000 mirrors
    # the L3-SWEEP-008 spec.
    max_candidates_per_iteration: int = Field(default=1_000, ge=1)
    # L3-SWEEP-020: stuck-claim recovery threshold. Rows whose
    # claim is older than this AND completed_at is NULL get
    # re-claimed, bumping attempts.
    stale_claim_threshold_seconds: int = Field(default=300, ge=1)
    # L3-SWEEP-021: cap on stuck-claim retries. After this many
    # attempts a row is abandoned (audited + completed_at set).
    max_dispatch_attempts: int = Field(default=3, ge=1)
    disposition_actions: list[DispositionAction] = Field(
        # The default SHALL only reference action ids that bootstrap actually
        # registers a handler for; otherwise a service started with the
        # shipped default would fail at first orphan. The two deferred
        # actions (SEND_PARTIAL_FLAGGED, NOTIFY_SUBSCRIBERS) remain valid
        # identifiers in the Literal above so that operators can opt in
        # later without a schema change, but referencing them today raises
        # ConfigurationError at startup (see SweeperUseCase.__init__).
        #
        # Empty lists are permitted per L3-SWEEP-011: orphaned runs receive
        # no action beyond the state transition (equivalent to a single
        # DISCARD_SILENTLY action). The SweeperUseCase tolerates the empty
        # tuple natively — it iterates configured actions per orphan.
        default_factory=lambda: ["NOTIFY_ADMINS", "DISCARD_SILENTLY"],  # type: ignore[arg-type]
    )


# -----------------------------------------------------------------------------
# Mail
# -----------------------------------------------------------------------------


class SmtpConfig(_FrozenForbid):
    """SMTP connection parameters (L2-MAIL-002)."""

    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65_535)
    username: SubstitutableStr = ""
    password: SubstitutableStr = ""
    use_starttls: bool = True


class MailRetryConfig(_FrozenForbid):
    """Exponential-backoff parameters for SMTP retries (L2-MAIL-006)."""

    max_retries: int = Field(default=5, ge=0, le=20)
    initial_interval_seconds: int = Field(default=2, ge=1)
    max_interval_seconds: int = Field(default=300, ge=1)


class MailConfig(_FrozenForbid):
    """Email delivery configuration.

    ``from_address`` and ``admin_recipients`` are validated as email
    addresses by Pydantic's :class:`EmailStr`.
    """

    from_address: EmailStr
    max_email_size_bytes: int = Field(default=26_214_400, ge=1_024)
    admin_recipients: list[EmailStr] = Field(default_factory=list)
    smtp: SmtpConfig
    retry: MailRetryConfig = Field(default_factory=MailRetryConfig)


# -----------------------------------------------------------------------------
# Auth
# -----------------------------------------------------------------------------


class Argon2Config(_FrozenForbid):
    """Argon2id KDF parameters (L2-AUTH-002)."""

    memory_cost: int = Field(default=65_536, ge=8)
    time_cost: int = Field(default=3, ge=1)
    parallelism: int = Field(default=4, ge=1)


class AuthConfig(_FrozenForbid):
    """Authentication parameters (L1-AUTH-002)."""

    session_idle_timeout_seconds: int = Field(default=3_600, ge=60)
    argon2: Argon2Config = Field(default_factory=Argon2Config)


# -----------------------------------------------------------------------------
# Observability
# -----------------------------------------------------------------------------


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
"""Permitted log levels (L3-OBS-021)."""


class AuditConfig(_FrozenForbid):
    """Audit log retention (L1-OBS-003)."""

    retention_days: int = Field(default=365, ge=1)


class ObservabilityConfig(_FrozenForbid):
    """Observability configuration (L1-OBS-004, L2-OBS-011)."""

    log_level: LogLevel = "INFO"
    audit: AuditConfig = Field(default_factory=AuditConfig)


# -----------------------------------------------------------------------------
# Service
# -----------------------------------------------------------------------------


class ServiceConfig(_FrozenForbid):
    """Process-level service parameters (L2-DEP-006)."""

    shutdown_grace_period_seconds: int = Field(default=30, ge=1, le=600)


# -----------------------------------------------------------------------------
# Top-level
# -----------------------------------------------------------------------------


class Config(_FrozenForbid):
    """Top-level configuration schema.

    Loaded from TOML by :func:`message_service.config.loader.load_config`.
    Composition mirrors the TOML section structure 1:1.
    """

    grpc: GrpcConfig
    dashboard: DashboardConfig
    persistence: PersistenceConfig
    templates: TemplatesConfig
    tags: TagsConfig
    sweeper: SweeperConfig = Field(default_factory=SweeperConfig)
    mail: MailConfig
    auth: AuthConfig = Field(default_factory=AuthConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    service: ServiceConfig = Field(default_factory=ServiceConfig)
    pipelines: PipelinesConfig = Field(default_factory=PipelinesConfig)


__all__ = [
    "SUBSTITUTABLE_MARKER",
    "Argon2Config",
    "AuditConfig",
    "AuthConfig",
    "Config",
    "DashboardConfig",
    "DispositionAction",
    "FilesystemPersistenceConfig",
    "GrpcConfig",
    "LogLevel",
    "MailConfig",
    "MailRetryConfig",
    "ObservabilityConfig",
    "PersistenceConfig",
    "PipelinesConfig",
    "ServiceConfig",
    "SmtpConfig",
    "SubstitutableStr",
    "SweeperConfig",
    "TagsConfig",
    "TemplatesConfig",
]
