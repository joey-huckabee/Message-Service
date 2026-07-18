"""gRPC servicer — the thin adapter between the wire protocol and use cases.

Responsibilities:

* Translate protobuf request messages into Pydantic :class:`Command` models.
* Delegate to the injected use case on the :class:`Service`.
* Translate the use case's return value into the protobuf response.
* Catch every exception that escapes the use case and convert it to a
  gRPC status via :func:`translate_to_grpc_status`.

Everything else — validation, state transitions, persistence, templating,
delivery — happens below this layer. The servicer holds no business logic.

Design notes
------------
* Every RPC method is ``async def`` per L3-API-006.
* The servicer owns no mutable state — all state lives behind the
  :class:`Service` reference handed to the constructor.
* `google.protobuf.Struct` ↔ `dict` conversion uses
  :func:`google.protobuf.json_format.MessageToDict` with
  ``preserving_proto_field_name=True`` and
  ``always_print_fields_with_no_presence=False`` per L3-AGGR-002.
  (Note: the historical kwarg ``including_default_value_fields`` was
  renamed to ``always_print_fields_with_no_presence`` in protobuf 5.x;
  we use the current name.)
* Timestamp responses are produced from the injected :class:`Clock` via
  :class:`Service.clock.now` rather than calling ``datetime.now()``
  directly — keeps tests deterministic.

Requirement references
----------------------
L1-API-001, L1-API-002, L1-API-004
L2-API-003, L2-API-004, L2-API-005
L3-API-005, L3-API-006, L3-API-011
L3-AGGR-002 (Struct to dict conversion)
L3-AGGR-004 (email body position UNSPECIFIED -> AFTER + DEBUG log)
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import grpc
import structlog
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct
from google.protobuf.timestamp_pb2 import Timestamp

# mypy doesn't yet understand the generated stubs' typing well enough for
# us to import them at module top level under strict mode without noise;
# put them behind TYPE_CHECKING for type checkers and re-import at runtime.
if TYPE_CHECKING:
    from message_service_proto.v1 import message_service_pb2 as pb
    from message_service_proto.v1 import message_service_pb2_grpc as pb_grpc
else:
    from message_service_proto.v1 import message_service_pb2 as pb
    from message_service_proto.v1 import message_service_pb2_grpc as pb_grpc

from message_service.application.use_cases.begin_run_command import (
    BeginRunCommand,
    DeclaredStageInput,
)
from message_service.application.use_cases.finalize_run_command import (
    FinalizeRunCommand,
)
from message_service.application.use_cases.submit_stage_report_command import (
    SubmitStageReportCommand,
)
from message_service.domain.aggregates.email_body_position import EmailBodyPosition
from message_service.domain.aggregates.run import AttachmentMode
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.interfaces.grpc.error_mapping import translate_to_grpc_status

if TYPE_CHECKING:
    from message_service.bootstrap.service import Service

_log = structlog.get_logger(__name__)

# -----------------------------------------------------------------------------
# Enum translation
# -----------------------------------------------------------------------------

# Proto enum int values are exposed on the generated module.
_PROTO_TO_DOMAIN_ATTACHMENT_MODE: dict[int, AttachmentMode] = {
    pb.ATTACHMENT_MODE_SINGLE_AGGREGATED: AttachmentMode.SINGLE_AGGREGATED,
    pb.ATTACHMENT_MODE_PER_STAGE: AttachmentMode.PER_STAGE,
}


def _attachment_mode_to_domain(value: int) -> AttachmentMode:
    """Translate proto ``AttachmentMode`` → domain :class:`AttachmentMode`.

    ``ATTACHMENT_MODE_UNSPECIFIED`` (the proto default) maps to
    ``SINGLE_AGGREGATED`` because that is the service's documented
    fallback when the client omits the field — see L2-AGGR-004.
    """
    if value == pb.ATTACHMENT_MODE_UNSPECIFIED:
        return AttachmentMode.SINGLE_AGGREGATED
    try:
        return _PROTO_TO_DOMAIN_ATTACHMENT_MODE[value]
    except KeyError as exc:  # pragma: no cover — proto validation prevents this
        raise ValueError(f"unknown AttachmentMode enum value: {value}") from exc


_PROTO_TO_DOMAIN_EMAIL_BODY_POSITION: dict[int, EmailBodyPosition] = {
    pb.EMAIL_BODY_POSITION_BEFORE_STAGES_SUMMARY: EmailBodyPosition.BEFORE_STAGES_SUMMARY,
    pb.EMAIL_BODY_POSITION_AFTER_STAGES_SUMMARY: EmailBodyPosition.AFTER_STAGES_SUMMARY,
}


def _email_body_position_to_domain(value: int, *, run_id: str, stage_id: str) -> EmailBodyPosition:
    """Translate proto ``EmailBodyPosition`` → domain enum (L3-AGGR-004).

    ``EMAIL_BODY_POSITION_UNSPECIFIED`` (the proto3 default, sent
    whenever the client omits ``position`` on a contribution it does
    provide) resolves to ``AFTER_STAGES_SUMMARY`` and emits a DEBUG log
    recording the defaulting; the domain never sees ``UNSPECIFIED``.
    An explicit ``BEFORE``/``AFTER`` is translated verbatim with no log.

    Args:
        value: The proto ``EmailBodyPosition`` int value.
        run_id: Request's run id — diagnostic context for the log.
        stage_id: Request's stage id — diagnostic context for the log.

    Returns:
        The resolved domain :class:`EmailBodyPosition`.
    """
    if value == pb.EMAIL_BODY_POSITION_UNSPECIFIED:
        _log.debug(
            "email_body_position_defaulted",
            run_id=run_id,
            stage_id=stage_id,
            resolved_position=EmailBodyPosition.AFTER_STAGES_SUMMARY.value,
        )
        return EmailBodyPosition.AFTER_STAGES_SUMMARY
    try:
        return _PROTO_TO_DOMAIN_EMAIL_BODY_POSITION[value]
    except KeyError as exc:  # pragma: no cover — proto validation prevents this
        raise ValueError(f"unknown EmailBodyPosition enum value: {value}") from exc


# -----------------------------------------------------------------------------
# Struct <-> dict
# -----------------------------------------------------------------------------


def _struct_to_dict(struct: Struct) -> dict[str, Any]:
    """Convert a protobuf ``Struct`` into a plain Python ``dict``.

    L3-AGGR-002 pins the kwargs; we use the current (protobuf-5+) names.
    """
    result: dict[str, Any] = MessageToDict(
        struct,
        preserving_proto_field_name=True,
        always_print_fields_with_no_presence=False,
    )
    return result


# -----------------------------------------------------------------------------
# Timestamp helper
# -----------------------------------------------------------------------------


def _timestamp_from_datetime(dt: datetime) -> Timestamp:
    """Build a ``google.protobuf.Timestamp`` from a timezone-aware ``datetime``."""
    ts = Timestamp()
    ts.FromDatetime(dt)
    return ts


# -----------------------------------------------------------------------------
# Template-ref helper
# -----------------------------------------------------------------------------


def _has_template_ref(tr: pb.TemplateRef) -> bool:
    """Detect whether the caller actually populated a ``TemplateRef``.

    proto3 doesn't expose ``HasField`` for scalar-filled messages; a
    ``TemplateRef`` with empty name and version is treated as "not set".
    """
    return bool(tr.name) or bool(tr.version)


def _template_ref_to_domain(tr: pb.TemplateRef) -> TemplateRef:
    return TemplateRef(name=tr.name, version=tr.version)


# -----------------------------------------------------------------------------
# Servicer
# -----------------------------------------------------------------------------


class MessageServiceServicer(pb_grpc.MessageServiceServicer):  # type: ignore[misc]
    """gRPC servicer for the Message-Service wire interface.

    Holds a reference to a composed :class:`Service` from the bootstrap.
    Each RPC method pulls the relevant use case off the service, adapts
    the request, invokes the use case, and adapts the response.

    Attributes:
        service: The fully-composed :class:`Service` (see
            :mod:`message_service.bootstrap`).
    """

    def __init__(self, service: Service) -> None:
        """Bind to an already-constructed :class:`Service`."""
        self._service = service

    # -- BeginRun ------------------------------------------------------------

    async def BeginRun(  # noqa: N802 — gRPC-stubs naming
        self,
        request: pb.BeginRunRequest,
        context: grpc.aio.ServicerContext,
    ) -> pb.BeginRunResponse:
        """Handle ``MessageService.BeginRun``."""
        try:
            attachment_mode = _attachment_mode_to_domain(request.attachment_mode)

            declared = tuple(
                DeclaredStageInput(
                    stage_id=ds.stage_id,
                    stage_order=ds.stage_order,
                    report_template_ref=_template_ref_to_domain(ds.report_template),
                )
                for ds in request.declared_stages
            )

            aggregation_ref: TemplateRef | None = None
            if _has_template_ref(request.aggregation_template):
                aggregation_ref = _template_ref_to_domain(request.aggregation_template)

            cmd = BeginRunCommand(
                pipeline_type=request.pipeline_type,
                tags=frozenset(request.run_tags),
                declared_stages=declared,
                attachment_mode=attachment_mode,
                aggregation_template_ref=aggregation_ref,
            )

            run_id = await self._service.begin_run.execute(cmd)
            initiated_at = self._service.clock.now()

            return pb.BeginRunResponse(
                run_id=run_id,
                initiated_at=_timestamp_from_datetime(initiated_at),
            )
        except BaseException as exc:
            await translate_to_grpc_status(context, exc)
            # ``context.abort`` raised inside translate_to_grpc_status, so this
            # line is unreachable. It's here to make the type checker happy.
            raise  # pragma: no cover

    # -- SubmitStageReport ---------------------------------------------------

    async def SubmitStageReport(  # noqa: N802 — gRPC-stubs naming
        self,
        request: pb.SubmitStageReportRequest,
        context: grpc.aio.ServicerContext,
    ) -> pb.SubmitStageReportResponse:
        """Handle ``MessageService.SubmitStageReport``."""
        try:
            # Report contribution is required by the proto but the Struct
            # may be empty or absent in the dict conversion; the command
            # treats empty dict and None distinctly (L3-STAGE-010/011).
            if request.HasField("report_contribution"):
                report_ctx = _struct_to_dict(request.report_contribution.context)
            else:
                report_ctx = None

            # Email body contribution is optional. HasField is the right
            # presence check for a message-typed field. When present, its
            # position resolves UNSPECIFIED -> AFTER (L3-AGGR-004); when
            # absent, both context and position are None so the pairing
            # invariant (L3-AGGR-018) holds.
            if request.HasField("email_body_contribution"):
                body_ctx: dict[str, Any] | None = _struct_to_dict(
                    request.email_body_contribution.context
                )
                body_position: EmailBodyPosition | None = _email_body_position_to_domain(
                    request.email_body_contribution.position,
                    run_id=request.run_id,
                    stage_id=request.stage_id,
                )
            else:
                body_ctx = None
                body_position = None

            cmd = SubmitStageReportCommand(
                run_id=request.run_id,
                stage_id=request.stage_id,
                report_context=report_ctx,
                email_body_context=body_ctx,
                email_body_position=body_position,
            )

            result = await self._service.submit_stage_report.execute(cmd)
            accepted_at = self._service.clock.now()

            return pb.SubmitStageReportResponse(
                accepted_at=_timestamp_from_datetime(accepted_at),
                was_retry=result.was_retry,
            )
        except BaseException as exc:
            await translate_to_grpc_status(context, exc)
            raise  # pragma: no cover

    # -- FinalizeRun ---------------------------------------------------------

    async def FinalizeRun(  # noqa: N802 — gRPC-stubs naming
        self,
        request: pb.FinalizeRunRequest,
        context: grpc.aio.ServicerContext,
    ) -> pb.FinalizeRunResponse:
        """Handle ``MessageService.FinalizeRun``."""
        try:
            cmd = FinalizeRunCommand(run_id=request.run_id)
            await self._service.finalize_run.execute(cmd)
            finalized_at = self._service.clock.now()

            return pb.FinalizeRunResponse(
                finalized_at=_timestamp_from_datetime(finalized_at),
            )
        except BaseException as exc:
            await translate_to_grpc_status(context, exc)
            raise  # pragma: no cover


def register(server: grpc.aio.Server, service: Service) -> None:
    """Register the servicer on a gRPC server.

    Keeps the main entrypoint thin::

        server = grpc.aio.server()
        register(server, service)

    Args:
        server: The ``grpc.aio.Server`` to register on.
        service: A fully-built :class:`Service` instance.
    """
    pb_grpc.add_MessageServiceServicer_to_server(
        MessageServiceServicer(service),
        server,
    )


__all__ = ["MessageServiceServicer", "register"]
