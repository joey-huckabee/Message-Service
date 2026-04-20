"""Input and result DTOs for :class:`FinalizeRunUseCase`.

The gRPC servicer constructs :class:`FinalizeRunCommand` from the
incoming ``FinalizeRunRequest`` proto and translates the returned
:class:`FinalizeRunResult` back into the response proto.

Requirement references
----------------------
L1-RUN-004 (FinalizeRun transitions AGGREGATING -> READY)
L2-RUN-012 (reject unless AGGREGATING)
L2-RUN-013 (non-blocking return)
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from message_service.domain.state_machines.run_states import RunState


class FinalizeRunCommand(BaseModel):
    """The use case's validated input command.

    Attributes:
        run_id: Target run identifier, canonical UUID-4 form.
            Well-formedness validated inside the use case via
            :func:`~message_service.domain.ids.validate_run_id_str`;
            existence validated against the repository.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)


class FinalizeRunResult(BaseModel):
    """The use case's structured return value.

    Returned promptly per L2-RUN-013: the run has transitioned to
    ``READY`` and the background assembly-and-delivery workflow has
    been scheduled, but has not yet been awaited.

    Attributes:
        run_id: The finalized run identifier, echoed back for
            caller-side correlation.
        state: Always :attr:`RunState.READY` on the success path. The
            background workflow will further transition through
            ``SENDING`` to ``SENT`` or ``FAILED``; callers that need
            to observe the eventual outcome poll the dashboard API or
            subscribe to audit events.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    state: RunState


__all__ = ["FinalizeRunCommand", "FinalizeRunResult"]
