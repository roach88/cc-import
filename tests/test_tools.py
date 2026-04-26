"""Tests for ``tools.py`` — agent-callable tool handlers and schemas."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


def _load_module(name: str, filename: str):
    repo_root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(name, repo_root / filename)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# tools.py's absolute-import fallback uses ``import converter`` and
# ``import state``, so both modules must be registered in sys.modules under
# those exact names before tools.py loads.
_CONVERTER = _load_module("converter", "converter.py")
_STATE = _load_module("state", "state.py")
_TOOLS = _load_module("cc_import_tools", "tools.py")


# Convenience: tools.py's handlers return JSON strings; tests parse them.
def _result(s: str) -> dict:
    return json.loads(s)


# ---------------------------------------------------------------------------
# _handle_install
# ---------------------------------------------------------------------------


class TestHandleInstall:
    """``_handle_install(args, **kwargs) -> JSON str`` — R1, R3, R8, R10."""

    def test_happy_path_returns_summary_with_deferred_state_flags(self, monkeypatch):
        captured: dict = {}

        def fake_import(git_url, **kwargs):
            captured["git_url"] = git_url
            captured["branch"] = kwargs.get("branch")
            captured["subdir"] = kwargs.get("subdir")
            return _CONVERTER.ImportSummary(plugin="myplug", skills_imported=2, agents_translated=1)

        monkeypatch.setattr(_TOOLS.converter, "import_plugin", fake_import)
        out = _result(_TOOLS._handle_install({"git_url": "https://github.com/Foo/Bar.git"}))
        # Envelope: {result: {...}, available_now, available_after, notice}
        assert out["result"]["plugin"] == "myplug"
        assert out["result"]["skills_imported"] == 2
        assert out["result"]["agents_translated"] == 1
        assert out["available_now"] is False
        assert out["available_after"] == "next_session"
        assert "notice" in out
        # Defaults applied
        assert captured["branch"] == "main"
        assert captured["subdir"] == ""

    def test_explicit_branch_and_subdir_pass_through(self, monkeypatch):
        captured: dict = {}

        def fake_import(git_url, **kwargs):
            captured.update(kwargs)
            return _CONVERTER.ImportSummary(plugin="p")

        monkeypatch.setattr(_TOOLS.converter, "import_plugin", fake_import)
        _TOOLS._handle_install(
            {
                "git_url": "https://gitlab.com/Foo/Bar.git",
                "branch": "dev",
                "subdir": "plugins/foo",
            }
        )
        assert captured["branch"] == "dev"
        assert captured["subdir"] == "plugins/foo"

    def test_missing_git_url_returns_missing_arg_error(self):
        out = _result(_TOOLS._handle_install({}))
        assert out["error"] == "missing_arg"
        assert "git_url" in out.get("message", "")

    def test_empty_git_url_returns_missing_arg_error(self):
        out = _result(_TOOLS._handle_install({"git_url": "  "}))
        assert out["error"] == "missing_arg"

    def test_disallowed_host_returns_disallowed_host_error(self):
        out = _result(_TOOLS._handle_install({"git_url": "https://evil.com/payload.git"}))
        assert out["error"] == "disallowed_host"

    def test_non_https_scheme_returns_invalid_arg_error(self):
        out = _result(_TOOLS._handle_install({"git_url": "file:///tmp/repo"}))
        assert out["error"] == "invalid_arg"

    def test_credential_url_returns_invalid_arg_not_disallowed_host(self):
        """Greptile P2: credential URLs must not be misclassified as disallowed_host.

        The dispatch used to substring-match ``"host"`` in the message,
        which matched the literal ``"user:token@host"`` example inside
        the credential-rejection text. Only ``"allowlist"`` (unique to
        the host-allowlist message) should pick disallowed_host.
        """
        out = _result(
            _TOOLS._handle_install({"git_url": "https://user:token@github.com/Foo/Bar.git"})
        )
        assert out["error"] == "invalid_arg", (
            f"credential URL should be invalid_arg, got {out['error']}: {out.get('message')}"
        )

    def test_unsafe_basename_url_returns_invalid_arg_error(self):
        """Greptile P1: ..-tailed allowlisted URL must reject before any clone."""
        out = _result(_TOOLS._handle_install({"git_url": "https://github.com/foo/.."}))
        assert out["error"] == "invalid_arg"
        assert (
            "basename" in out.get("message", "").lower() or "safe" in out.get("message", "").lower()
        )

    def test_clone_failure_returns_clone_failed_error(self, monkeypatch):
        def fake_import(*_a, **_kw):
            raise subprocess.CalledProcessError(returncode=128, cmd=["git", "clone"])

        monkeypatch.setattr(_TOOLS.converter, "import_plugin", fake_import)
        out = _result(_TOOLS._handle_install({"git_url": "https://github.com/Foo/Bar.git"}))
        assert out["error"] == "clone_failed"
        assert "128" in out.get("message", "")

    def test_clone_timeout_returns_clone_timeout_error(self, monkeypatch):
        # subprocess.TimeoutExpired propagates from clone_or_update; the
        # handler must map it to a distinct tool_error code so the agent
        # can surface "the network is slow / try again later" cleanly
        # without confusing it with a hard clone failure.
        def fake_import(*_a, **_kw):
            raise subprocess.TimeoutExpired(cmd=["git", "clone"], timeout=120)

        monkeypatch.setattr(_TOOLS.converter, "import_plugin", fake_import)
        out = _result(_TOOLS._handle_install({"git_url": "https://github.com/Foo/Bar.git"}))
        assert out["error"] == "clone_timeout"
        assert "120" in out.get("message", "")
        assert "CC_IMPORT_CLONE_TIMEOUT" in out.get("message", "")

    def test_validator_value_error_from_import_plugin_returns_invalid_arg(self, monkeypatch):
        # _validate_plugin_name / _validate_subdir raise ValueError inside import_plugin
        def fake_import(*_a, **_kw):
            raise ValueError("plugin_name '../core' contains disallowed characters")

        monkeypatch.setattr(_TOOLS.converter, "import_plugin", fake_import)
        out = _result(_TOOLS._handle_install({"git_url": "https://github.com/Foo/Bar.git"}))
        assert out["error"] == "invalid_arg"

    def test_generic_exception_becomes_internal_error_with_redacted_path(self, monkeypatch):
        def fake_import(*_a, **_kw):
            raise RuntimeError(
                "FileNotFoundError: [Errno 2] No such file: '/home/someuser/.hermes/x'"
            )

        monkeypatch.setattr(_TOOLS.converter, "import_plugin", fake_import)
        out = _result(_TOOLS._handle_install({"git_url": "https://github.com/Foo/Bar.git"}))
        assert out["error"] == "internal_error"
        msg = out.get("message", "")
        assert "/home/someuser" not in msg
        assert "<path>" in msg


# ---------------------------------------------------------------------------
# _handle_list
# ---------------------------------------------------------------------------


class TestHandleList:
    """``_handle_list(args, **kwargs) -> JSON str``."""

    def test_returns_plugins_array(self, monkeypatch):
        def fake_list(**_kw):
            return [
                _STATE.PluginListEntry(
                    name="fp",
                    skills_count=2,
                    agents_count=1,
                    url="u",
                    branch="main",
                    imported_at="t",
                ),
            ]

        monkeypatch.setattr(_TOOLS.state, "list_imports", fake_list)
        out = _result(_TOOLS._handle_list({}))
        assert "plugins" in out
        assert len(out["plugins"]) == 1
        assert out["plugins"][0]["name"] == "fp"
        assert out["plugins"][0]["skills_count"] == 2

    def test_empty_returns_empty_array(self, monkeypatch):
        monkeypatch.setattr(_TOOLS.state, "list_imports", lambda **_kw: [])
        out = _result(_TOOLS._handle_list({}))
        assert out == {"plugins": []}

    def test_exception_becomes_internal_error(self, monkeypatch):
        def boom(**_kw):
            raise RuntimeError("oops")

        monkeypatch.setattr(_TOOLS.state, "list_imports", boom)
        out = _result(_TOOLS._handle_list({}))
        assert out["error"] == "internal_error"


# ---------------------------------------------------------------------------
# _handle_remove
# ---------------------------------------------------------------------------


class TestHandleRemove:
    """``_handle_remove(args, **kwargs) -> JSON str`` — R1, R5, R8."""

    def test_happy_path_returns_result_with_deferred_state_flags(self, monkeypatch):
        def fake_remove(plugin, **kwargs):
            return _STATE.RemoveResult(
                plugin=plugin, removed_skills=2, removed_agents=1, clone_cache_status="removed"
            )

        monkeypatch.setattr(_TOOLS.state, "remove_import", fake_remove)
        out = _result(_TOOLS._handle_remove({"plugin": "fp"}))
        # Envelope: {result: {...}, available_now, available_after, notice}
        assert out["result"]["plugin"] == "fp"
        assert out["result"]["removed_skills"] == 2
        assert out["available_now"] is False
        assert out["available_after"] == "next_session"

    def test_dry_run_passes_through(self, monkeypatch):
        captured: dict = {}

        def fake_remove(plugin, **kwargs):
            captured.update(kwargs)
            return _STATE.RemoveResult(plugin=plugin, dry_run=True)

        monkeypatch.setattr(_TOOLS.state, "remove_import", fake_remove)
        _TOOLS._handle_remove({"plugin": "fp", "dry_run": True})
        assert captured["dry_run"] is True
        # Force is NEVER passed through
        assert captured.get("force") is False

    def test_force_true_returns_invalid_arg_error(self):
        # The tool surface explicitly rejects force=True; slash command only
        out = _result(_TOOLS._handle_remove({"plugin": "fp", "force": True}))
        assert out["error"] == "invalid_arg"
        assert "force" in out.get("message", "")

    def test_force_false_is_tolerated(self, monkeypatch):
        # Compliant dispatchers may serialize all schema-declared bool fields
        # with explicit false defaults. force=False must NOT be rejected.
        monkeypatch.setattr(
            _TOOLS.state,
            "remove_import",
            lambda plugin, **kw: _STATE.RemoveResult(plugin=plugin, removed_skills=1),
        )
        out = _result(_TOOLS._handle_remove({"plugin": "fp", "force": False}))
        assert out.get("error") != "invalid_arg"
        assert out["result"]["plugin"] == "fp"

    def test_missing_plugin_returns_missing_arg_error(self):
        out = _result(_TOOLS._handle_remove({}))
        assert out["error"] == "missing_arg"

    def test_no_changes_propagates(self, monkeypatch):
        monkeypatch.setattr(
            _TOOLS.state,
            "remove_import",
            lambda plugin, **kw: _STATE.RemoveResult(plugin=plugin, no_changes=True),
        )
        out = _result(_TOOLS._handle_remove({"plugin": "never-installed"}))
        assert out["result"]["no_changes"] is True
        # No restart notice on a no-op
        assert "available_now" not in out
        assert "notice" not in out

    def test_dry_run_does_not_emit_restart_notice(self, monkeypatch):
        # dry_run made no disk changes — telling the agent to "restart Hermes"
        # would mislead it into a restart cycle for nothing.
        monkeypatch.setattr(
            _TOOLS.state,
            "remove_import",
            lambda plugin, **kw: _STATE.RemoveResult(plugin=plugin, dry_run=True, removed_skills=2),
        )
        out = _result(_TOOLS._handle_remove({"plugin": "fp", "dry_run": True}))
        assert out["result"]["dry_run"] is True
        assert "available_now" not in out
        assert "notice" not in out


# ---------------------------------------------------------------------------
# _redact_paths
# ---------------------------------------------------------------------------


class TestRedactPaths:
    """``_redact_paths(text)`` — strip absolute path-like substrings."""

    def test_replaces_single_path(self):
        assert _TOOLS._redact_paths("/home/someuser/.hermes/x: not found") == "<path>: not found"

    def test_replaces_multiple_paths(self):
        out = _TOOLS._redact_paths("a /foo/bar b /baz/qux")
        assert out == "a <path> b <path>"

    def test_non_path_text_unchanged(self):
        assert _TOOLS._redact_paths("ordinary error message") == "ordinary error message"

    def test_empty_string_returns_empty(self):
        assert _TOOLS._redact_paths("") == ""

    def test_digit_starting_path_components_redacted(self):
        # /proc/PID, /tmp/<numeric-name>, version dirs, etc.
        assert "1234" not in _TOOLS._redact_paths("error at /proc/1234/fd")
        assert _TOOLS._redact_paths("/tmp/456-foo") == "<path>"


# ---------------------------------------------------------------------------
# TOOLS tuple + schema invariants
# ---------------------------------------------------------------------------


class TestToolsTuple:
    """The ``TOOLS`` tuple is consumed by ``__init__.register(ctx)``."""

    def test_three_tools_in_order(self):
        names = [t[0] for t in _TOOLS.TOOLS]
        assert names == ["cc_import_install", "cc_import_list", "cc_import_remove"]

    def test_each_tuple_has_4_elements(self):
        for entry in _TOOLS.TOOLS:
            assert len(entry) == 4
            name, schema, handler, emoji = entry
            assert isinstance(name, str)
            assert isinstance(schema, dict)
            assert callable(handler)
            assert isinstance(emoji, str)

    def test_all_schemas_have_required_keys(self):
        for _name, schema, _handler, _emoji in _TOOLS.TOOLS:
            assert "name" in schema
            assert "description" in schema
            assert "parameters" in schema
            params = schema["parameters"]
            assert params["type"] == "object"
            assert "properties" in params

    def test_remove_schema_has_no_force_field(self):
        # R5: agent surface never exposes destructive override
        assert "force" not in _TOOLS._REMOVE_SCHEMA["parameters"]["properties"]

    @pytest.mark.parametrize(
        "schema",
        [_TOOLS._INSTALL_SCHEMA, _TOOLS._REMOVE_SCHEMA],
    )
    def test_install_and_remove_descriptions_carry_important_warning(self, schema):
        # R8: deferred-state warning visible to LLM at planning time
        assert "IMPORTANT" in schema["description"]

    def test_no_description_names_other_toolsets(self):
        # AGENTS.md :628 — descriptions must not name tools from other toolsets.
        forbidden = ("spotify_", "hindsight_", "diskcleanup_")
        for _name, schema, _handler, _emoji in _TOOLS.TOOLS:
            for token in forbidden:
                assert token not in schema["description"]

    def test_schema_name_matches_tuple_name(self):
        # Drift between TOOLS[i][0] and TOOLS[i][1]["name"] would silently
        # break dispatch — tools register under TOOLS[i][0], but agents see
        # the schema's "name" field and dispatch against it.
        for name, schema, _handler, _emoji in _TOOLS.TOOLS:
            assert schema["name"] == name, (
                f"Tuple name {name!r} disagrees with schema name {schema['name']!r}"
            )

    def test_schemas_reject_additional_properties(self):
        # Strict schemas catch typos at the agent/dispatcher boundary instead
        # of silently dropping unknown fields inside the handler.
        for _name, schema, _handler, _emoji in _TOOLS.TOOLS:
            assert schema["parameters"].get("additionalProperties") is False


class TestPluginYamlDriftGuard:
    """``plugin.yaml`` ``provides_tools`` must stay in sync with ``tools.TOOLS``.

    The yaml block is documentation-only at runtime, but a mismatch
    between yaml and code is a footgun (the yaml is what humans read in
    PR descriptions and ``hermes plugins list``).
    """

    def test_provides_tools_matches_tools_TOOLS(self):
        import yaml as _yaml

        repo_root = Path(__file__).resolve().parents[1]
        manifest = _yaml.safe_load((repo_root / "plugin.yaml").read_text())
        yaml_tools = manifest.get("provides_tools", [])
        code_tools = [t[0] for t in _TOOLS.TOOLS]
        assert yaml_tools == code_tools
