"""End-to-end sweeper integration test.

Exercises the full stack:

* :func:`build_service` constructs every adapter including the
  sweeper use case and its loop.
* A run is seeded directly into the SQLite via the UoW with
  ``updated_at`` old enough to be considered expired.
* ``service.sweeper.tick()`` is called directly (not via the loop)
  so the test is deterministic — no ``asyncio.sleep`` wait for the
  real poll interval.
* Assertions on the final state: run is ``ORPHANED`` and a
  ``SWEEP_ORPHAN`` audit event was recorded.

We call ``tick()`` directly rather than starting the loop because
the loop is the infrastructure layer's concern; its own unit tests
cover start/stop/interval behavior. Integration is about the
ports-and-adapters composition.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from message_service.bootstrap import Service, build_service, shutdown_service
from message_service.config.loader import load_config
from message_service.domain.aggregates.audit_event import AuditAction
from message_service.domain.aggregates.declared_stage import DeclaredStage
from message_service.domain.aggregates.run import AttachmentMode, Run
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.run_states import RunState

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


def _write_config(tmp_path: Path) -> Path:
    """Minimal valid Config TOML with a short sweeper timeout."""
    (tmp_path / "body.html.j2").write_text("<p>{{ run_id }}</p>", encoding="utf-8")
    (tmp_path / "frag.html.j2").write_text("<p>{{ v }}</p>", encoding="utf-8")
    (tmp_path / "templates.toml").write_text(
        """
[[template]]
name = "email_body"
version = "1.0"
kind = "EMAIL_BODY"
source_path = "body.html.j2"

[[template]]
name = "frag"
version = "1.0"
kind = "REPORT_FRAGMENT"
source_path = "frag.html.j2"
""",
        encoding="utf-8",
    )
    (tmp_path / "tags.toml").write_text('[[tag]]\nname = "production"\n', encoding="utf-8")

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f"""
[grpc]
host = "127.0.0.1"
port = 50051

[dashboard]
host = "127.0.0.1"
port = 8080

[persistence]
sqlite_path = "{(tmp_path / "svc.db").as_posix()}"

[persistence.filesystem]
report_directory = "{(tmp_path / "reports").as_posix()}"

[templates]
manifest_path = "{(tmp_path / "templates.toml").as_posix()}"

[templates.email_body_template_ref]
name = "email_body"
version = "1.0"

[tags]
vocabulary_path = "{(tmp_path / "tags.toml").as_posix()}"

[pipelines]
registered = ["etl-nightly"]

[mail]
from_address = "svc@example.com"

[mail.smtp]
host = "smtp.example.com"
port = 587
username = "u"
password = "p"

[sweeper]
# 60 second grace window -- well under any test-created staleness.
run_timeout_seconds = 60
poll_interval_seconds = 3600  # loop won't tick during the test
disposition_actions = ["NOTIFY_ADMINS", "DISCARD_SILENTLY"]
""",
        encoding="utf-8",
    )
    return cfg_path


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[Service]:
    """Fully-composed service with sweeper wired, loop NOT started."""
    cfg_path = _write_config(tmp_path)
    config = load_config(cfg_path)
    svc = await build_service(config)
    try:
        yield svc
    finally:
        await shutdown_service(svc, timeout=2.0)


def _make_stale_run(*, run_id: str, staleness_seconds: int) -> Run:
    """Build a run whose last transition is older than the test's timeout."""
    now = datetime.now(UTC)
    updated = now - timedelta(seconds=staleness_seconds)
    return Run(
        run_id=RunId(run_id),
        pipeline_type="etl-nightly",
        tags=frozenset({"production"}),
        declared_stages=(
            DeclaredStage(
                stage_id=StageId("extract"),
                stage_order=0,
                report_template_ref=TemplateRef(name="frag", version="1.0"),
            ),
        ),
        state=RunState.AGGREGATING,
        attachment_mode=AttachmentMode.PER_STAGE,
        aggregation_template_ref=None,
        subscription_predicate_tags=frozenset({"production"}),
        created_at=updated,
        updated_at=updated,
    )


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-SWEEP-001")
async def test_stale_run_is_swept_to_orphaned(service: Service) -> None:
    """A run whose last transition exceeds run_timeout SHALL be transitioned
    to ORPHANED when the sweeper ticks."""
    # Seed a run 5 minutes stale; the configured timeout is 60s.
    stale = _make_stale_run(
        run_id="00000000-0000-4000-8000-000000000001",
        staleness_seconds=300,
    )
    async with service.uow_factory() as uow:
        await uow.run_repo.save(stale)

    result = await service.sweeper.tick()
    assert result.orphaned_count == 1

    async with service.uow_factory() as uow:
        reloaded = await uow.run_repo.get(stale.run_id)
    assert reloaded.state is RunState.ORPHANED


