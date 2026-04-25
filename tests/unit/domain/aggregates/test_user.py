"""Unit tests for :class:`message_service.domain.aggregates.user.User`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from message_service.domain.aggregates.user import User

_T0 = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


@pytest.mark.requirement("L1-AUTH-001")
def test_user_constructs_with_minimum_required_fields() -> None:
    u = User(
        email="alice@example.com",
        display_name="Alice",
        password_hash="$argon2id$v=19$m=8,t=1,p=1$YWFhYWFhYWE$ZHVtbXk",
        created_at=_T0,
    )
    assert u.email == "alice@example.com"
    assert u.display_name == "Alice"
    assert u.user_id is None
    assert u.is_admin is False
    assert u.disabled is False


@pytest.mark.requirement("L1-AUTH-001")
def test_user_is_frozen() -> None:
    u = User(email="a@x", display_name="A", password_hash="h", created_at=_T0)
    with pytest.raises((AttributeError, TypeError)):
        u.email = "b@x"  # type: ignore[misc]


@pytest.mark.requirement("L1-AUTH-001")
@pytest.mark.parametrize("field", ["email", "display_name"])
def test_user_rejects_empty_required_text_fields(field: str) -> None:
    kwargs = {
        "email": "a@x",
        "display_name": "A",
        "password_hash": "h",
        "created_at": _T0,
    }
    kwargs[field] = ""
    with pytest.raises(ValueError, match=f"User.{field}"):
        User(**kwargs)  # type: ignore[arg-type]


@pytest.mark.requirement("L3-CFG-005")
def test_user_rejects_naive_created_at() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        User(
            email="a@x",
            display_name="A",
            password_hash="h",
            created_at=datetime(2026, 4, 21, 12, 0, 0),  # naive
        )


@pytest.mark.requirement("L1-AUTH-001")
def test_admin_and_disabled_flags_settable() -> None:
    u = User(
        email="a@x",
        display_name="A",
        password_hash="h",
        created_at=_T0,
        is_admin=True,
        disabled=True,
    )
    assert u.is_admin is True
    assert u.disabled is True


def test_user_equality_is_value_based() -> None:
    a = User(email="a@x", display_name="A", password_hash="h", created_at=_T0)
    b = User(email="a@x", display_name="A", password_hash="h", created_at=_T0)
    assert a == b
