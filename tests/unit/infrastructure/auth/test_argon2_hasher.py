"""Unit tests for :class:`Argon2PasswordHasher`.

The hasher is the only adapter for the :class:`PasswordHasher` port and
is the integration point with ``argon2-cffi``. Tests use small cost
parameters so the suite stays fast; the real production cost
parameters are pinned by L3-AUTH-002 and exercised in the bootstrap
test that builds a Service from a real Config.
"""

from __future__ import annotations

import pytest

from message_service.domain.aggregates.password import Password
from message_service.infrastructure.auth.argon2_hasher import Argon2PasswordHasher


@pytest.fixture
def hasher() -> Argon2PasswordHasher:
    # Minimum-cost params for fast tests; production values pinned by
    # L3-AUTH-002 and tested via the bootstrap fixture.
    return Argon2PasswordHasher(
        memory_cost=8,
        time_cost=1,
        parallelism=1,
        hash_len=16,
        salt_len=8,
    )


@pytest.mark.requirement("L2-AUTH-001")
@pytest.mark.requirement("L3-AUTH-001")
def test_hash_produces_argon2id_phc_string(hasher: Argon2PasswordHasher) -> None:
    """L2-AUTH-001 / L3-AUTH-001: hashing uses ``argon2.PasswordHasher``;
    output is the canonical Argon2id PHC string.
    """
    encoded = hasher.hash(Password("hunter2"))
    # PHC: $argon2id$v=...$m=...,t=...,p=...$<salt>$<hash>
    assert encoded.startswith("$argon2id$")


@pytest.mark.requirement("L1-AUTH-001")
def test_hash_then_verify_succeeds(hasher: Argon2PasswordHasher) -> None:
    pw = Password("hunter2")
    encoded = hasher.hash(pw)
    assert hasher.verify(pw, encoded) is True


@pytest.mark.requirement("L3-AUTH-013")
def test_verify_returns_false_on_mismatch(hasher: Argon2PasswordHasher) -> None:
    """Mismatched password SHALL return False (no leak via exception)."""
    encoded = hasher.hash(Password("hunter2"))
    assert hasher.verify(Password("not-the-password"), encoded) is False


@pytest.mark.requirement("L3-AUTH-013")
def test_verify_empty_stored_hash_returns_false(hasher: Argon2PasswordHasher) -> None:
    """Migration-003 default '' SHALL never authenticate any password."""
    assert hasher.verify(Password("anything"), "") is False


@pytest.mark.requirement("L1-AUTH-001")
def test_verify_malformed_stored_hash_raises_value_error(
    hasher: Argon2PasswordHasher,
) -> None:
    """A structurally invalid stored hash is an operator/data error,
    not a credential error — surface it via ValueError."""
    with pytest.raises(ValueError, match="malformed password hash"):
        hasher.verify(Password("anything"), "not-a-real-phc-string")


@pytest.mark.requirement("L2-AUTH-001")
def test_each_hash_includes_fresh_salt(hasher: Argon2PasswordHasher) -> None:
    """Argon2 salts are random per call; identical plaintext produces
    distinct PHC strings."""
    pw = Password("hunter2")
    a = hasher.hash(pw)
    b = hasher.hash(pw)
    assert a != b
    # Both still verify against their respective hashes.
    assert hasher.verify(pw, a)
    assert hasher.verify(pw, b)


@pytest.mark.requirement("L2-AUTH-002")
@pytest.mark.requirement("L3-AUTH-002")
def test_cost_parameters_are_honoured() -> None:
    """L3-AUTH-002: the hasher SHALL forward the configured cost params;
    verify by inspecting the encoded PHC string for the m, t, p fields.
    The defaults (memory_cost=65536, time_cost=3, parallelism=4,
    hash_len=32, salt_len=16) are pinned by the config schema; this test
    uses smaller values to keep the suite fast.
    """
    h = Argon2PasswordHasher(
        memory_cost=16,
        time_cost=2,
        parallelism=1,
        hash_len=16,
        salt_len=8,
    )
    encoded = h.hash(Password("x"))
    # PHC encodes as $argon2id$v=19$m=16,t=2,p=1$...
    assert "m=16" in encoded
    assert "t=2" in encoded
    assert "p=1" in encoded


@pytest.mark.requirement("L3-AUTH-002")
@pytest.mark.requirement("L3-AUTH-003")
def test_default_argon2_parameters_match_spec() -> None:
    """L3-AUTH-002: the spec'd defaults are memory_cost=65536, time_cost=3,
    parallelism=4, hash_len=32, salt_len=16. The Argon2Config schema
    provides these.

    L3-AUTH-003: those parameters are tuned for a 50-500 ms working factor
    on typical CI hardware; the parameter pinning here is the inspection
    artifact for that contract (v1 does not run a tracking benchmark per
    the L3-AUTH-003 reword — see the requirement text for rationale).
    """
    from message_service.config.schema import Argon2Config

    cfg = Argon2Config()
    assert cfg.memory_cost == 65_536
    assert cfg.time_cost == 3
    assert cfg.parallelism == 4
    assert cfg.hash_len == 32
    assert cfg.salt_len == 16
