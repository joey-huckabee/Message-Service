"""Conformance: schema default ⊆ bootstrap-registered disposition handlers.

A service started with the shipped default config SHALL never reference an
action id that bootstrap doesn't have a handler for. Otherwise the
``SweeperUseCase`` constructor would raise ``ConfigurationError`` against
defaults the operator never overrode — i.e., the service wouldn't start
out of the box. This test prevents that drift from recurring.

The registry is the single source of truth for "implemented today"; the
schema default is the contract for "what runs without operator
intervention." This test asserts the second is a subset of the first.

Requirement references
----------------------
L1-SWEEP-003 (disposition policy)
L2-SWEEP-007 (configurable disposition set)
L3-SWEEP-012 (unknown action ids rejected at startup)
"""

from __future__ import annotations

import pytest

from message_service.config.schema import SweeperConfig
from message_service.infrastructure.sweeper.handlers import (
    build_disposition_handler_registry,
)


@pytest.mark.requirement("L2-SWEEP-007")
def test_schema_default_actions_are_all_registered() -> None:
    default_actions = set(SweeperConfig().disposition_actions)
    registered = set(build_disposition_handler_registry().keys())

    missing = default_actions - registered
    assert not missing, (
        f"schema default disposition_actions reference unregistered handlers: "
        f"{sorted(missing)}; registered={sorted(registered)}"
    )
