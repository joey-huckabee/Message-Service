"""Prometheus adapter for :class:`MetricsRecorder`.

Implements the L1-OBS-002 metric set with names + labels + buckets
pinned by L3-OBS-009 / L3-OBS-010 / L3-OBS-011. Backed by
``prometheus_client``'s default registry so a single ``/metrics``
FastAPI route (forthcoming in Increment 17) can scrape everything
this module declares.

Module-level metric singletons
------------------------------
Prometheus discourages re-declaring metrics in the same registry —
the second declaration raises ``ValueError("Duplicated timeseries…")``.
We declare each metric exactly once at import time. The
:class:`PrometheusMetricsRecorder` adapter is a thin wrapper that
calls ``.labels(...).inc()`` / ``.observe(...)`` on these shared
objects.

The sweeper iteration counter (added in Increment 14c.2) lives in
``infrastructure/sweeper/loop.py`` for historical reasons; that
declaration site is the authoritative one for L3-OBS-009's
``message_service_sweeper_iterations_total`` entry. Future cleanup
could move it here for symmetry, but it's not in 15's scope.

Requirement references
----------------------
L1-OBS-002, L2-OBS-004, L2-OBS-005, L2-OBS-006
L3-OBS-009 (metric names + labels)
L3-OBS-010 (email_size buckets)
L3-OBS-011 (run_duration buckets)
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

from message_service.application.ports.metrics_recorder import MetricsRecorder
from message_service.domain.state_machines.run_states import RunState
from message_service.domain.state_machines.stage_states import StageState

# -----------------------------------------------------------------------------
# Module-level Prometheus metrics. Names + labels per L3-OBS-009.
# -----------------------------------------------------------------------------

_RUN_STATE_TRANSITIONS = Counter(
    "message_service_run_state_transitions_total",
    "Count of run-lifecycle state transitions, labeled by target state.",
    labelnames=["target_state"],
)

_STAGE_STATE_TRANSITIONS = Counter(
    "message_service_stage_state_transitions_total",
    "Count of stage-lifecycle state transitions, labeled by target state.",
    labelnames=["target_state"],
)

_EMAIL_DELIVERY_OUTCOMES = Counter(
    "message_service_email_delivery_outcomes_total",
    "Count of email delivery attempts, labeled by outcome.",
    labelnames=["outcome"],
)

# L3-OBS-010: pinned bucket boundaries. 1 KiB / 10 KiB / 100 KiB /
# 1 MiB / 10 MiB / 25 MiB / 50 MiB cover typical and SMTP-limit
# boundary sizes.
_EMAIL_SIZE_BYTES = Histogram(
    "message_service_email_size_bytes",
    "Size of composed email messages in bytes (headers + body + attachments).",
    buckets=(1_000, 10_000, 100_000, 1_000_000, 10_000_000, 25_000_000, 50_000_000),
)

# L3-OBS-011: pinned bucket boundaries. Sub-minute through hour-scale.
_RUN_DURATION_SECONDS = Histogram(
    "message_service_run_duration_seconds",
    "End-to-end duration of a run, from BeginRun to terminal state.",
    buckets=(1, 5, 15, 60, 300, 900, 1800, 3600),
)


class PrometheusMetricsRecorder(MetricsRecorder):
    """Recorder that increments the module-level Prometheus singletons.

    Stateless — every method is a thin call onto the shared metric
    objects above. Construct one instance per service (in bootstrap)
    and inject into use cases through the port.
    """

    def record_run_state_transition(self, target_state: RunState) -> None:  # noqa: D102
        _RUN_STATE_TRANSITIONS.labels(target_state=target_state.value).inc()

    def record_stage_state_transition(self, target_state: StageState) -> None:  # noqa: D102
        _STAGE_STATE_TRANSITIONS.labels(target_state=target_state.value).inc()

    def record_email_delivery_outcome(self, outcome: str) -> None:  # noqa: D102
        _EMAIL_DELIVERY_OUTCOMES.labels(outcome=outcome).inc()

    def observe_email_size_bytes(self, size_bytes: int) -> None:  # noqa: D102
        _EMAIL_SIZE_BYTES.observe(size_bytes)

    def observe_run_duration_seconds(self, duration_seconds: float) -> None:  # noqa: D102
        _RUN_DURATION_SECONDS.observe(duration_seconds)


__all__ = ["PrometheusMetricsRecorder"]
