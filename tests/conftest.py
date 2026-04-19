"""Shared pytest configuration and fixtures.

Per-category fixtures (domain, application, infrastructure, e2e) live in
``tests/fixtures/`` and are imported here as needed. This file intentionally
stays small — fixture definitions belong near the code they support.
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:  # noqa: ARG001
    """Tag test items by the directory they come from so `-m unit` works without per-file markers."""
    for item in items:
        path_parts = item.path.parts
        if "unit" in path_parts:
            item.add_marker(pytest.mark.unit)
        elif "integration" in path_parts:
            item.add_marker(pytest.mark.integration)
        elif "e2e" in path_parts:
            item.add_marker(pytest.mark.e2e)
