"""Unit tests for :class:`message_service.domain.aggregates.password.Password`.

Per L3-AUTH-004 the Password value object SHALL redact its plaintext in
both ``repr`` and ``str``, expose the plaintext only via the explicit
``reveal()`` escape hatch, and use ``secrets.compare_digest`` for any
direct equality comparison so timing leaks are avoided.
"""

from __future__ import annotations

import pytest

from message_service.domain.aggregates.password import Password


@pytest.mark.requirement("L3-AUTH-004")
def test_repr_does_not_leak_plaintext() -> None:
    pw = Password("hunter2-this-must-not-leak")
    assert repr(pw) == "<Password>"
    assert "hunter2" not in repr(pw)


@pytest.mark.requirement("L3-AUTH-004")
def test_str_does_not_leak_plaintext() -> None:
    pw = Password("hunter2-this-must-not-leak")
    assert str(pw) == "<Password>"
    assert "hunter2" not in str(pw)


@pytest.mark.requirement("L3-AUTH-004")
def test_reveal_returns_plaintext() -> None:
    pw = Password("hunter2")
    assert pw.reveal() == "hunter2"


@pytest.mark.requirement("L1-AUTH-001")
def test_empty_plaintext_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        Password("")


@pytest.mark.requirement("L3-AUTH-004")
def test_constant_time_equals_matches_for_same_value() -> None:
    a = Password("xyz")
    b = Password("xyz")
    assert a.constant_time_equals(b)


@pytest.mark.requirement("L3-AUTH-004")
def test_constant_time_equals_rejects_different_values() -> None:
    a = Password("xyz")
    b = Password("abc")
    assert not a.constant_time_equals(b)


def test_password_is_frozen() -> None:
    pw = Password("xyz")
    with pytest.raises((AttributeError, TypeError)):
        pw._plaintext = "other"  # type: ignore[misc]
