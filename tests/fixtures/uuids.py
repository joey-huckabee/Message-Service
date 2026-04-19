"""UUID-related fixtures for tests asserting on minted identifiers.

Planned fixtures:

* ``frozen_uuid`` — monkeypatches ``uuid.uuid4`` to return a deterministic
  sequence. Yields the list of UUIDs that will be returned, in order.
* ``uuid_sequence`` — allows tests to request a specific sequence of UUIDs
  rather than auto-generating them.
"""

from __future__ import annotations

# TODO(L3-RUN-001): implement once use cases exist that mint run_ids.
