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

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

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
    """gRPC listener configuration (L2-API-007).

    ``max_concurrent_rpcs`` caps in-flight RPCs at the server level
    per L3-API-001; excess requests are queued by gRPC's internal
    scheduler. Default 100 is generous for the v1 single-tenant ETL
    workload while still bounding pathological burst behavior.

    ``max_in_flight_rpcs`` is the *rejecting* concurrency limit
    (L3-API-019): ``0`` (default) disables it and any positive value
    ``N`` caps concurrently-executing RPCs at ``N``, rejecting excess
    with ``RESOURCE_EXHAUSTED`` rather than queuing it. This is
    orthogonal to ``max_concurrent_rpcs`` (which only queues) and is
    off by default so existing deployments are unaffected.
    """

    host: str = Field(default="0.0.0.0", min_length=1)
    port: int = Field(default=50_051, ge=1, le=65_535)
    max_concurrent_rpcs: int = Field(default=100, ge=1)
    max_in_flight_rpcs: int = Field(default=0, ge=0)


class DashboardConfig(_FrozenForbid):
    """FastAPI dashboard listener configuration (L2-DASH-002).

    ``https_only`` controls the ``Secure`` attribute on the session
    cookie per L3-AUTH-009; default ``True`` for production
    deployments behind TLS termination, override to ``False`` for
    local development over plaintext HTTP.
    """

    host: str = Field(default="0.0.0.0", min_length=1)
    port: int = Field(default=8080, ge=1, le=65_535)
    https_only: bool = Field(default=True)


# -----------------------------------------------------------------------------
# Persistence
# -----------------------------------------------------------------------------


class FilesystemPersistenceConfig(_FrozenForbid):
    """Filesystem persistence for rendered reports (L1-PERS-002).

    The retention knobs (``report_retention_days``,
    ``prune_interval_seconds``, ``max_prunes_per_iteration``) drive
    the rendered-report retention pruner specified by L1-PERS-004
    and the L2-PERS-011 / L2-PERS-012 / L2-PERS-013 derivations.
    Defaults (90 days / 1 day cadence / 1000 files per tick) match
    the L3-PERS-027/029 constraints.
    """

    report_directory: Path
    report_retention_days: int = Field(default=90, ge=1)
    prune_interval_seconds: int = Field(default=86_400, ge=1)
    max_prunes_per_iteration: int = Field(default=1_000, ge=1)


class PersistenceConfig(_FrozenForbid):
    """SQLite persistence for metadata + in-flight state (L2-PERS-001)."""

    sqlite_path: Path
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
            body for every finalized run. Fixed service-wide in v1.
    """

    manifest_path: Path
    max_context_bytes: int = Field(default=1_048_576, ge=1_024)
    max_rendered_bytes: int = Field(default=10_485_760, ge=1_024)
    email_body_template_ref: TemplateRefConfig


class TagsConfig(_FrozenForbid):
    """Controlled tag vocabulary location (L2-SUB-006)."""

    vocabulary_path: Path


DispositionAction = Literal[
    "SEND_PARTIAL_FLAGGED",
    "DISCARD_SILENTLY",
    "NOTIFY_SUBSCRIBERS",
    "NOTIFY_ADMINS",
]
"""Permitted orphan-disposition action identifiers (L1-SWEEP-003).

