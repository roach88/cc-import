"""Tests for ``__init__.py`` — verifies ``register(ctx)`` wires the slash
command (slice 1) and three agent tools (slice 2).

Slice 3 will add the ``on_session_start`` hook (``ctx.register_hook``);
that assertion still guards against accidental early registration.
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
    """``register(ctx)`` — slash command (slice 1) + agent tools (slice 2)."""

    def test_register_calls_register_command_exactly_once(self):
        ctx = MagicMock()
        _PLUGIN.register(ctx)
        ctx.register_command.assert_called_once()

    def test_register_uses_cc_import_as_command_name(self):
        ctx = MagicMock()
        _PLUGIN.register(ctx)
        kwargs = ctx.register_command.call_args.kwargs
        assert kwargs.get("name") == "cc-import"

    def test_register_passes_callable_handler(self):
        ctx = MagicMock()
        _PLUGIN.register(ctx)
        kwargs = ctx.register_command.call_args.kwargs
        assert callable(kwargs.get("handler"))

    def test_register_calls_register_tool_three_times(self):
        ctx = MagicMock()
        _PLUGIN.register(ctx)
        assert ctx.register_tool.call_count == 3

    def test_register_tool_names_are_cc_import_install_list_remove(self):
        ctx = MagicMock()
        _PLUGIN.register(ctx)
        names = [call.kwargs.get("name") for call in ctx.register_tool.call_args_list]
        assert names == ["cc_import_install", "cc_import_list", "cc_import_remove"]

    def test_register_tool_uses_cc_import_toolset(self):
        ctx = MagicMock()
        _PLUGIN.register(ctx)
        for call in ctx.register_tool.call_args_list:
            assert call.kwargs.get("toolset") == "cc_import"

    def test_register_tool_handlers_are_callable(self):
        ctx = MagicMock()
        _PLUGIN.register(ctx)
        for call in ctx.register_tool.call_args_list:
            assert callable(call.kwargs.get("handler"))

    def test_register_tool_schemas_are_dicts(self):
        ctx = MagicMock()
        _PLUGIN.register(ctx)
        for call in ctx.register_tool.call_args_list:
            schema = call.kwargs.get("schema")
            assert isinstance(schema, dict)
            assert "name" in schema and "description" in schema and "parameters" in schema

    def test_register_does_not_register_hooks(self):
        ctx = MagicMock()
        _PLUGIN.register(ctx)
        ctx.register_hook.assert_not_called()

    def test_register_does_not_register_top_level_cli_command(self):
        # register_cli_command's argparse wiring isn't actually consumed by
        # Hermes main.py; we use register_command (slash command) instead.
        ctx = MagicMock()
        _PLUGIN.register(ctx)
        ctx.register_cli_command.assert_not_called()
