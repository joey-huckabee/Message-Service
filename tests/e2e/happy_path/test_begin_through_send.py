"""End-to-end happy path: BeginRun → 2x SubmitStageReport → FinalizeRun → email delivered.

Exercises every layer of the v1 stack:

* gRPC: real ``MessageServiceStub`` against an in-process
  ``grpc.aio.server()``.
* Use cases + UoW: ``BeginRunUseCase`` /
  ``SubmitStageReportUseCase`` / ``FinalizeRunUseCase`` /
  ``AssembleAndDeliverUseCase`` all run through the production
  composition root.
* Persistence: real on-disk SQLite (per-test ``tmp_path``); real
  filesystem report store under ``tmp_path/reports/``.
* Templating: real Jinja2 sandboxed renderer against on-disk
  manifests; produces real HTML.
* Email: real ``aiosmtplib`` SMTP client connecting to an
  in-process ``aiosmtpd`` capture; the captured envelope is the
  test's primary assertion.

Single test in this file — anything more elaborate goes in its
own file under ``happy_path/``.

Requirement references
----------------------
L1-RUN-001..004 (the run lifecycle)
L1-AGGR-002 (attachment-mode-driven assembly)
L1-MAIL-001 (SMTP delivery)
L1-PERS-002 (filesystem report store)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from google.protobuf.struct_pb2 import Struct
from message_service_proto.v1 import message_service_pb2 as pb

from message_service.domain.aggregates.subscription import SubscriptionGranularity
from message_service.domain.aggregates.user import User
from message_service.domain.ids import RunId, UserId
from message_service.domain.state_machines.run_states import RunState
from tests.fixtures.service import RunningService

_T0 = datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC)


async def _seed_subscriber(
    running_service: RunningService, *, email: str = "alice@example.com"
) -> None:
    """Insert a user + GLOBAL subscription via the production UoW.

    Setup-only: this exercises the persistence side directly
    (rather than driving login + POST /subscriptions) so the test
    body stays focused on the run lifecycle. The integration tier
    already covers the subscription-creation route end-to-end.
    """
    async with running_service.service.uow_factory() as uow:
        saved_user = await uow.user_repo.save(
            User(
                email=email,
                display_name="alice",
                password_hash="$argon2id$irrelevant-for-non-login-test",
                created_at=_T0,
                disabled=False,
                is_admin=False,
            ),
        )
        assert saved_user.user_id is not None
        await uow.subscription_repo.add(
            user_id=UserId(saved_user.user_id),
            granularity=SubscriptionGranularity.GLOBAL,
            target_value=None,
        )
        await uow.commit()


@pytest.mark.asyncio
@pytest.mark.requirement("L1-RUN-001")
@pytest.mark.requirement("L1-MAIL-001")
async def test_happy_path_full_pipeline_delivers_one_email(
    running_service: RunningService,
) -> None:
    """Drive the full pipeline; assert one email landed in SMTP capture."""
    await _seed_subscriber(running_service, email="alice@example.com")

    # 1. BeginRun via gRPC.
    begin_resp = await running_service.grpc_stub.BeginRun(
        pb.BeginRunRequest(
            pipeline_type="etl-nightly",
            run_tags=["production"],
            declared_stages=[
                pb.DeclaredStage(
                    stage_id="extract",
                    stage_order=0,
                    report_template=pb.TemplateRef(name="fragment", version="1.0"),
                ),
                pb.DeclaredStage(
                    stage_id="transform",
                    stage_order=1,
                    report_template=pb.TemplateRef(name="fragment", version="1.0"),
                ),
            ],
            attachment_mode=pb.ATTACHMENT_MODE_SINGLE_AGGREGATED,
            aggregation_template=pb.TemplateRef(name="aggregation", version="1.0"),
        )
    )
    assert begin_resp.run_id

    # 2. Submit two stage reports.
    for stage_id, metric in (("extract", 100), ("transform", 200)):
        ctx = Struct()
        ctx.update({"metric_name": stage_id, "metric_value": metric})
        await running_service.grpc_stub.SubmitStageReport(
            pb.SubmitStageReportRequest(
                run_id=begin_resp.run_id,
                stage_id=stage_id,
                report_contribution=pb.ReportContribution(
                    template=pb.TemplateRef(name="fragment", version="1.0"),
                    context=ctx,
                ),
            )
        )

    # 3. FinalizeRun.
    await running_service.grpc_stub.FinalizeRun(pb.FinalizeRunRequest(run_id=begin_resp.run_id))

    # 4. Wait for the asynchronous AssembleAndDeliver to fire and
    #    the SMTP message to land in the capture.
    await running_service.smtp_capture.wait_for(1, timeout_seconds=10.0)

    # 5. Assert on the captured email. The mailer uses To=From per
    #    its "undisclosed recipients" convention and puts subscribers
    #    on Bcc, so both addresses appear at the SMTP envelope level.
    msg = running_service.smtp_capture.messages[0]
    assert msg.mail_from == "svc@example.com"
    assert "alice@example.com" in msg.rcpt_tos
    assert begin_resp.run_id in msg.subject
    assert "[etl-nightly]" in msg.subject  # L2-MAIL-014 subject format
    body = msg.body_html
    assert begin_resp.run_id in body
    assert "etl-nightly" in body

    # 6. Assert the run reached SENT in the persistence layer.
    async with running_service.service.uow_factory() as uow:
        run = await uow.run_repo.get(RunId(begin_resp.run_id))
    assert run.state is RunState.SENT

    # 7. Assert the assembled email body landed in the report store.
    saved_body = running_service.service.report_store.read_email_body(RunId(begin_resp.run_id))
    assert saved_body is not None
    assert begin_resp.run_id in saved_body
