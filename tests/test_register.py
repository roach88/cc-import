"""Tests for ``__init__.py`` — verifies ``register(ctx)`` wires only the CLI
subcommand in slice 1.

Slice 2 will add agent tools (``ctx.register_tool``); slice 3 will add the
``on_session_start`` hook (``ctx.register_hook``). These assertions guard
against accidentally adding either before they're planned.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock


def _load_plugin_init():
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    spec = importlib.util.spec_from_file_location("cc_import_plugin", repo_root / "__init__.py")
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cc_import_plugin"] = mod
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
