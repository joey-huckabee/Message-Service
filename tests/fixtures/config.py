"""Configuration fixtures: ready-to-use Config objects for tests.

Planned fixtures:

* ``default_config`` — a minimally-valid ``Config`` with all required
  fields populated via other fixtures (sqlite_db_path, fake_smtp_server,
  temp_report_store, etc.).
* ``config_file`` — writes a TOML file to ``tmp_path`` matching
  ``default_config`` so tests can exercise the loader end-to-end.
"""

from __future__ import annotations

# TODO(L3-CFG-005): implement once Pydantic Config schema lands.
