"""Conversion logic — Claude Code plugin layout to Hermes skill layout.

Ported faithfully from ``~/Dev/hermes-depracted/scripts/plugin_sync.py``,
the battle-tested converter that imported the EveryInc/compound-engineering
plugin (36 skills + 48 agents) into a local Hermes install on 2026-04-25.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    """Write *manifest* to *path* atomically via ``.tmp`` + ``os.replace``.

    Creates parent directories if missing. Output uses ``indent=2`` and
    ``sort_keys=True`` so successive saves produce stable diffs.

    Atomic-rename mitigates torn writes under concurrent invocations
    (slice 2 R6). It does not serialize concurrent callers — single-
    threaded use remains the documented assumption — but a process
    killed mid-write leaves either the prior valid file or no file at
    all, never a half-written one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    os.replace(tmp, path)


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
    env = _safe_clone_env()
    if dest.exists() and (dest / ".git").exists():
        logger.info("Updating plugin repo at %s", dest)
        subprocess.run(
            ["git", "-C", str(dest), "fetch", "--depth=1", "origin", branch],
            check=True,
            env=env,
        )
        subprocess.run(
            ["git", "-C", str(dest), "reset", "--hard", f"origin/{branch}"],
            check=True,
            env=env,
        )
        return
    if dest.exists():
        logger.info("Removing non-git dir at %s before clone", dest)
        shutil.rmtree(dest)
    logger.info("Cloning %s@%s → %s", url, branch, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "git",
            "clone",
            "--depth=1",
            "--branch",
            branch,
            "--no-recurse-submodules",
            "--config",
            "core.hooksPath=/dev/null",
            url,
            str(dest),
        ],
        check=True,
        env=env,
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
) -> str:
    """Copy a Claude Code skill tree into the Hermes skills tree.

    The 3-hash decision matrix decides what to do on each call:
      - ``local == origin``           → UNCHANGED (refresh manifest, no rewrite)
      - ``local == prior_origin``     → COPY    (upstream updated, user clean)
      - ``local`` differs from both   → SKIP    (user-modified, preserved with warning)

    A skill dir without a ``SKILL.md`` is treated as not-a-skill and silently
    skipped — no manifest entry, no copy.

    Returns one of ``"COPY"``, ``"UNCHANGED"``, ``"SKIP"``, or ``"NOSKILL"``
    so callers (e.g. :func:`import_plugin`) can roll up summaries.
    """
    src_md = src_skill_dir / "SKILL.md"
    if not src_md.exists():
        logger.debug("No SKILL.md in %s — skipping", src_skill_dir)
        return "NOSKILL"

    key = str(dest_skill_dir.relative_to(skills_dir))
    origin_hash = sha256_file(src_md)
    entry = manifest.get(key)
    dest_md = dest_skill_dir / "SKILL.md"

    if dest_md.exists() and entry:
        local_hash = sha256_file(dest_md)
        if local_hash != entry["origin_hash"] and local_hash != origin_hash:
            logger.warning("SKIP (user-modified): %s", key)
            return "SKIP"
        if local_hash == origin_hash:
            logger.debug("UNCHANGED: %s", key)
            manifest[key] = {
                "plugin": plugin,
                "kind": "skill",
                "source_path": str(src_skill_dir),
                "origin_hash": origin_hash,
            }
            return "UNCHANGED"

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
    return "COPY"


