"""Tests for the configurable-local-admin startup reconciliation (L3-AUTH-019).

Covers the pure reconciliation step (`_reconcile_admin_account`) — create when
absent, re-assert privilege/enabled without touching the password when present,
and idempotence — plus the composition-root wiring (`build_service` invokes it
only when `[auth.admin]` is configured).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
from tests.fixtures.clocks import FakeClock

from message_service.bootstrap import build_service, shutdown_service
from message_service.bootstrap.service import _reconcile_admin_account
from message_service.config.loader import load_config
from message_service.config.schema import AdminAccountConfig
from message_service.domain.aggregates.password import Password
from message_service.domain.aggregates.user import User
from message_service.infrastructure.auth.argon2_hasher import Argon2PasswordHasher
from message_service.infrastructure.persistence.audit_log import SqliteAuditLog
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations
from message_service.infrastructure.persistence.run_repository import SqliteRunRepository
from message_service.infrastructure.persistence.session_repository import SqliteSessionRepository
from message_service.infrastructure.persistence.stage_repository import SqliteStageRepository
from message_service.infrastructure.persistence.subscription_repository import (
    SqliteSubscriptionRepository,
)
from message_service.infrastructure.persistence.sweeper_action_repository import (
    SqliteSweeperActionRepository,
)
from message_service.infrastructure.persistence.unit_of_work import SqliteUnitOfWorkFactory
from message_service.infrastructure.persistence.user_repository import SqliteUserRepository

pytestmark = pytest.mark.allow_io

_T0 = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def sqlite_conn() -> AsyncIterator[aiosqlite.Connection]:
    c = await open_connection(Path(":memory:"))
    try:
        await apply_migrations(c)
        yield c
    finally:
        await c.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(_T0)


@pytest.fixture
def hasher() -> Argon2PasswordHasher:
    return Argon2PasswordHasher(memory_cost=8, time_cost=1, parallelism=1, hash_len=16, salt_len=8)


@pytest.fixture
def uow_factory(sqlite_conn: aiosqlite.Connection, clock: FakeClock) -> SqliteUnitOfWorkFactory:
    return SqliteUnitOfWorkFactory(
        conn=sqlite_conn,
        run_repo_factory=lambda c: SqliteRunRepository(c),
        stage_repo_factory=lambda c: SqliteStageRepository(c),
        subscription_repo_factory=lambda c: SqliteSubscriptionRepository(c, clock=clock),
        audit_log_factory=lambda c: SqliteAuditLog(c),
        sweeper_action_repo_factory=lambda c: SqliteSweeperActionRepository(c),
        user_repo_factory=lambda c: SqliteUserRepository(c),
        session_repo_factory=lambda c: SqliteSessionRepository(c),
    )


def _admin(email: str = "admin@example.com", password: str = "s3cret-pw") -> AdminAccountConfig:
    return AdminAccountConfig(email=email, password=password)


# -----------------------------------------------------------------------------
# _reconcile_admin_account — L3-AUTH-019
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-019")
async def test_reconcile_creates_admin_when_absent(
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    clock: FakeClock,
) -> None:
    """Absent account → created with admin privilege, enabled, hashed password."""
    admin = _admin()
    await _reconcile_admin_account(
        admin, uow_factory=uow_factory, password_hasher=hasher, clock=clock
    )

    async with uow_factory() as uow:
        user = await uow.user_repo.get_by_email(admin.email)
    assert user is not None
    assert user.is_admin is True
    assert user.disabled is False
    assert user.display_name == admin.email
    # Password is stored hashed and verifies against the configured secret.
    assert user.password_hash != admin.password
    assert hasher.verify(Password(admin.password), user.password_hash) is True


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-019")
async def test_reconcile_reasserts_without_touching_password(
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    clock: FakeClock,
) -> None:
    """Existing account → privilege/enabled restored, stored password preserved."""
    admin = _admin()
    # Pre-seed a de-privileged, disabled account whose password was "rotated".
    rotated_hash = hasher.hash(Password("rotated-different-pw"))
    async with uow_factory() as uow:
        await uow.user_repo.save(
            User(
                email=admin.email,
                display_name="Rotated Admin",
                password_hash=rotated_hash,
                created_at=_T0,
                is_admin=False,
                disabled=True,
            )
        )
        await uow.commit()

    await _reconcile_admin_account(
        admin, uow_factory=uow_factory, password_hasher=hasher, clock=clock
    )

    async with uow_factory() as uow:
        user = await uow.user_repo.get_by_email(admin.email)
    assert user is not None
    assert user.is_admin is True  # re-asserted
    assert user.disabled is False  # re-enabled
    assert user.password_hash == rotated_hash  # NOT overwritten by the config value
    assert hasher.verify(Password("rotated-different-pw"), user.password_hash) is True
    assert hasher.verify(Password(admin.password), user.password_hash) is False
    assert user.display_name == "Rotated Admin"  # not clobbered either


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-019")
async def test_reconcile_is_idempotent(
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    clock: FakeClock,
) -> None:
    """Running twice leaves a single, stable, privileged, enabled account."""
    admin = _admin()
    await _reconcile_admin_account(
        admin, uow_factory=uow_factory, password_hasher=hasher, clock=clock
    )
    async with uow_factory() as uow:
        first = await uow.user_repo.get_by_email(admin.email)
    assert first is not None

    await _reconcile_admin_account(
        admin, uow_factory=uow_factory, password_hasher=hasher, clock=clock
    )
    async with uow_factory() as uow:
        second = await uow.user_repo.get_by_email(admin.email)
    assert second is not None
    assert second.user_id == first.user_id  # no duplicate row
    assert second.is_admin is True
    assert second.disabled is False
    assert second.password_hash == first.password_hash  # untouched on the re-assert pass


# -----------------------------------------------------------------------------
# build_service wiring — L3-AUTH-019
# -----------------------------------------------------------------------------


def _write_config(tmp_path: Path, *, admin_section: str = "") -> Path:
    (tmp_path / "body.html.j2").write_text("<p>{{ run_id }}</p>")
    (tmp_path / "frag.html.j2").write_text("<p>{{ v }}</p>")
    (tmp_path / "templates.toml").write_text(
        '[[template]]\nname = "email_body"\nversion = "1.0"\nkind = "EMAIL_BODY"\n'
        'source_path = "body.html.j2"\n\n'
        '[[template]]\nname = "frag"\nversion = "1.0"\nkind = "REPORT_FRAGMENT"\n'
        'source_path = "frag.html.j2"\n'
    )
    (tmp_path / "tags.toml").write_text('[[tag]]\nname = "production"\n')
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[grpc]
host = "0.0.0.0"
port = 50051

[dashboard]
host = "0.0.0.0"
port = 8080

[persistence]
sqlite_path = "{(tmp_path / "svc.db").as_posix()}"

[persistence.filesystem]
report_directory = "{(tmp_path / "reports").as_posix()}"

[templates]
manifest_path = "{(tmp_path / "templates.toml").as_posix()}"
max_context_bytes = 524288
max_rendered_bytes = 5242880

[templates.email_body_template_ref]
name = "email_body"
version = "1.0"

[tags]
vocabulary_path = "{(tmp_path / "tags.toml").as_posix()}"

[pipelines]
registered = ["etl-nightly"]

[mail]
from_address = "svc@example.com"
max_email_size_bytes = 10485760

[mail.smtp]
host = "smtp.example.com"
port = 587
use_starttls = true

[mail.retry]
max_retries = 3
initial_interval_seconds = 1
max_interval_seconds = 60
{admin_section}"""
    )
    return config_path


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-019")
async def test_build_service_provisions_configured_admin(tmp_path: Path) -> None:
    """`build_service` with `[auth.admin]` leaves a usable admin account."""
    admin_section = '\n[auth.admin]\nemail = "boot@example.com"\npassword = "boot-pw-123"\n'
    config = load_config(_write_config(tmp_path, admin_section=admin_section))
    svc = await build_service(config)
    try:
        async with svc.uow_factory() as uow:
            user = await uow.user_repo.get_by_email("boot@example.com")
        assert user is not None
        assert user.is_admin is True
        assert user.disabled is False
        assert svc.password_hasher.verify(Password("boot-pw-123"), user.password_hash) is True
    finally:
        await shutdown_service(svc, timeout=1.0)


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-019")
async def test_build_service_without_admin_section_provisions_nobody(tmp_path: Path) -> None:
    """With no `[auth.admin]` section, startup creates no admin (backward compatible)."""
    config = load_config(_write_config(tmp_path))
    assert config.auth.admin is None
    svc = await build_service(config)
    try:
        async with svc.uow_factory() as uow:
            user = await uow.user_repo.get_by_email("boot@example.com")
        assert user is None
    finally:
        await shutdown_service(svc, timeout=1.0)
