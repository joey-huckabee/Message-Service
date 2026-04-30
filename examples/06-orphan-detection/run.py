"""06-orphan-detection — sweeper transitions abandoned runs to ORPHANED.

Demonstrates the orphan-sweeper path that nobody ever hand-tests:
BeginRun + SubmitStageReport but NEVER FinalizeRun. The sweeper
runs every ``poll_interval_seconds`` and transitions any
non-terminal run older than ``run_timeout_seconds`` to ORPHANED
(L1-SWEEP-001 / L2-SWEEP-007).

The configured disposition actions are NOTIFY_ADMINS (a log-only
event in v1) and DISCARD_SILENTLY (no further action). No email is
sent on orphan in v1; the demo verifies the state transition and
the structured-log notification by reading the SQLite ``runs`` table
directly.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import time
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

_GRPC_PORT = 50056
_DASHBOARD_PORT = 8085
_SMTP_PORT = 1030

# Matches config.toml: orphan after 5 seconds of inactivity, swept
# every 1 second. The demo waits this long plus a small grace.
_RUN_TIMEOUT_SECONDS = 5
_SWEEPER_POLL_INTERVAL = 1
_GRACE_SECONDS = 4


def _setup() -> None:
    common.reset_state_dirs(_TMP, _REPORTS)
    common.reset_sqlite_files(_DB)
    template_paths = common.write_default_templates(_TEMPLATES_DIR)
    common.write_template_manifest(_TEMPLATES_MANIFEST, template_paths)
    common.write_tag_vocabulary(_TAGS, ["production", "staging"])


async def _drive_partial_flow() -> str:
    """BeginRun + Submit; deliberately no FinalizeRun."""
    async with grpc.aio.insecure_channel(f"127.0.0.1:{_GRPC_PORT}") as channel:
        stub = pb_grpc.MessageServiceStub(channel)

        pretty.step(3, "BeginRun (this run will be ABANDONED on purpose)")
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

        pretty.step(4, "SubmitStageReport: extract (only one of two declared stages)")
        ctx = Struct()
        ctx.update({"stage_id": "extract", "payload": "extracted but never finalized"})
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

        pretty.warn("DELIBERATELY skipping FinalizeRun (simulating a stuck pipeline)")
        return run_id


def _read_run_state(db_path: Path, run_id: str) -> str | None:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("SELECT state FROM runs WHERE run_id = ?", (run_id,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


async def _wait_for_orphan(db_path: Path, run_id: str, deadline: float) -> str:
    """Poll the runs table until state == ORPHANED or deadline elapses."""
    while time.monotonic() < deadline:
        state = _read_run_state(db_path, run_id)
        if state == "ORPHANED":
            return state
        await asyncio.sleep(0.5)
    return _read_run_state(db_path, run_id) or "(missing)"


async def _async_main() -> None:
    pretty.header("Scenario 06 — orphan detection")
    pretty.step(1, "Reset state, write templates + tag vocabulary")
    _setup()

    with SmtpCapture(host="127.0.0.1", port=_SMTP_PORT) as capture:
        pretty.step(2, f"Booting service ({_CONFIG.relative_to(_REPO_ROOT)})")
        with running_service(
            _CONFIG,
            grpc_port=_GRPC_PORT,
            dashboard_port=_DASHBOARD_PORT,
        ):
            run_id = await _drive_partial_flow()

            wait_seconds = _RUN_TIMEOUT_SECONDS + _SWEEPER_POLL_INTERVAL + _GRACE_SECONDS
            pretty.step(
                5,
                (
                    f"Waiting up to {wait_seconds}s for the sweeper to "
                    f"transition the run to ORPHANED (run_timeout_seconds="
                    f"{_RUN_TIMEOUT_SECONDS})"
                ),
            )
            deadline = time.monotonic() + wait_seconds
            final_state = await _wait_for_orphan(_DB, run_id, deadline)

            pretty.detail(f"final state observed: {final_state}")

        pretty.header("Verification")
        pretty.info(f"Run ID: {run_id}")
        pretty.info(f"Final state: {final_state}")
        pretty.info(f"SMTP messages captured: {len(capture.messages)}")
        pretty.detail(
            "(The default disposition actions are NOTIFY_ADMINS — log-only"
            " in v1 — and DISCARD_SILENTLY. No email is delivered on orphan.)"
        )

        pretty.header("Expectations")
        expect = Expectations()
        expect.equals("run state == ORPHANED", final_state, "ORPHANED")
        expect.length(
            "no SMTP messages — orphan path does not deliver mail",
            capture.messages,
            0,
        )
        expect.report_and_exit()


def main() -> None:
    """Entry point for ``poetry run python examples/06-orphan-detection/run.py``."""
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
