"""Port: :class:`DispositionHandler` — per-action sweeper handlers.

Each disposition action identifier in
:data:`message_service.config.schema.DispositionAction`
(``SEND_PARTIAL_FLAGGED``, ``DISCARD_SILENTLY``, ``NOTIFY_SUBSCRIBERS``,
``NOTIFY_ADMINS``) is implemented as an independent async handler.

The :class:`SweeperUseCase` dispatches to every handler whose
identifier appears in the configured ``sweeper.disposition_actions``
list, in the order they appear (L2-SWEEP-008, L2-SWEEP-009).

Handlers are invoked **after** the ``INITIATED/AGGREGATING/READY/SENDING
→ ORPHANED`` transition commits (L2-SWEEP-006). A handler that raises
logs the failure but does not roll back the state transition — the
run is already ORPHANED and the dispositions are best-effort
notifications.

Requirement references
----------------------
L2-SWEEP-008, L2-SWEEP-009
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from message_service.config.schema import DispositionAction
    from message_service.domain.aggregates.run import Run


class DispositionHandler(ABC):
    """Protocol for a single disposition action.

    Subclasses declare :attr:`action_id` as a class-level attribute
    matching one of the :data:`DispositionAction` literal values. The
    bootstrap registers handlers by this identifier so the sweeper can
    dispatch by config string.

    Attributes:
        action_id: The :data:`DispositionAction` identifier this
            handler implements. Subclasses MUST override.
    """

    action_id: ClassVar[DispositionAction]

    @abstractmethod
    async def handle(self, run: Run) -> None:
        """Execute the disposition action for ``run``.

        Args:
            run: The aggregate that has just been transitioned to
                ``ORPHANED``. Handlers may read any aggregate field
                but MUST NOT mutate the run or the repository directly;
                state changes belong in use cases.

        Raises:
            Exception: The sweeper catches and logs handler failures;
                a raised exception does not roll back the ORPHANED
                transition that preceded dispatch. Handlers should
                still raise on failure so the operator sees the
                structured log entry.
        """


__all__ = ["DispositionHandler"]
