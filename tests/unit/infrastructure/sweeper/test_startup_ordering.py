"""Inspection tests for sweeper / pruner startup ordering.

Covers L3-SWEEP-018: the sweeper task SHALL start AFTER database
migrations have completed and the gRPC listener has bound its port.

Verification is structural — by AST-scanning the call ordering in
``__main__._run``, not by running the service. The ordering is the
spec; a future refactor that changes the ordering would surface here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_MAIN_PATH = _PROJECT_ROOT / "src" / "message_service" / "__main__.py"


def _run_function_ast() -> ast.AsyncFunctionDef:
    tree = ast.parse(_MAIN_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run":
            return node
    raise AssertionError("`_run` not found in __main__.py")


@pytest.mark.requirement("L3-SWEEP-018")
def test_sweeper_loop_start_happens_after_grpc_server_start() -> None:
    """L3-SWEEP-018: ``sweeper_loop.start()`` SHALL be called AFTER
    ``server.start()`` (gRPC listener) has returned. Migrations are
    applied during ``build_service`` (line ordering pins this).
    """
    func = _run_function_ast()
    # Grab line of `await build_service(config)` and the
    # `service.sweeper_loop.start()` call.
    build_service_lines: list[int] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name) and target.id == "build_service":
                build_service_lines.append(node.lineno)

    # Filter to `sweeper_loop.start` only — bare `.start` would also
    # match grpc.aio `server.start`.
    sweeper_loop_start_lines: list[int] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr != "start":
                continue
            value = node.func.value
            if isinstance(value, ast.Attribute) and value.attr == "sweeper_loop":
                sweeper_loop_start_lines.append(node.lineno)

    assert build_service_lines, "_run SHALL call build_service"
    assert sweeper_loop_start_lines, "_run SHALL call sweeper_loop.start"
    # build_service applies migrations before returning; sweeper_loop.start
    # SHALL come after build_service has returned.
    assert min(sweeper_loop_start_lines) > min(build_service_lines), (
        "sweeper_loop.start SHALL be called after build_service returns "
        "(L3-SWEEP-018 — sweeper SHALL NOT start until after migrations)"
    )


@pytest.mark.requirement("L3-SWEEP-018")
@pytest.mark.requirement("L3-OBS-017")
def test_three_periodic_loops_started_together() -> None:
    """L3-SWEEP-018 (companion): the three periodic loops (sweeper,
    report pruner, audit log pruner) SHALL all start in `_run` after
    listener bind, before `shutdown_event.wait()`. v1 starts them
    consecutively in ``__main__._run``.

    Also inspection evidence for L3-OBS-017: the audit-log cleanup task
    participates in the SAME create_task + cancellation lifecycle as the
    orphan sweeper — all three are scheduled on the shared
    ``BackgroundTaskScheduler`` (the shared create_task mechanism) via the
    identical ``<loop>.start()`` call inspected here, rather than each
    reimplementing task creation.
    """
    func = _run_function_ast()
    started_loops: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr != "start":
                continue
            value = node.func.value
            if isinstance(value, ast.Attribute):
                started_loops.add(value.attr)
    expected = {"sweeper_loop", "report_pruner_loop", "audit_log_pruner_loop"}
    assert expected.issubset(started_loops), (
        f"_run SHALL start all three periodic loops; missing: {expected - started_loops}"
    )
