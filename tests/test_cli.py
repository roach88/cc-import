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
