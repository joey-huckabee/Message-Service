"""04-retry-flow — same stage submitted twice; second wins.

Exercises the idempotent-overwrite path on SubmitStageReport
(L1-STAGE-002 / L2-STAGE-004). The first submission for a stage
returns ``was_retry=False`` and lands in SUBMITTED; a second
submission for the *same* stage_id returns ``was_retry=True``,
transitions the stage to RETRIED, and replaces the prior context.

The captured email's aggregated attachment should show the SECOND
context's payload (the "winning" submission), proving overwrite
semantics end-to-end.
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

_GRPC_PORT = 50054
_DASHBOARD_PORT = 8083
_SMTP_PORT = 1028


def _seed_global_subscriber(db_path: Path, email: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            "INSERT INTO users (email, display_name, disabled, created_at) VALUES (?, ?, 0, ?)",
            (email, "Retry Recipient", "2026-04-30T00:00:00Z"),
        )
        user_id = cur.lastrowid
        conn.execute(
            "INSERT INTO subscriptions "
            "(user_id, granularity, target_value, created_at) "
            "VALUES (?, 'GLOBAL', NULL, ?)",
            (user_id, "2026-04-30T00:00:00Z"),
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


async def _drive_grpc_flow() -> tuple[str, bool, bool]:
    """Drive the run; return (run_id, was_retry_first, was_retry_second)."""
    async with grpc.aio.insecure_channel(f"127.0.0.1:{_GRPC_PORT}") as channel:
        stub = pb_grpc.MessageServiceStub(channel)

        pretty.step(3, "BeginRun (pipeline=etl-nightly, stages=extract,load)")
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
                    pb.DeclaredStage(
                        stage_id="load",
                        stage_order=1,
                        report_template=pb.TemplateRef(name="fragment", version="1.0"),
                    ),
                ],
                attachment_mode=pb.AttachmentMode.ATTACHMENT_MODE_SINGLE_AGGREGATED,
                aggregation_template=pb.TemplateRef(name="aggregation", version="1.0"),
            )
        )
        run_id = begin_resp.run_id
        pretty.detail(f"run_id = {run_id}")

        pretty.step(4, "SubmitStageReport: extract — first attempt (initial run)")
        ctx_first = Struct()
        ctx_first.update({"stage_id": "extract", "payload": "FIRST_ATTEMPT records=1000"})
        first = await stub.SubmitStageReport(
            pb.SubmitStageReportRequest(
                run_id=run_id,
                stage_id="extract",
                report_contribution=pb.ReportContribution(
                    template=pb.TemplateRef(name="fragment", version="1.0"),
                    context=ctx_first,
                ),
            )
        )
        pretty.detail(f"was_retry = {first.was_retry}  (expect False)")

        pretty.step(5, "SubmitStageReport: extract — RESUBMIT (this is the retry)")
        ctx_second = Struct()
        ctx_second.update(
            {"stage_id": "extract", "payload": "SECOND_ATTEMPT records=1500_corrected"}
        )
        second = await stub.SubmitStageReport(
            pb.SubmitStageReportRequest(
                run_id=run_id,
                stage_id="extract",
                report_contribution=pb.ReportContribution(
                    template=pb.TemplateRef(name="fragment", version="1.0"),
                    context=ctx_second,
                ),
            )
        )
        pretty.detail(f"was_retry = {second.was_retry}  (expect True)")

        pretty.step(6, "SubmitStageReport: load — first attempt")
        ctx_load = Struct()
        ctx_load.update({"stage_id": "load", "payload": "loaded 1500 records"})
        await stub.SubmitStageReport(
            pb.SubmitStageReportRequest(
                run_id=run_id,
                stage_id="load",
                report_contribution=pb.ReportContribution(
                    template=pb.TemplateRef(name="fragment", version="1.0"),
                    context=ctx_load,
                ),
            )
        )

        pretty.step(7, "FinalizeRun → triggers aggregation + delivery")
        await stub.FinalizeRun(pb.FinalizeRunRequest(run_id=run_id))
        return run_id, first.was_retry, second.was_retry


def _decode_payload(part: object) -> str:
    payload = part.get_payload(decode=True)  # type: ignore[attr-defined]
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    return str(payload or "")


async def _async_main() -> None:
    pretty.header("Scenario 04 — retry flow (idempotent overwrite)")
    pretty.step(1, "Reset state, write templates + tag vocabulary")
    _setup()

    with SmtpCapture(host="127.0.0.1", port=_SMTP_PORT) as capture:
        pretty.step(2, f"Booting service ({_CONFIG.relative_to(_REPO_ROOT)})")
        with running_service(
            _CONFIG,
            grpc_port=_GRPC_PORT,
            dashboard_port=_DASHBOARD_PORT,
        ):
            _seed_global_subscriber(_DB, "ops@example.com")
            run_id, was_retry_first, was_retry_second = await _drive_grpc_flow()
            pretty.step(8, "Wait for SMTP capture to receive the email")
            await capture.wait_for(count=1, timeout=15.0)

        pretty.header("Captured email")
        msg = capture.messages[0]
        attachments = list(msg.parsed.iter_attachments())
        agg_payload = _decode_payload(attachments[0]) if attachments else ""

        pretty.info(f"Subject:     {msg.subject}")
        pretty.info(f"Attachments: {len(attachments)}")
        pretty.info("Aggregated attachment preview (first 240 chars):")
        pretty.detail(agg_payload[:240].replace("\n", " "))

        pretty.header("Expectations")
        expect = Expectations()
        expect.length("exactly one email captured", capture.messages, 1)
        expect.equals("first submission was_retry == False", was_retry_first, False)
        expect.equals("second submission was_retry == True", was_retry_second, True)
        expect.length("ONE aggregated attachment", attachments, 1)
        expect.contains(
            "aggregated attachment shows SECOND attempt's payload",
            agg_payload,
            "SECOND_ATTEMPT records=1500_corrected",
        )
        expect.truthy(
            "aggregated attachment does NOT show the first attempt",
            "FIRST_ATTEMPT" not in agg_payload,
        )
        expect.contains(
            "aggregated attachment includes the load stage",
            agg_payload,
            "loaded 1500 records",
        )
        expect.contains(
            "subject pins the run_id",
            msg.subject,
            run_id,
        )
        expect.report_and_exit()


def main() -> None:
    """Entry point for ``poetry run python examples/04-retry-flow/run.py``."""
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
