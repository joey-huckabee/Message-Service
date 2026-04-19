"""Requirement-coverage conformance test.

This test parses ``docs/L1-REQ.md``, ``docs/L2-REQ.md``, and ``docs/L3-REQ.md``
and cross-checks:

* Every L2 has a ``Parent:`` field referencing an existing L1.
* Every L3 has a ``Parent:`` field referencing an existing L2.
* Every requirement status value is one of the permitted values.

Additionally, this test scans the test suite for ``@pytest.mark.requirement``
markers and asserts:

* Every referenced requirement id exists in one of the three REQ docs.
* (When enabled) every "Approved" requirement with a Test (T) verification
  method has at least one marked test.

The second assertion is gated by an environment variable
``MSG_SERVICE_ENFORCE_REQ_COVERAGE=1`` so that it can be introduced
gradually as implementation proceeds.
"""

from __future__ import annotations

# TODO: implement after L3-REQ.md is approved. The parser is straightforward
# — regex extract of identifier patterns from the markdown, then set
# comparisons. Kept as a stub here so the test tree is complete and the
# intent is captured.
