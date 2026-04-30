"""08-error-recovery — three deliberate gRPC misuse cases.

Demonstrates the structured-error envelope (L1-API-004 / L2-API-011):
every domain rejection surfaces as a gRPC status code paired with a
trailer ``x-message-service-error-code: <ERROR_CODE_*>``. Clients
match on the error code, not on the human-readable detail string.

Three cases are exercised:

1. UNKNOWN_PIPELINE_TYPE — pipeline not in
   ``[pipelines].registered``.
2. UNKNOWN_TAG — tag not in the configured vocabulary.
3. DUPLICATE_STAGE_ID — two declared stages share a stage_id.

No successful run is produced. The SMTP capture should be empty;
all errors come back synchronously from the gRPC call.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

# ruff: noqa: E402 — every import below depends on the sys.path tweak above.
import grpc
from examples._lib import common, pretty
from examples._lib.expectations import Expectations
from examples._lib.service_runner import running_service
from examples._lib.smtp_capture import SmtpCapture
from message_service_proto.v1 import message_service_pb2 as pb
from message_service_proto.v1 import message_service_pb2_grpc as pb_grpc

_HERE = Path(__file__).parent
_CONFIG = _HERE / "config.toml"
_TMP = _HERE / ".tmp"
_DB = _TMP / "message-service.db"
_REPORTS = _TMP / "reports"
_TEMPLATES_DIR = _HERE / "templates"
_TEMPLATES_MANIFEST = _HERE / "templates.toml"
_TAGS = _HERE / "tags.toml"

_GRPC_PORT = 50058
_DASHBOARD_PORT = 8087
_SMTP_PORT = 1032

_ERROR_CODE_TRAILER = "x-message-service-error-code"


@dataclass(frozen=True, slots=True)
class _ErrorObservation:
    """One captured gRPC error from a deliberate misuse case."""

    label: str
    grpc_code: str
    error_code: str
    detail: str


def _setup() -> None:
    common.reset_state_dirs(_TMP, _REPORTS)
    common.reset_sqlite_files(_DB)
    template_paths = common.write_default_templates(_TEMPLATES_DIR)
    common.write_template_manifest(_TEMPLATES_MANIFEST, template_paths)
    common.write_tag_vocabulary(_TAGS, ["production", "staging"])


def _extract_error_code(err: grpc.aio.AioRpcError) -> str:
    """Pull the ``x-message-service-error-code`` value out of trailers."""
    for key, value in err.trailing_metadata() or ():
        if key == _ERROR_CODE_TRAILER:
            return value
    return "(missing)"


def _valid_stage(stage_id: str, order: int) -> pb.DeclaredStage:
    return pb.DeclaredStage(
        stage_id=stage_id,
        stage_order=order,
        report_template=pb.TemplateRef(name="fragment", version="1.0"),
    )


async def _drive_each_case(
    stub: pb_grpc.MessageServiceStub,
) -> list[_ErrorObservation]:
    observations: list[_ErrorObservation] = []

    # ---- Case 1: unknown pipeline_type ----
    pretty.step(3, "Case 1: BeginRun with pipeline_type='not-registered'")
    try:
        await stub.BeginRun(
            pb.BeginRunRequest(
                pipeline_type="not-registered",
                run_tags=["production"],
                declared_stages=[_valid_stage("extract", 0)],
                attachment_mode=pb.AttachmentMode.ATTACHMENT_MODE_SINGLE_AGGREGATED,
                aggregation_template=pb.TemplateRef(name="aggregation", version="1.0"),
            )
        )
    except grpc.aio.AioRpcError as err:
        obs = _ErrorObservation(
            label="unknown pipeline_type",
            grpc_code=err.code().name,
            error_code=_extract_error_code(err),
            detail=err.details() or "",
        )
        observations.append(obs)
        pretty.detail(f"caught: gRPC {obs.grpc_code} / {obs.error_code}")
    else:
        pretty.failure("unknown-pipeline call did NOT raise")

    # ---- Case 2: unknown tag ----
    pretty.step(4, "Case 2: BeginRun with run_tags=['gibberish'] (not in vocabulary)")
    try:
        await stub.BeginRun(
            pb.BeginRunRequest(
                pipeline_type="etl-nightly",
                run_tags=["gibberish"],
                declared_stages=[_valid_stage("extract", 0)],
                attachment_mode=pb.AttachmentMode.ATTACHMENT_MODE_SINGLE_AGGREGATED,
                aggregation_template=pb.TemplateRef(name="aggregation", version="1.0"),
            )
        )
    except grpc.aio.AioRpcError as err:
        obs = _ErrorObservation(
            label="unknown tag",
            grpc_code=err.code().name,
            error_code=_extract_error_code(err),
            detail=err.details() or "",
        )
        observations.append(obs)
        pretty.detail(f"caught: gRPC {obs.grpc_code} / {obs.error_code}")
    else:
        pretty.failure("unknown-tag call did NOT raise")

    # ---- Case 3: duplicate stage_id ----
    pretty.step(5, "Case 3: BeginRun with two declared_stages sharing stage_id='extract'")
    try:
        await stub.BeginRun(
            pb.BeginRunRequest(
                pipeline_type="etl-nightly",
                run_tags=["production"],
                declared_stages=[
                    _valid_stage("extract", 0),
                    _valid_stage("extract", 1),  # duplicate stage_id, on purpose
                ],
                attachment_mode=pb.AttachmentMode.ATTACHMENT_MODE_SINGLE_AGGREGATED,
                aggregation_template=pb.TemplateRef(name="aggregation", version="1.0"),
            )
        )
    except grpc.aio.AioRpcError as err:
        obs = _ErrorObservation(
            label="duplicate stage_id",
            grpc_code=err.code().name,
            error_code=_extract_error_code(err),
            detail=err.details() or "",
        )
        observations.append(obs)
        pretty.detail(f"caught: gRPC {obs.grpc_code} / {obs.error_code}")
    else:
        pretty.failure("duplicate-stage call did NOT raise")

    return observations


async def _async_main() -> None:
    pretty.header("Scenario 08 — error recovery")
    pretty.step(1, "Reset state, write templates + tag vocabulary")
    _setup()

    with SmtpCapture(host="127.0.0.1", port=_SMTP_PORT) as capture:
        pretty.step(2, f"Booting service ({_CONFIG.relative_to(_REPO_ROOT)})")
        with running_service(
            _CONFIG,
            grpc_port=_GRPC_PORT,
            dashboard_port=_DASHBOARD_PORT,
        ):
            async with grpc.aio.insecure_channel(f"127.0.0.1:{_GRPC_PORT}") as channel:
                stub = pb_grpc.MessageServiceStub(channel)
                observations = await _drive_each_case(stub)

        pretty.header("Captured errors")
        for obs in observations:
            pretty.info(f"{obs.label}:")
            pretty.detail(f"  gRPC code:  {obs.grpc_code}")
            pretty.detail(f"  error code: {obs.error_code}")
            pretty.detail(f"  detail:     {obs.detail}")

        by_label = {obs.label: obs for obs in observations}

        pretty.header("Expectations")
        expect = Expectations()
        expect.length("three error cases observed", observations, 3)
        expect.length(
            "no SMTP messages — every BeginRun rejected before delivery",
            capture.messages,
            0,
        )

        # Case 1 expectations
        case1 = by_label.get("unknown pipeline_type")
        expect.truthy("case 1 captured", case1 is not None)
        if case1 is not None:
            expect.equals(
                "case 1 gRPC code is INVALID_ARGUMENT",
                case1.grpc_code,
                "INVALID_ARGUMENT",
            )
            expect.equals(
                "case 1 error code is ERROR_CODE_UNKNOWN_PIPELINE_TYPE",
                case1.error_code,
                "ERROR_CODE_UNKNOWN_PIPELINE_TYPE",
            )

        case2 = by_label.get("unknown tag")
        expect.truthy("case 2 captured", case2 is not None)
        if case2 is not None:
            expect.equals(
                "case 2 gRPC code is INVALID_ARGUMENT",
                case2.grpc_code,
                "INVALID_ARGUMENT",
            )
            expect.equals(
                "case 2 error code is ERROR_CODE_UNKNOWN_TAG",
                case2.error_code,
                "ERROR_CODE_UNKNOWN_TAG",
            )

        case3 = by_label.get("duplicate stage_id")
        expect.truthy("case 3 captured", case3 is not None)
        if case3 is not None:
            expect.equals(
                "case 3 gRPC code is INVALID_ARGUMENT",
                case3.grpc_code,
                "INVALID_ARGUMENT",
            )
            expect.equals(
                "case 3 error code is ERROR_CODE_DUPLICATE_STAGE_ID",
                case3.error_code,
                "ERROR_CODE_DUPLICATE_STAGE_ID",
            )

        expect.report_and_exit()


def main() -> None:
    """Entry point for ``poetry run python examples/08-error-recovery/run.py``."""
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
