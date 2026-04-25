"""Port: Prometheus-style metrics recording.

Use cases call domain-meaningful methods on this port (e.g.,
``record_run_state_transition(RunState.AGGREGATING)``) and the
adapter translates to ``prometheus_client.Counter.labels(...).inc()``
under the hood. Domain and application layers stay framework-free —
no ``import prometheus_client`` outside ``infrastructure/``.

The metric names and label sets pinned by L3-OBS-009 / L3-OBS-010 /
L3-OBS-011 are *adapter-internal* concerns. Use cases name the
*event*, not the metric.

Implementations
---------------
* :class:`~message_service.infrastructure.observability.metrics.PrometheusMetricsRecorder`
  — production adapter writing to the prometheus_client default registry.
* :class:`NoOpMetricsRecorder` — defined here for tests that don't
  care about metrics; keeps test setup short.

Requirement references
----------------------
L1-OBS-002 (Prometheus metrics)
L2-OBS-004 (prometheus_client at /metrics)
L2-OBS-005 (message_service_ prefix)
L2-OBS-006 (the canonical metric set)
L3-OBS-009 (exact metric names + labels)
L3-OBS-010 (email_size buckets)
L3-OBS-011 (run_duration buckets)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from message_service.domain.state_machines.run_states import RunState
    from message_service.domain.state_machines.stage_states import StageState


class MetricsRecorder(ABC):
    """Abstract recorder for the L1-OBS-002 metric set.

    Methods are domain-named (`record_<event>`, `observe_<measure>`)
    not infrastructure-named (`counter_inc`, `histogram_observe`), so
    use cases stay framework-agnostic. The implementation maps each
    method to the specific Prometheus metric named by L3-OBS-009.
    """

    @abstractmethod
    def record_run_state_transition(self, target_state: RunState) -> None:
        """Record a run-lifecycle transition into ``target_state``.

        Maps to ``message_service_run_state_transitions_total{target_state}``.
        Called by every use case that transitions a Run.
        """

    @abstractmethod
    def record_stage_state_transition(self, target_state: StageState) -> None:
        """Record a stage transition into ``target_state``.

        Maps to ``message_service_stage_state_transitions_total{target_state}``.
        """

    @abstractmethod
    def record_email_delivery_outcome(self, outcome: str) -> None:
        """Record one email-delivery outcome.

        Maps to ``message_service_email_delivery_outcomes_total{outcome}``.

        Args:
            outcome: One of ``"success"``, ``"transient_failure"``,
                ``"permanent_failure"``. The label set is documented
                here rather than enforced as an enum to keep the port
                stable as new outcomes are added.
        """

    @abstractmethod
    def observe_email_size_bytes(self, size_bytes: int) -> None:
        """Observe an email's total size at delivery time.

        Maps to the ``message_service_email_size_bytes`` histogram.
        Buckets are pinned by L3-OBS-010.
        """

    @abstractmethod
    def observe_run_duration_seconds(self, duration_seconds: float) -> None:
        """Observe the end-to-end duration of one run.

        Called once when a run reaches a terminal state (``SENT``,
        ``FAILED``, ``ORPHANED``). Maps to the
        ``message_service_run_duration_seconds`` histogram. Buckets
        pinned by L3-OBS-011.
        """


class NoOpMetricsRecorder(MetricsRecorder):
    """Recorder that discards every observation. For tests."""

    def record_run_state_transition(self, target_state: RunState) -> None:  # noqa: D102
        return None

    def record_stage_state_transition(self, target_state: StageState) -> None:  # noqa: D102
        return None

    def record_email_delivery_outcome(self, outcome: str) -> None:  # noqa: D102
        return None

    def observe_email_size_bytes(self, size_bytes: int) -> None:  # noqa: D102
        return None

    def observe_run_duration_seconds(self, duration_seconds: float) -> None:  # noqa: D102
        return None


__all__ = ["MetricsRecorder", "NoOpMetricsRecorder"]
