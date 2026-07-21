"""Unit tests for :class:`message_service.domain.aggregates.session.Session`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from message_service.domain.aggregates.session import Session

_T0 = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)
_VALID_HASH = "a" * 64  # 64 hex chars


@pytest.mark.requirement("L1-AUTH-002")
def test_session_constructs_with_valid_hash_and_tz_aware_timestamps() -> None:
    s = Session(
        token_hash=_VALID_HASH,
        user_id=1,
        created_at=_T0,
        last_activity_at=_T0,
    )
    assert s.token_hash == _VALID_HASH
    assert s.user_id == 1


@pytest.mark.requirement("L3-AUTH-007")
@pytest.mark.parametrize(
    "bad_hash",
    ["", "a" * 63, "a" * 65, "g" * 64],  # too short, too long, non-hex
    ids=["empty", "63chars", "65chars", "non_hex"],
)
def test_session_rejects_invalid_token_hash(bad_hash: str) -> None:
    with pytest.raises(ValueError, match="token_hash"):
        Session(
            token_hash=bad_hash,
            user_id=1,
            created_at=_T0,
            last_activity_at=_T0,
        )


@pytest.mark.requirement("L1-AUTH-002")
def test_session_rejects_naive_created_at() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Session(
            token_hash=_VALID_HASH,
            user_id=1,
            created_at=datetime(2026, 4, 21, 12, 0, 0),  # naive
            last_activity_at=_T0,
        )


@pytest.mark.requirement("L1-AUTH-002")
def test_session_rejects_naive_last_activity_at() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Session(
            token_hash=_VALID_HASH,
            user_id=1,
            created_at=_T0,
            last_activity_at=datetime(2026, 4, 21, 12, 0, 0),  # naive
        )


@pytest.mark.requirement("L1-AUTH-002")
def test_session_rejects_last_activity_before_created() -> None:
    earlier = _T0 - timedelta(seconds=1)
    with pytest.raises(ValueError, match="precede"):
        Session(
            token_hash=_VALID_HASH,
            user_id=1,
            created_at=_T0,
            last_activity_at=earlier,
        )


@pytest.mark.requirement("L1-AUTH-002")
def test_session_allows_last_activity_equal_to_created() -> None:
    """The freshly-minted session has last_activity_at == created_at."""
    s = Session(
        token_hash=_VALID_HASH,
        user_id=1,
        created_at=_T0,
        last_activity_at=_T0,
    )
    assert s.last_activity_at == s.created_at


def test_session_is_frozen() -> None:
    s = Session(
        token_hash=_VALID_HASH,
        user_id=1,
        created_at=_T0,
        last_activity_at=_T0,
    )
    with pytest.raises((AttributeError, TypeError)):
        s.user_id = 2  # type: ignore[misc]
