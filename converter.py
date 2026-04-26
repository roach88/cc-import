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
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)

_FM_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


# Match absolute path-like substrings. Each component starts with at least
# one path-allowed char (alnum, dot, dash, underscore) so digit-starting
# components like /proc/1234 / /tmp/456-foo are also redacted. Shared by
# the slash surface (:mod:`cli`) and the agent tool surface (:mod:`tools`)
# so error messages don't leak filesystem layout.
_PATH_RE = re.compile(r"/[\w.][\w./-]*")


def _redact_paths(text: str) -> str:
    """Replace absolute path-like substrings with ``<path>``.

    Exception messages routinely embed filesystem paths
    (``FileNotFoundError: ...: '/Users/.../state.json'``); leaking those
    in ``tool_error`` output (or in slash output that may also reach an
    agent via gateway sessions / transcript replay) lets a prompt-injected
    agent map the local layout.
    """
    return _PATH_RE.sub("<path>", text or "")


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
    """Write *manifest* to *path* atomically via ``.tmp.<uuid>`` + ``os.replace``.

    Creates parent directories if missing. Output uses ``indent=2`` and
    ``sort_keys=True`` so successive saves produce stable diffs.

    Atomic-rename mitigates torn writes (R6). The tmp filename includes
    a per-call uuid so two concurrent savers — different agent turns,
    different processes — write to different tmp files and never
    collide on ``os.replace``. Single-threaded use is still the
    documented assumption (last-writer-wins on the final state.json),
    but this hardens the failure mode from ``FileNotFoundError`` to
    benign data loss.

    On write failure (disk full, permission error), the partial tmp
    file is cleaned up so retries don't leave orphans.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp.{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------


def _clone_timeout_seconds() -> int:
    """Resolve the git-subprocess timeout from env, defaulting to 120s.

    A hung upstream (slow network, stalled server) would otherwise freeze
    agent tool dispatch indefinitely. The env override exists for slow
    networks and large historical repos; 120s is the default for the
    common case of small Claude Code plugin repos on a healthy connection.
    """
    raw = os.environ.get("CC_IMPORT_CLONE_TIMEOUT", "")
    try:
        value = int(raw)
        return value if value > 0 else 120
    except (TypeError, ValueError):
        return 120


def clone_or_update(url: str, branch: str, dest: Path) -> None:
    """Clone *url*@*branch* into *dest*, or fetch + reset if already cloned.

    Three cases:
      - ``dest`` does not exist → ``git clone --depth=1 --branch <branch>``
      - ``dest`` is an existing checkout (has ``.git/``) → ``git fetch`` +
        ``git reset --hard origin/<branch>`` to align with upstream
      - ``dest`` exists but is not a git checkout → removed first, then
        cloned (anything inside is discarded — the clone workspace is owned
        by the caller and should not contain user data)

    All three subprocess calls carry a ``timeout=`` so a hung upstream
    can't freeze agent dispatch. ``subprocess.TimeoutExpired`` propagates
    to the caller; tool handlers in :mod:`tools` map it to
    ``tool_error("clone_timeout", ...)``.
    """
    env = _safe_clone_env()
    timeout = _clone_timeout_seconds()
    if dest.exists() and (dest / ".git").exists():
        logger.info("Updating plugin repo at %s", dest)
        subprocess.run(
            ["git", "-C", str(dest), "fetch", "--depth=1", "origin", branch],
            check=True,
            env=env,
            timeout=timeout,
        )
        subprocess.run(
            ["git", "-C", str(dest), "reset", "--hard", f"origin/{branch}"],
            check=True,
            env=env,
            timeout=timeout,
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
            f"core.hooksPath={os.devnull}",
            url,
            str(dest),
        ],
        check=True,
        env=env,
        timeout=timeout,
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


def _skill_has_user_changes(dest_dir: Path, entry: dict[str, Any]) -> bool:
    """Detect user changes inside ``dest_dir`` relative to its source clone.

    Slice 1 only compared ``SKILL.md`` hashes — that misses user-added
    auxiliary files (``helper.py``, ``config.yaml``) and files the user
    edited beyond SKILL.md. Slice 2 extends the check to a whole-tree
    compare against the manifest entry's ``source_path``:

    1. SKILL.md hash differs from ``origin_hash`` → user-modified.
    2. Any file in ``dest_dir`` not present in ``source_path`` → user-added.
    3. Any file in both with differing hash → user-modified.

    Falls back to the slice-1 SKILL.md-only result when the source clone
    is missing or empty (clone cache already pruned, or test fixture
    didn't seed source files). Conservative on I/O errors — returns
    True so the dir is preserved.
    """
    origin_hash = entry.get("origin_hash")
    dest_md = dest_dir / "SKILL.md"

    if dest_md.exists() and origin_hash:
        try:
            if sha256_file(dest_md) != origin_hash:
                return True
        except OSError:
            return True

    source_path = entry.get("source_path")
    if not isinstance(source_path, str) or not source_path:
        return False
    src_dir = Path(source_path)
    if not src_dir.exists():
        return False

    try:
        src_files = {p.relative_to(src_dir): p for p in src_dir.rglob("*") if p.is_file()}
    except OSError:
        return False
    if not src_files:
        # Source has no files to compare — SKILL.md-only check above decided.
        return False

    try:
        dest_files = {p.relative_to(dest_dir): p for p in dest_dir.rglob("*") if p.is_file()}
    except OSError:
        return True

    # User-added: any file in dest not in src
    if any(rel not in src_files for rel in dest_files):
        return True
    # User-modified: hash differs for any file in both. Files only in src
    # (deleted by user) don't count — we're about to remove the dir anyway.
    for rel, dest_file in dest_files.items():
        try:
            if sha256_file(dest_file) != sha256_file(src_files[rel]):
                return True
        except OSError:
            return True
    return False


def prune_removed(
    plugin: str,
    seen_keys: set[str],
    manifest: dict[str, Any],
    skills_dir: Path,
) -> None:
    """Remove dest dirs for skills no longer in upstream, if unmodified.

    Iterates manifest entries scoped to ``plugin`` that were *not* observed
    during this sync (i.e., not in ``seen_keys``). For each such stale
    entry: if the on-disk tree is unmodified relative to the recorded
    source (whole-tree check via :func:`_skill_has_user_changes` —
    SKILL.md hash plus user-added auxiliary files like ``helper.py`` /
    ``config.yaml``), delete it and pop the manifest entry; otherwise log
    a KEEP warning and retain both file and manifest entry. If the file
    has already been deleted out from under us, the manifest entry is
    popped cleanly.
    """
    stale = [k for k, v in manifest.items() if v.get("plugin") == plugin and k not in seen_keys]
    for key in stale:
        entry = manifest[key]
        dest_dir = skills_dir / key
        dest_md = dest_dir / "SKILL.md"
        if dest_md.exists():
            if _skill_has_user_changes(dest_dir, entry):
                logger.warning("KEEP (user-modified, upstream removed): %s", key)
                continue
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

    Four layered checks:
      1. URL must be a non-empty HTTPS string. Anything else (file://,
         git://, ssh://, scp-style ``git@host:org/repo``, plain http://)
         raises with a "https" or "scheme" hint.
      2. URL must parse to a hostname. Empty or unparseable values raise.
      3. URL must NOT carry userinfo (``https://user:token@host/...``).
         Embedded credentials would persist in ``_plugins[plugin].url``
         and re-enter agent context on every ``cc_import_list`` call —
         a real exfil vector via prompt injection.
      4. Hostname must be on :data:`_ALLOWED_HOSTS`. Anything else raises
         with an "allowlist" hint so tool handlers can surface
         ``tool_error("disallowed_host", ...)``.

    Slash command callers do **not** invoke this — they're considered
    human-vetted. Tool handlers in :mod:`tools` do.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("git_url is required and must be a non-empty string")
    if not url.startswith("https://"):
        # Don't echo the user-supplied URL in the error message. tool_error
        # surfaces this and a malicious SCP-style URL (git@host:org/repo)
        # would slip past _redact_paths.
        raise ValueError(
            "git_url must use the https:// scheme. SCP-style and other "
            "non-HTTPS URLs are not permitted."
        )
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("git_url has no parseable hostname")
    if parsed.username or parsed.password:
        raise ValueError(
            "git_url must not embed credentials (user:token@host). "
            "Use Hermes's credential layer or git's credential helper for "
            "private repos; tokens in URLs persist in cc-import state."
        )
    if host not in _ALLOWED_HOSTS:
        raise ValueError(
            f"git_url host {host!r} is not on the allowlist ({', '.join(sorted(_ALLOWED_HOSTS))})"
        )


def _sanitize_url(url: str) -> str:
    """Return *url* with any userinfo (``user:token@``) stripped.

    Belt-and-suspenders for the slash-command path: ``_validate_url``
    already rejects userinfo on the agent surface, but the slash command
    bypasses validation. Sanitize before storing in the ``_plugins``
    install-cache to ensure no surface accidentally persists credentials.
    """
    if not isinstance(url, str) or "@" not in url:
        return url
    parsed = urlparse(url)
    if not parsed.username and not parsed.password:
        return url
    netloc = parsed.hostname or ""
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return parsed._replace(netloc=netloc).geturl()


# First char must be alphanumeric (not a dot, dash, or underscore). This
# rejects the special directory entries '.', '..', '.hidden', as well as
# names that would be hidden in `ls` or behave oddly under shell globs.
_PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_plugin_name(name: str) -> None:
    """Reject plugin names that could escape ``$HERMES_HOME/skills/`` (R11).

    The name is used as a directory under ``skills/``, so any path-special
    character would let a malicious ``plugin.json`` write outside the
    intended subtree. We require an alphanumeric leading character, then
    only ASCII letters/digits/dot/dash/underscore — which together reject
    ``.``, ``..``, ``.hidden``, and shell-meta-character names.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("plugin_name is required and must be a non-empty string")
    if not _PLUGIN_NAME_RE.match(name):
        raise ValueError(
            f"plugin_name {name!r} contains disallowed characters (only [A-Za-z0-9._-] permitted)"
        )


def _safe_clone_env() -> dict[str, str]:
    """Return an env dict that suppresses git's system + global config (R10).

    The env vars only suppress system and global git config — they do not
    suppress hooks. Hook suppression comes from the
    ``--config core.hooksPath=...`` flag passed to ``git clone``, which
    persists into the cloned repo's ``.git/config`` and is therefore
    inherited by subsequent fetch/reset operations on that repo. With
    ``--no-recurse-submodules`` added on the clone argv, this combination
    addresses the CVE-2017-1000117 vector class.
    """
    return {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
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
    # R6: install-cache index. Always reflect the *current* call's source
    # parameters — the index is a derived view of what's installed right
    # now, not a record of the first install. (Preserving stale values
    # under idempotent-rerun would silently lie when a user re-imports
    # the same plugin on a different branch.) Slice 3's sources.yaml
    # becomes the canonical user-intent store; this stays the derived
    # cache. URL is sanitized to strip any userinfo (token@host) — see
    # _sanitize_url's docstring for the exfil-via-list rationale.
    plugins_index = manifest.setdefault("_plugins", {})
    plugins_index[plugin_name] = {
        "url": _sanitize_url(git_url),
        "branch": branch,
        "subdir": subdir,
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
