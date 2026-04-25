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
from pathlib import Path


def _load_converter():
    repo_root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("cc_import_converter", repo_root / "converter.py")
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
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
