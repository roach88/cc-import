"""Tests for ``cli.py`` — the ``/cc-import`` slash-command handler.

Slash commands have signature ``fn(raw_args: str) -> str | None``. Our
handler dispatches to subcommands (``install`` in slice 1) and returns a
result string suitable for display in CLI / chat / gateway sessions.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


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
_CLI = _PLUGIN.cli
_CONVERTER = _CLI.converter
_STATE = _CLI.state


class TestHandleCommand:
    """``handle_command(raw_args) -> str`` — slash-command dispatcher."""

    def test_install_happy_path_calls_import_plugin(self, monkeypatch):
        captured: dict = {}

        def fake_import(git_url, **kwargs):
            captured["git_url"] = git_url
            captured["kwargs"] = kwargs
            return _CONVERTER.ImportSummary(plugin="myplug", skills_imported=2, agents_translated=1)

        monkeypatch.setattr(_CONVERTER, "import_plugin", fake_import)
        result = _CLI.handle_command("install https://example.com/foo.git")
        assert captured["git_url"] == "https://example.com/foo.git"
        assert captured["kwargs"] == {"branch": "main", "subdir": ""}
        assert "myplug" in result
        assert "2" in result and "1" in result

    def test_install_parses_branch_and_subdir_options(self, monkeypatch):
        captured: dict = {}

        def fake_import(git_url, **kwargs):
            captured["kwargs"] = kwargs
            return _CONVERTER.ImportSummary(plugin="x")

        monkeypatch.setattr(_CONVERTER, "import_plugin", fake_import)
        _CLI.handle_command("install URL --branch dev --subdir plugins/foo")
        assert captured["kwargs"] == {"branch": "dev", "subdir": "plugins/foo"}

    def test_empty_args_returns_usage(self):
        result = _CLI.handle_command("")
        assert "install" in result.lower() or "usage" in result.lower()

    def test_unknown_subcommand_returns_helpful_error(self):
        result = _CLI.handle_command("frobulate URL")
        assert "frobulate" in result or "unknown" in result.lower() or "install" in result

    def test_install_without_url_returns_error(self):
        result = _CLI.handle_command("install")
        # argparse error or usage mention
        assert "git_url" in result.lower() or "url" in result.lower() or "error" in result.lower()

    def test_clone_failure_returns_error_string_with_url(self, monkeypatch):
        def fake_import(git_url, **kwargs):
            raise subprocess.CalledProcessError(returncode=128, cmd=["git", "clone"])

        monkeypatch.setattr(_CONVERTER, "import_plugin", fake_import)
        result = _CLI.handle_command("install BAD-URL")
        assert "BAD-URL" in result

    def test_unexpected_exception_caught_and_reported(self, monkeypatch):
        def fake_import(git_url, **kwargs):
            raise RuntimeError("something exploded")

        monkeypatch.setattr(_CONVERTER, "import_plugin", fake_import)
        result = _CLI.handle_command("install URL")
        assert "exploded" in result or "error" in result.lower()

    def test_skipped_user_modified_surfaced(self, monkeypatch):
        def fake_import(git_url, **kwargs):
            return _CONVERTER.ImportSummary(
                plugin="myplug",
                skills_imported=1,
                skipped_user_modified=["myplug/agents/foo", "myplug/bar"],
            )

        monkeypatch.setattr(_CONVERTER, "import_plugin", fake_import)
        result = _CLI.handle_command("install URL")
        assert "myplug/agents/foo" in result or "user-modified" in result.lower()

    def test_unchanged_counts_surfaced_when_present(self, monkeypatch):
        def fake_import(git_url, **kwargs):
            return _CONVERTER.ImportSummary(
                plugin="myplug",
                skills_imported=0,
                skills_unchanged=5,
                agents_unchanged=3,
            )

        monkeypatch.setattr(_CONVERTER, "import_plugin", fake_import)
        result = _CLI.handle_command("install URL")
        assert "5" in result and "3" in result


class TestListSubcommand:
    """``/cc-import list`` — slice 2 slash subcommand."""

    def test_text_output_renders_table(self, monkeypatch):
        monkeypatch.setattr(
            _STATE,
            "list_imports",
            lambda **_kw: [
                _STATE.PluginListEntry(
                    name="fp",
                    skills_count=2,
                    agents_count=1,
                    url="https://github.com/Foo/Bar.git",
                    branch="main",
                    imported_at="2026-04-25T16:00:00Z",
                ),
            ],
        )
        result = _CLI.handle_command("list")
        assert "NAME" in result and "SKILLS" in result
        assert "fp" in result
        assert "2" in result and "1" in result

    def test_json_flag_emits_parseable_json(self, monkeypatch):
        import json as _json

        monkeypatch.setattr(
            _STATE,
            "list_imports",
            lambda **_kw: [
                _STATE.PluginListEntry(
                    name="fp",
                    skills_count=2,
                    agents_count=1,
                    url="u",
                    branch="main",
                    imported_at="t",
                ),
            ],
        )
        result = _CLI.handle_command("list --json")
        parsed = _json.loads(result)
        # Slash --json wraps in {plugins: [...]} to match cc_import_list tool
        # envelope so a single agent harness can parse either surface uniformly.
        assert "plugins" in parsed
        assert parsed["plugins"][0]["name"] == "fp"
        assert parsed["plugins"][0]["skills_count"] == 2

    def test_empty_list_text_output(self, monkeypatch):
        monkeypatch.setattr(_STATE, "list_imports", lambda **_kw: [])
        result = _CLI.handle_command("list")
        assert "No plugins" in result or "no plugins" in result.lower()

    def test_empty_list_json_output(self, monkeypatch):
        import json as _json

        monkeypatch.setattr(_STATE, "list_imports", lambda **_kw: [])
        result = _CLI.handle_command("list --json")
        assert _json.loads(result) == {"plugins": []}

    def test_backend_exception_returns_error_string(self, monkeypatch):
        def boom(**_kw):
            raise RuntimeError("oops")

        monkeypatch.setattr(_STATE, "list_imports", boom)
        result = _CLI.handle_command("list")
        assert result.lower().startswith("error")


class TestRemoveSubcommand:
    """``/cc-import remove`` — slice 2 slash subcommand. Slash keeps --force."""

    def test_happy_path_calls_remove_import(self, monkeypatch):
        captured: dict = {}

        def fake_remove(plugin, **kwargs):
            captured["plugin"] = plugin
            captured["kwargs"] = kwargs
            return _STATE.RemoveResult(
                plugin=plugin, removed_skills=2, removed_agents=1, clone_cache_status="removed"
            )

        monkeypatch.setattr(_STATE, "remove_import", fake_remove)
        result = _CLI.handle_command("remove fp")
        assert captured["plugin"] == "fp"
        assert captured["kwargs"] == {"force": False, "dry_run": False}
        assert "fp" in result and "Removed" in result

    def test_force_and_dry_run_flags_pass_through(self, monkeypatch):
        captured: dict = {}

        def fake_remove(plugin, **kwargs):
            captured.update(kwargs)
            return _STATE.RemoveResult(plugin=plugin, dry_run=True, removed_skills=1)

        monkeypatch.setattr(_STATE, "remove_import", fake_remove)
        _CLI.handle_command("remove fp --force --dry-run")
        assert captured["force"] is True
        assert captured["dry_run"] is True

    def test_no_changes_message(self, monkeypatch):
        monkeypatch.setattr(
            _STATE,
            "remove_import",
            lambda plugin, **kw: _STATE.RemoveResult(plugin=plugin, no_changes=True),
        )
        result = _CLI.handle_command("remove never-installed")
        assert "No changes" in result or "not installed" in result

    def test_user_modified_kept_surfaces_with_force_hint(self, monkeypatch):
        def fake_remove(plugin, **kwargs):
            return _STATE.RemoveResult(
                plugin=plugin,
                kept_user_modified=["fp/alpha"],
                removed_skills=0,
                clone_cache_status="not_attempted",
            )

        monkeypatch.setattr(_STATE, "remove_import", fake_remove)
        result = _CLI.handle_command("remove fp")
        assert "fp/alpha" in result
        assert "force" in result.lower()

    def test_dry_run_says_would_remove(self, monkeypatch):
        def fake_remove(plugin, **kwargs):
            return _STATE.RemoveResult(plugin=plugin, dry_run=True, removed_skills=2)

        monkeypatch.setattr(_STATE, "remove_import", fake_remove)
        result = _CLI.handle_command("remove fp --dry-run")
        assert "Would remove" in result or "would remove" in result.lower()

    def test_dry_run_clone_cache_uses_future_tense(self, monkeypatch):
        """Dry-run must not announce the clone cache as already deleted (Greptile P1)."""

        def fake_remove(plugin, **kwargs):
            return _STATE.RemoveResult(
                plugin=plugin,
                dry_run=True,
                removed_skills=1,
                clone_cache_status="removed",
                clone_cache_path="/tmp/fp-repo",
            )

        monkeypatch.setattr(_STATE, "remove_import", fake_remove)
        result = _CLI.handle_command("remove fp --dry-run")
        assert "clone cache: would remove" in result
        assert "clone cache: removed" not in result

    def test_real_remove_clone_cache_uses_past_tense(self, monkeypatch):
        """Non-dry-run still reports past tense once the deletion has actually run."""

        def fake_remove(plugin, **kwargs):
            return _STATE.RemoveResult(
                plugin=plugin,
                dry_run=False,
                removed_skills=1,
                clone_cache_status="removed",
                clone_cache_path="/tmp/fp-repo",
            )

        monkeypatch.setattr(_STATE, "remove_import", fake_remove)
        result = _CLI.handle_command("remove fp")
        assert "clone cache: removed" in result
        assert "would remove" not in result

    def test_missing_plugin_arg_returns_error(self):
        result = _CLI.handle_command("remove")
        assert "error" in result.lower() or "plugin" in result.lower()

    def test_backend_exception_returns_error_string(self, monkeypatch):
        def boom(plugin, **kwargs):
            raise RuntimeError("backend failed")

        monkeypatch.setattr(_STATE, "remove_import", boom)
        result = _CLI.handle_command("remove fp")
        assert result.lower().startswith("error")


class TestUnknownSubcommandIncludesNewVerbs:
    """The unknown-subcommand error should now mention list and remove."""

    def test_unknown_lists_install_list_remove(self):
        result = _CLI.handle_command("frobulate")
        assert "install" in result and "list" in result and "remove" in result
