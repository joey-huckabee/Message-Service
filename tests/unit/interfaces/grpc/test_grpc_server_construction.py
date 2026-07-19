"""Inspection tests for gRPC servicer + server-construction shape.

Covers L3-API-006 (servicer signatures), L3-API-007 (no streaming),
L3-API-008 (insecure-port-only), L3-API-017 (proto enum value 0 =
UNSPECIFIED), and L3-API-001 (max_concurrent_rpcs plumb-through to
``grpc.aio.server``).

These are static / inspection-shape tests. The runtime behavior they
guard (the server actually bounding RPCs, etc.) is exercised in
``tests/unit/test_main.py`` and ``tests/integration/grpc/test_servicer.py``.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from message_service_proto.v1 import message_service_pb2

from message_service.interfaces.grpc.servicer import MessageServiceServicer

_PROJECT_ROOT = Path(__file__).resolve().parents[4]


# -----------------------------------------------------------------------------
# L3-API-006: servicer methods are async def with (self, request, context)
# -----------------------------------------------------------------------------


_RPC_METHOD_NAMES = ("BeginRun", "SubmitStageReport", "FinalizeRun")


@pytest.mark.requirement("L3-API-006")
@pytest.mark.parametrize("method_name", _RPC_METHOD_NAMES)
def test_servicer_method_is_async_def(method_name: str) -> None:
    """L3-API-006: every servicer RPC method SHALL be ``async def``."""
    method = getattr(MessageServiceServicer, method_name)
    assert inspect.iscoroutinefunction(method), (
        f"MessageServiceServicer.{method_name} is not declared `async def`"
    )


@pytest.mark.requirement("L3-API-006")
@pytest.mark.parametrize("method_name", _RPC_METHOD_NAMES)
def test_servicer_method_signature_is_request_context(method_name: str) -> None:
    """L3-API-006: every servicer RPC method SHALL take exactly
    ``(self, request, context)`` — no extra parameters.
    """
    method = getattr(MessageServiceServicer, method_name)
    sig = inspect.signature(method)
    assert list(sig.parameters) == ["self", "request", "context"], (
        f"MessageServiceServicer.{method_name} has parameters "
        f"{list(sig.parameters)}, expected ['self', 'request', 'context']"
    )


# -----------------------------------------------------------------------------
# L3-API-007: no streaming-iterator parameters or returns on any public method
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-API-007")
@pytest.mark.parametrize("method_name", _RPC_METHOD_NAMES)
def test_servicer_method_takes_no_async_iterator_param(method_name: str) -> None:
    """L3-API-007: no public method SHALL accept a streaming iterator."""
    method = getattr(MessageServiceServicer, method_name)
    annotations = inspect.get_annotations(method)
    for param_name, annotation in annotations.items():
        if param_name == "return":
            continue
        # Reject the obvious streaming shapes.
        origin = getattr(annotation, "__origin__", None)
        bad_shapes = (AsyncIterator, Iterator)
        assert annotation not in bad_shapes and origin not in bad_shapes, (
            f"{method_name} parameter {param_name!r} appears to be a streaming "
            f"iterator: {annotation!r}"
        )


@pytest.mark.requirement("L3-API-007")
@pytest.mark.parametrize("method_name", _RPC_METHOD_NAMES)
def test_servicer_method_returns_no_async_iterator(method_name: str) -> None:
    """L3-API-007: no public method SHALL return an async iterator."""
    method = getattr(MessageServiceServicer, method_name)
    annotations = inspect.get_annotations(method)
    return_anno = annotations.get("return")
    if return_anno is None:
        return  # untyped return; no streaming declared
    origin = getattr(return_anno, "__origin__", None)
    bad_shapes = (AsyncIterator, Iterator)
    assert return_anno not in bad_shapes and origin not in bad_shapes, (
        f"{method_name} return annotation appears to be a streaming iterator: {return_anno!r}"
    )


# -----------------------------------------------------------------------------
# L3-API-008: __main__ uses add_insecure_port and never add_secure_port
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-API-008")
def test_main_uses_add_insecure_port_only() -> None:
    """L3-API-008: ``__main__.py`` SHALL call ``add_insecure_port`` and
    SHALL NOT call ``add_secure_port`` or construct ``grpc.ServerCredentials``.
    """
    main_path = _PROJECT_ROOT / "src" / "message_service" / "__main__.py"
    source = main_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    insecure_calls: list[ast.Call] = []
    secure_calls: list[ast.Call] = []
    credentials_constructions: list[ast.Attribute] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "add_insecure_port":
                insecure_calls.append(node)
            elif node.func.attr == "add_secure_port":
                secure_calls.append(node)
        if isinstance(node, ast.Attribute) and node.attr == "ServerCredentials":
            credentials_constructions.append(node)

    assert len(insecure_calls) >= 1, "__main__.py SHALL call add_insecure_port"
    assert len(secure_calls) == 0, (
        f"__main__.py SHALL NOT call add_secure_port (found {len(secure_calls)})"
    )
    assert len(credentials_constructions) == 0, (
        f"__main__.py SHALL NOT reference grpc.ServerCredentials "
        f"(found {len(credentials_constructions)})"
    )


# -----------------------------------------------------------------------------
# L3-API-017: proto ErrorCode value 0 is reserved for ERROR_CODE_UNSPECIFIED
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-API-017")
def test_proto_error_code_zero_is_unspecified() -> None:
    """L3-API-017: value 0 in the proto ``ErrorCode`` enum SHALL be
    ``ERROR_CODE_UNSPECIFIED``; no semantic code SHALL occupy value 0.
    """
    enum = message_service_pb2.ErrorCode
    name_at_zero = enum.Name(0)
    assert name_at_zero == "ERROR_CODE_UNSPECIFIED", (
        f"proto ErrorCode value 0 is {name_at_zero!r}, expected ERROR_CODE_UNSPECIFIED"
    )


# -----------------------------------------------------------------------------
# L3-API-001: max_concurrent_rpcs flows from config to grpc.aio.server
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-API-001")
async def test_build_grpc_server_passes_max_concurrent_rpcs() -> None:
    """L3-API-001: ``_build_grpc_server`` SHALL pass
    ``service.config.grpc.max_concurrent_rpcs`` to ``grpc.aio.server`` as
    its ``maximum_concurrent_rpcs`` keyword argument.
    """
    import grpc

    from message_service import __main__ as main_module

    captured_kwargs: dict[str, object] = {}

    real_server = grpc.aio.server

    def _capturing_server(*args: object, **kwargs: object) -> object:
        captured_kwargs.update(kwargs)
        return real_server(*args, **kwargs)

    # Build a minimal Service-shaped object — _build_grpc_server only
    # touches `service.config.grpc.{host,port,max_concurrent_rpcs,
    # max_in_flight_rpcs}` and passes the service to `register`. We patch
    # register to a no-op so we don't need a fully constructed Service here.
    class _Cfg:
        host = "127.0.0.1"
        port = 0
        max_concurrent_rpcs = 137
        max_in_flight_rpcs = 0

    class _Service:
        config = type("C", (), {"grpc": _Cfg})()

    with (
        patch.object(grpc.aio, "server", new=_capturing_server),
        patch.object(main_module, "register", new=lambda server, service: None),
    ):
        server, _bind = await main_module._build_grpc_server(_Service())  # type: ignore[arg-type]
        try:
            assert captured_kwargs.get("maximum_concurrent_rpcs") == 137
        finally:
            await server.stop(grace=0)


# -----------------------------------------------------------------------------
# L3-API-020: ConcurrencyLimitInterceptor installed only when limit > 0
# -----------------------------------------------------------------------------


async def _capture_interceptors(max_in_flight_rpcs: int) -> list[object]:
    """Build the gRPC server and return the interceptor list it was given."""
    import grpc

    from message_service import __main__ as main_module

    captured_kwargs: dict[str, object] = {}
    real_server = grpc.aio.server

    def _capturing_server(*args: object, **kwargs: object) -> object:
        captured_kwargs.update(kwargs)
        return real_server(*args, **kwargs)

    cfg_type = type(
        "_Cfg",
        (),
        {
            "host": "127.0.0.1",
            "port": 0,
            "max_concurrent_rpcs": 100,
            "max_in_flight_rpcs": max_in_flight_rpcs,
        },
    )

    class _Service:
        config = type("C", (), {"grpc": cfg_type})()

    with (
        patch.object(grpc.aio, "server", new=_capturing_server),
        patch.object(main_module, "register", new=lambda server, service: None),
    ):
        server, _bind = await main_module._build_grpc_server(_Service())  # type: ignore[arg-type]
        try:
            interceptors = captured_kwargs.get("interceptors", [])
            assert isinstance(interceptors, list)
            return interceptors
        finally:
            await server.stop(grace=0)


@pytest.mark.asyncio
@pytest.mark.requirement("L3-API-020")
async def test_concurrency_interceptor_absent_when_limit_disabled() -> None:
    """L3-API-020: with ``max_in_flight_rpcs == 0`` no limiter is installed."""
    from message_service.interfaces.grpc.concurrency_limit_interceptor import (
        ConcurrencyLimitInterceptor,
    )

    interceptors = await _capture_interceptors(max_in_flight_rpcs=0)
    assert not any(isinstance(i, ConcurrencyLimitInterceptor) for i in interceptors)


@pytest.mark.asyncio
@pytest.mark.requirement("L3-API-020")
async def test_concurrency_interceptor_installed_after_correlation_when_enabled() -> None:
    """L3-API-020: with a positive limit the limiter is installed, ordered
    after the correlation interceptor (so rejection logs carry correlation_id).
    """
    from message_service.interfaces.grpc.concurrency_limit_interceptor import (
        ConcurrencyLimitInterceptor,
    )
    from message_service.interfaces.grpc.correlation_interceptor import CorrelationIdInterceptor

    interceptors = await _capture_interceptors(max_in_flight_rpcs=5)
    types = [type(i) for i in interceptors]
    assert CorrelationIdInterceptor in types
    assert ConcurrencyLimitInterceptor in types
    assert types.index(CorrelationIdInterceptor) < types.index(ConcurrencyLimitInterceptor)
