"""Repo-root conftest — tells pytest to ignore the plugin's own Python files
during collection.

The plugin's ``__init__.py`` lives at the repo root (Hermes plugin
convention). Pytest's package detection would otherwise try to import
``__init__.py`` as part of test module discovery, failing on its
``from . import cli`` relative import (no parent package at that scope).
Excluding the plugin files here lets pytest collect ``tests/`` cleanly
while leaving the plugin's package layout intact for Hermes installs.
"""

collect_ignore = ["__init__.py", "cli.py", "converter.py"]
