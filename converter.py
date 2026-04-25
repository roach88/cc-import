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

# Claude Code tool name → Hermes toolset. Covers the common CC tools;
# unknowns are reported, not dropped. Extend as upstream plugins reveal new
# tools. Ports verbatim from plugin_sync.py.
TOOL_MAP: dict[str, str] = {
    "Read": "file",
    "Grep": "file",
    "Glob": "file",
    "Edit": "file",
    "Write": "file",
    "NotebookEdit": "file",
    "Bash": "terminal",
    "WebFetch": "web",
    "WebSearch": "web",
}

# Tools dropped silently — present in source CC plugins but not actionable in
# Hermes. ``Task`` is dropped because Hermes sub-agents (delegation skills)
# cannot delegate further.
TOOL_DROP: set[str] = {"Task"}

# Default toolsets used when (a) the source has no `tools:` declared, or
# (b) every declared tool is unknown to TOOL_MAP. Matches plugin_sync.py.
_DEFAULT_TOOLSETS: list[str] = ["file", "web"]


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


def translate_tools(cc_tools: Any) -> tuple[list[str], list[str]]:
    """Map Claude Code tool names to Hermes toolsets.

    Returns ``(hermes_toolsets, unknown_tool_names)``. Inputs:
      - ``None`` / empty string / empty list / unexpected type: default fallback
      - comma-separated string: split, trim, translate
      - list of strings: translate

    Tools in :data:`TOOL_DROP` are silently skipped. Tools not in
    :data:`TOOL_MAP` are returned in the unknown list. When no input tool
    maps to a known toolset, the default ``["file", "web"]`` is returned so
    the resulting agent has at least the most common toolsets available.
    """
    if not cc_tools:
        return list(_DEFAULT_TOOLSETS), []

    if isinstance(cc_tools, str):
        names = [t.strip() for t in cc_tools.split(",") if t.strip()]
    elif isinstance(cc_tools, list):
        names = [str(t).strip() for t in cc_tools]
    else:
        return list(_DEFAULT_TOOLSETS), []

    toolsets: list[str] = []
    unknown: list[str] = []
    for name in names:
        if name in TOOL_DROP:
            continue
        mapped = TOOL_MAP.get(name)
        if mapped:
            if mapped not in toolsets:
                toolsets.append(mapped)
        else:
            unknown.append(name)
    if not toolsets:
        toolsets = list(_DEFAULT_TOOLSETS)
    return toolsets, unknown


def build_delegation_skill(
    plugin: str,
    agent_name: str,
    cc_fm: dict[str, Any],
    cc_body: str,
) -> str:
    """Translate a Claude Code agent file into a Hermes delegation skill.

    Hermes does not have a native sub-agent primitive, so each Claude Code
    agent becomes a "delegation skill" that, when matched, instructs Hermes
    to invoke ``delegate_task`` with the agent's persona as context. The
    returned string is a complete SKILL.md ready to be written to disk.
    """
    description = cc_fm.get("description", "").strip()
    toolsets, unknown_tools = translate_tools(cc_fm.get("tools"))

    new_fm = {
        "name": f"{plugin}/agent/{agent_name}",
        "description": description or f"Delegate to the {agent_name} sub-agent persona.",
        "version": "1.0.0",
        "metadata": {
            "hermes": {
                "source": plugin,
                "source_kind": "agent",
                "upstream_name": agent_name,
                "toolsets": toolsets,
            }
        },
    }

    unknown_note = ""
    if unknown_tools:
        unknown_note = (
            f"\n> ⚠️ Upstream tools not mapped to Hermes toolsets: {', '.join(unknown_tools)}\n"
        )

    body = (
        "> 🤖 **Delegation skill** — translated from a Claude Code agent.\n"
        "> When this skill matches, invoke `delegate_task` with:\n"
        f"> - `toolsets`: {toolsets}\n"
        "> - `context`: the persona text below, verbatim\n"
        "> - `goal`: restate the user's ask from this persona's perspective\n"
        "> - `max_iterations`: 30\n"
        f"{unknown_note}\n"
        "## Persona\n\n"
        f"{cc_body.lstrip()}"
    )
    return render_frontmatter(new_fm, body)
