"""Conversion logic — Claude Code plugin layout to Hermes skill layout.

Ported faithfully from ``~/Dev/hermes-depracted/scripts/plugin_sync.py``,
the battle-tested converter that imported the EveryInc/compound-engineering
plugin (36 skills + 48 agents) into a local Hermes install on 2026-04-25.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    """Return the SHA-256 hex digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of *path*'s bytes.

    Reads the file in binary mode so line-ending differences (CRLF vs LF)
    produce different hashes — load-bearing for the manifest's
    user-modified detection.
    """
    return sha256_bytes(path.read_bytes())


# ---------------------------------------------------------------------------
# Manifest I/O
# ---------------------------------------------------------------------------


def load_manifest(path: Path) -> dict[str, Any]:
    """Load the per-plugin state manifest, or return ``{}`` if missing or corrupt.

    Corrupt JSON is treated as an empty manifest with a warning, so a
    botched on-disk state file does not crash the next sync — the next save
    will overwrite it.
    """
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        logger.warning("Manifest at %s is corrupt — treating as empty", path)
        return {}


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Write *manifest* to *path* as deterministic, diff-friendly JSON.

    Creates parent directories if missing. Output uses ``indent=2`` and
    ``sort_keys=True`` so successive saves produce stable diffs.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------


def clone_or_update(url: str, branch: str, dest: Path) -> None:
    """Clone *url*@*branch* into *dest*, or fetch + reset if already cloned.

    Three cases:
      - ``dest`` does not exist → ``git clone --depth=1 --branch <branch>``
      - ``dest`` is an existing checkout (has ``.git/``) → ``git fetch`` +
        ``git reset --hard origin/<branch>`` to align with upstream
      - ``dest`` exists but is not a git checkout → removed first, then
        cloned (anything inside is discarded — the clone workspace is owned
        by the caller and should not contain user data)
    """
    if dest.exists() and (dest / ".git").exists():
        logger.info("Updating plugin repo at %s", dest)
        subprocess.run(
            ["git", "-C", str(dest), "fetch", "--depth=1", "origin", branch],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(dest), "reset", "--hard", f"origin/{branch}"],
            check=True,
        )
        return
    if dest.exists():
        logger.info("Removing non-git dir at %s before clone", dest)
        shutil.rmtree(dest)
    logger.info("Cloning %s@%s → %s", url, branch, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth=1", "--branch", branch, url, str(dest)],
        check=True,
    )


# ---------------------------------------------------------------------------
# Migration core — skill copy, agent translation, prune cleanup
# ---------------------------------------------------------------------------


def migrate_skill(
    src_skill_dir: Path,
    dest_skill_dir: Path,
    manifest: dict[str, Any],
    plugin: str,
    skills_dir: Path,
) -> None:
    """Copy a Claude Code skill tree into the Hermes skills tree.

    The 3-hash decision matrix decides what to do on each call:
      - ``local == origin``           → UNCHANGED (refresh manifest, no rewrite)
      - ``local == prior_origin``     → COPY    (upstream updated, user clean)
      - ``local`` differs from both   → SKIP    (user-modified, preserved with warning)

    A skill dir without a ``SKILL.md`` is treated as not-a-skill and silently
    skipped — no manifest entry, no copy.
    """
    src_md = src_skill_dir / "SKILL.md"
    if not src_md.exists():
        logger.debug("No SKILL.md in %s — skipping", src_skill_dir)
        return

    key = str(dest_skill_dir.relative_to(skills_dir))
    origin_hash = sha256_file(src_md)
    entry = manifest.get(key)
    dest_md = dest_skill_dir / "SKILL.md"

    if dest_md.exists() and entry:
        local_hash = sha256_file(dest_md)
        if local_hash != entry["origin_hash"] and local_hash != origin_hash:
            logger.warning("SKIP (user-modified): %s", key)
            return
        if local_hash == origin_hash:
            logger.debug("UNCHANGED: %s", key)
            manifest[key] = {
                "plugin": plugin,
                "kind": "skill",
                "source_path": str(src_skill_dir),
                "origin_hash": origin_hash,
            }
            return

    if dest_skill_dir.exists():
        shutil.rmtree(dest_skill_dir)
    shutil.copytree(src_skill_dir, dest_skill_dir)
    manifest[key] = {
        "plugin": plugin,
        "kind": "skill",
        "source_path": str(src_skill_dir),
        "origin_hash": origin_hash,
    }
    logger.info("COPY skill: %s", key)


def migrate_agent(
    src_agent_md: Path,
    dest_skill_dir: Path,
    manifest: dict[str, Any],
    plugin: str,
    skills_dir: Path,
) -> None:
    """Translate a Claude Code agent ``.md`` into a Hermes delegation skill.

    Same 3-hash matrix as :func:`migrate_skill`, but ``origin_hash`` is
    computed over the *translated* output — so a change in
    :func:`build_delegation_skill`'s logic also triggers a re-translation
    on the next sync.
    """
    content = src_agent_md.read_text()
    cc_fm, cc_body = parse_frontmatter(content)
    agent_name = cc_fm.get("name") or src_agent_md.stem

    new_content = build_delegation_skill(plugin, agent_name, cc_fm, cc_body)

    key = str(dest_skill_dir.relative_to(skills_dir))
    origin_hash = sha256_bytes(new_content.encode())
    entry = manifest.get(key)
    dest_md = dest_skill_dir / "SKILL.md"

    if dest_md.exists() and entry:
        local_hash = sha256_file(dest_md)
        if local_hash != entry["origin_hash"] and local_hash != origin_hash:
            logger.warning("SKIP (user-modified agent): %s", key)
            return
        if local_hash == origin_hash:
            manifest[key] = {
                "plugin": plugin,
                "kind": "agent",
                "source_path": str(src_agent_md),
                "origin_hash": origin_hash,
            }
            return

    dest_skill_dir.mkdir(parents=True, exist_ok=True)
    dest_md.write_text(new_content)
    manifest[key] = {
        "plugin": plugin,
        "kind": "agent",
        "source_path": str(src_agent_md),
        "origin_hash": origin_hash,
    }
    logger.info("TRANSLATE agent: %s", key)


def prune_removed(
    plugin: str,
    seen_keys: set[str],
    manifest: dict[str, Any],
    skills_dir: Path,
) -> None:
    """Remove dest dirs for skills no longer in upstream, if unmodified.

    Iterates manifest entries scoped to ``plugin`` that were *not* observed
    during this sync (i.e., not in ``seen_keys``). For each such stale
    entry: if the on-disk file is unmodified relative to the recorded
    ``origin_hash``, delete it and pop the manifest entry; otherwise log a
    KEEP warning and retain both file and manifest entry. If the file has
    already been deleted out from under us, the manifest entry is popped
    cleanly.
    """
    stale = [k for k, v in manifest.items() if v.get("plugin") == plugin and k not in seen_keys]
    for key in stale:
        entry = manifest[key]
        dest_md = skills_dir / key / "SKILL.md"
        if dest_md.exists():
            local_hash = sha256_file(dest_md)
            if local_hash != entry["origin_hash"]:
                logger.warning("KEEP (user-modified, upstream removed): %s", key)
                continue
            dest_dir = skills_dir / key
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            logger.info("REMOVE (upstream deleted): %s", key)
        manifest.pop(key, None)
