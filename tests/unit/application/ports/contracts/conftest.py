"""Shared helpers for port contract tests.

Port contract tests verify:

1. The port is an ``ABC`` — instantiation fails unless every
   ``@abstractmethod`` is implemented (L2-PERS-008).
2. :class:`~unittest.mock.MagicMock` with ``spec=Port`` exposes the
   same public methods as the real port (L3-PERS-013, L3-PERS-014),
   so use-case tests can substitute mocks freely.
3. Every abstract method carries correct type annotations — callers
   depend on mypy catching signature mismatches at use-case boundaries.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock


def assert_port_is_abstract_and_specable(port_cls: type[Any]) -> None:
    """Verify the port is a well-formed ABC with at least one abstract method.

    Args:
        port_cls: The port class under test.
    """
    # 1. Port must have at least one abstract method (a port with none is
    # trivially concrete and probably a mistake).
    abstract: frozenset[str] = getattr(port_cls, "__abstractmethods__", frozenset())
    assert abstract, f"{port_cls.__name__} has no abstract methods"

    # 2. Direct instantiation must fail (ABC enforcement).
    try:
        port_cls()
    except TypeError:
        pass
    else:
        raise AssertionError(f"{port_cls.__name__} was instantiable despite being abstract")

    # 3. MagicMock(spec=...) must successfully expose the public API.
    mock = MagicMock(spec=port_cls)
    for method_name in abstract:
        assert hasattr(mock, method_name), (
            f"MagicMock(spec={port_cls.__name__}) missing method '{method_name}'"
        )


def assert_all_abstract_methods_annotated(port_cls: type[Any]) -> None:
    """Every abstract method SHALL carry a return annotation.

    Missing annotations break mypy's ability to verify use-case
    contracts. We enforce that every abstract method declares its
    return type; parameter annotations are similarly required.

    Args:
        port_cls: The port class under test.
    """
    abstract: frozenset[str] = getattr(port_cls, "__abstractmethods__", frozenset())
    for method_name in abstract:
        method = inspect.getattr_static(port_cls, method_name)
        sig = inspect.signature(method)
        assert sig.return_annotation is not inspect.Signature.empty, (
            f"{port_cls.__name__}.{method_name} is missing a return annotation"
        )
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            assert param.annotation is not inspect.Parameter.empty, (
                f"{port_cls.__name__}.{method_name} "
                f"parameter '{param_name}' is missing an annotation"
            )
