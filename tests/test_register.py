"""Tests for ``__init__.py`` — verifies ``register(ctx)`` wires only the CLI
subcommand in slice 1.

Slice 2 will add agent tools (``ctx.register_tool``); slice 3 will add the
``on_session_start`` hook (``ctx.register_hook``). These assertions guard
against accidentally adding either before they're planned.

Loading uses the namespace-package pattern from
``hermes-agent/tests/plugins/test_disk_cleanup_plugin.py`` so the
``from . import cli`` relative import inside ``__init__.py`` resolves.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock


def _load_plugin_init():
    repo_root = Path(__file__).resolve().parents[1]
    plugin_dir = repo_root
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.cc_import",
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    assert spec is not None
    assert spec.loader is not None
    if "hermes_plugins" not in sys.modules:
        ns = types.ModuleType("hermes_plugins")
        ns.__path__ = []
        sys.modules["hermes_plugins"] = ns
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "hermes_plugins.cc_import"
    mod.__path__ = [str(plugin_dir)]
    sys.modules["hermes_plugins.cc_import"] = mod
    spec.loader.exec_module(mod)
    return mod


_PLUGIN = _load_plugin_init()


class TestRegister:
    """``register(ctx)`` — slice 1 wires CLI only, no tools, no hooks."""

    def test_register_calls_register_cli_command_exactly_once(self):
        ctx = MagicMock()
        _PLUGIN.register(ctx)
        ctx.register_cli_command.assert_called_once()

    def test_register_uses_cc_import_as_name(self):
        ctx = MagicMock()
        _PLUGIN.register(ctx)
        kwargs = ctx.register_cli_command.call_args.kwargs
        assert kwargs.get("name") == "cc-import"

    def test_register_passes_callable_setup_fn(self):
        ctx = MagicMock()
        _PLUGIN.register(ctx)
        kwargs = ctx.register_cli_command.call_args.kwargs
        assert callable(kwargs.get("setup_fn"))

    def test_register_does_not_register_tools(self):
        ctx = MagicMock()
        _PLUGIN.register(ctx)
        ctx.register_tool.assert_not_called()

    def test_register_does_not_register_hooks(self):
        ctx = MagicMock()
        _PLUGIN.register(ctx)
        ctx.register_hook.assert_not_called()

    def test_register_does_not_register_slash_commands(self):
        ctx = MagicMock()
        _PLUGIN.register(ctx)
        ctx.register_command.assert_not_called()
