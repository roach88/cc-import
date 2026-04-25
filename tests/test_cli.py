"""Tests for ``cli.py`` — the ``hermes cc-import install`` subcommand.

``setup_parser`` adds the ``install`` subparser (one positional + two
options). ``cmd_install`` dispatches to :func:`converter.import_plugin` and
prints a one-line summary.
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


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


class TestSetupParser:
    """``setup_parser(parser)`` — wires the ``install`` subcommand."""

    def _fresh_parser(self):
        parser = argparse.ArgumentParser(prog="hermes cc-import")
        _CLI.setup_parser(parser)
        return parser

    def test_install_required_url_with_defaults(self):
        parser = self._fresh_parser()
        args = parser.parse_args(["install", "https://example.com/foo.git"])
        assert args.git_url == "https://example.com/foo.git"
        assert args.branch == "main"
        assert args.subdir == ""

    def test_install_with_explicit_branch_and_subdir(self):
        parser = self._fresh_parser()
        args = parser.parse_args(["install", "URL", "--branch", "dev", "--subdir", "plugins/foo"])
        assert args.branch == "dev"
        assert args.subdir == "plugins/foo"

    def test_install_dispatches_to_cmd_install(self):
        parser = self._fresh_parser()
        args = parser.parse_args(["install", "URL"])
        assert args.func is _CLI.cmd_install

    def test_missing_positional_url_exits(self):
        parser = self._fresh_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["install"])

    def test_no_subcommand_exits(self):
        parser = self._fresh_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])


def _make_args(git_url: str = "URL", branch: str = "main", subdir: str = ""):
    return argparse.Namespace(git_url=git_url, branch=branch, subdir=subdir)


class TestCmdInstall:
    """``cmd_install(args)`` — calls converter.import_plugin, prints summary."""

    def test_happy_path_calls_import_plugin_with_parsed_args(self, monkeypatch, capsys):
        captured: dict = {}

        def fake_import(git_url, **kwargs):
            captured["git_url"] = git_url
            captured["kwargs"] = kwargs
            return _CONVERTER.ImportSummary(
                plugin="myplug",
                skills_imported=2,
                agents_translated=1,
            )

        monkeypatch.setattr(_CONVERTER, "import_plugin", fake_import)
        rc = _CLI.cmd_install(_make_args("https://example.com/foo.git", "dev", "plugins/foo"))
        assert rc == 0
        assert captured["git_url"] == "https://example.com/foo.git"
        assert captured["kwargs"] == {"branch": "dev", "subdir": "plugins/foo"}

    def test_happy_path_prints_summary_with_plugin_name_and_counts(self, monkeypatch, capsys):
        def fake_import(git_url, **kwargs):
            return _CONVERTER.ImportSummary(
                plugin="myplug",
                skills_imported=3,
                agents_translated=2,
            )

        monkeypatch.setattr(_CONVERTER, "import_plugin", fake_import)
        _CLI.cmd_install(_make_args())
        out = capsys.readouterr().out
        assert "myplug" in out
        assert "3" in out
        assert "2" in out

    def test_clone_failure_returns_nonzero_with_friendly_message(self, monkeypatch, capsys):
        def fake_import(git_url, **kwargs):
            raise subprocess.CalledProcessError(returncode=128, cmd=["git", "clone"])

        monkeypatch.setattr(_CONVERTER, "import_plugin", fake_import)
        rc = _CLI.cmd_install(_make_args("BAD-URL"))
        assert rc != 0
        captured = capsys.readouterr()
        # Friendly error mentions the URL and the failure
        combined = captured.out + captured.err
        assert "BAD-URL" in combined

    def test_unexpected_exception_caught_and_reported(self, monkeypatch, capsys):
        def fake_import(git_url, **kwargs):
            raise RuntimeError("something exploded")

        monkeypatch.setattr(_CONVERTER, "import_plugin", fake_import)
        rc = _CLI.cmd_install(_make_args("URL"))
        assert rc != 0
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "exploded" in combined or "Error" in combined or "error" in combined

    def test_skipped_user_modified_surfaced_in_output(self, monkeypatch, capsys):
        def fake_import(git_url, **kwargs):
            return _CONVERTER.ImportSummary(
                plugin="myplug",
                skills_imported=1,
                agents_translated=0,
                skipped_user_modified=["myplug/agents/foo", "myplug/bar"],
            )

        monkeypatch.setattr(_CONVERTER, "import_plugin", fake_import)
        _CLI.cmd_install(_make_args())
        out = capsys.readouterr().out
        # User-modified skips should be visible to the operator
        assert "myplug/agents/foo" in out or "user-modified" in out.lower()
