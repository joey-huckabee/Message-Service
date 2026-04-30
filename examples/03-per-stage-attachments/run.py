"""03-per-stage-attachments — 4 stages, one MIME attachment per stage.

Same pipeline shape as scenario 02 but with PER_STAGE attachment
mode. The aggregation template is intentionally NOT supplied — the
service forbids one for PER_STAGE.
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

_GRPC_PORT = 50053
_DASHBOARD_PORT = 8082
_SMTP_PORT = 1027

_STAGES = [
    ("extract", 0, "extracted 12345 records"),
    ("validate", 1, "validated 12338 records (7 failed)"),
    ("transform", 2, "transformed in 14.2s"),
    ("load", 3, "loaded to bigquery_prod"),
]


def _seed_global_subscriber(db_path: Path, email: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            "INSERT INTO users (email, display_name, disabled, created_at) VALUES (?, ?, 0, ?)",
            (email, "Per-stage Recipient", "2026-04-30T00:00:00Z"),
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


async def _drive_grpc_flow() -> str:
    async with grpc.aio.insecure_channel(f"127.0.0.1:{_GRPC_PORT}") as channel:
        stub = pb_grpc.MessageServiceStub(channel)

        pretty.step(3, "BeginRun (PER_STAGE, no aggregation template)")
        declared = [
            pb.DeclaredStage(
                stage_id=stage_id,
                stage_order=order,
                report_template=pb.TemplateRef(name="fragment", version="1.0"),
            )
            for stage_id, order, _ in _STAGES
        ]
        begin_resp = await stub.BeginRun(
            pb.BeginRunRequest(
                pipeline_type="etl-nightly",
                run_tags=["production"],
                declared_stages=declared,
                attachment_mode=pb.AttachmentMode.ATTACHMENT_MODE_PER_STAGE,
                # NO aggregation_template — PER_STAGE forbids one.
            )
        )
        run_id = begin_resp.run_id
        pretty.detail(f"run_id = {run_id}")

        for stage_id, _, payload in _STAGES:
            pretty.step(4, f"SubmitStageReport: {stage_id}")
            ctx = Struct()
            ctx.update({"stage_id": stage_id, "payload": payload})
            await stub.SubmitStageReport(
                pb.SubmitStageReportRequest(
                    run_id=run_id,
                    stage_id=stage_id,
                    report_contribution=pb.ReportContribution(
                        template=pb.TemplateRef(name="fragment", version="1.0"),
                        context=ctx,
                    ),
                )
            )

        pretty.step(5, "FinalizeRun → triggers per-stage assembly + delivery")
        await stub.FinalizeRun(pb.FinalizeRunRequest(run_id=run_id))
        return run_id


def _decode_payload(part: object) -> str:
    payload = part.get_payload(decode=True)  # type: ignore[attr-defined]
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    return str(payload or "")


async def _async_main() -> None:
    pretty.header("Scenario 03 — PER_STAGE attachments")
    pretty.step(1, "Reset state, write templates + tag vocabulary")
    _setup()

    with SmtpCapture(host="127.0.0.1", port=_SMTP_PORT) as capture:
        pretty.step(2, f"Booting service ({_CONFIG.relative_to(_REPO_ROOT)})")
        with running_service(
            _CONFIG,
            grpc_port=_GRPC_PORT,
            dashboard_port=_DASHBOARD_PORT,
        ):
            _seed_global_subscriber(_DB, "etl-team@example.com")
            run_id = await _drive_grpc_flow()
            pretty.step(6, "Wait for SMTP capture to receive the email")
            await capture.wait_for(count=1, timeout=15.0)

        pretty.header("Captured email")
        msg = capture.messages[0]
        attachments = list(msg.parsed.iter_attachments())
        attachment_payloads = [
            (att.get_filename() or "(unnamed)", _decode_payload(att)) for att in attachments
        ]
        filenames = [name for name, _ in attachment_payloads]

        pretty.info(f"From:        {msg.mail_from}")
        pretty.info(f"Envelope to: {', '.join(msg.rcpt_tos)}")
        pretty.info(f"Subject:     {msg.subject}")
        pretty.info(f"Attachments: {len(attachments)}")
        for filename, payload in attachment_payloads:
            pretty.detail(f"{filename} ({len(payload)} bytes)")

        pretty.header("Expectations")
        expect = Expectations()
        expect.length("exactly one email captured", capture.messages, 1)
        expect.contains(
            "etl-team is on the SMTP envelope",
            ", ".join(msg.rcpt_tos),
            "etl-team@example.com",
        )
        expect.length(
            "FOUR attachments (one per stage)",
            attachments,
            len(_STAGES),
        )
        for stage_id, _, _ in _STAGES:
            expected_suffix = f"_{stage_id}.html"
            expect.truthy(
                f"attachment for stage '{stage_id}' present",
                any(name.endswith(expected_suffix) for name in filenames),
            )
        for stage_id, _, _payload in _STAGES:
            expected_suffix = f"_{stage_id}.html"
            payload_for_stage = next(
                (body for name, body in attachment_payloads if name.endswith(expected_suffix)),
                "",
            )
            expect.contains(
                f"'{stage_id}' attachment carries its own payload",
                payload_for_stage,
                stage_id,
            )
        expect.contains(
            "filenames embed the run_id",
            ", ".join(filenames),
            run_id[:8],
        )
        expect.report_and_exit()


def main() -> None:
    """Entry point for ``poetry run python examples/03-per-stage-attachments/run.py``."""
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
