"""01-hello-world — single-stage pipeline smoke test.

Drives BeginRun → SubmitStageReport → FinalizeRun against a freshly
booted service, asserts that the in-process SMTP capture received
exactly one email, and prints what came through.

Run from the repo root:

    poetry run python examples/01-hello-world/run.py
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import uuid
from pathlib import Path

# Make `examples` importable when this script is invoked directly.
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

_GRPC_PORT = 50051
_DASHBOARD_PORT = 8080
_SMTP_PORT = 1025


def _seed_global_subscriber(db_path: Path, email: str) -> None:
    """Insert a user + GLOBAL subscription so the run has a recipient.

    GLOBAL subscriptions match every run regardless of pipeline_type
    or tags. Inserted directly because the dashboard requires an
    authenticated session, which would add CSRF + login-flow noise to
    a smoke test.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            "INSERT INTO users (email, display_name, disabled, created_at) VALUES (?, ?, 0, ?)",
            (email, "Hello World Recipient", "2026-04-30T00:00:00Z"),
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
    """Reset state, write templates + tag vocabulary."""
    common.reset_state_dirs(_TMP, _REPORTS)
    common.reset_sqlite_files(_DB)
    template_paths = common.write_default_templates(_TEMPLATES_DIR)
    common.write_template_manifest(_TEMPLATES_MANIFEST, template_paths)
    common.write_tag_vocabulary(_TAGS, ["production", "staging"])


async def _drive_grpc_flow() -> str:
    """Drive BeginRun → Submit → Finalize. Return the run_id."""
    async with grpc.aio.insecure_channel(f"127.0.0.1:{_GRPC_PORT}") as channel:
        stub = pb_grpc.MessageServiceStub(channel)

        pretty.step(3, "BeginRun (pipeline=hello-world, single stage 'greeting')")
        begin_resp = await stub.BeginRun(
            pb.BeginRunRequest(
                pipeline_type="hello-world",
                run_tags=["production"],
                declared_stages=[
                    pb.DeclaredStage(
                        stage_id="greeting",
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

        pretty.step(4, "SubmitStageReport for stage 'greeting'")
        ctx = Struct()
        ctx.update({"stage_id": "greeting", "payload": "Hello, world!"})
        submit_resp = await stub.SubmitStageReport(
            pb.SubmitStageReportRequest(
                run_id=run_id,
                stage_id="greeting",
                report_contribution=pb.ReportContribution(
                    template=pb.TemplateRef(name="fragment", version="1.0"),
                    context=ctx,
                ),
            )
        )
        pretty.detail(f"was_retry = {submit_resp.was_retry}")

        pretty.step(5, "FinalizeRun → triggers aggregation + delivery")
        await stub.FinalizeRun(pb.FinalizeRunRequest(run_id=run_id))
        return uuid.UUID(run_id).hex


async def _async_main() -> None:
    pretty.header("Scenario 01 — hello-world")
    pretty.step(1, "Reset state, write templates + tag vocabulary")
    _setup()

    # Open SMTP capture FIRST — the service connects to it on first send.
    with SmtpCapture(host="127.0.0.1", port=_SMTP_PORT) as capture:
        pretty.step(2, f"Booting service ({_CONFIG.relative_to(_REPO_ROOT)})")
        with running_service(
            _CONFIG,
            grpc_port=_GRPC_PORT,
            dashboard_port=_DASHBOARD_PORT,
        ):
            # Service has migrated the DB; now seed a subscriber.
            _seed_global_subscriber(_DB, "alice@example.com")

            run_hex = await _drive_grpc_flow()

            pretty.step(6, "Wait for SMTP capture to receive the email")
            await capture.wait_for(count=1, timeout=15.0)

        # Service has shut down by the time we reach here.

        pretty.header("Captured email")
        msg = capture.messages[0]
        attachments = list(msg.parsed.iter_attachments())
        attachment_payloads = [
            (att.get_filename() or "(unnamed)", _decode_payload(att)) for att in attachments
        ]

        pretty.info(f"From:        {msg.mail_from}")
        pretty.info(f"Envelope to: {', '.join(msg.rcpt_tos)}")
        pretty.info(f"Subject:     {msg.subject}")
        pretty.info(f"Body preview ({len(msg.body_text())} bytes of HTML):")
        pretty.detail(msg.body_text()[:200].replace("\n", " ") + "…")
        pretty.info(f"Attachments: {len(attachments)}")
        for filename, payload in attachment_payloads:
            pretty.detail(f"{filename} ({len(payload)} bytes)")

        pretty.header("Expectations")
        expect = Expectations()
        expect.length("exactly one email captured", capture.messages, 1)
        expect.contains(
            "alice is on the SMTP envelope",
            ", ".join(msg.rcpt_tos),
            "alice@example.com",
        )
        expect.contains(
            "subject names the pipeline",
            msg.subject,
            "[hello-world]",
        )
        expect.contains(
            "body references the run id",
            msg.body_text(),
            run_hex[:8],  # first 8 hex chars of the run UUID
        )
        expect.length("one HTML attachment (SINGLE_AGGREGATED)", attachments, 1)
        expect.contains(
            "attachment contains 'Hello, world!'",
            attachment_payloads[0][1] if attachment_payloads else "",
            "Hello, world!",
        )
        expect.report_and_exit()


def _decode_payload(part: object) -> str:
    """Decode a MIME part's body to a UTF-8 string for inspection."""
    payload = part.get_payload(decode=True)  # type: ignore[attr-defined]
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    return str(payload or "")


def main() -> None:
    """Entry point for ``poetry run python examples/01-hello-world/run.py``."""
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
