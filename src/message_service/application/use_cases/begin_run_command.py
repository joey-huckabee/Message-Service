"""Input DTO for :class:`BeginRunUseCase`.

Translates ``BeginRunRequest`` (proto) into the use case's typed
command. Field-level validation happens here via Pydantic:

* Pipeline type is a non-empty string.
* Tags, declared stages, and templates are checked for basic
  well-formedness (non-empty, non-duplicate).

Business-rule validation (pipeline in registry, tags in vocabulary,
templates in manifest) happens inside the use case against injected
ports — it cannot happen in this DTO because those rules require
stateful lookups.

This DTO is deliberately agnostic to the transport. The gRPC servicer
constructs it from ``BeginRunRequest`` in the proto package; a REST
endpoint or CLI invoker would construct it equivalently.

Requirement references
----------------------
L1-RUN-003 (BeginRun validates every field before transitioning)
L2-RUN-007, L2-RUN-008, L2-RUN-009, L2-RUN-010
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from message_service.domain.aggregates.run import AttachmentMode
from message_service.domain.aggregates.template_ref import TemplateRef


class DeclaredStageInput(BaseModel):
    """One stage declaration within a :class:`BeginRunCommand`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage_id: str = Field(min_length=1)
    stage_order: int = Field(ge=0)
    report_template_ref: TemplateRef


class BeginRunCommand(BaseModel):
    """The use case's validated input command.

    Attributes:
        pipeline_type: Identifier of the pipeline issuing the run.
            Must be in ``pipelines.registered`` (enforced by use case).
        tags: Controlled-vocabulary tags. Must all be in the tag
            vocabulary (enforced by use case).
        declared_stages: Ordered sequence of stage declarations. Stage
            ids must be unique within the sequence (enforced here); at
            least zero (L3-RUN-015 permits empty).
        attachment_mode: ``SINGLE_AGGREGATED`` or ``PER_STAGE``.
        aggregation_template_ref: Required when ``attachment_mode`` is
            ``SINGLE_AGGREGATED``. Silently unused when ``PER_STAGE``
            (L3-RUN-018) — callers may supply it either way.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    pipeline_type: str = Field(min_length=1)
    tags: frozenset[str]
    declared_stages: tuple[DeclaredStageInput, ...]
    attachment_mode: AttachmentMode
    aggregation_template_ref: TemplateRef | None = None


__all__ = ["BeginRunCommand", "DeclaredStageInput"]
