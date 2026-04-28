"""End-to-end orphan path: BeginRun → silent stages → sweeper → ORPHANED.

Exercises the orphan disposition flow against a real running service:

* Real BeginRun via gRPC, putting the run into a non-terminal state.
* Real sweeper loop running on the asyncio scheduler with a tight
  ``run_timeout_seconds`` configured at bootstrap.
* Real orphan-state transition with the audit row written.
* Real disposition handlers fired (the default
  ``[NOTIFY_ADMINS, DISCARD_SILENTLY]`` policy from the config
  schema; both are log-only in v1, so we assert on audit rows
  rather than side effects).

This test takes ~3 seconds to run because it waits for the
sweeper's ``run_timeout_seconds`` plus a poll interval. Auto-marked
``slow`` by the e2e conftest.

Requirement references
----------------------
L1-SWEEP-001 (orphan detection)
L1-SWEEP-002 (disposition policy)
L2-SWEEP-007 (sweeper loop)
L1-OBS-003 (audit log retains the SWEEP_ORPHAN record)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from message_service_proto.v1 import message_service_pb2 as pb

from message_service.domain.aggregates.audit_event import AuditAction
from message_service.domain.ids import RunId
from message_service.domain.state_machines.run_states import RunState
from tests.fixtures.email import SmtpCapture
from tests.fixtures.service import RunningService, build_running_service


@pytest.fixture
async def running_service_short_sweeper(
    tmp_path: Path,
    smtp_capture: SmtpCapture,
) -> AsyncIterator[RunningService]:
    """Service with sweeper_run_timeout=2s, poll_interval=1s.

    Caller starts the sweeper loop explicitly AFTER any
    BeginRun-style setup work. The original reason for this
    ordering was a real concurrency bug — the shared SQLite
    connection had no in-process serialization, so a sweeper-tick
    UoW could collide at BEGIN with the test's own BeginRun UoW
    and raise "cannot start a transaction within a transaction".
    Increment 27 fixed that bug by introducing an asyncio.Lock
    around BEGIN/COMMIT (L2-PERS-004 + L3-PERS-006/007/021), so
    concurrent UoWs now serialize cleanly. The start-after-BeginRun
    ordering is preserved for test-readability — it keeps the
    happy-path BeginRun out of the sweeper-loop's polling cadence,
    which makes timing-sensitive assertions easier to reason about
    — but it is no longer a correctness workaround. Stop is
    best-effort in teardown.
    """
    async with build_running_service(
        tmp_path,
        smtp_capture,
        sweeper_run_timeout_seconds=2,
        sweeper_poll_interval_seconds=1,
    ) as handle:
        try:
            yield handle
        finally:
            handle.service.sweeper_loop.stop()


async def _wait_for_run_state(
    handle: RunningService,
    run_id: str,
    target_state: RunState,
    *,
    timeout_seconds: float = 8.0,
) -> RunState:
    """Async-poll the run's state until it matches ``target_state``."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        async with handle.service.uow_factory() as uow:
            run = await uow.run_repo.get(RunId(run_id))
        if run.state is target_state:
            return run.state
        await asyncio.sleep(0.2)
    async with handle.service.uow_factory() as uow:
        run = await uow.run_repo.get(RunId(run_id))
    raise AssertionError(
        f"timed out waiting for run {run_id} to reach {target_state.name}; "
        f"current state is {run.state.name}"
    )


@pytest.mark.asyncio
@pytest.mark.requirement("L1-SWEEP-001")
@pytest.mark.requirement("L1-SWEEP-002")
async def test_sweeper_orphans_silent_run_and_audits(
    running_service_short_sweeper: RunningService,
) -> None:
    """A run with no submissions SHALL transition to ORPHANED past the timeout."""
    handle = running_service_short_sweeper

    # 1. BeginRun. The run is in INITIATED state. We never submit
    #    any stages, never finalize.
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
    run_id = begin_resp.run_id
    assert run_id

    # 2. Start the sweeper loop AFTER BeginRun. Post-Increment-27
    #    this is a readability ordering, not a correctness workaround:
    #    the asyncio.Lock around BEGIN/COMMIT (L2-PERS-004) handles
    #    sweeper-tick / BeginRun overlap correctly. Keeping the
    #    BeginRun out of the polling cadence makes the subsequent
    #    timing assertions easier to reason about.
    handle.service.sweeper_loop.start()

    # 3. Wait for the sweeper to fire. With run_timeout_seconds=2
    #    and poll_interval_seconds=1, the run should be picked up
    #    within ~4 seconds. The 15s budget is generous for slow CI
    #    or coverage-instrumented runs (8s was right at the edge
    #    when coverage instrumentation was active alongside a full
    #    suite's memory pressure).
    await _wait_for_run_state(handle, run_id, RunState.ORPHANED, timeout_seconds=15.0)

    # 3. Assert the SWEEP_ORPHAN audit row was written.
    async with handle.service.uow_factory() as uow:
        events = list(
            await uow.audit_log.query(
                action=AuditAction.SWEEP_ORPHAN,
                resource=f"run:{run_id}",
            )
        )
    assert len(events) == 1
    sweep_event = events[0]
    assert sweep_event.outcome.value == "SUCCESS"
    assert sweep_event.details.get("run_id") == run_id

    # 4. No SMTP traffic SHALL have been emitted — the orphan path
    #    does not invoke the mailer.
    assert handle.smtp_capture.messages == []

    # 5. Disposition handlers SHALL have run. The default policy is
    #    [NOTIFY_ADMINS, DISCARD_SILENTLY]; both are log-only in v1
    #    but their dispatcher emits one audit-like row per action via
    #    sweeper_actions table. We check the table directly through
    #    the connection.
    async with (
        handle.service.uow_factory() as uow,
        uow._conn.execute(
            "SELECT action_name, completed_at FROM sweeper_actions "
            "WHERE run_id = ? ORDER BY action_id",
            (run_id,),
        ) as cur,
    ):
        rows = await cur.fetchall()
    actions = [str(row[0]) for row in rows]
    assert "NOTIFY_ADMINS" in actions
    assert "DISCARD_SILENTLY" in actions
    # Each action SHALL have completed_at set (not stuck in pending).
    for row in rows:
        assert row[1] is not None, f"action {row[0]} not completed"
