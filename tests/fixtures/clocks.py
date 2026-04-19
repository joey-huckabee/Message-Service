"""Time-related fixtures: FakeClock, frozen system clock.

Planned fixtures:

* ``fake_clock`` — a ``FakeClock`` implementation of the ``Clock`` port
  with ``tick(seconds)`` and ``set(datetime)`` methods for deterministic
  time-based assertions.
* ``frozen_clock`` — monkeypatches ``datetime.now`` (used only in places
  that cannot accept Clock injection).
"""

from __future__ import annotations

# TODO(L3-RUN-024): implement once the Clock port lands in application/ports/.
