"""Unit tests for the :class:`MetricsRecorder` port.

Pins the ABC contract and verifies :class:`NoOpMetricsRecorder` —
the test-only implementation defined in the port module — discards
every observation cleanly.
"""

from __future__ import annotations

import pytest

from message_service.application.ports.metrics_recorder import (
    MetricsRecorder,
    NoOpMetricsRecorder,
)
from message_service.domain.state_machines.run_states import RunState
from message_service.domain.state_machines.stage_states import StageState


def test_noop_recorder_implements_port() -> None:
    """NoOpMetricsRecorder is a usable MetricsRecorder."""
    assert isinstance(NoOpMetricsRecorder(), MetricsRecorder)


def test_noop_recorder_methods_silently_discard() -> None:
    """Every method discards the observation cleanly — no exception,
    no side effect. Required for use cases that take a MetricsRecorder
    by injection but the test doesn't care about metric output."""
    rec = NoOpMetricsRecorder()
    # Calls SHALL NOT raise. Type-system already pins the return as
    # None; we don't re-assert that here (mypy strict catches
    # `assert rec.method() is None` as a func-returns-value error).
    rec.record_run_state_transition(RunState.READY)
    rec.record_stage_state_transition(StageState.ACCEPTED)
    rec.record_email_delivery_outcome("success")
    rec.observe_email_size_bytes(1024)
    rec.observe_run_duration_seconds(60.0)


def test_metrics_recorder_cannot_be_instantiated_directly() -> None:
    """ABC enforcement — direct construction SHALL raise."""
    with pytest.raises(TypeError):
        MetricsRecorder()  # type: ignore[abstract]
