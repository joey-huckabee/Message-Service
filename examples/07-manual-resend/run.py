"""07-manual-resend — re-deliver a completed run via the dashboard.

Demonstrates the operator-facing resend path (L1-DASH-003 /
L3-DASH-027/028). After a run reaches SENT, an authenticated
operator can ``POST /runs/{run_id}/resend`` to reissue the email
to the *currently* matching subscribers — a recovery handle for
mailbox loss, accidental delete, or "the email never landed"
support tickets.

The full flow:

1. Seed a user with a real Argon2 password hash + GLOBAL
   subscription so they receive both deliveries.
2. Run an end-to-end pipeline (BeginRun → Submit → FinalizeRun);
   wait for the first email.
3. Log into the dashboard via ``POST /login``; capture the session
   and CSRF cookies.
4. Send ``POST /runs/{run_id}/resend`` with both cookies + the
   ``X-CSRF-Token`` header. Wait for the second email.
5. Verify two distinct deliveries arrived for the same run_id.
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
import httpx
from argon2 import PasswordHasher
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

_GRPC_PORT = 50057
_DASHBOARD_PORT = 8086
_SMTP_PORT = 1031

_USER_EMAIL = "ops-lead@example.com"
_USER_PASSWORD = "demo-password-123"


def _seed_authenticated_user(db_path: Path) -> None:
    """Insert a user with an Argon2 hash + GLOBAL subscription.

    The password matches ``_USER_PASSWORD`` so the demo can log in
    via ``POST /login`` later. Argon2 default cost matches what
    ``[auth.argon2]`` configures.
    """
    pwhash = PasswordHasher().hash(_USER_PASSWORD)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            "INSERT INTO users "
            "(email, display_name, disabled, password_hash, is_admin, created_at) "
            "VALUES (?, ?, 0, ?, 0, ?)",
            (_USER_EMAIL, "Operations Lead", pwhash, "2026-04-30T00:00:00Z"),
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

        pretty.step(3, "BeginRun (pipeline=etl-nightly, single stage)")
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
        ctx.update({"stage_id": "extract", "payload": "delivered once, then resent"})
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

        pretty.step(5, "FinalizeRun → triggers FIRST delivery")
        await stub.FinalizeRun(pb.FinalizeRunRequest(run_id=run_id))
        return run_id


async def _trigger_resend(run_id: str) -> int:
    """Login + POST /runs/{run_id}/resend. Return the resend response code."""
    base_url = f"http://127.0.0.1:{_DASHBOARD_PORT}"
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        pretty.step(7, "POST /login (acquire session + CSRF cookies)")
        login_resp = await client.post(
            "/login",
            json={"email": _USER_EMAIL, "password": _USER_PASSWORD},
        )
        login_resp.raise_for_status()
        csrf_token = client.cookies.get("msp_csrf")
        if csrf_token is None:
            msg = "no CSRF cookie returned from /login"
            raise RuntimeError(msg)
        pretty.detail(f"login OK; csrf cookie present (length {len(csrf_token)})")

        pretty.step(8, f"POST /runs/{run_id[:8]}…/resend (CSRF-protected)")
        resp = await client.post(
            f"/runs/{run_id}/resend",
            headers={"X-CSRF-Token": csrf_token},
        )
        pretty.detail(f"HTTP {resp.status_code} {resp.text.strip()}")
        return resp.status_code


async def _async_main() -> None:
    pretty.header("Scenario 07 — manual resend via dashboard")
    pretty.step(1, "Reset state, write templates + tag vocabulary")
    _setup()

    with SmtpCapture(host="127.0.0.1", port=_SMTP_PORT) as capture:
        pretty.step(2, f"Booting service ({_CONFIG.relative_to(_REPO_ROOT)})")
        with running_service(
            _CONFIG,
            grpc_port=_GRPC_PORT,
            dashboard_port=_DASHBOARD_PORT,
        ):
            _seed_authenticated_user(_DB)

            run_id = await _drive_grpc_flow()
            pretty.step(6, "Wait for FIRST email (FinalizeRun delivery)")
            await capture.wait_for(count=1, timeout=15.0)

            resend_status = await _trigger_resend(run_id)
            pretty.step(9, "Wait for SECOND email (resend delivery)")
            await capture.wait_for(count=2, timeout=15.0)

        pretty.header("Captured deliveries")
        pretty.info(f"messages captured: {len(capture.messages)}")
        for idx, msg in enumerate(capture.messages, start=1):
            pretty.detail(f"  #{idx}: subject={msg.subject!r}")

        pretty.header("Expectations")
        expect = Expectations()
        expect.equals("resend route returned HTTP 202", resend_status, 202)
        expect.length("two emails captured (original + resend)", capture.messages, 2)
        expect.contains(
            "first delivery's subject pins the run_id",
            capture.messages[0].subject,
            run_id,
        )
        expect.contains(
            "resend's subject pins the same run_id",
            capture.messages[1].subject,
            run_id,
        )
        expect.equals(
            "both deliveries went to the same recipient set",
            sorted(capture.messages[0].rcpt_tos),
            sorted(capture.messages[1].rcpt_tos),
        )
        expect.report_and_exit()


def main() -> None:
    """Entry point for ``poetry run python examples/07-manual-resend/run.py``."""
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
