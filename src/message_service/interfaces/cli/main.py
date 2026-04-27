"""Console-script entry point declared in `[tool.poetry.scripts]`.

The actual lifecycle implementation lives in
:mod:`message_service.__main__` (the `python -m message_service`
entrypoint); this module is the namespace `pyproject.toml`'s
``message-service = "message_service.interfaces.cli.main:main"``
declaration points at, per `L3-DEP-015`. Keeping the
implementation in ``__main__.py`` lets ``python -m message_service``
work natively while routing the installed-script path through
the documented ``interfaces.cli`` namespace.
"""

from __future__ import annotations

from message_service.__main__ import main

__all__ = ["main"]
