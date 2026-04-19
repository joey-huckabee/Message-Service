"""Shared pytest fixtures for Message-Service.

Organized by concern. Fixtures are defined here and re-exported from
level-specific conftest files (``tests/unit/conftest.py``,
``tests/integration/conftest.py``, ``tests/e2e/conftest.py``) so that
they are discoverable by pytest at the appropriate scope.

Convention: one module per concern, each module exposing one or more
``@pytest.fixture``-decorated functions.
"""