@pytest.mark.asyncio
@pytest.mark.requirement("L2-SWEEP-006")
async def test_sweep_orphan_audit_event_recorded(service: Service) -> None:
    stale = _make_stale_run(
        run_id="00000000-0000-4000-8000-000000000002",
        staleness_seconds=300,
    )
    async with service.uow_factory() as uow:
        await uow.run_repo.save(stale)

    await service.sweeper.tick()

    async with service.uow_factory() as uow:
        events = await uow.audit_log.query(action=AuditAction.SWEEP_ORPHAN)
    assert len(events) >= 1
    # Find the one for our run.
    match = next(e for e in events if e.resource == f"run:{stale.run_id}")
    assert match.actor == "system:sweeper"
    assert match.details["prior_state"] == "AGGREGATING"
    assert match.details["new_state"] == "ORPHANED"


@pytest.mark.asyncio
async def test_fresh_run_is_not_swept(service: Service) -> None:
    """A run that just transitioned SHALL NOT be swept regardless of how
    old ``created_at`` is."""

    # A run created 10 minutes ago but with a 5-second-old transition.
    now = datetime.now(UTC)
    fresh = Run(
        run_id=RunId("00000000-0000-4000-8000-0000000000bb"),
        pipeline_type="etl-nightly",
        tags=frozenset({"production"}),
        declared_stages=(
            DeclaredStage(
                stage_id=StageId("extract"),
                stage_order=0,
                report_template_ref=TemplateRef(name="frag", version="1.0"),
            ),
        ),
        state=RunState.AGGREGATING,
        attachment_mode=AttachmentMode.PER_STAGE,
        aggregation_template_ref=None,
        subscription_predicate_tags=frozenset({"production"}),
        created_at=now - timedelta(minutes=10),
        updated_at=now - timedelta(seconds=5),
    )
    async with service.uow_factory() as uow:
        await uow.run_repo.save(fresh)

    result = await service.sweeper.tick()
    assert result.orphaned_count == 0

    async with service.uow_factory() as uow:
        reloaded = await uow.run_repo.get(fresh.run_id)
    assert reloaded.state is RunState.AGGREGATING


@pytest.mark.asyncio
@pytest.mark.requirement("L2-SWEEP-009")
async def test_disposition_actions_enqueued_in_config_order(
    service: Service,
) -> None:
    """Orphan sweeping SHALL enqueue one ``sweeper_actions`` row per
    configured action, in configured order. Handler invocation is the
    dispatcher's job (14b.3) and is not asserted here."""
    stale = _make_stale_run(
        run_id="00000000-0000-4000-8000-0000000000cc",
        staleness_seconds=300,
    )
    async with service.uow_factory() as uow:
        await uow.run_repo.save(stale)

    result = await service.sweeper.tick()

    # Config has ["NOTIFY_ADMINS", "DISCARD_SILENTLY"] — both are
    # registered, so both should enqueue cleanly.
    assert result.orphaned_count == 1
    assert result.enqueued_actions == 2

    # Verify the outbox shape: one pending row per action, in order.
    # A fresh aiosqlite connection avoids reaching into the UoW's
    # private connection attribute.
    side_conn = await aiosqlite.connect(service.config.persistence.sqlite_path)
    try:
        async with side_conn.execute(
            "SELECT action_name, claimed_at, completed_at "
            "FROM sweeper_actions WHERE run_id = ? ORDER BY action_id",
            (str(stale.run_id),),
        ) as cur:
            rows = await cur.fetchall()
    finally:
        await side_conn.close()
    assert [r[0] for r in rows] == ["NOTIFY_ADMINS", "DISCARD_SILENTLY"]
    for _, claimed_at, completed_at in rows:
        assert claimed_at is None
        assert completed_at is None
