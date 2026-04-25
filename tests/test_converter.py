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
