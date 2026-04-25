"""Unit test: ``dashboard.port`` and ``grpc.port`` must differ.

Verifies the L3-DASH-004 cross-section validator on
:class:`message_service.config.schema.Config`. The two listeners share
no state but cannot bind the same port, and the validator surfaces the
collision at config-load time rather than as a confusing
``OSError: address in use`` from one of the two servers at startup.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from message_service.config.schema import Config


def _valid_config_dict(*, grpc_port: int, dashboard_port: int) -> dict[str, object]:
    """Build the smallest dict that ``Config.model_validate`` accepts."""
    return {
        "grpc": {"host": "127.0.0.1", "port": grpc_port},
        "dashboard": {"host": "127.0.0.1", "port": dashboard_port},
        "persistence": {
            "sqlite_path": "/tmp/svc.db",
            "filesystem": {"report_directory": "/tmp/reports"},
        },
        "templates": {
            "manifest_path": "/tmp/templates.toml",
            "email_body_template_ref": {"name": "email_body", "version": "1.0"},
        },
        "tags": {"vocabulary_path": "/tmp/tags.toml"},
        "pipelines": {"registered": ["etl"]},
        "mail": {
            "from_address": "svc@example.com",
            "smtp": {"host": "smtp.example.com", "port": 587},
        },
    }


@pytest.mark.requirement("L3-DASH-004")
def test_distinct_ports_validate() -> None:
    """Distinct ports SHALL pass validation."""
    cfg = Config.model_validate(_valid_config_dict(grpc_port=50051, dashboard_port=8080))
    assert cfg.grpc.port == 50051
    assert cfg.dashboard.port == 8080


@pytest.mark.requirement("L3-DASH-004")
def test_collision_raises_validation_error() -> None:
    """``dashboard.port == grpc.port`` SHALL raise at validation time."""
    with pytest.raises(ValidationError, match="must differ from"):
        Config.model_validate(_valid_config_dict(grpc_port=50051, dashboard_port=50051))
