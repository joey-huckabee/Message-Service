"""Unit tests for :mod:`message_service.domain.ids`."""

from __future__ import annotations

import re

import pytest

from message_service.domain.errors import MalformedRequestError
from message_service.domain.ids import (
    RunId,
    new_run_id,
    validate_run_id_str,
)

# -----------------------------------------------------------------------------
# new_run_id (L3-RUN-001)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-001")
def test_new_run_id_returns_canonical_uuid4_string() -> None:
    """new_run_id SHALL return a canonical lowercase-hex UUID-4 string."""
    rid = new_run_id()
    assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", rid)


@pytest.mark.requirement("L3-RUN-001")
def test_new_run_id_generates_unique_values() -> None:
    """Successive calls SHALL return distinct ids."""
    ids = {new_run_id() for _ in range(100)}
    assert len(ids) == 100


# -----------------------------------------------------------------------------
# validate_run_id_str (L3-RUN-003)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-003")
def test_validate_run_id_accepts_canonical_form() -> None:
    valid = "00000000-0000-4000-8000-000000000000"
    assert valid == validate_run_id_str(valid)


@pytest.mark.requirement("L3-RUN-003")
@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-uuid",
        "00000000000040008000000000000000",  # no dashes
        "00000000-0000-4000-8000-00000000000",  # short
        "00000000-0000-4000-8000-0000000000000",  # long
        "00000000-0000-4000-8000-00000000000Z",  # non-hex char
        "00000000-0000-4000-8000-00000000000G",  # non-hex char
        "ABCDEFAB-0000-4000-8000-000000000000",  # uppercase rejected
        " 00000000-0000-4000-8000-000000000000",  # leading whitespace
        "00000000-0000-4000-8000-000000000000 ",  # trailing whitespace
    ],
)
def test_validate_run_id_rejects_malformed(bad: str) -> None:
    with pytest.raises(MalformedRequestError) as exc_info:
        validate_run_id_str(bad)
    assert exc_info.value.details["run_id"] == bad


@pytest.mark.requirement("L3-RUN-003")
def test_validate_run_id_error_includes_expected_pattern() -> None:
    with pytest.raises(MalformedRequestError) as exc_info:
        validate_run_id_str("garbage")
    assert "expected_pattern" in exc_info.value.details


# -----------------------------------------------------------------------------
# NewType identity (static-only; runtime behavior == underlying type)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-RUN-002")
def test_new_type_run_id_is_runtime_str() -> None:
    """NewType wrappers SHALL have zero runtime cost (isinstance str)."""
    rid: RunId = new_run_id()
    assert isinstance(rid, str)
