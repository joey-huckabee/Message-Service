"""Inspection tests for the FastAPI app factory shape.

Covers L3-DASH-001 (no module-level `app` global; routers attached
via `include_router`) and L3-DASH-002 (lifespan startup/shutdown
handler registered via the lifespan context manager).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_APP_PATH = _PROJECT_ROOT / "src" / "message_service" / "interfaces" / "rest" / "app.py"


def _module_top_level_names() -> set[str]:
    """Return the set of top-level names assigned in app.py."""
    tree = ast.parse(_APP_PATH.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


@pytest.mark.requirement("L3-DASH-001")
def test_no_module_level_app_global() -> None:
    """L3-DASH-001: `create_app(service)` factory SHALL be the entrypoint;
    NO module-level `app` global SHALL exist (which would break the
    per-test/per-Service composition story).
    """
    top_level = _module_top_level_names()
    assert "app" not in top_level, (
        "interfaces/rest/app.py SHALL NOT define a module-level `app` global "
        "(L3-DASH-001 — every app instance comes from create_app(service))"
    )


@pytest.mark.requirement("L3-DASH-001")
def test_create_app_function_exists_and_is_factory_shaped() -> None:
    """L3-DASH-001: `create_app` SHALL exist as a callable accepting a
    `Service` and returning a `FastAPI` instance.
    """
    from message_service.interfaces.rest.app import create_app

    sig = inspect.signature(create_app)
    params = list(sig.parameters)
    assert params == ["service"], f"create_app signature SHALL be (service); got {params}"


@pytest.mark.requirement("L3-DASH-001")
def test_create_app_uses_include_router_for_router_attachment() -> None:
    """L3-DASH-001: routers SHALL be attached via `app.include_router`,
    not via a global router registry.
    """
    text = _APP_PATH.read_text(encoding="utf-8")
    assert "include_router" in text, (
        "interfaces/rest/app.py SHALL use app.include_router(...) for "
        "router attachment (L3-DASH-001)"
    )


@pytest.mark.requirement("L3-DASH-002")
def test_create_app_registers_lifespan_handler() -> None:
    """L3-DASH-002: the factory SHALL register startup/shutdown handlers
    via the lifespan context manager.
    """
    text = _APP_PATH.read_text(encoding="utf-8")
    # FastAPI 0.100+ uses the `lifespan=` kwarg to FastAPI(...).
    assert "lifespan=" in text, "create_app SHALL pass lifespan=... to FastAPI(...) (L3-DASH-002)"
