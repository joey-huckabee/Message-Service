"""Schema-shape inspection tests for the templates configuration.

Covers L3-TMPL-025 (default size limits) and L3-TMPL-026 (positive-int
validation at startup).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from message_service.config.schema import TemplateRefConfig, TemplatesConfig


def _ref() -> TemplateRefConfig:
    return TemplateRefConfig(name="email_body", version="1.0")


# -----------------------------------------------------------------------------
# L3-TMPL-025: default size limits
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-TMPL-025")
def test_max_context_bytes_default_is_1_mib() -> None:
    """L3-TMPL-025: default ``templates.max_context_bytes`` SHALL be
    1_048_576 (1 MiB).
    """
    cfg = TemplatesConfig(
        manifest_path="manifest.toml",
        email_body_template_ref=_ref(),
    )
    assert cfg.max_context_bytes == 1_048_576


@pytest.mark.requirement("L3-TMPL-025")
def test_max_rendered_bytes_default_is_10_mib() -> None:
    """L3-TMPL-025: default ``templates.max_rendered_bytes`` SHALL be
    10_485_760 (10 MiB).
    """
    cfg = TemplatesConfig(
        manifest_path="manifest.toml",
        email_body_template_ref=_ref(),
    )
    assert cfg.max_rendered_bytes == 10_485_760


# -----------------------------------------------------------------------------
# L3-TMPL-026: positive-int validation
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-TMPL-026")
@pytest.mark.parametrize("bad_value", [0, -1, -1024])
def test_max_context_bytes_rejects_non_positive(bad_value: int) -> None:
    """L3-TMPL-026: zero or negative SHALL raise validation error."""
    with pytest.raises(ValidationError):
        TemplatesConfig(
            manifest_path="manifest.toml",
            email_body_template_ref=_ref(),
            max_context_bytes=bad_value,
        )


@pytest.mark.requirement("L3-TMPL-026")
@pytest.mark.parametrize("bad_value", [0, -1, -1024])
def test_max_rendered_bytes_rejects_non_positive(bad_value: int) -> None:
    """L3-TMPL-026: zero or negative SHALL raise validation error."""
    with pytest.raises(ValidationError):
        TemplatesConfig(
            manifest_path="manifest.toml",
            email_body_template_ref=_ref(),
            max_rendered_bytes=bad_value,
        )