def migrate_agent(
    src_agent_md: Path,
    dest_skill_dir: Path,
    manifest: dict[str, Any],
    plugin: str,
    skills_dir: Path,
) -> str:
    """Translate a Claude Code agent ``.md`` into a Hermes delegation skill.

    Same 3-hash matrix as :func:`migrate_skill`, but ``origin_hash`` is
    computed over the *translated* output — so a change in
    :func:`build_delegation_skill`'s logic also triggers a re-translation
    on the next sync.

    Returns one of ``"TRANSLATE"``, ``"UNCHANGED"``, or ``"SKIP"``.
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
            return "SKIP"
        if local_hash == origin_hash:
            manifest[key] = {
                "plugin": plugin,
                "kind": "agent",
                "source_path": str(src_agent_md),
                "origin_hash": origin_hash,
            }
            return "UNCHANGED"

    dest_skill_dir.mkdir(parents=True, exist_ok=True)
    dest_md.write_text(new_content)
    manifest[key] = {
        "plugin": plugin,
        "kind": "agent",
        "source_path": str(src_agent_md),
        "origin_hash": origin_hash,
    }
    logger.info("TRANSLATE agent: %s", key)
    return "TRANSLATE"


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


# ---------------------------------------------------------------------------
# Orchestrator — public entrypoint
# ---------------------------------------------------------------------------


@dataclass
class ImportSummary:
    """Result of a single :func:`import_plugin` call."""

    plugin: str
    skills_imported: int = 0
    skills_unchanged: int = 0
    agents_translated: int = 0
    agents_unchanged: int = 0
    skipped_user_modified: list[str] = field(default_factory=list)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 ``...Z`` string.

    Used for ``imported_at`` on manifest entries. Format is fixed at
    seconds precision with a trailing ``Z`` so successive saves produce
    stable, sortable, jq-friendly timestamps.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# Hostnames an agent-callable installer is allowed to clone from. Slice 2
# hardcodes the most common public Git hosts. Slice 3 may surface a config
# override; for now slash-command users bypass this check entirely.
_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {"github.com", "gitlab.com", "bitbucket.org", "codeberg.org"}
)


def _validate_url(url: str) -> None:
    """Validate *url* for agent-callable use (R10). Raises ``ValueError`` on rejection.

    Three layered checks:
      1. URL must be a non-empty HTTPS string. Anything else (file://,
         git://, ssh://, scp-style ``git@host:org/repo``, plain http://)
         raises with a "https" or "scheme" hint.
      2. URL must parse to a hostname. Empty or unparseable values raise.
      3. Hostname must be on :data:`_ALLOWED_HOSTS`. Anything else raises
         with an "allowlist" hint so tool handlers can surface
         ``tool_error("disallowed_host", ...)``.

    Slash command callers do **not** invoke this — they're considered
    human-vetted. Tool handlers in :mod:`tools` do.
    """
    from urllib.parse import urlparse

    if not isinstance(url, str) or not url.strip():
        raise ValueError("git_url is required and must be a non-empty string")
    if not url.startswith("https://"):
        raise ValueError("git_url must use https scheme; got " + url.split("://", 1)[0] + "://")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("git_url has no parseable hostname")
    if host not in _ALLOWED_HOSTS:
        raise ValueError(
            f"git_url host {host!r} is not on the allowlist ({', '.join(sorted(_ALLOWED_HOSTS))})"
        )


_PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_plugin_name(name: str) -> None:
    """Reject plugin names that could escape ``$HERMES_HOME/skills/`` (R11).

    The name is used as a directory under ``skills/``, so any path-special
    character would let a malicious ``plugin.json`` write outside the
    intended subtree. We require ASCII letters/digits/dot/dash/underscore
    and reject the literal ``..`` traversal token.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("plugin_name is required and must be a non-empty string")
    if name == "..":
        raise ValueError("plugin_name cannot be '..'")
    if not _PLUGIN_NAME_RE.match(name):
        raise ValueError(
            f"plugin_name {name!r} contains disallowed characters (only [A-Za-z0-9._-] permitted)"
        )


def _safe_clone_env() -> dict[str, str]:
    """Return an env dict that suppresses git's system + global config (R10).

    Combined with ``--config core.hooksPath=/dev/null`` and
    ``--no-recurse-submodules`` flags on the ``git clone`` argv, this
    eliminates the most common arbitrary-code-execution vectors in
    cloned repos: post-checkout hooks, ``core.fsmonitor`` payloads in
    a malicious ``.git/config``, and submodule recursion (per
    CVE-2017-1000117 mitigation guidance).
    """
    return {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }


def _validate_subdir(subdir: str, clone_root: Path) -> Path:
    """Resolve *subdir* against *clone_root* and assert it stays inside (R11).

    Returns the resolved absolute path on success. Raises ``ValueError`` if
    the resolved path escapes the clone root via ``..`` or absolute-path
    components. ``subdir=""`` is treated as the clone root itself.
    """
    if not isinstance(subdir, str):
        raise ValueError("subdir must be a string")
    root = clone_root.resolve()
    candidate = (clone_root / subdir).resolve() if subdir else root
    if not (candidate == root or candidate.is_relative_to(root)):
        raise ValueError(f"subdir {subdir!r} resolves outside clone root {clone_root}")
    return candidate


def _resolve_hermes_home(hermes_home: Path | None) -> Path:
    if hermes_home is not None:
        return hermes_home
    env = os.environ.get("HERMES_HOME")
    return Path(env) if env else Path.home() / ".hermes"


def _repo_basename(git_url: str) -> str:
    """Return the trailing path component of *git_url*, stripped of any ``.git``."""
    base = git_url.rstrip("/").split("/")[-1]
    if base.endswith(".git"):
        base = base[:-4]
    return base


def _resolve_plugin_name(plugin_root: Path, fallback: str) -> str:
    """Read ``plugin.json``'s ``name`` field, or fall back to *fallback*."""
    plugin_json = plugin_root / "plugin.json"
    if plugin_json.exists():
        try:
            data = json.loads(plugin_json.read_text())
            name = data.get("name")
            if isinstance(name, str) and name:
                return name
        except (json.JSONDecodeError, OSError):
            pass
    return fallback


def import_plugin(
    git_url: str,
    *,
    branch: str = "main",
    subdir: str = "",
    hermes_home: Path | None = None,
) -> ImportSummary:
    """Import a Claude Code plugin into a local Hermes install.

    Clones (or fetches) *git_url*@*branch* into the plugin's clone cache,
    then walks ``<repo>/<subdir>/skills/*`` and ``<repo>/<subdir>/agents/*.md``
    invoking :func:`migrate_skill` and :func:`migrate_agent` for each. The
    plugin name is read from ``plugin.json``'s ``name`` field if present,
    otherwise derived from the git URL's trailing path component (sans
    ``.git``). Manifest is persisted at
    ``$HERMES_HOME/plugins/cc-import/state.json``.

    *hermes_home* defaults to the ``HERMES_HOME`` env var or ``~/.hermes``.
    """
    home = _resolve_hermes_home(hermes_home)
    skills_dir = home / "skills"
    state_dir = home / "plugins" / "cc-import"
    clone_root = state_dir / "clones"
    manifest_path = state_dir / "state.json"

    repo_basename = _repo_basename(git_url)
    clone_dest = clone_root / repo_basename

    clone_or_update(git_url, branch, clone_dest)

    # R11: validate subdir against the resolved clone root before any read.
    plugin_root = _validate_subdir(subdir, clone_dest)
    plugin_name = _resolve_plugin_name(plugin_root, repo_basename)
    # R11: validate plugin name (could come from a malicious plugin.json)
    # before using it as a directory under skills_dir.
    _validate_plugin_name(plugin_name)

    manifest = load_manifest(manifest_path)
    # R6: install-cache index. Preserve url/branch/subdir on rerun;
    # always refresh imported_at. Slice 3's sources.yaml will become the
    # canonical user-intent store; this remains the derived cache.
    plugins_index = manifest.setdefault("_plugins", {})
    existing = plugins_index.get(plugin_name, {})
    plugins_index[plugin_name] = {
        "url": existing.get("url", git_url),
        "branch": existing.get("branch", branch),
        "subdir": existing.get("subdir", subdir),
        "imported_at": _now_iso(),
    }

    summary = ImportSummary(plugin=plugin_name)
    seen_keys: set[str] = set()

    skills_src = plugin_root / "skills"
    if skills_src.is_dir():
        plugin_dest = skills_dir / plugin_name
        plugin_dest.mkdir(parents=True, exist_ok=True)
        for skill_dir in sorted(skills_src.iterdir()):
            if not skill_dir.is_dir():
                continue
            dest = plugin_dest / skill_dir.name
            status = migrate_skill(skill_dir, dest, manifest, plugin_name, skills_dir)
            key = str(dest.relative_to(skills_dir))
            seen_keys.add(key)
            if status == "COPY":
                summary.skills_imported += 1
            elif status == "UNCHANGED":
                summary.skills_unchanged += 1
            elif status == "SKIP":
                summary.skipped_user_modified.append(key)

    agents_src = plugin_root / "agents"
    if agents_src.is_dir():
        agents_dest_root = skills_dir / plugin_name / "agents"
        agents_dest_root.mkdir(parents=True, exist_ok=True)
        for agent_md in sorted(agents_src.rglob("*.md")):
            agent_name = agent_md.stem
            dest = agents_dest_root / agent_name
            status = migrate_agent(agent_md, dest, manifest, plugin_name, skills_dir)
            key = str(dest.relative_to(skills_dir))
            seen_keys.add(key)
            if status == "TRANSLATE":
                summary.agents_translated += 1
            elif status == "UNCHANGED":
                summary.agents_unchanged += 1
            elif status == "SKIP":
                summary.skipped_user_modified.append(key)

    prune_removed(plugin_name, seen_keys, manifest, skills_dir)
    save_manifest(manifest_path, manifest)
    return summary
