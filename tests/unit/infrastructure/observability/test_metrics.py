"""Unit tests for :mod:`infrastructure.observability.metrics`.

Pins the metric names + label names + bucket boundaries from
L3-OBS-009, L3-OBS-010, L3-OBS-011. The module-level metric singletons
register against the prometheus_client default registry at import
time; tests read values back through the same registry.

Tests SHALL NOT reset metrics between cases — that would require
peeking at private prometheus_client state. Instead each test reads
"before" + "after" and asserts the delta, so cumulative state from
prior tests doesn't matter.
"""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

from message_service.domain.state_machines.run_states import RunState
from message_service.domain.state_machines.stage_states import StageState
from message_service.infrastructure.observability.metrics import (
    PrometheusMetricsRecorder,
)


def _counter(name: str, labels: dict[str, str]) -> float:
    """Read a counter sample by name + label dict from the default registry."""
    value = REGISTRY.get_sample_value(name, labels)
    return float(value) if value is not None else 0.0


def _histogram_count(name: str) -> float:
    """Read the _count sample of a histogram (total observations)."""
    value = REGISTRY.get_sample_value(f"{name}_count")
    return float(value) if value is not None else 0.0


@pytest.fixture
def recorder() -> PrometheusMetricsRecorder:
    return PrometheusMetricsRecorder()


# -----------------------------------------------------------------------------
# Run / stage state transitions (L3-OBS-009)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-OBS-009")
def test_run_state_transition_increments_labeled_counter(
    recorder: PrometheusMetricsRecorder,
) -> None:
    name = "message_service_run_state_transitions_total"
    labels = {"target_state": "READY"}
    before = _counter(name, labels)
    recorder.record_run_state_transition(RunState.READY)
    after = _counter(name, labels)
    assert after == before + 1


@pytest.mark.requirement("L3-OBS-009")
def test_stage_state_transition_increments_labeled_counter(
    recorder: PrometheusMetricsRecorder,
) -> None:
    name = "message_service_stage_state_transitions_total"
    labels = {"target_state": "ACCEPTED"}
    before = _counter(name, labels)
    recorder.record_stage_state_transition(StageState.ACCEPTED)
    after = _counter(name, labels)
    assert after == before + 1


# -----------------------------------------------------------------------------
# Email delivery outcomes (L3-OBS-009)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-OBS-009")
def test_email_delivery_outcome_counter(recorder: PrometheusMetricsRecorder) -> None:
    name = "message_service_email_delivery_outcomes_total"
    for outcome in ("success", "transient_failure", "permanent_failure"):
        before = _counter(name, {"outcome": outcome})
        recorder.record_email_delivery_outcome(outcome)
        after = _counter(name, {"outcome": outcome})
        assert after == before + 1, f"counter not incremented for outcome={outcome}"


# -----------------------------------------------------------------------------
# Email size histogram (L3-OBS-009 + L3-OBS-010)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-OBS-009")
def test_email_size_histogram_observe_increments_count(
    recorder: PrometheusMetricsRecorder,
) -> None:
    name = "message_service_email_size_bytes"
    before = _histogram_count(name)
    recorder.observe_email_size_bytes(50_000)
    after = _histogram_count(name)
    assert after == before + 1


def _bucket_le_floats(metric_name: str) -> set[float]:
    """Read the unique ``le`` boundary values from a histogram, normalized
    to floats so scientific notation (``"1e+06"``) and decimal
    (``"1000000.0"``) compare equal."""
    seen: set[float] = set()
    for metric in REGISTRY.collect():
        for sample in metric.samples:
            if sample.name == metric_name:
                le = sample.labels["le"]
                seen.add(float("inf") if le == "+Inf" else float(le))
    return seen


@pytest.mark.requirement("L3-OBS-010")
def test_email_size_histogram_buckets_pinned() -> None:
    """L3-OBS-010 pins exactly these bucket boundaries."""
    expected = {
        1_000.0,
        10_000.0,
        100_000.0,
        1_000_000.0,
        10_000_000.0,
        25_000_000.0,
        50_000_000.0,
        float("inf"),
    }
    assert _bucket_le_floats("message_service_email_size_bytes_bucket") == expected


# -----------------------------------------------------------------------------
# Run duration histogram (L3-OBS-009 + L3-OBS-011)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-OBS-009")
def test_run_duration_histogram_observe_increments_count(
    recorder: PrometheusMetricsRecorder,
) -> None:
    name = "message_service_run_duration_seconds"
    before = _histogram_count(name)
    recorder.observe_run_duration_seconds(42.0)
    after = _histogram_count(name)
    assert after == before + 1


@pytest.mark.requirement("L3-OBS-011")
def test_run_duration_histogram_buckets_pinned() -> None:
    """L3-OBS-011 pins exactly these bucket boundaries (sub-minute to hour-scale)."""
    expected = {1.0, 5.0, 15.0, 60.0, 300.0, 900.0, 1800.0, 3600.0, float("inf")}
    assert _bucket_le_floats("message_service_run_duration_seconds_bucket") == expected


# -----------------------------------------------------------------------------
# Naming convention (L3-OBS-008)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-OBS-008")
def test_all_module_metrics_use_message_service_prefix() -> None:
    """L3-OBS-008: every metric declared by the codebase SHALL be
    named with the ``message_service_`` prefix.

    ``prometheus_client`` strips the ``_total`` suffix from Counter
    family names internally (so the family is named
    ``message_service_run_state_transitions``, while the *sample*
    name carries ``_total``). Both forms must satisfy the prefix
    contract.
    """
    own_prefix = "message_service_"
    # Family names (Counter without _total + Histogram with bare name).
    expected_families = {
        "message_service_run_state_transitions",
        "message_service_stage_state_transitions",
        "message_service_email_delivery_outcomes",
        "message_service_email_size_bytes",
        "message_service_run_duration_seconds",
        "message_service_sweeper_iterations",
    }
    seen_families: set[str] = set()
    for metric in REGISTRY.collect():
        if metric.name.startswith(own_prefix):
            seen_families.add(metric.name)
    missing = expected_families - seen_families
    assert not missing, f"L3-OBS-008: expected metric families not registered: {sorted(missing)}"
    # Defense-in-depth: ANY metric with our prefix must satisfy the
    # naming convention (lowercase + underscores). Catch typos or
    # accidental UPPER_CASE additions.
    for fam_name in seen_families:
        assert fam_name == fam_name.lower(), f"metric family {fam_name!r} not lowercase"
