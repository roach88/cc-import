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


def _seed_skill_file(hermes: Path, plugin: str, name: str, content: str) -> Path:
    """Create a SKILL.md on disk under skills/<plugin>/<name>/ and return the file path."""
    skill_dir = hermes / "skills" / plugin / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    md = skill_dir / "SKILL.md"
    md.write_text(content)
    return md


def _seed_agent_file(hermes: Path, plugin: str, name: str, content: str) -> Path:
    skill_dir = hermes / "skills" / plugin / "agents" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    md = skill_dir / "SKILL.md"
    md.write_text(content)
    return md


def _hash(text: str) -> str:
    return _CONVERTER.sha256_bytes(text.encode())


class TestRemoveImport:
    """``remove_import(plugin, *, force, dry_run, hermes_home) -> RemoveResult``."""

    def test_happy_path_removes_skills_agents_clone_cache_and_meta(self, tmp_path):
        hermes = tmp_path / ".hermes"
        # Seed two skills + one agent on disk
        body_a = "skill alpha"
        body_b = "skill beta"
        body_g = "agent gamma"
        _seed_skill_file(hermes, "fp", "alpha", body_a)
        _seed_skill_file(hermes, "fp", "beta", body_b)
        _seed_agent_file(hermes, "fp", "gamma", body_g)
        # Seed the clone cache
        clone_dir = hermes / "plugins" / "cc-import" / "clones" / "fp-repo"
        (clone_dir / "skills" / "alpha").mkdir(parents=True)
        # Seed manifest with hashes that match the seeded file content
        _seed_manifest(
            hermes,
            {
                "fp/alpha": {
                    "plugin": "fp",
                    "kind": "skill",
                    "source_path": str(clone_dir / "skills" / "alpha"),
                    "origin_hash": _hash(body_a),
                },
                "fp/beta": {
                    "plugin": "fp",
                    "kind": "skill",
                    "source_path": str(clone_dir / "skills" / "beta"),
                    "origin_hash": _hash(body_b),
                },
                "fp/agents/gamma": {
                    "plugin": "fp",
                    "kind": "agent",
                    "source_path": str(clone_dir / "agents" / "gamma.md"),
                    "origin_hash": _hash(body_g),
                },
                "_plugins": {
                    "fp": {
                        "url": "https://github.com/Foo/fp-repo.git",
                        "branch": "main",
                        "subdir": "",
                        "imported_at": "2026-04-25T16:00:00Z",
                    }
                },
            },
        )

        result = _STATE.remove_import("fp", hermes_home=hermes)

        assert result.no_changes is False
        assert result.dry_run is False
        assert result.removed_skills == 2
        assert result.removed_agents == 1
        assert result.kept_user_modified == []
        assert result.clone_cache_status == "removed"
        assert not (hermes / "skills" / "fp").exists()
        assert not clone_dir.exists()
        manifest = _CONVERTER.load_manifest(hermes / "plugins" / "cc-import" / "state.json")
        assert "fp/alpha" not in manifest
        assert "fp" not in manifest.get("_plugins", {})

    def test_idempotent_rerun_returns_no_changes(self, tmp_path):
        hermes = tmp_path / ".hermes"
        _seed_manifest(hermes, {})
        result = _STATE.remove_import("never-installed", hermes_home=hermes)
        assert result.no_changes is True
        assert result.removed_skills == 0
        assert result.removed_agents == 0

    def test_user_modified_preserved_by_default(self, tmp_path):
        hermes = tmp_path / ".hermes"
        body = "original"
        md = _seed_skill_file(hermes, "fp", "alpha", body)
        # User edits the file after import
        md.write_text("user-edited content")
        _seed_manifest(
            hermes,
            {
                "fp/alpha": {
                    "plugin": "fp",
                    "kind": "skill",
                    "source_path": "/some/path",
                    "origin_hash": _hash(body),
                },
                "_plugins": {
                    "fp": {"url": "u", "branch": "main", "subdir": "", "imported_at": "t"}
                },
            },
        )
        result = _STATE.remove_import("fp", hermes_home=hermes)

        assert result.kept_user_modified == ["fp/alpha"]
        assert result.removed_skills == 0
        assert md.exists()
        assert md.read_text() == "user-edited content"
        # _plugins entry retained because not all entries were removed
        manifest = _CONVERTER.load_manifest(hermes / "plugins" / "cc-import" / "state.json")
        assert "fp/alpha" in manifest
        assert "fp" in manifest.get("_plugins", {})

    def test_force_overrides_user_modified(self, tmp_path):
        hermes = tmp_path / ".hermes"
        body = "original"
        md = _seed_skill_file(hermes, "fp", "alpha", body)
        md.write_text("user-edited content")
        _seed_manifest(
            hermes,
            {
                "fp/alpha": {
                    "plugin": "fp",
                    "kind": "skill",
                    "source_path": "/some/path",
                    "origin_hash": _hash(body),
                },
            },
        )
        result = _STATE.remove_import("fp", force=True, hermes_home=hermes)
        assert result.kept_user_modified == []
        assert result.removed_skills == 1
        assert not md.exists()

    def test_force_dry_run_combo_reports_without_writing(self, tmp_path):
        hermes = tmp_path / ".hermes"
        body = "original"
        md = _seed_skill_file(hermes, "fp", "alpha", body)
        md.write_text("user-edited content")
        _seed_manifest(
            hermes,
            {
                "fp/alpha": {
                    "plugin": "fp",
                    "kind": "skill",
                    "source_path": "/some/path",
                    "origin_hash": _hash(body),
                },
            },
        )
        result = _STATE.remove_import("fp", force=True, dry_run=True, hermes_home=hermes)
        assert result.dry_run is True
        # Under force, the user-modified file would be removed — counts reflect that
        assert result.removed_skills == 1
        assert result.kept_user_modified == []
        # But disk is unchanged
        assert md.exists()
        assert md.read_text() == "user-edited content"

    def test_dry_run_preserves_disk_and_manifest(self, tmp_path):
        hermes = tmp_path / ".hermes"
        body = "alpha body"
        md = _seed_skill_file(hermes, "fp", "alpha", body)
        manifest_in = {
            "fp/alpha": {
                "plugin": "fp",
                "kind": "skill",
                "source_path": "/some/path",
                "origin_hash": _hash(body),
            },
        }
        _seed_manifest(hermes, manifest_in)
        result = _STATE.remove_import("fp", dry_run=True, hermes_home=hermes)
        assert result.dry_run is True
        assert result.removed_skills == 1
        # Disk unchanged
        assert md.exists()
        manifest_out = _CONVERTER.load_manifest(hermes / "plugins" / "cc-import" / "state.json")
        assert "fp/alpha" in manifest_out

    def test_clone_cache_anchored_to_cc_import_clones_dir(self, tmp_path):
        # source_path inside home/plugins/cc-import/clones/<basename>/...
        hermes = tmp_path / ".hermes"
        body = "alpha"
        _seed_skill_file(hermes, "fp", "alpha", body)
        clone_dir = hermes / "plugins" / "cc-import" / "clones" / "fp-repo"
        clone_dir.mkdir(parents=True)
        (clone_dir / "skills" / "alpha").mkdir(parents=True)
        _seed_manifest(
            hermes,
            {
                "fp/alpha": {
                    "plugin": "fp",
                    "kind": "skill",
                    "source_path": str(clone_dir / "skills" / "alpha"),
                    "origin_hash": _hash(body),
                },
                # No _plugins index → walk-up path used
            },
        )
        result = _STATE.remove_import("fp", hermes_home=hermes)
        assert result.clone_cache_status == "removed"
        assert not clone_dir.exists()

    def test_clone_cache_outside_anchor_skipped(self, tmp_path):
        hermes = tmp_path / ".hermes"
        body = "alpha"
        _seed_skill_file(hermes, "fp", "alpha", body)
        # source_path points OUTSIDE the cc-import clones dir
        rogue = tmp_path / "elsewhere" / "clones" / "fp-repo" / "skills" / "alpha"
        rogue.mkdir(parents=True)
        _seed_manifest(
            hermes,
            {
                "fp/alpha": {
                    "plugin": "fp",
                    "kind": "skill",
                    "source_path": str(rogue),
                    "origin_hash": _hash(body),
                },
            },
        )
        result = _STATE.remove_import("fp", hermes_home=hermes)
        assert result.clone_cache_status == "skipped_path_outside_anchor"
        # Rogue directory is untouched
        assert rogue.exists()
        # But skills tree was still removed
        assert not (hermes / "skills" / "fp").exists()

    def test_unfindable_clone_cache_skipped(self, tmp_path):
        hermes = tmp_path / ".hermes"
        body = "alpha"
        _seed_skill_file(hermes, "fp", "alpha", body)
        # source_path doesn't contain the anchor at all
        _seed_manifest(
            hermes,
            {
                "fp/alpha": {
                    "plugin": "fp",
                    "kind": "skill",
                    "source_path": "/totally/unrelated/path",
                    "origin_hash": _hash(body),
                },
            },
        )
        result = _STATE.remove_import("fp", hermes_home=hermes)
        assert result.clone_cache_status == "skipped_unfindable"
        assert not (hermes / "skills" / "fp").exists()

    def test_skill_dir_already_missing_no_error(self, tmp_path):
        hermes = tmp_path / ".hermes"
        body = "alpha"
        # Manifest references a skill, but the skill dir doesn't exist on disk
        _seed_manifest(
            hermes,
            {
                "fp/alpha": {
                    "plugin": "fp",
                    "kind": "skill",
                    "source_path": "/some/path",
                    "origin_hash": _hash(body),
                },
            },
        )
        result = _STATE.remove_import("fp", hermes_home=hermes)
        # Counted as removed (manifest entry dropped); no exception raised
        assert result.removed_skills == 1
        manifest = _CONVERTER.load_manifest(hermes / "plugins" / "cc-import" / "state.json")
        assert "fp/alpha" not in manifest
