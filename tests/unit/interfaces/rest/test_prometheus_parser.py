"""Tests for the DOM-free Prometheus-exposition parser (L3-DASH-036)."""

from __future__ import annotations

import math

import pytest

from message_service.interfaces.rest.prometheus_parser import parse_exposition


def _family(families: tuple[object, ...], name: str) -> object:
    return next(f for f in families if f.name == name)  # type: ignore[attr-defined]


@pytest.mark.requirement("L3-DASH-036")
def test_parses_counter_with_labels() -> None:
    """A counter family exposes its labeled samples."""
    text = (
        "# HELP svc_transitions_total Count of transitions.\n"
        "# TYPE svc_transitions_total counter\n"
        'svc_transitions_total{target_state="SENT"} 3.0\n'
        'svc_transitions_total{target_state="FAILED"} 1.0\n'
    )
    (family,) = parse_exposition(text)
    assert family.name == "svc_transitions_total"
    assert family.type == "counter"
    assert family.help == "Count of transitions."
    by_state = {s.labels["target_state"]: s.value for s in family.samples}
    assert by_state == {"SENT": 3.0, "FAILED": 1.0}


@pytest.mark.requirement("L3-DASH-036")
def test_parses_histogram_buckets_sum_and_count() -> None:
    """A histogram family groups its _bucket / _sum / _count samples."""
    text = (
        "# HELP svc_size_bytes Email size.\n"
        "# TYPE svc_size_bytes histogram\n"
        'svc_size_bytes_bucket{le="1000.0"} 0.0\n'
        'svc_size_bytes_bucket{le="+Inf"} 5.0\n'
        "svc_size_bytes_sum 2048.0\n"
        "svc_size_bytes_count 5.0\n"
    )
    (family,) = parse_exposition(text)
    assert family.name == "svc_size_bytes"
    assert family.type == "histogram"
    buckets = {s.labels["le"]: s.value for s in family.samples if s.name.endswith("_bucket")}
    assert buckets == {"1000.0": 0.0, "+Inf": 5.0}
    total = next(s.value for s in family.samples if s.name.endswith("_sum"))
    count = next(s.value for s in family.samples if s.name.endswith("_count"))
    assert (total, count) == (2048.0, 5.0)


@pytest.mark.requirement("L3-DASH-036")
def test_parses_inf_and_scientific_notation_values() -> None:
    """A sample whose value is +Inf or scientific notation parses to the right float."""
    text = "# TYPE svc_inf gauge\nsvc_inf +Inf\n# TYPE svc_sci gauge\nsvc_sci 1.78e+09\n"
    families = parse_exposition(text)
    assert math.isinf(_family(families, "svc_inf").samples[0].value)  # type: ignore[attr-defined]
    assert _family(families, "svc_sci").samples[0].value == 1.78e9  # type: ignore[attr-defined]


@pytest.mark.requirement("L3-DASH-036")
def test_families_preserve_declaration_order() -> None:
    """Families come back in # TYPE declaration order."""
    text = "# TYPE a_total counter\na_total 1.0\n# TYPE b_total counter\nb_total 2.0\n"
    assert [f.name for f in parse_exposition(text)] == ["a_total", "b_total"]


@pytest.mark.requirement("L3-DASH-036")
def test_malformed_and_empty_input_do_not_raise() -> None:
    """Blank input yields no families; junk lines are skipped."""
    assert parse_exposition("") == ()
    assert parse_exposition("\n\n   \n") == ()
    # A sample with no declared TYPE surfaces under an implicit untyped family.
    (family,) = parse_exposition("orphan_metric 42.0\n")
    assert family.name == "orphan_metric"
    assert family.type == "untyped"
    assert family.samples[0].value == 42.0


@pytest.mark.requirement("L3-DASH-036")
def test_parses_the_services_own_exposition() -> None:
    """End-to-end: parse the real exposition the service produces."""
    from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

    registry = CollectorRegistry()
    c = Counter("svc_outcomes_total", "Outcomes.", ["outcome"], registry=registry)
    c.labels(outcome="success").inc()
    h = Histogram("svc_dur_seconds", "Duration.", registry=registry, buckets=(1.0, 5.0))
    h.observe(3.0)
    families = parse_exposition(generate_latest(registry).decode())

    outcomes = _family(families, "svc_outcomes_total")
    assert outcomes.type == "counter"  # type: ignore[attr-defined]
    assert outcomes.samples[0].labels == {"outcome": "success"}  # type: ignore[attr-defined]

    dur = _family(families, "svc_dur_seconds")
    assert dur.type == "histogram"  # type: ignore[attr-defined]
    assert any(s.name.endswith("_count") and s.value == 1.0 for s in dur.samples)  # type: ignore[attr-defined]
