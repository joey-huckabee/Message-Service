"""Persistence fixtures: SQLite databases, filesystem report stores.

Planned fixtures:

* ``sqlite_db_path`` — creates a fresh SQLite file under ``tmp_path``,
  applies all migrations, returns the path. Function scoped.
* ``sqlite_connection_pool`` — opens the connection pool against
  ``sqlite_db_path`` with the production pragma settings.
* ``temp_report_store`` — filesystem report store rooted at ``tmp_path``,
  implements the ``ReportStore`` port.
* ``seeded_runs`` — populates the database with a configurable set of
  runs in various states for sweeper and dashboard tests.
"""

from __future__ import annotations

# TODO(L3-PERS-002, L3-PERS-004, L3-PERS-005): implement when sqlite
# adapter lands.
