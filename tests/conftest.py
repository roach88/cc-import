"""Test fixtures for the cc-import plugin.

Mirrors the ``_isolate_env`` pattern from
``hermes-agent/tests/plugins/test_disk_cleanup_plugin.py`` — every test gets a
fresh ``HERMES_HOME`` pointing at a tmp_path, so plugin state writes never
leak between tests or contaminate the developer's real ``~/.hermes/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate HERMES_HOME for each test."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    yield hermes_home
