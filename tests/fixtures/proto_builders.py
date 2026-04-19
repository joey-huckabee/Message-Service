"""Builders for proto message objects used across test tiers.

Having a centralized builder module avoids scattering test-data
construction across every test file. Each builder returns a valid default
that tests override only with the fields relevant to them.

Planned builders:

* ``begin_run_request(*, pipeline_type=..., stages=..., tags=..., ...)`` —
  construct a valid ``BeginRunRequest`` with reasonable defaults.
* ``submit_stage_report_request(*, run_id, stage_id, context=..., ...)``.
* ``finalize_run_request(*, run_id)``.
* ``declared_stage(*, stage_id, order, template)``.
"""

from __future__ import annotations

# TODO(L3-API-005): implement once the proto stubs are installed via
# message-service-proto v0.1.0.
