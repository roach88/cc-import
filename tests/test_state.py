"""Tests for ``state.py`` — list and remove operations on the manifest.

Loaded via ``importlib.util`` so the tests work both in the standalone repo
layout and after a future ``git mv`` into ``hermes-agent/plugins/cc-import/``.
"""

from __future__ import annotations

import importlib.util
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


# Load converter first; state.py imports from it via the try-relative-then-
# absolute idiom, so converter must already be in sys.modules under its
# absolute-import name when state.py loads.
_CONVERTER = _load_module("converter", "converter.py")
_STATE = _load_module("cc_import_state", "state.py")


def _seed_manifest(hermes_home: Path, manifest: dict) -> Path:
    """Write *manifest* to the canonical state.json path under *hermes_home*."""
    path = hermes_home / "plugins" / "cc-import" / "state.json"
    _CONVERTER.save_manifest(path, manifest)
    return path


def _entry(plugin: str, kind: str, source_path: str = "/some/path", origin_hash: str = "abc"):
    return {
        "plugin": plugin,
        "kind": kind,
        "source_path": source_path,
        "origin_hash": origin_hash,
    }


class TestListImports:
    """``list_imports(hermes_home=None) -> list[PluginListEntry]``."""

    def test_single_plugin_with_meta(self, tmp_path):
        hermes = tmp_path / ".hermes"
        _seed_manifest(
            hermes,
            {
                "fp/alpha": _entry("fp", "skill"),
                "fp/beta": _entry("fp", "skill"),
                "fp/agents/gamma": _entry("fp", "agent"),
                "_plugins": {
                    "fp": {
                        "url": "https://github.com/Foo/Bar.git",
                        "branch": "main",
                        "subdir": "",
                        "imported_at": "2026-04-25T16:00:00Z",
                    }
                },
            },
        )
        entries = _STATE.list_imports(hermes_home=hermes)
        assert len(entries) == 1
        e = entries[0]
        assert e.name == "fp"
        assert e.skills_count == 2
        assert e.agents_count == 1
        assert e.url == "https://github.com/Foo/Bar.git"
        assert e.branch == "main"
        assert e.imported_at == "2026-04-25T16:00:00Z"

    def test_multiple_plugins_sorted_by_name(self, tmp_path):
        hermes = tmp_path / ".hermes"
        _seed_manifest(
            hermes,
            {
                "zeta/x": _entry("zeta", "skill"),
                "alpha/y": _entry("alpha", "skill"),
                "_plugins": {
                    "zeta": {"url": "u1", "branch": "main", "subdir": "", "imported_at": "t"},
                    "alpha": {"url": "u2", "branch": "main", "subdir": "", "imported_at": "t"},
                },
            },
        )
        entries = _STATE.list_imports(hermes_home=hermes)
        assert [e.name for e in entries] == ["alpha", "zeta"]

    def test_v1_manifest_returns_none_meta_fields(self, tmp_path):
        # No _plugins index — older slice-1 manifest shape
        hermes = tmp_path / ".hermes"
        _seed_manifest(
            hermes,
            {
                "fp/alpha": _entry("fp", "skill"),
                "fp/beta": _entry("fp", "skill"),
            },
        )
        entries = _STATE.list_imports(hermes_home=hermes)
        assert len(entries) == 1
        e = entries[0]
        assert e.url is None
        assert e.branch is None
        assert e.imported_at is None
        assert e.skills_count == 2

    def test_empty_manifest_returns_empty_list(self, tmp_path):
        hermes = tmp_path / ".hermes"
        _seed_manifest(hermes, {})
        assert _STATE.list_imports(hermes_home=hermes) == []

    def test_only_plugins_index_no_file_entries_returns_empty(self, tmp_path):
        # _plugins meta without any actual file entries doesn't count as installed
        hermes = tmp_path / ".hermes"
        _seed_manifest(
            hermes,
            {
                "_plugins": {
                    "ghost": {"url": "u", "branch": "main", "subdir": "", "imported_at": "t"},
                },
            },
        )
        assert _STATE.list_imports(hermes_home=hermes) == []

    def test_mixed_kinds_split_correctly(self, tmp_path):
        hermes = tmp_path / ".hermes"
        _seed_manifest(
            hermes,
            {
                "fp/skill_a": _entry("fp", "skill"),
                "fp/skill_b": _entry("fp", "skill"),
                "fp/skill_c": _entry("fp", "skill"),
                "fp/agents/x": _entry("fp", "agent"),
                "fp/agents/y": _entry("fp", "agent"),
            },
        )
        entries = _STATE.list_imports(hermes_home=hermes)
        assert len(entries) == 1
        assert entries[0].skills_count == 3
        assert entries[0].agents_count == 2

    def test_corrupt_entry_without_plugin_key_skipped(self, tmp_path):
        # An entry lacking the "plugin" key shouldn't crash; just skip it
        hermes = tmp_path / ".hermes"
        _seed_manifest(
            hermes,
            {
                "good/alpha": _entry("good", "skill"),
                "orphan/x": {"kind": "skill", "source_path": "/x", "origin_hash": "h"},
            },
        )
        entries = _STATE.list_imports(hermes_home=hermes)
        assert [e.name for e in entries] == ["good"]
