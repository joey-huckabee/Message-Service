"""Architecture-boundary conformance test (L2-PERS-010, L3-PERS-016).

Enforces the hexagonal dependency rule: ``domain/`` and ``application/``
SHALL NOT import from ``infrastructure/`` or ``interfaces/``. The
``application/ports/`` subpackage is the sole exception — ports are
abstract and may be referenced from domain code through dependency
injection.

Implementation plan:

1. Walk ``src/message_service/domain/`` and ``src/message_service/application/``
   (excluding ``application/ports/``).
2. For each ``.py`` file, parse with ``ast`` and collect every ``Import``
   and ``ImportFrom`` node.
3. Assert no import path starts with ``message_service.infrastructure`` or
   ``message_service.interfaces``.

Failures SHALL report the offending file, line number, and disallowed
import, suitable for direct copy into a fix.
"""

from __future__ import annotations

# TODO: implement once domain/application modules have real content.
