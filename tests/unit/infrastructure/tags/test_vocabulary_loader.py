"""Unit tests for :mod:`message_service.infrastructure.tags.vocabulary_loader`."""

from __future__ import annotations

from pathlib import Path

import pytest

from message_service.domain.errors import ConfigurationError
from message_service.infrastructure.tags.vocabulary_loader import (
    InMemoryTagVocabulary,
    load_tag_vocabulary,
)

# -----------------------------------------------------------------------------
# InMemoryTagVocabulary direct construction
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-SUB-006")
def test_contains_is_case_sensitive() -> None:
    vocab = InMemoryTagVocabulary(frozenset({"production"}))
    assert vocab.contains("production") is True
    assert vocab.contains("Production") is False


@pytest.mark.requirement("L2-SUB-006")
def test_all_tags_returns_frozenset() -> None:
    original = frozenset({"a", "b", "c"})
    vocab = InMemoryTagVocabulary(original)
    assert vocab.all_tags() == original


# -----------------------------------------------------------------------------
# Loader happy path
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-SUB-009")
def test_loader_reads_multiple_tags(tmp_path: Path) -> None:
    vocab_file = tmp_path / "tags.toml"
    vocab_file.write_text(
        """
[[tag]]
name = "production"
description = "Prod tier"

[[tag]]
name = "critical"

[[tag]]
name = "debug"
"""
    )
    vocab = load_tag_vocabulary(vocab_file)
    assert vocab.all_tags() == frozenset({"production", "critical", "debug"})


@pytest.mark.requirement("L3-SUB-009")
def test_loader_accepts_empty_vocabulary(tmp_path: Path) -> None:
    """An empty file (no [[tag]] entries) loads an empty vocabulary."""
    vocab_file = tmp_path / "tags.toml"
    vocab_file.write_text("# no tags\n")
    vocab = load_tag_vocabulary(vocab_file)
    assert vocab.all_tags() == frozenset()


# -----------------------------------------------------------------------------
# Error paths
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-SUB-009")
def test_loader_raises_when_file_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.toml"
    with pytest.raises(ConfigurationError, match="not found"):
        load_tag_vocabulary(missing)


@pytest.mark.requirement("L3-SUB-009")
def test_loader_raises_on_malformed_toml(tmp_path: Path) -> None:
    vocab_file = tmp_path / "tags.toml"
    vocab_file.write_text("this is [[[ not valid toml")
    with pytest.raises(ConfigurationError) as exc_info:
        load_tag_vocabulary(vocab_file)
    assert "parser_error" in exc_info.value.details


@pytest.mark.requirement("L3-SUB-009")
def test_loader_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    vocab_file = tmp_path / "tags.toml"
    vocab_file.write_text(
        """
[[tag]]
name = "x"

[other_section]
key = "value"
"""
    )
    with pytest.raises(ConfigurationError, match="unknown top-level key"):
        load_tag_vocabulary(vocab_file)


@pytest.mark.requirement("L3-SUB-009")
def test_loader_rejects_unknown_tag_key(tmp_path: Path) -> None:
    vocab_file = tmp_path / "tags.toml"
    vocab_file.write_text(
        """
[[tag]]
name = "x"
category = "team"
"""
    )
    with pytest.raises(ConfigurationError) as exc_info:
        load_tag_vocabulary(vocab_file)
    assert "category" in exc_info.value.details["unknown_keys"]


@pytest.mark.requirement("L3-SUB-009")
def test_loader_rejects_tag_without_name(tmp_path: Path) -> None:
    vocab_file = tmp_path / "tags.toml"
    vocab_file.write_text(
        """
[[tag]]
description = "no name"
"""
    )
    with pytest.raises(ConfigurationError, match="missing required 'name'"):
        load_tag_vocabulary(vocab_file)


@pytest.mark.requirement("L3-SUB-010")
@pytest.mark.parametrize(
    "bad_name",
    [
        "Uppercase",
        "has spaces",
        "has.dot",
        "1starts_with_digit",
        "has$special",
        "",
        "x" * 65,  # too long
    ],
)
def test_loader_rejects_malformed_tag_name(tmp_path: Path, bad_name: str) -> None:
    vocab_file = tmp_path / "tags.toml"
    vocab_file.write_text(
        f"""
[[tag]]
name = "{bad_name}"
"""
    )
    with pytest.raises(ConfigurationError):
        load_tag_vocabulary(vocab_file)


@pytest.mark.requirement("L3-SUB-010")
@pytest.mark.parametrize(
    "good_name",
    [
        "production",
        "a",
        "a1",
        "has-dash",
        "has_underscore",
        "x" * 64,  # exactly at max
    ],
)
def test_loader_accepts_well_formed_tag_names(tmp_path: Path, good_name: str) -> None:
    vocab_file = tmp_path / "tags.toml"
    vocab_file.write_text(
        f"""
[[tag]]
name = "{good_name}"
"""
    )
    vocab = load_tag_vocabulary(vocab_file)
    assert good_name in vocab.all_tags()


@pytest.mark.requirement("L3-SUB-009")
def test_loader_rejects_duplicate_tag_names(tmp_path: Path) -> None:
    vocab_file = tmp_path / "tags.toml"
    vocab_file.write_text(
        """
[[tag]]
name = "production"

[[tag]]
name = "production"
"""
    )
    with pytest.raises(ConfigurationError, match="more than once"):
        load_tag_vocabulary(vocab_file)
