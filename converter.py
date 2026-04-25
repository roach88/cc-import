"""Conversion logic — Claude Code plugin layout to Hermes skill layout.

Ported faithfully from ``~/Dev/hermes-depracted/scripts/plugin_sync.py``,
the battle-tested converter that imported the EveryInc/compound-engineering
plugin (36 skills + 48 agents) into a local Hermes install on 2026-04-25.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

_FM_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown-with-frontmatter document into (frontmatter, body).

    Returns ``({}, text)`` if no frontmatter delimiters are present.
    Returns ``({}, body)`` if the delimiters are present but the YAML is
    malformed — the malformed frontmatter content is discarded; the body
    is preserved.
    """
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    fm_raw, body = m.group(1), m.group(2)
    try:
        fm = yaml.safe_load(fm_raw) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, body


def render_frontmatter(fm: dict[str, Any], body: str) -> str:
    """Render a (frontmatter_dict, body) pair as a frontmatter-prefixed document.

    Round-trips with :func:`parse_frontmatter` for any ``body`` that does not
    have leading whitespace — leading whitespace is stripped via
    ``body.lstrip()`` before insertion (mirroring plugin_sync.py's behavior).
    """
    fm_yaml = yaml.safe_dump(fm, sort_keys=False).strip()
    return f"---\n{fm_yaml}\n---\n\n{body.lstrip()}"
