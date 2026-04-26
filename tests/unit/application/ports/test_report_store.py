"""Unit tests for :mod:`message_service.application.ports.report_store`.

The port itself is an :class:`abc.ABC` with four methods; these tests
verify the abstract-method declaration (per L3-PERS-024) and that the
:class:`NoOpReportStore` implementation behaves as documented.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from message_service.application.ports.report_store import (
    NoOpReportStore,
    ReportStore,
)
from message_service.domain.ids import RunId, StageId

# -----------------------------------------------------------------------------
# Abstract port surface (L3-PERS-024 + L3-PERS-013/014)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-PERS-024")
def test_report_store_cannot_be_instantiated_directly() -> None:
    """The port is abstract; instantiation SHALL raise TypeError."""
    with pytest.raises(TypeError):
        ReportStore()  # type: ignore[abstract]


@pytest.mark.requirement("L3-PERS-024")
@pytest.mark.parametrize(
    "method_name",
    ["save_email_body", "read_email_body", "save_fragment", "read_fragment"],
)
def test_report_store_method_is_abstract(method_name: str) -> None:
    """L3-PERS-024 names four methods; each SHALL be declared abstract."""
    method = getattr(ReportStore, method_name)
    assert getattr(method, "__isabstractmethod__", False) is True


@pytest.mark.requirement("L3-PERS-014")
@pytest.mark.requirement("L3-PERS-024")
def test_report_store_is_specifiable_via_magicmock() -> None:
    """Abstract port SHALL be usable as a ``MagicMock(spec=ReportStore)``."""
    mock = MagicMock(spec=ReportStore)
    # Each declared method should be present on the spec'd mock.
    for name in ("save_email_body", "read_email_body", "save_fragment", "read_fragment"):
        assert hasattr(mock, name)


# -----------------------------------------------------------------------------
# NoOpReportStore behavior
# -----------------------------------------------------------------------------


_RID = RunId("00000000-0000-4000-8000-000000000001")
_SID = StageId("extract")


@pytest.mark.requirement("L3-PERS-024")
def test_noop_report_store_save_email_body_is_silent() -> None:
    """NoOp SHALL accept writes without raising."""
    NoOpReportStore().save_email_body(_RID, "<p>body</p>")


@pytest.mark.requirement("L3-PERS-024")
def test_noop_report_store_save_fragment_is_silent() -> None:
    NoOpReportStore().save_fragment(_RID, _SID, "<p>frag</p>")


@pytest.mark.requirement("L3-PERS-024")
def test_noop_report_store_read_email_body_returns_none() -> None:
    """NoOp SHALL return ``None`` from every read (no persistence)."""
    assert NoOpReportStore().read_email_body(_RID) is None


@pytest.mark.requirement("L3-PERS-024")
def test_noop_report_store_read_fragment_returns_none() -> None:
    assert NoOpReportStore().read_fragment(_RID, _SID) is None
