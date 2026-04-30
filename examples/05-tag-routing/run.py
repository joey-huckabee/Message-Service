"""05-tag-routing — predicate-based subscriber selection.

Seeds two users with different TAG-granularity subscriptions
(``production`` vs ``staging``), runs a pipeline tagged
``production``, and asserts that only the production subscriber
receives the email — proving that tag predicates filter recipients
per L1-SUB-004 / L2-SUB-003 / L3-SUB-005.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

# ruff: noqa: E402 — every import below depends on the sys.path tweak above.
import grpc
from examples._lib import common, pretty
from examples._lib.expectations import Expectations
from examples._lib.service_runner import running_service
from examples._lib.smtp_capture import SmtpCapture
from google.protobuf.struct_pb2 import Struct
from message_service_proto.v1 import message_service_pb2 as pb
from message_service_proto.v1 import message_service_pb2_grpc as pb_grpc

_HERE = Path(__file__).parent
_CONFIG = _HERE / "config.toml"
_TMP = _HERE / ".tmp"
_DB = _TMP / "message-service.db"
_REPORTS = _TMP / "reports"
_TEMPLATES_DIR = _HERE / "templates"
_TEMPLATES_MANIFEST = _HERE / "templates.toml"
_TAGS = _HERE / "tags.toml"

_GRPC_PORT = 50055
_DASHBOARD_PORT = 8084
_SMTP_PORT = 1029

_PROD_USER = "prod-watcher@example.com"
_STAGING_USER = "staging-watcher@example.com"


def _seed_tag_subscribers(db_path: Path) -> None:
    """Two users, two distinct TAG subscriptions."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        for email, tag in (
            (_PROD_USER, "production"),
            (_STAGING_USER, "staging"),
        ):
            cur = conn.execute(
                "INSERT INTO users (email, display_name, disabled, created_at) VALUES (?, ?, 0, ?)",
                (email, f"{tag} watcher", "2026-04-30T00:00:00Z"),
            )
            user_id = cur.lastrowid
            conn.execute(
                "INSERT INTO subscriptions "
                "(user_id, granularity, target_value, created_at) "
                "VALUES (?, 'TAG', ?, ?)",
                (user_id, tag, "2026-04-30T00:00:00Z"),
            )
        conn.commit()
    finally:
        conn.close()


def _setup() -> None:
    common.reset_state_dirs(_TMP, _REPORTS)
    common.reset_sqlite_files(_DB)
    template_paths = common.write_default_templates(_TEMPLATES_DIR)
    common.write_template_manifest(_TEMPLATES_MANIFEST, template_paths)
    common.write_tag_vocabulary(_TAGS, ["production", "staging"])


async def _drive_grpc_flow() -> str:
    async with grpc.aio.insecure_channel(f"127.0.0.1:{_GRPC_PORT}") as channel:
        stub = pb_grpc.MessageServiceStub(channel)

        pretty.step(3, "BeginRun (run_tags=['production'] — should match prod-watcher only)")
        begin_resp = await stub.BeginRun(
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
                attachment_mode=pb.AttachmentMode.ATTACHMENT_MODE_SINGLE_AGGREGATED,
                aggregation_template=pb.TemplateRef(name="aggregation", version="1.0"),
            )
        )
        run_id = begin_resp.run_id
        pretty.detail(f"run_id = {run_id}")

        pretty.step(4, "SubmitStageReport: extract")
        ctx = Struct()
        ctx.update({"stage_id": "extract", "payload": "production data extracted"})
        await stub.SubmitStageReport(
            pb.SubmitStageReportRequest(
                run_id=run_id,
                stage_id="extract",
                report_contribution=pb.ReportContribution(
                    template=pb.TemplateRef(name="fragment", version="1.0"),
                    context=ctx,
                ),
            )
        )

        pretty.step(5, "FinalizeRun → triggers delivery")
        await stub.FinalizeRun(pb.FinalizeRunRequest(run_id=run_id))
        return run_id


async def _async_main() -> None:
    pretty.header("Scenario 05 — tag-based recipient routing")
    pretty.step(1, "Reset state, write templates + tag vocabulary")
    _setup()

    with SmtpCapture(host="127.0.0.1", port=_SMTP_PORT) as capture:
        pretty.step(2, f"Booting service ({_CONFIG.relative_to(_REPO_ROOT)})")
        with running_service(
            _CONFIG,
            grpc_port=_GRPC_PORT,
            dashboard_port=_DASHBOARD_PORT,
        ):
            pretty.detail(f"seeding subscribers: {_PROD_USER} (TAG=production)")
            pretty.detail(f"                    {_STAGING_USER} (TAG=staging)")
            _seed_tag_subscribers(_DB)

            run_id = await _drive_grpc_flow()
            pretty.step(6, "Wait for SMTP capture to receive the email")
            await capture.wait_for(count=1, timeout=15.0)

        pretty.header("Captured email")
        msg = capture.messages[0]
        envelope_str = ", ".join(msg.rcpt_tos)

        pretty.info(f"From:        {msg.mail_from}")
        pretty.info(f"Envelope to: {envelope_str}")
        pretty.info(f"Subject:     {msg.subject}")

        pretty.header("Expectations")
        expect = Expectations()
        expect.length("exactly one email captured", capture.messages, 1)
        expect.contains(
            "production subscriber WAS routed (tag matched)",
            envelope_str,
            _PROD_USER,
        )
        expect.truthy(
            "staging subscriber was NOT routed (tag did not match)",
            _STAGING_USER not in envelope_str,
        )
        expect.contains(
            "subject pins the run_id",
            msg.subject,
            run_id,
        )
        expect.report_and_exit()


def main() -> None:
    """Entry point for ``poetry run python examples/05-tag-routing/run.py``."""
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
