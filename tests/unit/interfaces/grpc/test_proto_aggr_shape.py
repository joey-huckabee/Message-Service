"""Inspection tests for the proto-level shape of aggregation-related fields.

Covers L3-AGGR-001 (``ReportContribution.context`` is ``google.protobuf.Struct``).
The other L3-AGGR ids are covered by behavioral tests in
``test_servicer.py`` and ``test_assemble_and_deliver.py``.
"""

from __future__ import annotations

import pytest
from google.protobuf import struct_pb2
from message_service_proto.v1 import message_service_pb2 as pb


@pytest.mark.requirement("L3-AGGR-016")
def test_template_metadata_has_per_template_schema_field() -> None:
    """L3-AGGR-016: ``TemplateMetadata`` SHALL carry an optional
    ``context_schema_path`` field, allowing aggregation and stage-report
    templates to declare distinct schemas per `(name, version)` entry.
    The field is optional — schemas are not mandatory in v1 (per
    L3-TMPL-030).
    """
    from message_service.domain.aggregates.template_metadata import (
        TemplateMetadata,
    )

    fields = TemplateMetadata.__dataclass_fields__
    assert "context_schema_path" in fields, (
        "TemplateMetadata SHALL declare a context_schema_path field "
        "supporting per-template schema selection (L3-AGGR-016 + L3-TMPL-018)"
    )


@pytest.mark.requirement("L3-AGGR-001")
def test_report_contribution_context_field_is_struct() -> None:
    """L3-AGGR-001: ``ReportContribution.context`` SHALL be declared as
    ``google.protobuf.Struct``.

    Inspection via the proto descriptor: locate the ``context`` field on
    the ``ReportContribution`` message and assert its message type is
    the well-known ``Struct``. This pins the wire-format choice that
    enables arbitrary-shape stage context without per-template proto
    schemas.
    """
    msg = pb.ReportContribution.DESCRIPTOR
    field = msg.fields_by_name.get("context")
    assert field is not None, "ReportContribution SHALL have a `context` field"
    # Field type 11 = TYPE_MESSAGE.
    assert field.type == field.TYPE_MESSAGE
    # The message type SHALL be google.protobuf.Struct.
    assert field.message_type is struct_pb2.Struct.DESCRIPTOR, (
        f"context field type is {field.message_type.full_name!r}, expected google.protobuf.Struct"
    )
