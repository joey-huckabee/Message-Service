"""Unit tests for the gRPC ``Struct`` → ``dict`` conversion helpers.

A protobuf ``Struct`` stores every number as a double, so integer inputs
arrive as Python floats. ``_struct_to_dict`` demotes integral, finite
doubles back to ``int`` (L3-AGGR-002) so a record count of ``42`` does
not render as ``"42.0"`` in the assembled report.
"""

from __future__ import annotations

import json

import pytest
from google.protobuf.struct_pb2 import Struct

from message_service.interfaces.grpc.servicer import (
    _demote_integral_floats,
    _struct_to_dict,
)


@pytest.mark.requirement("L3-AGGR-002")
def test_integral_struct_number_becomes_int_not_float() -> None:
    struct = Struct()
    struct.update({"records": 42})

    result = _struct_to_dict(struct)

    assert result["records"] == 42
    assert isinstance(result["records"], int)
    # And it serializes as "42", not "42.0".
    assert json.dumps(result) == '{"records": 42}'


@pytest.mark.requirement("L3-AGGR-002")
def test_fractional_struct_number_stays_float() -> None:
    struct = Struct()
    struct.update({"ratio": 0.5})

    result = _struct_to_dict(struct)

    assert result["ratio"] == 0.5
    assert isinstance(result["ratio"], float)


@pytest.mark.requirement("L3-AGGR-002")
def test_bool_struct_value_is_not_coerced_to_int() -> None:
    struct = Struct()
    struct.update({"ok": True, "off": False})

    result = _struct_to_dict(struct)

    assert result["ok"] is True
    assert result["off"] is False


@pytest.mark.requirement("L3-AGGR-002")
def test_nested_and_list_integral_floats_demoted_recursively() -> None:
    struct = Struct()
    struct.update(
        {
            "meta": {"count": 3, "rate": 1.5},
            "series": [1, 2, 3.5],
        }
    )

    result = _struct_to_dict(struct)

    assert result["meta"]["count"] == 3
    assert isinstance(result["meta"]["count"], int)
    assert result["meta"]["rate"] == 1.5
    assert result["series"] == [1, 2, 3.5]
    assert [type(v) for v in result["series"]] == [int, int, float]


@pytest.mark.requirement("L3-AGGR-002")
def test_demote_helper_leaves_non_finite_untouched() -> None:
    assert _demote_integral_floats(float("inf")) == float("inf")
    # nan != nan, so compare via isinstance + isnan semantics.
    result = _demote_integral_floats(float("nan"))
    assert isinstance(result, float)
    assert result != result
