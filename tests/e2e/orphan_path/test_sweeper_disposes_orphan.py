"""End-to-end orphan path: BeginRun → silent stages → sweeper → ORPHANED.

Exercises the orphan disposition flow against a real running service:

* Real BeginRun via gRPC, putting the run into a non-terminal state.
* Real :class:`SweeperUseCase.tick` and
  :class:`SweeperActionDispatcherUseCase.dispatch_pending` invoked
  directly on the composed service. The background
  :class:`SweeperLoop` is *not* started — driving the use cases
  synchronously gives the test exact sequencing of orphan-detection
  and action-completion, so the post-condition assertions read
  state that is structurally guaranteed to be settled. No polling,
  no race window.
* Real orphan-state transition with the audit row written.
* Real disposition handlers fired (the default
  ``[NOTIFY_ADMINS, DISCARD_SILENTLY]`` policy from the config
  schema; both are log-only in v1, so we assert on audit rows
  rather than side effects).

The only real-time wait is :func:`asyncio.sleep` for slightly
longer than ``sweeper_run_timeout_seconds``. This crosses the
orphan-eligibility threshold deterministically — the run's
``last_transition_at`` is set during BeginRun and the sweeper
considers any non-terminal run with ``now - last_transition >=
run_timeout_seconds`` to be orphaned. After the sleep, the
synchronous tick→drain sequence guarantees:

1. ``service.sweeper.tick()`` opens its own UoW, transitions the
   run to ORPHANED, writes the SWEEP_ORPHAN audit row, and inserts
   the disposition-action rows in one transaction.
2. ``service.sweeper_action_dispatcher.dispatch_pending()`` claims
   the inserted rows, awaits each handler's ``handle()`` to
   completion, and settles each row with ``mark_completed`` in a
   per-row UoW. When this call returns, every claimed row has its
   ``completed_at`` written.

Both calls are awaited to completion before assertions begin.
There is no background work in flight when the assertions run, so
no observation race against unfinished dispatcher progress is
possible.

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

# Smallest legal value for ``sweeper.run_timeout_seconds`` per the
# config schema (``ge=1``). Keeps the test's real-time wait minimal.
_RUN_TIMEOUT_SECONDS = 1
# Buffer added to the real-time wait so we are reliably past the
# orphan-eligibility threshold even with sub-second clock drift or
# event-loop scheduling variance. Empirically 100ms is more than
# adequate; doubling for safety.
_THRESHOLD_BUFFER_SECONDS = 0.2


@pytest.fixture
async def running_service_short_sweeper(
    tmp_path: Path,
    smtp_capture: SmtpCapture,
) -> AsyncIterator[RunningService]:
    """Service with a 1-second sweeper run-timeout; loop NOT started.

    The background :class:`SweeperLoop` is built but never started by
    this test. The test drives :meth:`SweeperUseCase.tick` and
    :meth:`SweeperActionDispatcherUseCase.dispatch_pending`
    synchronously through the composed service to remove the
    background-cadence/foreground-assertion race that the previous
    loop-driven version of this test had.

    ``poll_interval_seconds`` is set to a value comfortably larger
    than the test's runtime so the loop, if accidentally started,
    would not tick during the test. The test does not start it.
    """
    async with build_running_service(
        tmp_path,
        smtp_capture,
        sweeper_run_timeout_seconds=_RUN_TIMEOUT_SECONDS,
        sweeper_poll_interval_seconds=60,
    ) as handle:
        yield handle


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

    # 2. Cross the orphan-eligibility threshold deterministically.
    #    The run's last_transition_at was set during BeginRun; the
    #    sweeper considers it orphaned once now - last_transition
    #    >= run_timeout_seconds. Sleeping the timeout plus a small
    #    buffer guarantees the next tick will detect it.
    await asyncio.sleep(_RUN_TIMEOUT_SECONDS + _THRESHOLD_BUFFER_SECONDS)

    # 3. Drive orphan detection synchronously. tick() opens a UoW,
    #    transitions the run to ORPHANED, writes the SWEEP_ORPHAN
    #    audit row, and inserts the disposition-action rows — all
    #    in a single transaction — before returning.
    tick_result = await handle.service.sweeper.tick()
    assert tick_result.orphaned_count == 1, (
        f"expected exactly one orphan, got {tick_result.orphaned_count}"
    )
    # The default disposition policy is [NOTIFY_ADMINS, DISCARD_SILENTLY],
    # so two action rows are enqueued per orphaned run.
    assert tick_result.enqueued_actions == 2, (
        f"expected 2 enqueued actions, got {tick_result.enqueued_actions}"
    )

    # 4. Drive the dispatcher drain synchronously. This claims the
    #    just-enqueued action rows, runs each handler to completion,
    #    and writes completed_at on each row in its own per-row UoW.
    #    When this call returns there is no background work in
    #    flight — every claimed row's completed_at is settled.
    dispatch_result = await handle.service.sweeper_action_dispatcher.dispatch_pending()
    assert dispatch_result.claimed == 2
    assert dispatch_result.succeeded == 2
    assert dispatch_result.failed == 0

    # 5. Assert the run is now ORPHANED.
    async with handle.service.uow_factory() as uow:
        run = await uow.run_repo.get(RunId(run_id))
    assert run.state is RunState.ORPHANED, f"expected ORPHANED, got {run.state.name}"

    # 6. Assert the SWEEP_ORPHAN audit row was written exactly once.
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

    # 7. No SMTP traffic SHALL have been emitted — the orphan path
    #    does not invoke the mailer.
    assert handle.smtp_capture.messages == []

    # 8. Both disposition actions SHALL have completed_at set
    #    (already structurally guaranteed by step 4's await).
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
    for row in rows:
        assert row[1] is not None, f"action {row[0]} not completed"
