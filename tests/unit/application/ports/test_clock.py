"""Unit tests for :mod:`message_service.application.ports.clock`."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from message_service.application.ports.clock import (
    Clock,
    is_iso_z,
    iso_z,
)

# -----------------------------------------------------------------------------
# Clock ABC contract (L2-RUN-014, L3-RUN-024)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-024")
def test_clock_cannot_be_instantiated_directly() -> None:
    """Clock is abstract; attempting to instantiate SHALL raise TypeError."""
    with pytest.raises(TypeError):
        Clock()  # type: ignore[abstract]


@pytest.mark.requirement("L3-RUN-024")
def test_clock_now_is_abstract() -> None:
    """Clock.now SHALL be declared abstract."""
    assert getattr(Clock.now, "__isabstractmethod__", False) is True


# -----------------------------------------------------------------------------
# iso_z formatting (L3-RUN-025)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-025")
def test_iso_z_emits_literal_z_suffix() -> None:
    """Output SHALL end with ``Z``, never ``+00:00``."""
    value = datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc)
    formatted = iso_z(value)
    assert formatted.endswith("Z")
    assert "+00:00" not in formatted


@pytest.mark.requirement("L3-RUN-025")
def test_iso_z_preserves_microseconds() -> None:
    """Microseconds SHALL appear in the output when non-zero."""
    value = datetime(2026, 4, 19, 12, 0, 0, 123456, tzinfo=timezone.utc)
    assert iso_z(value) == "2026-04-19T12:00:00.123456Z"


@pytest.mark.requirement("L3-RUN-025")
def test_iso_z_omits_microseconds_when_zero() -> None:
    """Whole-second times SHALL NOT carry a trailing ``.000000``."""
    value = datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc)
    assert iso_z(value) == "2026-04-19T12:00:00Z"


@pytest.mark.requirement("L3-RUN-025")
def test_iso_z_converts_non_utc_to_utc() -> None:
    """A tz-aware non-UTC datetime SHALL be converted to UTC before formatting."""
    est = timezone(timedelta(hours=-5))
    value = datetime(2026, 4, 19, 7, 0, 0, tzinfo=est)  # 12:00 UTC
    assert iso_z(value) == "2026-04-19T12:00:00Z"


@pytest.mark.requirement("L3-RUN-025")
def test_iso_z_rejects_naive_datetime() -> None:
    """Naive datetimes SHALL raise ValueError — no silent UTC assumption."""
    naive = datetime(2026, 4, 19, 12, 0, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        iso_z(naive)


# -----------------------------------------------------------------------------
# is_iso_z validator (L3-RUN-025)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-025")
@pytest.mark.parametrize(
    "candidate",
    [
        "2026-04-19T12:00:00Z",
        "2026-04-19T12:00:00.123456Z",
        "1970-01-01T00:00:00Z",
        "2026-12-31T23:59:59.999999Z",
    ],
)
def test_is_iso_z_accepts_valid_forms(candidate: str) -> None:
    assert is_iso_z(candidate) is True


@pytest.mark.requirement("L3-RUN-025")
@pytest.mark.parametrize(
    "candidate",
    [
        "2026-04-19T12:00:00+00:00",  # plus-offset form forbidden
        "2026-04-19T12:00:00",  # no suffix
        "2026-04-19 12:00:00Z",  # space instead of T
        "2026-04-19T12:00Z",  # missing seconds
        "",
        "not a timestamp",
    ],
)
def test_is_iso_z_rejects_invalid_forms(candidate: str) -> None:
    assert is_iso_z(candidate) is False


@pytest.mark.requirement("L3-RUN-025")
def test_iso_z_output_always_passes_is_iso_z() -> None:
    """Property-style: the formatter's output SHALL always pass the validator."""
    samples = [
        datetime(1970, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 19, 12, 0, 0, 1, tzinfo=timezone.utc),
        datetime(2099, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc),
    ]
    for value in samples:
        formatted = iso_z(value)
        assert is_iso_z(formatted), f"round-trip failed for {value!r} -> {formatted!r}"


# -----------------------------------------------------------------------------
# Pattern-structure sanity check
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-025")
def test_iso_z_pattern_matches_spec() -> None:
    """The regex SHALL match the exact spec grammar; use a hand-written check."""
    spec = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
    value = datetime(2026, 4, 19, 12, 30, 45, 678901, tzinfo=timezone.utc)
    assert spec.match(iso_z(value))
