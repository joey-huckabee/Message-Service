"""Contract tests for all 10 application-layer ports.

Each port is verified for:

* ABC enforcement (cannot instantiate without implementing all
  abstract methods).
* ``MagicMock(spec=Port)`` exposes every abstract method so use-case
  tests can substitute mocks.
* Every abstract method carries full type annotations (mypy depends on
  these).
* Async methods are declared ``async def``.

Requirement references
----------------------
L2-PERS-008 (ports live in application/ports as ABCs)
L3-PERS-013, L3-PERS-014 (MagicMock spec-compat for use-case tests)
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest
from tests.unit.application.ports.contracts.conftest import (
    assert_all_abstract_methods_annotated,
    assert_port_is_abstract_and_specable,
)

from message_service.application.ports.audit_log import AuditLog
from message_service.application.ports.background_task_scheduler import BackgroundTaskScheduler
from message_service.application.ports.mailer import Mailer
from message_service.application.ports.password_hasher import PasswordHasher
from message_service.application.ports.run_repository import RunRepository
from message_service.application.ports.session_repository import SessionRepository
from message_service.application.ports.stage_repository import StageRepository
from message_service.application.ports.subscription_repository import SubscriptionRepository
from message_service.application.ports.tag_vocabulary import TagVocabulary
from message_service.application.ports.template_renderer import TemplateRenderer
from message_service.application.ports.template_repository import TemplateRepository
from message_service.application.ports.unit_of_work import UnitOfWork
from message_service.application.ports.user_repository import UserRepository

ALL_PORTS = [
    RunRepository,
    StageRepository,
    TemplateRepository,
    SubscriptionRepository,
    Mailer,
    AuditLog,
    TagVocabulary,
    UnitOfWork,
    TemplateRenderer,
    BackgroundTaskScheduler,
    PasswordHasher,
    UserRepository,
    SessionRepository,
]


# -----------------------------------------------------------------------------
# Generic structural checks applied to every port
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-PERS-008")
@pytest.mark.parametrize("port_cls", ALL_PORTS, ids=lambda p: p.__name__)
def test_port_is_abstract_and_specable(port_cls: type) -> None:
    """Every port SHALL be an ABC usable as a MagicMock spec."""
    assert_port_is_abstract_and_specable(port_cls)


@pytest.mark.requirement("L3-PERS-014")
@pytest.mark.parametrize("port_cls", ALL_PORTS, ids=lambda p: p.__name__)
def test_port_methods_are_fully_annotated(port_cls: type) -> None:
    """Every abstract method SHALL have full type annotations."""
    assert_all_abstract_methods_annotated(port_cls)


# -----------------------------------------------------------------------------
# Per-port method-set verification
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-PERS-008")
def test_run_repository_exposes_expected_methods() -> None:
    expected = {
        "save",
        "get",
        "update_state",
        "list_in_states",
        "list_expired",
        "list_paginated",
    }
    assert expected == RunRepository.__abstractmethods__


@pytest.mark.requirement("L2-PERS-008")
def test_stage_repository_exposes_expected_methods() -> None:
    expected = {"save", "get", "list_by_run", "update_state", "list_pending_by_run"}
    assert expected == StageRepository.__abstractmethods__


@pytest.mark.requirement("L2-PERS-008")
def test_template_repository_exposes_expected_methods() -> None:
    expected = {"get", "exists", "resolve_latest", "list_by_kind", "list_all"}
    assert expected == TemplateRepository.__abstractmethods__


@pytest.mark.requirement("L2-PERS-008")
def test_subscription_repository_exposes_expected_methods() -> None:
    expected = {
        "list_recipients_for_run",
        "list_for_user",
        "get_by_id",
        "add",
        "remove",
    }
    assert expected == SubscriptionRepository.__abstractmethods__


@pytest.mark.requirement("L2-PERS-008")
def test_mailer_exposes_expected_methods() -> None:
    expected = {"send"}
    assert expected == Mailer.__abstractmethods__


@pytest.mark.requirement("L2-PERS-008")
def test_audit_log_exposes_expected_methods() -> None:
    expected = {"record", "query", "list_paginated", "delete_older_than"}
    assert expected == AuditLog.__abstractmethods__


@pytest.mark.requirement("L2-PERS-008")
def test_tag_vocabulary_exposes_expected_methods() -> None:
    expected = {"contains", "all_tags"}
    assert expected == TagVocabulary.__abstractmethods__


@pytest.mark.requirement("L3-RUN-004")
def test_unit_of_work_exposes_expected_methods() -> None:
    expected = {"__aenter__", "__aexit__", "commit", "rollback"}
    assert expected == UnitOfWork.__abstractmethods__


@pytest.mark.requirement("L2-TMPL-004")
def test_template_renderer_exposes_expected_methods() -> None:
    expected = {"render"}
    assert expected == TemplateRenderer.__abstractmethods__


@pytest.mark.requirement("L2-RUN-013")
def test_background_task_scheduler_exposes_expected_methods() -> None:
    expected = {"schedule"}
    assert expected == BackgroundTaskScheduler.__abstractmethods__


@pytest.mark.requirement("L1-AUTH-001")
def test_password_hasher_exposes_expected_methods() -> None:
    expected = {"hash", "verify"}
    assert expected == PasswordHasher.__abstractmethods__


@pytest.mark.requirement("L1-AUTH-001")
def test_user_repository_exposes_expected_methods() -> None:
    expected = {"save", "get_by_email", "get_by_id", "update"}
    assert expected == UserRepository.__abstractmethods__


@pytest.mark.requirement("L1-AUTH-002")
def test_session_repository_exposes_expected_methods() -> None:
    expected = {
        "save",
        "get_by_token_hash",
        "touch",
        "delete_by_token_hash",
        "delete_expired",
    }
    assert expected == SessionRepository.__abstractmethods__


# -----------------------------------------------------------------------------
# Async-ness: IO-bound methods are declared async
# -----------------------------------------------------------------------------


_ASYNC_METHODS: list[tuple[type, set[str]]] = [
    (
        RunRepository,
        {"save", "get", "update_state", "list_in_states", "list_expired"},
    ),
    (
        StageRepository,
        {"save", "get", "list_by_run", "update_state", "list_pending_by_run"},
    ),
    (
        SubscriptionRepository,
        {"list_recipients_for_run", "list_for_user", "add", "remove"},
    ),
    (Mailer, {"send"}),
    (AuditLog, {"record", "query"}),
    (UnitOfWork, {"__aenter__", "__aexit__", "commit", "rollback"}),
    (UserRepository, {"save", "get_by_email", "get_by_id"}),
    (
        SessionRepository,
        {
            "save",
            "get_by_token_hash",
            "touch",
            "delete_by_token_hash",
            "delete_expired",
        },
    ),
]


@pytest.mark.requirement("L2-PERS-008")
@pytest.mark.parametrize(
    ("port_cls", "async_methods"),
    _ASYNC_METHODS,
    ids=lambda x: x.__name__ if inspect.isclass(x) else repr(x),
)
def test_io_bound_ports_use_async_def(port_cls: type, async_methods: set[str]) -> None:
    """I/O-bound port methods SHALL be declared ``async def``."""
    for method_name in async_methods:
        method = inspect.getattr_static(port_cls, method_name)
        assert inspect.iscoroutinefunction(method), (
            f"{port_cls.__name__}.{method_name} SHOULD be async def"
        )


# -----------------------------------------------------------------------------
# Purely-sync ports do NOT use async (in-memory lookups, zero I/O)
# -----------------------------------------------------------------------------


def test_template_repository_is_sync() -> None:
    """TemplateRepository loads at startup; lookups are in-memory, not async."""
    for method_name in TemplateRepository.__abstractmethods__:
        method = inspect.getattr_static(TemplateRepository, method_name)
        assert not inspect.iscoroutinefunction(method), (
            f"TemplateRepository.{method_name} should be sync"
        )


def test_tag_vocabulary_is_sync() -> None:
    """TagVocabulary is an in-memory frozen set; lookups are sync."""
    for method_name in TagVocabulary.__abstractmethods__:
        method = inspect.getattr_static(TagVocabulary, method_name)
        assert not inspect.iscoroutinefunction(method), (
            f"TagVocabulary.{method_name} should be sync"
        )


def test_template_renderer_is_sync() -> None:
    """TemplateRenderer is CPU-bound (Jinja2); rendering is sync."""
    for method_name in TemplateRenderer.__abstractmethods__:
        method = inspect.getattr_static(TemplateRenderer, method_name)
        assert not inspect.iscoroutinefunction(method), (
            f"TemplateRenderer.{method_name} should be sync"
        )


def test_background_task_scheduler_is_sync() -> None:
    """BackgroundTaskScheduler.schedule SHALL NOT await; it returns immediately."""
    for method_name in BackgroundTaskScheduler.__abstractmethods__:
        method = inspect.getattr_static(BackgroundTaskScheduler, method_name)
        assert not inspect.iscoroutinefunction(method), (
            f"BackgroundTaskScheduler.{method_name} should be sync (non-blocking)"
        )


def test_password_hasher_is_sync() -> None:
    """PasswordHasher is CPU-bound (Argon2); both methods are sync."""
    for method_name in PasswordHasher.__abstractmethods__:
        method = inspect.getattr_static(PasswordHasher, method_name)
        assert not inspect.iscoroutinefunction(method), (
            f"PasswordHasher.{method_name} should be sync (CPU-bound)"
        )


# -----------------------------------------------------------------------------
# MagicMock compatibility smoke test per port
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-PERS-013")
@pytest.mark.parametrize("port_cls", ALL_PORTS, ids=lambda p: p.__name__)
def test_port_is_mockable_with_spec(port_cls: type) -> None:
    """Use-case tests SHALL be able to create MagicMock(spec=Port)."""
    mock = MagicMock(spec=port_cls)
    # Calling a non-existent method should raise AttributeError — that's
    # the whole point of spec=. We verify by poking a deliberately wrong
    # method name.
    with pytest.raises(AttributeError):
        mock.this_method_does_not_exist_on_the_port()
