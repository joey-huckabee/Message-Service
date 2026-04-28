"""Integration-test conftest.

Integration tests exercise multiple components together with real local
dependencies: real SQLite files in tmp directories, real Jinja2 renderers
against real template files, real aiosmtpd SMTP server in-process.

No external network calls, no shared filesystem state between tests.

Fixtures provided (to be implemented in ``tests/fixtures/``):

* ``sqlite_db_path`` — fresh SQLite file in a ``tmp_path`` directory,
  with migrations applied.
* ``sqlite_connection`` — a single aiosqlite connection bound to
  ``sqlite_db_path`` (per L2-PERS-004 post-Increment-27, the runtime
  uses one shared connection serialized via asyncio.Lock — not a pool).
* ``temp_report_store`` — filesystem report store rooted at ``tmp_path``.
* ``sandboxed_template_env`` — fully-configured ``SandboxedEnvironment``
  backed by a test manifest.
* ``fake_smtp_server`` — in-process aiosmtpd server capturing sent messages
  in memory; exposes ``.captured_messages`` list.
* ``test_config`` — a validated ``Config`` instance pointing at the
  fixture-provided SQLite path, report directory, and SMTP server.
"""

from __future__ import annotations

# Placeholder imports — populate as fixtures are implemented.
#
# from tests.fixtures.persistence import sqlite_db_path, sqlite_connection
# from tests.fixtures.persistence import temp_report_store
# from tests.fixtures.templating import sandboxed_template_env
# from tests.fixtures.email import fake_smtp_server
# from tests.fixtures.config import test_config
