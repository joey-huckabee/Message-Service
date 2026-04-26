"""End-to-end resend: full happy path → admin POST /runs/{id}/resend → second email.

Exercises the L1-DASH-003 "trigger manual resends" clause against
a fully-running service:

* Drive the happy path via gRPC to get a SENT run.
* Authenticate as an admin via the dashboard.
* POST ``/runs/{run_id}/resend`` and assert a second SMTP envelope
  is captured.
* Assert a ``RESEND_REPORT`` audit row is written alongside the
  original ``SEND_REPORT``.

Requirement references
----------------------
L1-DASH-003 (manual resend clause)
L2-DASH-008 (resend re-resolves recipients)
L3-DASH-013 (RESEND_REPORT audit format)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from google.protobuf.struct_pb2 import Struct
from message_service_proto.v1 import message_service_pb2 as pb

from message_service.application.ports.password_hasher import PasswordHasher
from message_service.domain.aggregates.audit_event import AuditAction
from message_service.domain.aggregates.password import Password
from message_service.domain.aggregates.subscription import SubscriptionGranularity
from message_service.domain.aggregates.user import User
from message_service.domain.ids import UserId
from tests.fixtures.service import RunningService

_T0 = datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC)


async def _seed_admin_and_subscriber(
    handle: RunningService,
    *,
    admin_email: str = "admin@example.com",
    admin_password: str = "hunter2",
    subscriber_email: str = "alice@example.com",
) -> None:
    """Insert one admin user (real Argon2 hash) + one subscriber + a GLOBAL sub."""
    hasher: PasswordHasher = handle.service.password_hasher
    admin_hash = hasher.hash(Password(admin_password))
    async with handle.service.uow_factory() as uow:
        await uow.user_repo.save(
            User(
                email=admin_email,
                display_name="admin",
                password_hash=admin_hash,
                created_at=_T0,
                disabled=False,
                is_admin=True,
            ),
        )
        subscriber = await uow.user_repo.save(
            User(
                email=subscriber_email,
                display_name="alice",
                password_hash="$argon2id$irrelevant",
                created_at=_T0,
                disabled=False,
                is_admin=False,
            ),
        )
        assert subscriber.user_id is not None
        await uow.subscription_repo.add(
            user_id=UserId(subscriber.user_id),
            granularity=SubscriptionGranularity.GLOBAL,
            target_value=None,
        )
        await uow.commit()


async def _drive_happy_path(handle: RunningService) -> str:
    """Run BeginRun + 1 stage submit + FinalizeRun. Returns run_id."""
    begin_resp = await handle.grpc_stub.BeginRun(
        pb.BeginRunRequest(
            pipeline_type="etl-nightly",
            run_tags=["production"],
            declared_stages=[
                pb.DeclaredStage(
                    stage_id="extract",
                    stage_order=0,
                    report_template=pb.TemplateRef(name="fragment", version="1.0"),
                ),
            ],
            attachment_mode=pb.ATTACHMENT_MODE_SINGLE_AGGREGATED,
            aggregation_template=pb.TemplateRef(name="aggregation", version="1.0"),
        )
    )
    ctx = Struct()
    ctx.update({"metric_name": "extract", "metric_value": 42})
    await handle.grpc_stub.SubmitStageReport(
        pb.SubmitStageReportRequest(
            run_id=begin_resp.run_id,
            stage_id="extract",
            report_contribution=pb.ReportContribution(
                template=pb.TemplateRef(name="fragment", version="1.0"),
                context=ctx,
            ),
        )
    )
    await handle.grpc_stub.FinalizeRun(pb.FinalizeRunRequest(run_id=begin_resp.run_id))
    return str(begin_resp.run_id)


@pytest.mark.asyncio
@pytest.mark.requirement("L1-DASH-003")
@pytest.mark.requirement("L3-DASH-013")
async def test_admin_resend_emits_second_email_and_audit(
    running_service: RunningService,
) -> None:
    """End-to-end: original SEND_REPORT + admin resend → RESEND_REPORT + 2 emails."""
    await _seed_admin_and_subscriber(running_service)

    # 1. First send: drive the happy path. Capture the original email.
    run_id = await _drive_happy_path(running_service)
    await running_service.smtp_capture.wait_for(1, timeout_seconds=10.0)
    assert len(running_service.smtp_capture.messages) == 1

    # 2. Log in as the admin via the dashboard.
    login_resp = await running_service.dashboard_client.post(
        "/login",
        json={"email": "admin@example.com", "password": "hunter2"},
    )
    assert login_resp.status_code == 200
    csrf = login_resp.cookies["msp_csrf"]

    # 3. POST /runs/{run_id}/resend.
    resend_resp = await running_service.dashboard_client.post(
        f"/runs/{run_id}/resend",
        headers={"X-CSRF-Token": csrf},
    )
    assert resend_resp.status_code == 202

    # 4. Wait for the second SMTP message.
    await running_service.smtp_capture.wait_for(2, timeout_seconds=10.0)
    assert len(running_service.smtp_capture.messages) == 2
    second = running_service.smtp_capture.messages[1]
    assert "alice@example.com" in second.rcpt_tos
    assert run_id in second.subject

    # 5. Assert audit-log carries both SEND_REPORT (original) and
    #    RESEND_REPORT records, both for this run_id.
    async with running_service.service.uow_factory() as uow:
        send_events = list(
            await uow.audit_log.query(action=AuditAction.SEND_REPORT, resource=f"run:{run_id}")
        )
        resend_events = list(
            await uow.audit_log.query(action=AuditAction.RESEND_REPORT, resource=f"run:{run_id}")
        )
    assert len(send_events) == 1
    assert send_events[0].outcome.value == "SUCCESS"
    assert len(resend_events) == 1
    assert resend_events[0].outcome.value == "SUCCESS"
    # Resend audit's actor is the admin user, not "system:..."
    assert resend_events[0].actor.startswith("user:")