Defined here (above :class:`PipelinesConfig`) because both the global
``sweeper.disposition_actions`` and the per-pipeline
``pipelines.orphan_disposition_overrides`` reference it.
"""


class PipelinesConfig(_FrozenForbid):
    """Registry of known pipeline_type values (L2-RUN-007).

    BeginRun requests with a pipeline_type not in this list SHALL be
    rejected with ``UnknownPipelineTypeError``.

    Attributes:
        registered: The allowed ``pipeline_type`` values.
        subject_templates: Optional per-pipeline email-subject override
            (L2-MAIL-014 / L3-MAIL-032). Maps a registered
            ``pipeline_type`` to a ``str.format`` template that may
            reference only ``{pipeline_type}`` and ``{run_id}``. Pipelines
            without an entry use the default ``[{pipeline_type}] run
            {run_id}`` format, so an empty mapping (the default) preserves
            v1 behavior exactly.
        email_body_template_overrides: Optional per-pipeline email-body
            template override (L2-TMPL-015 / L3-TMPL-033). Maps a
            registered ``pipeline_type`` to a ``(name, version)`` template
            reference; the referenced template must exist in the manifest
            (validated at startup, L3-TMPL-034). Pipelines without an entry
            render the email body from the service-wide
            ``templates.email_body_template_ref``, so an empty mapping (the
            default) preserves the single-template behavior exactly.
        orphan_disposition_overrides: Optional per-pipeline orphan
            disposition policy override (L2-SWEEP-011 / L3-SWEEP-022). Maps a
            registered ``pipeline_type`` to an ordered list of
            ``DispositionAction`` identifiers applied by the sweeper when a
            run of that pipeline orphans; pipelines without an entry use the
            global ``sweeper.disposition_actions``. An empty list means
            "orphan but take no action". Handler registration for each action
            is validated at startup (L3-SWEEP-024).
    """

    registered: list[str] = Field(default_factory=list)
    subject_templates: dict[str, str] = Field(default_factory=dict)
    email_body_template_overrides: dict[str, TemplateRefConfig] = Field(default_factory=dict)
    orphan_disposition_overrides: dict[str, list[DispositionAction]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_subject_templates(self) -> PipelinesConfig:
        """Validate the per-pipeline subject templates at load time (L3-MAIL-033).

        Returns:
            The validated model instance.

        Raises:
            ValueError: A template targets an unregistered pipeline_type,
                references a placeholder other than ``{pipeline_type}`` /
                ``{run_id}`` (or is otherwise malformed for ``str.format``),
                or contains a raw CR/LF. Pydantic surfaces this as a schema
                ``ValidationError`` per L3-CFG-006.
        """
        registered = set(self.registered)
        for pipeline_type, template in self.subject_templates.items():
            if pipeline_type not in registered:
                raise ValueError(
                    f"subject_templates key {pipeline_type!r} is not a registered "
                    f"pipeline_type; a template for an unregistered pipeline can never fire"
                )
            if "\r" in template or "\n" in template:
                raise ValueError(
                    f"subject_templates[{pipeline_type!r}] must not contain CR or LF characters"
                )
            try:
                template.format(pipeline_type="", run_id="")
            except (KeyError, IndexError, ValueError) as exc:
                raise ValueError(
                    f"subject_templates[{pipeline_type!r}] is not a valid subject template "
                    f"(only {{pipeline_type}} and {{run_id}} placeholders are allowed): {exc}"
                ) from exc
        return self

    @model_validator(mode="after")
    def _validate_body_template_override_keys(self) -> PipelinesConfig:
        """Validate per-pipeline body-template override keys at load time (L3-TMPL-033).

        Manifest existence of each referenced template is validated
        separately at startup (L3-TMPL-034), where the loaded manifest is
        available; this load-time check only rejects overrides keyed on a
        non-registered ``pipeline_type``.

        Returns:
            The validated model instance.

        Raises:
            ValueError: An override key is not a member of ``registered``.
                Pydantic surfaces this as a schema ``ValidationError`` per
                L3-CFG-006.
        """
        registered = set(self.registered)
        for pipeline_type in self.email_body_template_overrides:
            if pipeline_type not in registered:
                raise ValueError(
                    f"email_body_template_overrides key {pipeline_type!r} is not a registered "
                    f"pipeline_type; an override for an unregistered pipeline can never fire"
                )
        return self

    @model_validator(mode="after")
    def _validate_orphan_disposition_override_keys(self) -> PipelinesConfig:
        """Validate per-pipeline orphan-disposition override keys at load (L3-SWEEP-022).

        Handler registration for each override action is validated separately
        at sweeper construction (L3-SWEEP-024); the ``DispositionAction``
        ``Literal`` rejects unknown identifiers at parse time. This check only
        rejects overrides keyed on a non-registered ``pipeline_type``.

        Returns:
            The validated model instance.

        Raises:
            ValueError: An override key is not a member of ``registered``.
                Pydantic surfaces this as a schema ``ValidationError`` per
                L3-CFG-006.
        """
        registered = set(self.registered)
        for pipeline_type in self.orphan_disposition_overrides:
            if pipeline_type not in registered:
                raise ValueError(
                    f"orphan_disposition_overrides key {pipeline_type!r} is not a registered "
                    f"pipeline_type; an override for an unregistered pipeline can never fire"
                )
        return self


# -----------------------------------------------------------------------------
# Sweeper
# -----------------------------------------------------------------------------


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
    """Argon2id KDF parameters (L2-AUTH-002 / L3-AUTH-002)."""

    memory_cost: int = Field(default=65_536, ge=8)
    time_cost: int = Field(default=3, ge=1)
    parallelism: int = Field(default=4, ge=1)
    hash_len: int = Field(default=32, ge=16)
    salt_len: int = Field(default=16, ge=8)


class AdminAccountConfig(_FrozenForbid):
    """Configurable local administrator account (L2-AUTH-010).

    Provisioned at startup by the composition root (L2-AUTH-011). ``password``
    is substitutable from the environment like the SMTP credentials, so the
    secret stays out of the committed config file; an empty password (after
    substitution) is rejected at load time.
    """

    email: EmailStr
    password: SubstitutableStr

    @model_validator(mode="after")
    def _password_non_empty(self) -> AdminAccountConfig:
        """Reject an empty/whitespace password (L3-AUTH-018)."""
        if not self.password.strip():
            raise ValueError("auth.admin.password must not be empty")
        return self


class AuthConfig(_FrozenForbid):
    """Authentication parameters (L1-AUTH-002)."""

    session_idle_timeout_seconds: int = Field(default=3_600, ge=60)
    argon2: Argon2Config = Field(default_factory=Argon2Config)
    admin: AdminAccountConfig | None = Field(default=None)


# -----------------------------------------------------------------------------
# Observability
# -----------------------------------------------------------------------------


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
"""Permitted log levels (L3-OBS-021)."""


class AuditConfig(_FrozenForbid):
    """Audit log retention (L1-OBS-003).

    The retention pruner consumes ``retention_days`` (rows older than
    ``now - retention_days`` are deleted), ``cleanup_interval_hours``
    (poll cadence — defaults to 24h per L3-OBS-014), and
    ``cleanup_batch_size`` (per-tick delete-batch ceiling per
    L3-OBS-016, default 10000 rows to avoid long-running deletes
    blocking other writers on the shared SQLite connection).
    """

    retention_days: int = Field(default=365, ge=1)
    cleanup_interval_hours: int = Field(default=24, ge=1)
    cleanup_batch_size: int = Field(default=10_000, ge=100, le=1_000_000)
    # L2-OBS-019 / L3-OBS-041: optional archive location. When set, the pruner
    # archives expired rows before deleting them; validated writable at startup.
    # Unset (the default) means delete without archiving (prior behavior).
    archive_directory: Path | None = None


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

    @model_validator(mode="after")
    def _check_listener_ports_distinct(self) -> Config:
        """L3-DASH-004: ``dashboard.port`` and ``grpc.port`` SHALL differ.

        Raised at load time so misconfiguration fails fast at startup
        rather than producing a confusing bind-error at one of the two
        servers.
        """
        if self.dashboard.port == self.grpc.port:
            raise ValueError(
                f"dashboard.port ({self.dashboard.port}) must differ from "
                f"grpc.port ({self.grpc.port}); both listeners cannot share "
                "a port",
            )
        return self


__all__ = [
    "SUBSTITUTABLE_MARKER",
    "AdminAccountConfig",
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
