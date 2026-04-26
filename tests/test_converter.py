"""Tests for ``converter.py`` — the pure helpers ported from
``~/Dev/hermes-depracted/scripts/plugin_sync.py``.

The module is loaded via ``importlib.util`` rather than a normal ``import``
because Hermes plugins live at ``plugins/<name>/`` and are discovered from
disk paths, not from ``sys.path``. Mirroring this pattern in the standalone
repo means the test suite continues to work after the eventual upstream
``git mv`` into ``hermes-agent/plugins/cc-import/``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


def _load_converter():
    repo_root = Path(__file__).resolve().parents[1]
    name = "cc_import_converter"
    spec = importlib.util.spec_from_file_location(name, repo_root / "converter.py")
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass can introspect cls.__module__ via
    # sys.modules during forward-ref resolution. (`from __future__ import
    # annotations` makes all annotations strings; dataclasses resolves them
    # by looking up the module in sys.modules.)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_CONVERTER = _load_converter()


class TestParseFrontmatter:
    """``parse_frontmatter(text) -> (dict, body)``."""

    def test_happy_path_valid_frontmatter(self):
        text = "---\nname: foo\ndescription: bar\n---\nbody text"
        fm, body = _CONVERTER.parse_frontmatter(text)
        assert fm == {"name": "foo", "description": "bar"}
        assert body == "body text"

    def test_no_frontmatter_returns_empty_dict_and_full_text(self):
        text = "no frontmatter at all"
        fm, body = _CONVERTER.parse_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_empty_frontmatter_block_returns_empty_dict(self):
        # An empty FM block needs at least a blank line between the fences for
        # the regex to find a closing fence — `---\n---\nbody` is treated as no
        # frontmatter at all (returns the whole input as body).
        text = "---\n\n---\nbody"
        fm, body = _CONVERTER.parse_frontmatter(text)
        assert fm == {}
        assert body == "body"

    def test_malformed_yaml_returns_empty_dict_with_body_preserved(self):
        # Faithful to plugin_sync.py: when the FM block matches the regex but
        # YAML parsing fails, fm becomes {} and body is whatever followed the
        # closing fence (the malformed FM content itself is discarded).
        text = "---\nfoo: [unclosed\n---\nactual body content"
        fm, body = _CONVERTER.parse_frontmatter(text)
        assert fm == {}
        assert body == "actual body content"

    def test_closing_fence_without_trailing_newline(self):
        # Regex uses \n? after closing --- so both forms parse.
        text = "---\nname: foo\n---\nbody"
        fm, body = _CONVERTER.parse_frontmatter(text)
        assert fm == {"name": "foo"}
        assert body == "body"

    def test_multiline_body_preserved(self):
        text = "---\nname: foo\n---\nline 1\nline 2\nline 3"
        fm, body = _CONVERTER.parse_frontmatter(text)
        assert fm == {"name": "foo"}
        assert body == "line 1\nline 2\nline 3"

    def test_yaml_with_list_value(self):
        text = "---\ntools:\n  - Read\n  - Bash\n---\nbody"
        fm, _ = _CONVERTER.parse_frontmatter(text)
        assert fm == {"tools": ["Read", "Bash"]}


class TestRenderFrontmatter:
    """``render_frontmatter(fm, body) -> str`` — emits a frontmatter-prefixed document.

    Per plugin_sync.py: ``body.lstrip()`` runs before insertion, so leading
    whitespace in ``body`` is dropped. The output structure is fixed:
    ``"---\\n{yaml}\\n---\\n\\n{body.lstrip()}"``. Round-tripping with
    ``parse_frontmatter`` is not strict: the rendered double-newline after the
    closing fence + parse's single-``\\n?`` capture leave a residual leading
    newline in the parsed body. Tests here exercise the contract directly
    rather than asserting strict round-trip equality.
    """

    def test_includes_frontmatter_yaml(self):
        rendered = _CONVERTER.render_frontmatter({"name": "foo"}, "body")
        assert "name: foo" in rendered

    def test_starts_with_opening_fence(self):
        rendered = _CONVERTER.render_frontmatter({"name": "foo"}, "body")
        assert rendered.startswith("---\n")

    def test_closing_fence_followed_by_blank_line_then_body(self):
        rendered = _CONVERTER.render_frontmatter({"name": "foo"}, "body content")
        assert "---\n\nbody content" in rendered

    def test_body_leading_whitespace_is_stripped_before_insertion(self):
        rendered = _CONVERTER.render_frontmatter({"name": "foo"}, "\n\nactual body")
        assert "---\n\nactual body" in rendered
        # No triple newline at the boundary
        assert "---\n\n\n" not in rendered

    def test_empty_body_emits_only_frontmatter_block(self):
        rendered = _CONVERTER.render_frontmatter({"name": "foo"}, "")
        assert rendered.startswith("---\n")
        assert rendered.endswith("---\n\n")

    def test_empty_frontmatter_renders_as_braces(self):
        # yaml.safe_dump({}) is "{}\n"; .strip() yields "{}"
        rendered = _CONVERTER.render_frontmatter({}, "body")
        assert "---\n{}\n---" in rendered
        assert "---\n\nbody" in rendered

    def test_yaml_key_order_preserved(self):
        # sort_keys=False preserves insertion order across dicts
        rendered = _CONVERTER.render_frontmatter({"zzz": 1, "aaa": 2, "mmm": 3}, "body")
        zzz_idx = rendered.index("zzz")
        aaa_idx = rendered.index("aaa")
        mmm_idx = rendered.index("mmm")
        assert zzz_idx < aaa_idx < mmm_idx

    def test_nested_dict_serialized_as_yaml(self):
        rendered = _CONVERTER.render_frontmatter(
            {"metadata": {"hermes": {"toolsets": ["file", "web"]}}}, "body"
        )
        # Nested keys appear as block YAML in the output
        assert "metadata:" in rendered
        assert "hermes:" in rendered
        assert "toolsets:" in rendered


class TestTranslateTools:
    """``translate_tools(cc_tools) -> (hermes_toolsets, unknown_names)``.

    Maps Claude Code tool names to Hermes coarse toolsets:
      - ``file``: Read, Grep, Glob, Edit, Write, NotebookEdit
      - ``terminal``: Bash
      - ``web``: WebFetch, WebSearch
    Drops ``Task`` silently (Hermes sub-agents cannot delegate further).
    Returns ``["file", "web"]`` as the default toolset list when the input is
    empty/missing or when no provided tool name maps to a known toolset.
    """

    def test_happy_path_list_of_known_tools(self):
        toolsets, unknown = _CONVERTER.translate_tools(["Read", "Grep", "Bash"])
        # Read + Grep both map to "file" — should appear once
        assert toolsets == ["file", "terminal"]
        assert unknown == []

    def test_none_returns_default(self):
        toolsets, unknown = _CONVERTER.translate_tools(None)
        assert toolsets == ["file", "web"]
        assert unknown == []

    def test_empty_string_returns_default(self):
        toolsets, unknown = _CONVERTER.translate_tools("")
        assert toolsets == ["file", "web"]
        assert unknown == []

    def test_empty_list_returns_default(self):
        toolsets, unknown = _CONVERTER.translate_tools([])
        assert toolsets == ["file", "web"]
        assert unknown == []

    def test_comma_separated_string_input(self):
        toolsets, unknown = _CONVERTER.translate_tools("Read, Bash, WebSearch")
        assert toolsets == ["file", "terminal", "web"]
        assert unknown == []

    def test_task_is_dropped_silently_not_reported_unknown(self):
        toolsets, unknown = _CONVERTER.translate_tools(["Read", "Task"])
        assert toolsets == ["file"]
        # Task is in TOOL_DROP — neither registered as a toolset nor reported
        # as unknown. The contract: known-but-skipped, not unknown.
        assert unknown == []

    def test_unknown_tool_reported(self):
        toolsets, unknown = _CONVERTER.translate_tools(["Read", "MagicTool"])
        assert toolsets == ["file"]
        assert unknown == ["MagicTool"]

    def test_all_unknown_falls_back_to_default_toolsets(self):
        toolsets, unknown = _CONVERTER.translate_tools(["Magic", "Mystery"])
        assert toolsets == ["file", "web"]
        assert sorted(unknown) == ["Magic", "Mystery"]

    def test_invalid_type_returns_default(self):
        toolsets, unknown = _CONVERTER.translate_tools(123)  # type: ignore[arg-type]
        assert toolsets == ["file", "web"]
        assert unknown == []

    def test_file_toolset_deduped_across_all_file_tools(self):
        toolsets, unknown = _CONVERTER.translate_tools(
            ["Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit"]
        )
        assert toolsets == ["file"]
        assert unknown == []

    def test_all_three_toolsets_in_one_call(self):
        toolsets, unknown = _CONVERTER.translate_tools(["Read", "Bash", "WebSearch"])
        assert sorted(toolsets) == ["file", "terminal", "web"]
        assert unknown == []

    def test_whitespace_trimmed_in_comma_separated_input(self):
        toolsets, unknown = _CONVERTER.translate_tools("  Read  ,  Bash  ")
        assert toolsets == ["file", "terminal"]
        assert unknown == []


class TestBuildDelegationSkill:
    """``build_delegation_skill(plugin, agent_name, cc_fm, cc_body) -> str``.

    Translates a Claude Code agent markdown file into a Hermes "delegation
    skill" SKILL.md — frontmatter is rewritten to identify the skill as a
    delegation, body wraps the original CC persona under a ``## Persona``
    section preceded by instructions on how Hermes should invoke
    ``delegate_task``.
    """

    def test_happy_path_full_translation(self):
        cc_fm = {
            "name": "secsentinel",
            "description": "Audit security posture",
            "tools": "Read,Bash",
        }
        cc_body = "You are a security reviewer.\nYour job is to find vulns."
        result = _CONVERTER.build_delegation_skill(
            "compound-engineering", "secsentinel", cc_fm, cc_body
        )
        fm, body = _CONVERTER.parse_frontmatter(result)
        assert fm["name"] == "compound-engineering/agent/secsentinel"
        assert fm["description"] == "Audit security posture"
        assert fm["version"] == "1.0.0"
        hermes_meta = fm["metadata"]["hermes"]
        assert hermes_meta["source"] == "compound-engineering"
        assert hermes_meta["source_kind"] == "agent"
        assert hermes_meta["upstream_name"] == "secsentinel"
        assert hermes_meta["toolsets"] == ["file", "terminal"]
        # Body contents
        assert "Delegation skill" in body
        assert "## Persona" in body
        assert "You are a security reviewer." in body
        assert "Your job is to find vulns." in body

    def test_missing_description_uses_fallback(self):
        cc_fm = {"name": "x", "tools": "Read"}
        result = _CONVERTER.build_delegation_skill("plug", "x", cc_fm, "persona body")
        fm, _ = _CONVERTER.parse_frontmatter(result)
        assert fm["description"] == "Delegate to the x sub-agent persona."

    def test_empty_description_uses_fallback(self):
        cc_fm = {"name": "x", "description": "   ", "tools": "Read"}
        result = _CONVERTER.build_delegation_skill("plug", "x", cc_fm, "persona body")
        fm, _ = _CONVERTER.parse_frontmatter(result)
        assert fm["description"] == "Delegate to the x sub-agent persona."

    def test_unknown_tools_emit_warning_note_in_body(self):
        cc_fm = {"description": "X", "tools": ["Read", "MagicTool", "Mystery"]}
        result = _CONVERTER.build_delegation_skill("plug", "x", cc_fm, "p")
        _, body = _CONVERTER.parse_frontmatter(result)
        assert "Upstream tools not mapped" in body
        assert "MagicTool" in body
        assert "Mystery" in body

    def test_no_unknown_tools_no_warning_note(self):
        cc_fm = {"description": "X", "tools": ["Read", "Bash"]}
        result = _CONVERTER.build_delegation_skill("plug", "x", cc_fm, "p")
        _, body = _CONVERTER.parse_frontmatter(result)
        assert "Upstream tools not mapped" not in body

    def test_empty_cc_body_still_produces_well_formed_output(self):
        cc_fm = {"description": "X", "tools": "Read"}
        result = _CONVERTER.build_delegation_skill("plug", "x", cc_fm, "")
        fm, body = _CONVERTER.parse_frontmatter(result)
        assert fm["name"] == "plug/agent/x"
        assert "## Persona" in body

    def test_no_tools_in_cc_fm_uses_default_toolsets(self):
        cc_fm = {"description": "X"}
        result = _CONVERTER.build_delegation_skill("plug", "x", cc_fm, "p")
        fm, _ = _CONVERTER.parse_frontmatter(result)
        assert fm["metadata"]["hermes"]["toolsets"] == ["file", "web"]

    def test_toolsets_appear_in_body_for_delegate_task_invocation(self):
        cc_fm = {"description": "X", "tools": "Read,Bash,WebSearch"}
        result = _CONVERTER.build_delegation_skill("plug", "x", cc_fm, "p")
        fm, body = _CONVERTER.parse_frontmatter(result)
        toolsets = fm["metadata"]["hermes"]["toolsets"]
        assert toolsets == ["file", "terminal", "web"]
        # The body's `toolsets:` invocation line shows the Python list repr
        assert str(toolsets) in body

    def test_body_documents_delegate_task_invocation_shape(self):
        cc_fm = {"description": "X", "tools": "Read"}
        result = _CONVERTER.build_delegation_skill("plug", "x", cc_fm, "p")
        _, body = _CONVERTER.parse_frontmatter(result)
        assert "delegate_task" in body
        assert "context" in body
        assert "goal" in body
        assert "max_iterations" in body

    def test_cc_body_leading_whitespace_stripped_before_persona_section(self):
        cc_fm = {"description": "X", "tools": "Read"}
        result = _CONVERTER.build_delegation_skill(
            "plug", "x", cc_fm, "\n\n\nactual persona content"
        )
        _, body = _CONVERTER.parse_frontmatter(result)
        # cc_body.lstrip() inside the function removes the leading newlines
        # before they hit the "## Persona\n\n<body>" boundary
        assert "## Persona\n\nactual persona content" in body


class TestSha256Helpers:
    """``sha256_bytes(data) -> hex``, ``sha256_file(path) -> hex``."""

    def test_sha256_bytes_empty_input(self):
        assert (
            _CONVERTER.sha256_bytes(b"")
            == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_sha256_bytes_known_input(self):
        # Hand-verified reference value
        assert (
            _CONVERTER.sha256_bytes(b"hello")
            == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        )

    def test_sha256_file_matches_sha256_bytes(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_bytes(b"hello\nworld\n")
        assert _CONVERTER.sha256_file(p) == _CONVERTER.sha256_bytes(b"hello\nworld\n")

    def test_sha256_file_reads_bytes_so_line_endings_matter(self, tmp_path):
        # Reading via bytes preserves CRLF / LF differences — important because
        # the manifest's user-modified detection compares hashes of files that
        # may have been touched by editors that normalize line endings.
        p_lf = tmp_path / "lf.txt"
        p_crlf = tmp_path / "crlf.txt"
        p_lf.write_bytes(b"a\nb\n")
        p_crlf.write_bytes(b"a\r\nb\r\n")
        assert _CONVERTER.sha256_file(p_lf) != _CONVERTER.sha256_file(p_crlf)


class TestManifestIO:
    """``load_manifest(path)``, ``save_manifest(path, manifest)``."""

    def test_load_returns_empty_when_path_does_not_exist(self, tmp_path):
        result = _CONVERTER.load_manifest(tmp_path / "nonexistent.json")
        assert result == {}

    def test_save_then_load_round_trip(self, tmp_path):
        path = tmp_path / "state.json"
        data = {"foo/skill1": {"plugin": "foo", "origin_hash": "abc123", "kind": "skill"}}
        _CONVERTER.save_manifest(path, data)
        loaded = _CONVERTER.load_manifest(path)
        assert loaded == data

    def test_save_creates_parent_directories(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "state.json"
        _CONVERTER.save_manifest(path, {"x": {"plugin": "y"}})
        assert path.exists()
        assert path.parent.is_dir()

    def test_save_emits_diff_friendly_json(self, tmp_path):
        # indent=2 + sort_keys=True so successive saves produce stable diffs.
        path = tmp_path / "state.json"
        _CONVERTER.save_manifest(path, {"zzz": 1, "aaa": 2})
        text = path.read_text()
        # Keys sorted alphabetically (aaa appears before zzz)
        assert text.index('"aaa"') < text.index('"zzz"')
        # Multi-line indented output
        assert "\n" in text
        assert "  " in text  # 2-space indent

    def test_load_corrupt_json_returns_empty_with_warning(self, tmp_path, caplog):
        import logging

        path = tmp_path / "state.json"
        path.write_text("this is not { valid json")
        with caplog.at_level(logging.WARNING):
            result = _CONVERTER.load_manifest(path)
        assert result == {}
        assert any("corrupt" in rec.message.lower() for rec in caplog.records)


@pytest.fixture
def bare_upstream(tmp_path):
    """Create a bare git repo on ``main`` with one initial commit. Yields the path.

    Uses real ``git`` subprocess calls (no mocks) so :func:`clone_or_update`
    exercises the actual clone/fetch/reset paths against a real ``file://``
    upstream. The bare repo and its scratch worktree both live under
    ``tmp_path`` so cleanup is automatic.
    """
    bare = tmp_path / "upstream.git"
    work = tmp_path / "upstream-work"

    def _git(*args: str, cwd: Path | None = None) -> None:
        cmd = ["git"]
        if cwd is not None:
            cmd.extend(["-C", str(cwd)])
        cmd.extend(args)
        subprocess.run(cmd, check=True, capture_output=True)

    _git("init", "--bare", "-b", "main", str(bare))
    _git("init", "-b", "main", str(work))
    (work / "README.md").write_text("initial\n")
    _git("config", "user.email", "test@example.com", cwd=work)
    _git("config", "user.name", "Test", cwd=work)
    _git("add", "README.md", cwd=work)
    _git("commit", "-m", "initial", cwd=work)
    _git("remote", "add", "origin", f"file://{bare}", cwd=work)
    _git("push", "origin", "main", cwd=work)
    return bare


def _git_in(work: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(work), *args], check=True, capture_output=True)


def _add_upstream_commit(bare: Path, tmp_path: Path, filename: str, content: str) -> None:
    """Clone the bare upstream into a scratch worktree, add a file, push back."""
    work = tmp_path / f"scratch-{filename}"
    subprocess.run(["git", "clone", f"file://{bare}", str(work)], check=True, capture_output=True)
    (work / filename).write_text(content)
    _git_in(work, "config", "user.email", "test@example.com")
    _git_in(work, "config", "user.name", "Test")
    _git_in(work, "add", filename)
    _git_in(work, "commit", "-m", f"add {filename}")
    _git_in(work, "push", "origin", "main")


class TestCloneOrUpdate:
    """``clone_or_update(url, branch, dest)`` — wraps git clone/fetch/reset."""

    def test_clones_to_fresh_destination(self, tmp_path, bare_upstream):
        dest = tmp_path / "dest"
        _CONVERTER.clone_or_update(f"file://{bare_upstream}", "main", dest)
        assert dest.is_dir()
        assert (dest / ".git").is_dir()
        assert (dest / "README.md").exists()

    def test_picks_up_upstream_changes_on_rerun(self, tmp_path, bare_upstream):
        dest = tmp_path / "dest"
        url = f"file://{bare_upstream}"
        _CONVERTER.clone_or_update(url, "main", dest)
        assert not (dest / "added.md").exists()

        _add_upstream_commit(bare_upstream, tmp_path, "added.md", "added content")
        _CONVERTER.clone_or_update(url, "main", dest)

        assert (dest / "added.md").exists()
        assert (dest / "added.md").read_text() == "added content"

    def test_replaces_non_git_directory_with_clone(self, tmp_path, bare_upstream):
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "stray.txt").write_text("garbage left from somewhere else")

        _CONVERTER.clone_or_update(f"file://{bare_upstream}", "main", dest)

        assert not (dest / "stray.txt").exists()
        assert (dest / ".git").is_dir()
        assert (dest / "README.md").exists()

    def test_invalid_branch_raises_called_process_error(self, tmp_path, bare_upstream):
        dest = tmp_path / "dest"
        with pytest.raises(subprocess.CalledProcessError):
            _CONVERTER.clone_or_update(f"file://{bare_upstream}", "nonexistent-branch", dest)


def _make_src_skill(root: Path, name: str = "myskill", body: str = "skill body content") -> Path:
    """Create a fixture source skill dir with a SKILL.md inside."""
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n{body}")
    return d


def _make_src_agent(
    parent: Path,
    name: str = "myagent",
    description: str = "Do stuff",
    tools: str = "Read,Bash",
    body: str = "you are an agent",
) -> Path:
    """Create a fixture source agent ``.md`` file."""
    parent.mkdir(parents=True, exist_ok=True)
    agent_md = parent / f"{name}.md"
    agent_md.write_text(
        f"---\nname: {name}\ndescription: {description}\ntools: {tools}\n---\n{body}"
    )
    return agent_md


class TestMigrateSkill:
    """``migrate_skill(src_skill_dir, dest_skill_dir, manifest, plugin, skills_dir)``.

    The 3-hash decision matrix governs all branches:

    | local==origin | local==prior_origin | Action |
    |---|---|---|
    | true | (any) | UNCHANGED, refresh manifest |
    | false | true | COPY (upstream updated, user clean) |
    | false | false | SKIP with warning (user-modified) |
    """

    def test_happy_path_first_write_records_manifest_entry(self, tmp_path):
        src = _make_src_skill(tmp_path / "src", "s", "body v1")
        skills_dir = tmp_path / "skills"
        dest = skills_dir / "plug" / "s"
        manifest: dict = {}

        _CONVERTER.migrate_skill(src, dest, manifest, "plug", skills_dir)

        assert (dest / "SKILL.md").exists()
        assert "plug/s" in manifest
        entry = manifest["plug/s"]
        assert entry["plugin"] == "plug"
        assert entry["kind"] == "skill"
        assert entry["origin_hash"] == _CONVERTER.sha256_file(src / "SKILL.md")

    def test_copies_whole_tree_not_just_skill_md(self, tmp_path):
        src = tmp_path / "src" / "s"
        src.mkdir(parents=True)
        (src / "SKILL.md").write_text("---\nname: s\n---\nbody")
        (src / "helper.py").write_text("def x(): pass")
        (src / "subdir").mkdir()
        (src / "subdir" / "data.json").write_text("{}")

        skills_dir = tmp_path / "skills"
        dest = skills_dir / "plug" / "s"
        _CONVERTER.migrate_skill(src, dest, {}, "plug", skills_dir)

        assert (dest / "SKILL.md").exists()
        assert (dest / "helper.py").exists()
        assert (dest / "subdir" / "data.json").exists()

    def test_idempotent_rerun_does_not_rewrite(self, tmp_path):
        src = _make_src_skill(tmp_path / "src", "s", "body")
        skills_dir = tmp_path / "skills"
        dest = skills_dir / "plug" / "s"
        manifest: dict = {}

        _CONVERTER.migrate_skill(src, dest, manifest, "plug", skills_dir)
        mtime_before = (dest / "SKILL.md").stat().st_mtime_ns

        _CONVERTER.migrate_skill(src, dest, manifest, "plug", skills_dir)
        mtime_after = (dest / "SKILL.md").stat().st_mtime_ns

        assert mtime_before == mtime_after

    def test_upstream_update_propagates(self, tmp_path):
        src = _make_src_skill(tmp_path / "src", "s", "v1")
        skills_dir = tmp_path / "skills"
        dest = skills_dir / "plug" / "s"
        manifest: dict = {}

        _CONVERTER.migrate_skill(src, dest, manifest, "plug", skills_dir)

        (src / "SKILL.md").write_text("---\nname: s\n---\nv2 from upstream")
        _CONVERTER.migrate_skill(src, dest, manifest, "plug", skills_dir)

        assert "v2 from upstream" in (dest / "SKILL.md").read_text()
        assert manifest["plug/s"]["origin_hash"] == _CONVERTER.sha256_file(src / "SKILL.md")

    def test_user_modified_skill_preserved_with_warning(self, tmp_path, caplog):
        import logging

        src = _make_src_skill(tmp_path / "src", "s", "v1")
        skills_dir = tmp_path / "skills"
        dest = skills_dir / "plug" / "s"
        manifest: dict = {}

        _CONVERTER.migrate_skill(src, dest, manifest, "plug", skills_dir)

        (dest / "SKILL.md").write_text("---\nname: s\n---\nuser-edited content")
        (src / "SKILL.md").write_text("---\nname: s\n---\nv2 from upstream")

        with caplog.at_level(logging.WARNING):
            _CONVERTER.migrate_skill(src, dest, manifest, "plug", skills_dir)

        assert "user-edited content" in (dest / "SKILL.md").read_text()
        assert any(
            "SKIP" in rec.message and "user-modified" in rec.message.lower()
            for rec in caplog.records
        )

    def test_skill_dir_without_skill_md_is_skipped(self, tmp_path):
        src = tmp_path / "src" / "notaskill"
        src.mkdir(parents=True)
        (src / "random.txt").write_text("not a skill")
        skills_dir = tmp_path / "skills"
        dest = skills_dir / "plug" / "notaskill"
        manifest: dict = {}

        _CONVERTER.migrate_skill(src, dest, manifest, "plug", skills_dir)

        assert not dest.exists()
        assert manifest == {}


class TestMigrateAgent:
    """``migrate_agent(src_agent_md, dest_skill_dir, manifest, plugin, skills_dir)``.

    Translates a Claude Code agent markdown file to a Hermes delegation skill
    SKILL.md at ``<skills_dir>/<plugin>/agents/<agent_name>/SKILL.md``. Same
    3-hash decision matrix as :class:`TestMigrateSkill` for idempotent
    handling, but ``origin_hash`` is computed over the *translated* output —
    so a change in build_delegation_skill's logic would also invalidate.
    """

    def test_happy_path_translates_to_delegation_skill(self, tmp_path):
        src = _make_src_agent(
            tmp_path / "src" / "agents",
            name="myagent",
            description="Audit security",
            tools="Read,Bash",
            body="You are an auditor.",
        )
        skills_dir = tmp_path / "skills"
        dest = skills_dir / "plug" / "agents" / "myagent"
        manifest: dict = {}

        _CONVERTER.migrate_agent(src, dest, manifest, "plug", skills_dir)

        dest_md = dest / "SKILL.md"
        assert dest_md.exists()
        fm, body = _CONVERTER.parse_frontmatter(dest_md.read_text())
        assert fm["name"] == "plug/agent/myagent"
        assert fm["description"] == "Audit security"
        assert fm["metadata"]["hermes"]["toolsets"] == ["file", "terminal"]
        assert fm["metadata"]["hermes"]["source_kind"] == "agent"
        assert fm["metadata"]["hermes"]["upstream_name"] == "myagent"
        assert "## Persona" in body
        assert "You are an auditor." in body
        # Manifest entry recorded with kind=agent
        key = "plug/agents/myagent"
        assert key in manifest
        assert manifest[key]["kind"] == "agent"

    def test_agent_without_name_frontmatter_uses_filename_stem(self, tmp_path):
        agent_md = tmp_path / "src" / "filename-stem.md"
        agent_md.parent.mkdir(parents=True)
        agent_md.write_text("---\ndescription: x\ntools: Read\n---\nbody")
        skills_dir = tmp_path / "skills"
        dest = skills_dir / "plug" / "agents" / "filename-stem"

        _CONVERTER.migrate_agent(agent_md, dest, {}, "plug", skills_dir)

        fm, _ = _CONVERTER.parse_frontmatter((dest / "SKILL.md").read_text())
        assert fm["name"] == "plug/agent/filename-stem"

    def test_agent_without_frontmatter_still_produces_output(self, tmp_path):
        agent_md = tmp_path / "src" / "noname.md"
        agent_md.parent.mkdir(parents=True)
        agent_md.write_text("just body, no frontmatter")
        skills_dir = tmp_path / "skills"
        dest = skills_dir / "plug" / "agents" / "noname"

        _CONVERTER.migrate_agent(agent_md, dest, {}, "plug", skills_dir)

        assert (dest / "SKILL.md").exists()
        fm, _ = _CONVERTER.parse_frontmatter((dest / "SKILL.md").read_text())
        # Defaults: filename stem for name, default toolsets for tools
        assert fm["name"] == "plug/agent/noname"
        assert fm["metadata"]["hermes"]["toolsets"] == ["file", "web"]

    def test_idempotent_rerun_does_not_rewrite(self, tmp_path):
        src = _make_src_agent(tmp_path / "src", "a", "d", "Read", "body")
        skills_dir = tmp_path / "skills"
        dest = skills_dir / "plug" / "agents" / "a"
        manifest: dict = {}

        _CONVERTER.migrate_agent(src, dest, manifest, "plug", skills_dir)
        mtime_before = (dest / "SKILL.md").stat().st_mtime_ns

        _CONVERTER.migrate_agent(src, dest, manifest, "plug", skills_dir)
        mtime_after = (dest / "SKILL.md").stat().st_mtime_ns

        assert mtime_before == mtime_after

    def test_upstream_update_propagates(self, tmp_path):
        src = _make_src_agent(tmp_path / "src", "a", "d", "Read", "v1 body")
        skills_dir = tmp_path / "skills"
        dest = skills_dir / "plug" / "agents" / "a"
        manifest: dict = {}

        _CONVERTER.migrate_agent(src, dest, manifest, "plug", skills_dir)

        src.write_text("---\nname: a\ndescription: d\ntools: Read\n---\nv2 body")
        _CONVERTER.migrate_agent(src, dest, manifest, "plug", skills_dir)

        _, body = _CONVERTER.parse_frontmatter((dest / "SKILL.md").read_text())
        assert "v2 body" in body

    def test_user_modified_agent_preserved_with_warning(self, tmp_path, caplog):
        import logging

        src = _make_src_agent(tmp_path / "src", "a", "d", "Read", "v1")
        skills_dir = tmp_path / "skills"
        dest = skills_dir / "plug" / "agents" / "a"
        manifest: dict = {}

        _CONVERTER.migrate_agent(src, dest, manifest, "plug", skills_dir)

        (dest / "SKILL.md").write_text("user-edited content")
        src.write_text("---\nname: a\ndescription: new\ntools: Read\n---\nv2")

        with caplog.at_level(logging.WARNING):
            _CONVERTER.migrate_agent(src, dest, manifest, "plug", skills_dir)

        assert (dest / "SKILL.md").read_text() == "user-edited content"
        assert any(
            "SKIP" in rec.message and "user-modified" in rec.message.lower()
            for rec in caplog.records
        )


class TestPruneRemoved:
    """``prune_removed(plugin, seen_keys, manifest, skills_dir)``.

    Removes manifest entries (and their on-disk files) for skills that are no
    longer present in upstream. Protects user-modified files from deletion by
    comparing the on-disk hash against the manifest's last-known origin hash.
    """

    def test_removes_stale_unmodified_entry(self, tmp_path):
        skills_dir = tmp_path / "skills"
        dest = skills_dir / "plug" / "stale"
        dest.mkdir(parents=True)
        (dest / "SKILL.md").write_text("content")
        origin_hash = _CONVERTER.sha256_file(dest / "SKILL.md")
        manifest: dict = {
            "plug/stale": {
                "plugin": "plug",
                "kind": "skill",
                "source_path": "irrelevant",
                "origin_hash": origin_hash,
            }
        }

        _CONVERTER.prune_removed("plug", set(), manifest, skills_dir)

        assert not dest.exists()
        assert "plug/stale" not in manifest

    def test_keeps_user_modified_stale_entry_with_warning(self, tmp_path, caplog):
        import logging

        skills_dir = tmp_path / "skills"
        dest = skills_dir / "plug" / "stale"
        dest.mkdir(parents=True)
        (dest / "SKILL.md").write_text("user-edited")
        # origin_hash is the hash of DIFFERENT content — user has since edited
        manifest: dict = {
            "plug/stale": {
                "plugin": "plug",
                "kind": "skill",
                "source_path": "x",
                "origin_hash": _CONVERTER.sha256_bytes(b"original-upstream-content"),
            }
        }

        with caplog.at_level(logging.WARNING):
            _CONVERTER.prune_removed("plug", set(), manifest, skills_dir)

        assert (dest / "SKILL.md").exists()
        assert (dest / "SKILL.md").read_text() == "user-edited"
        assert any("KEEP" in rec.message for rec in caplog.records)
        # Manifest entry retained so a future rerun still tracks it
        assert "plug/stale" in manifest

    def test_stale_entry_with_missing_file_removed_cleanly(self, tmp_path):
        skills_dir = tmp_path / "skills"
        manifest: dict = {
            "plug/missing": {
                "plugin": "plug",
                "kind": "skill",
                "source_path": "x",
                "origin_hash": "abc",
            }
        }

        _CONVERTER.prune_removed("plug", set(), manifest, skills_dir)

        assert "plug/missing" not in manifest

    def test_does_not_touch_other_plugin_entries(self, tmp_path):
        skills_dir = tmp_path / "skills"
        d1 = skills_dir / "plug1" / "x"
        d1.mkdir(parents=True)
        (d1 / "SKILL.md").write_text("c1")
        d2 = skills_dir / "plug2" / "y"
        d2.mkdir(parents=True)
        (d2 / "SKILL.md").write_text("c2")

        manifest: dict = {
            "plug1/x": {
                "plugin": "plug1",
                "kind": "skill",
                "source_path": "a",
                "origin_hash": _CONVERTER.sha256_file(d1 / "SKILL.md"),
            },
            "plug2/y": {
                "plugin": "plug2",
                "kind": "skill",
                "source_path": "b",
                "origin_hash": _CONVERTER.sha256_file(d2 / "SKILL.md"),
            },
        }

        _CONVERTER.prune_removed("plug1", set(), manifest, skills_dir)

        assert "plug1/x" not in manifest
        assert "plug2/y" in manifest
        assert d2.exists()

    def test_seen_keys_protect_entries_from_pruning(self, tmp_path):
        skills_dir = tmp_path / "skills"
        dest = skills_dir / "plug" / "still-here"
        dest.mkdir(parents=True)
        (dest / "SKILL.md").write_text("c")
        manifest: dict = {
            "plug/still-here": {
                "plugin": "plug",
                "kind": "skill",
                "source_path": "x",
                "origin_hash": _CONVERTER.sha256_file(dest / "SKILL.md"),
            }
        }

        _CONVERTER.prune_removed("plug", {"plug/still-here"}, manifest, skills_dir)

        assert "plug/still-here" in manifest
        assert dest.exists()


@pytest.fixture
def bare_cc_plugin(tmp_path, bare_upstream):
    """Bare git repo with a CC-shaped plugin already committed.

    Layout (committed on ``main``):

        plugin.json               {"name": "fixture-plugin", "version": "1.0.0"}
        skills/alpha/SKILL.md
        skills/beta/SKILL.md
        agents/gamma.md           (frontmatter declares Read,Bash tools)
    """
    work = tmp_path / "cc-plugin-work"
    subprocess.run(
        ["git", "clone", f"file://{bare_upstream}", str(work)],
        check=True,
        capture_output=True,
    )
    (work / "plugin.json").write_text('{"name": "fixture-plugin", "version": "1.0.0"}\n')
    (work / "skills" / "alpha").mkdir(parents=True)
    (work / "skills" / "alpha" / "SKILL.md").write_text("---\nname: alpha\n---\nalpha v1")
    (work / "skills" / "beta").mkdir(parents=True)
    (work / "skills" / "beta" / "SKILL.md").write_text("---\nname: beta\n---\nbeta v1")
    (work / "agents").mkdir()
    (work / "agents" / "gamma.md").write_text(
        "---\nname: gamma\ndescription: Gamma agent\ntools: Read,Bash\n---\nYou are gamma."
    )
    _git_in(work, "config", "user.email", "test@example.com")
    _git_in(work, "config", "user.name", "Test")
    _git_in(work, "add", "plugin.json", "skills", "agents")
    _git_in(work, "commit", "-m", "add cc plugin content")
    _git_in(work, "push", "origin", "main")
    return bare_upstream


def _push_upstream_change(bare: Path, tmp_path: Path, label: str, mutator) -> None:
    """Clone ``bare`` into a scratch worktree, apply *mutator(work_dir)*, push back."""
    work = tmp_path / f"upstream-edit-{label}"
    subprocess.run(["git", "clone", f"file://{bare}", str(work)], check=True, capture_output=True)
    _git_in(work, "config", "user.email", "test@example.com")
    _git_in(work, "config", "user.name", "Test")
    mutator(work)
    _git_in(work, "add", "-A")
    _git_in(work, "commit", "-m", f"upstream edit: {label}")
    _git_in(work, "push", "origin", "main")


class TestImportPlugin:
    """``import_plugin(git_url, *, branch, subdir, hermes_home) -> ImportSummary``.

    End-to-end orchestrator that ties clone_or_update, migrate_skill,
    migrate_agent, prune_removed, and manifest IO together. Tests run against
    real bare git repos in tmp_path.
    """

    def test_happy_path_imports_skills_and_translates_agents(self, tmp_path, bare_cc_plugin):
        hermes_home = tmp_path / "alt-hermes"
        summary = _CONVERTER.import_plugin(f"file://{bare_cc_plugin}", hermes_home=hermes_home)
        assert summary.plugin == "fixture-plugin"
        assert summary.skills_imported == 2
        assert summary.skills_unchanged == 0
        assert summary.agents_translated == 1
        assert summary.agents_unchanged == 0
        assert summary.skipped_user_modified == []

        plugin_skills = hermes_home / "skills" / "fixture-plugin"
        assert (plugin_skills / "alpha" / "SKILL.md").exists()
        assert (plugin_skills / "beta" / "SKILL.md").exists()
        # Agent translated to delegation skill
        agent_skill = plugin_skills / "agents" / "gamma" / "SKILL.md"
        assert agent_skill.exists()
        fm, body = _CONVERTER.parse_frontmatter(agent_skill.read_text())
        assert fm["name"] == "fixture-plugin/agent/gamma"
        assert fm["metadata"]["hermes"]["toolsets"] == ["file", "terminal"]
        assert "You are gamma." in body
        # Manifest persisted
        manifest_path = hermes_home / "plugins" / "cc-import" / "state.json"
        assert manifest_path.exists()
        manifest = _CONVERTER.load_manifest(manifest_path)
        assert "fixture-plugin/alpha" in manifest
        assert "fixture-plugin/beta" in manifest
        assert "fixture-plugin/agents/gamma" in manifest

    def test_idempotent_rerun_does_not_rewrite(self, tmp_path, bare_cc_plugin):
        hermes_home = tmp_path / "alt-hermes"
        url = f"file://{bare_cc_plugin}"
        _CONVERTER.import_plugin(url, hermes_home=hermes_home)

        plugin_root = hermes_home / "skills" / "fixture-plugin"
        mtimes = {p: p.stat().st_mtime_ns for p in plugin_root.rglob("SKILL.md")}

        s2 = _CONVERTER.import_plugin(url, hermes_home=hermes_home)

        for p, mt in mtimes.items():
            assert p.stat().st_mtime_ns == mt
        assert s2.skills_imported == 0
        assert s2.skills_unchanged == 2
        assert s2.agents_translated == 0
        assert s2.agents_unchanged == 1

    def test_upstream_skill_update_propagates(self, tmp_path, bare_cc_plugin):
        hermes_home = tmp_path / "alt-hermes"
        url = f"file://{bare_cc_plugin}"
        _CONVERTER.import_plugin(url, hermes_home=hermes_home)

        def edit_alpha(work: Path) -> None:
            (work / "skills" / "alpha" / "SKILL.md").write_text(
                "---\nname: alpha\n---\nalpha v2 updated"
            )

        _push_upstream_change(bare_cc_plugin, tmp_path, "alpha-v2", edit_alpha)
        s2 = _CONVERTER.import_plugin(url, hermes_home=hermes_home)

        alpha_md = hermes_home / "skills" / "fixture-plugin" / "alpha" / "SKILL.md"
        assert "alpha v2 updated" in alpha_md.read_text()
        assert s2.skills_imported == 1
        assert s2.skills_unchanged == 1  # beta still unchanged

    def test_user_modified_agent_reported_in_summary(self, tmp_path, bare_cc_plugin):
        hermes_home = tmp_path / "alt-hermes"
        url = f"file://{bare_cc_plugin}"
        _CONVERTER.import_plugin(url, hermes_home=hermes_home)

        gamma_md = hermes_home / "skills" / "fixture-plugin" / "agents" / "gamma" / "SKILL.md"
        gamma_md.write_text("user-edited content")

        def update_gamma(work: Path) -> None:
            (work / "agents" / "gamma.md").write_text(
                "---\nname: gamma\ndescription: Updated\ntools: Read\n---\nupdated upstream body"
            )

        _push_upstream_change(bare_cc_plugin, tmp_path, "gamma-v2", update_gamma)
        s2 = _CONVERTER.import_plugin(url, hermes_home=hermes_home)

        # User edit preserved
        assert gamma_md.read_text() == "user-edited content"
        # Summary reports the skip
        assert "fixture-plugin/agents/gamma" in s2.skipped_user_modified

    def test_explicit_hermes_home_overrides_env_var(self, tmp_path, bare_cc_plugin, monkeypatch):
        env_home = tmp_path / "env-home"
        monkeypatch.setenv("HERMES_HOME", str(env_home))
        explicit_home = tmp_path / "explicit-home"

        _CONVERTER.import_plugin(f"file://{bare_cc_plugin}", hermes_home=explicit_home)

        assert (explicit_home / "skills" / "fixture-plugin" / "alpha").exists()
        # env_home was untouched
        assert not (env_home / "skills").exists()

    def test_subdir_support(self, tmp_path, bare_upstream):
        # Build upstream with content nested under plugins/nested-plugin/
        def build(work: Path) -> None:
            nested = work / "plugins" / "nested-plugin"
            nested.mkdir(parents=True)
            (nested / "plugin.json").write_text('{"name": "nested-plugin"}\n')
            (nested / "skills" / "x").mkdir(parents=True)
            (nested / "skills" / "x" / "SKILL.md").write_text("---\nname: x\n---\nx body")

        _push_upstream_change(bare_upstream, tmp_path, "nested", build)

        hermes_home = tmp_path / "alt-hermes"
        summary = _CONVERTER.import_plugin(
            f"file://{bare_upstream}",
            subdir="plugins/nested-plugin",
            hermes_home=hermes_home,
        )
        assert summary.plugin == "nested-plugin"
        assert (hermes_home / "skills" / "nested-plugin" / "x" / "SKILL.md").exists()

    def test_plugin_name_falls_back_to_url_basename_when_no_plugin_json(
        self, tmp_path, bare_upstream
    ):
        def build(work: Path) -> None:
            (work / "skills" / "s").mkdir(parents=True)
            (work / "skills" / "s" / "SKILL.md").write_text("---\nname: s\n---\nbody")

        _push_upstream_change(bare_upstream, tmp_path, "noname", build)

        hermes_home = tmp_path / "alt-hermes"
        summary = _CONVERTER.import_plugin(f"file://{bare_upstream}", hermes_home=hermes_home)
        # bare_upstream's path is tmp_path/upstream.git → basename "upstream.git"
        # → ".git" stripped → "upstream"
        assert summary.plugin == "upstream"

    def test_invalid_branch_raises_called_process_error(self, tmp_path, bare_cc_plugin):
        hermes_home = tmp_path / "alt-hermes"
        with pytest.raises(subprocess.CalledProcessError):
            _CONVERTER.import_plugin(
                f"file://{bare_cc_plugin}",
                branch="nonexistent-branch",
                hermes_home=hermes_home,
            )


# ---------------------------------------------------------------------------
# Slice 2 additions: ISO timestamp helper, security validators, hardened
# clone env, atomic-rename save, manifest v2 _plugins index.
# ---------------------------------------------------------------------------


class TestNowIso:
    """``_now_iso() -> str`` — ISO-8601 UTC timestamp for ``imported_at``."""

    def test_returns_iso8601_z_format(self):
        import re

        ts = _CONVERTER._now_iso()
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts), ts

    def test_returns_current_year_or_later(self):
        from datetime import datetime

        ts = _CONVERTER._now_iso()
        parsed = datetime.fromisoformat(ts.rstrip("Z"))
        assert parsed.year >= 2026
