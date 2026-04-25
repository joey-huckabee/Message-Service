"""Unit tests for the FastAPI app factory's pure / non-I/O surface.

Covers the input model (``LoginRequest`` rejects extras / empty
fields) and the cookie helpers (correct attributes per L3-AUTH-008
and L3-AUTH-009). Behavioural tests of the middleware + routes that
require a real ``Service`` live in
``tests/integration/rest/test_app.py``.
"""

from __future__ import annotations

import pytest
from fastapi import Response
from pydantic import ValidationError

from message_service.interfaces.rest.app import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    LoginRequest,
    _clear_auth_cookies,
    _hash_token,
    _set_csrf_cookie,
    _set_session_cookie,
)

# -----------------------------------------------------------------------------
# LoginRequest schema
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L1-AUTH-001")
def test_login_request_accepts_minimal_valid_body() -> None:
    """A body with email + password SHALL validate."""
    body = LoginRequest.model_validate(
        {"email": "alice@example.com", "password": "hunter2"},
    )
    assert body.email == "alice@example.com"
    assert body.password == "hunter2"


@pytest.mark.requirement("L1-AUTH-001")
def test_login_request_rejects_extra_fields() -> None:
    """Extra body keys SHALL be rejected (defense against tampering)."""
    with pytest.raises(ValidationError):
        LoginRequest.model_validate(
            {
                "email": "alice@example.com",
                "password": "hunter2",
                "is_admin": True,  # parameter-tampering attempt
            },
        )


@pytest.mark.requirement("L1-AUTH-001")
def test_login_request_rejects_empty_email() -> None:
    """Empty email SHALL fail min_length validation."""
    with pytest.raises(ValidationError):
        LoginRequest.model_validate({"email": "", "password": "hunter2"})


@pytest.mark.requirement("L1-AUTH-001")
def test_login_request_rejects_empty_password() -> None:
    """Empty password SHALL fail min_length validation."""
    with pytest.raises(ValidationError):
        LoginRequest.model_validate({"email": "alice@example.com", "password": ""})


# -----------------------------------------------------------------------------
# Token hashing — L3-AUTH-007
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-AUTH-007")
def test_hash_token_is_sha256_hex() -> None:
    """``_hash_token`` SHALL produce a 64-char SHA-256 hex digest."""
    digest = _hash_token("a-plaintext-token")
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


@pytest.mark.requirement("L3-AUTH-007")
def test_hash_token_is_deterministic() -> None:
    """The same plaintext SHALL hash to the same digest."""
    assert _hash_token("alpha") == _hash_token("alpha")
    assert _hash_token("alpha") != _hash_token("beta")


# -----------------------------------------------------------------------------
# Cookie helpers — L3-AUTH-008, L3-AUTH-009
# -----------------------------------------------------------------------------


def _cookie_header(response: Response, name: str) -> str | None:
    """Find the ``Set-Cookie`` header for the given cookie name."""
    for raw_name, raw_value in response.raw_headers:
        if raw_name.lower() == b"set-cookie":
            decoded = raw_value.decode("latin-1")
            if decoded.startswith(f"{name}="):
                return decoded
    return None


@pytest.mark.requirement("L3-AUTH-008")
def test_set_session_cookie_uses_named_constants_and_safe_attrs() -> None:
    """L3-AUTH-008: cookie name + HttpOnly + SameSite=Lax."""
    response = Response()
    _set_session_cookie(response, "the-token", https_only=True)
    header = _cookie_header(response, SESSION_COOKIE_NAME)
    assert header is not None
    assert "HttpOnly" in header
    assert "SameSite=lax" in header.lower() or "samesite=lax" in header.lower()
    assert "Secure" in header


@pytest.mark.requirement("L3-AUTH-009")
def test_set_session_cookie_drops_secure_when_https_only_false() -> None:
    """L3-AUTH-009: ``Secure`` is gated by ``https_only``."""
    response = Response()
    _set_session_cookie(response, "the-token", https_only=False)
    header = _cookie_header(response, SESSION_COOKIE_NAME)
    assert header is not None
    assert "Secure" not in header


@pytest.mark.requirement("L3-DASH-018")
def test_set_csrf_cookie_is_not_httponly() -> None:
    """The CSRF cookie SHALL be readable by JS (no HttpOnly)."""
    response = Response()
    _set_csrf_cookie(response, "csrf-token-123", https_only=True)
    header = _cookie_header(response, CSRF_COOKIE_NAME)
    assert header is not None
    assert "HttpOnly" not in header


def test_clear_auth_cookies_emits_deletion_for_both() -> None:
    """``_clear_auth_cookies`` SHALL emit deletion ``Set-Cookie`` for both names."""
    response = Response()
    _clear_auth_cookies(response)
    set_cookie_values = [
        value.decode("latin-1")
        for name, value in response.raw_headers
        if name.lower() == b"set-cookie"
    ]
    combined = "\n".join(set_cookie_values)
    assert SESSION_COOKIE_NAME in combined
    assert CSRF_COOKIE_NAME in combined
