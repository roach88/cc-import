"""State introspection + removal — the pure-logic core for ``cc_import_list``
and ``cc_import_remove`` (and their slash-command equivalents).

``list_imports`` reads the manifest and groups file entries by plugin,
augmenting each with metadata from the ``_plugins`` install-cache index
when present. ``remove_import`` deletes a plugin's skills tree, clone
cache, and manifest entries while honoring the user-modified
preservation matrix from slice 1.

This module imports manifest helpers (``load_manifest``, ``save_manifest``,
``sha256_file``, ``_resolve_hermes_home``, ``_repo_basename``) from
``converter.py`` rather than re-implementing them.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Hermes loads this module under package context (relative import works).
# Pytest loads via ``importlib.util`` after registering ``converter`` in
# sys.modules, so the absolute fallback finds the same module object.
try:
    from . import converter
except ImportError:
    import converter  # type: ignore[no-redef]


@dataclass
class PluginListEntry:
    """One installed plugin as surfaced by ``cc_import_list``."""

    name: str
    skills_count: int
    agents_count: int
    url: str | None
    branch: str | None
    imported_at: str | None


def list_imports(hermes_home: Path | None = None) -> list[PluginListEntry]:
    """Return all installed plugins sorted by name.

    File entries are grouped by ``entry["plugin"]`` and counted by ``kind``.
    Per-plugin URL/branch/imported_at are pulled from the v2 ``_plugins``
    install-cache index when present; a v1 manifest (no index) yields
    entries with ``url``/``branch``/``imported_at`` set to ``None``.

    Entries without a ``plugin`` key are silently skipped — corrupt or
    foreign manifest data shouldn't crash the listing.
    """
    home = converter._resolve_hermes_home(hermes_home)
    manifest_path = home / "plugins" / "cc-import" / "state.json"
    manifest = converter.load_manifest(manifest_path)

    plugins_index: dict[str, dict[str, Any]] = manifest.get("_plugins") or {}

    counts: dict[str, dict[str, int]] = {}
    for key, entry in manifest.items():
        if key == "_plugins":
            continue
        if not isinstance(entry, dict):
            continue
        plugin = entry.get("plugin")
        if not isinstance(plugin, str) or not plugin:
            continue
        kind = entry.get("kind")
        bucket = counts.setdefault(plugin, {"skill": 0, "agent": 0})
        if kind == "skill":
            bucket["skill"] += 1
        elif kind == "agent":
            bucket["agent"] += 1

    result: list[PluginListEntry] = []
    for name in sorted(counts.keys()):
        meta = plugins_index.get(name) or {}
        result.append(
            PluginListEntry(
                name=name,
                skills_count=counts[name]["skill"],
                agents_count=counts[name]["agent"],
                url=meta.get("url"),
                branch=meta.get("branch"),
                imported_at=meta.get("imported_at"),
            )
        )
    return result


@dataclass
class RemoveResult:
    """Outcome of a single ``remove_import`` call.

    ``clone_cache_status`` is one of:
      - ``"removed"`` — clone cache was found and deleted
      - ``"already_missing"`` — clone cache path was determined but didn't
        exist on disk; manifest entries still cleaned up
      - ``"skipped_unfindable"`` — neither the ``_plugins`` index nor
        ``source_path`` walk-up yielded a cache location; warning logged
      - ``"skipped_path_outside_anchor"`` — the resolved cache path is
        not a child of ``$HERMES_HOME/plugins/cc-import/clones/``;
        deletion refused for safety
      - ``"not_attempted"`` — no entries were actually removed (dry_run
        with no skills to delete, or no_changes); cache untouched
    """

    plugin: str
    dry_run: bool = False
    removed_skills: int = 0
    removed_agents: int = 0
    kept_user_modified: list[str] = field(default_factory=list)
    clone_cache_status: str = "not_attempted"
    clone_cache_path: str | None = None
    no_changes: bool = False


def _find_clone_cache(
    home: Path,
    plugins_index: dict[str, dict[str, Any]],
    plugin_name: str,
    target_entries: list[dict[str, Any]],
) -> tuple[str, Path | None]:
    """Locate the clone cache directory for *plugin_name*.

    Returns ``(status, path | None)``. The path is *unresolved* so the
    caller's logging carries the canonical ``$HERMES_HOME/...`` form.
    """
    anchor = home / "plugins" / "cc-import" / "clones"
    anchor_resolved = anchor.resolve()

    # Prefer the v2 _plugins install-cache URL.
    meta = plugins_index.get(plugin_name) or {}
    url = meta.get("url")
    if isinstance(url, str) and url:
        basename = converter._repo_basename(url)
        cache = anchor / basename
        if cache.exists():
            return ("removed", cache)
        return ("already_missing", cache)

    # Fallback: walk up source_path looking for the anchor as a parent.
    saw_clones_outside_anchor = False
    for entry in target_entries:
        sp = entry.get("source_path")
        if not isinstance(sp, str) or not sp:
            continue
        try:
            sp_resolved = Path(sp).resolve()
        except (OSError, ValueError):
            continue
        try:
            if sp_resolved.is_relative_to(anchor_resolved):
                relative = sp_resolved.relative_to(anchor_resolved)
                if relative.parts:
                    cache = anchor / relative.parts[0]
                    if cache.exists():
                        return ("removed", cache)
                    return ("already_missing", cache)
        except ValueError:
            pass
        if "clones" in sp_resolved.parts:
            saw_clones_outside_anchor = True

    if saw_clones_outside_anchor:
        return ("skipped_path_outside_anchor", None)
    return ("skipped_unfindable", None)


def remove_import(
    plugin_name: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    hermes_home: Path | None = None,
) -> RemoveResult:
    """Delete *plugin_name*'s skills tree, clone cache, and manifest entries.

    User-modified files (where the on-disk hash differs from the recorded
    ``origin_hash``) are preserved by default and listed in
    ``kept_user_modified``. Pass ``force=True`` to delete them anyway.
    Pass ``dry_run=True`` to compute the result without touching disk
    or manifest.

    Idempotent: removing a plugin that isn't installed returns
    ``RemoveResult(no_changes=True)`` rather than raising.
    """
    home = converter._resolve_hermes_home(hermes_home)
    skills_dir = home / "skills"
    manifest_path = home / "plugins" / "cc-import" / "state.json"

    manifest = converter.load_manifest(manifest_path)
    plugins_index: dict[str, dict[str, Any]] = manifest.get("_plugins") or {}

    # Collect target entries: file entries scoped to this plugin.
    target_keys: list[str] = []
    target_entries: list[dict[str, Any]] = []
    for key, entry in manifest.items():
        if key == "_plugins":
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("plugin") == plugin_name:
            target_keys.append(key)
            target_entries.append(entry)

    if not target_keys and plugin_name not in plugins_index:
        return RemoveResult(plugin=plugin_name, dry_run=dry_run, no_changes=True)

    # Decide per-entry: delete or keep (user-modified).
    to_delete: list[tuple[str, dict[str, Any]]] = []
    kept: list[str] = []
    for key, entry in zip(target_keys, target_entries, strict=True):
        dest_md = skills_dir / key / "SKILL.md"
        delete_this = True
        if dest_md.exists() and not force:
            try:
                local_hash = converter.sha256_file(dest_md)
            except OSError:
                local_hash = None
            origin_hash = entry.get("origin_hash")
            if local_hash is not None and origin_hash and local_hash != origin_hash:
                delete_this = False
        if delete_this:
            to_delete.append((key, entry))
        else:
            kept.append(key)

    removed_skills = sum(1 for _, e in to_delete if e.get("kind") == "skill")
    removed_agents = sum(1 for _, e in to_delete if e.get("kind") == "agent")

    # Determine clone cache (still meaningful even in dry_run for reporting).
    cache_status, cache_path = _find_clone_cache(home, plugins_index, plugin_name, target_entries)
    cache_path_str = str(cache_path) if cache_path is not None else None

    if dry_run:
        return RemoveResult(
            plugin=plugin_name,
            dry_run=True,
            removed_skills=removed_skills,
            removed_agents=removed_agents,
            kept_user_modified=kept,
            clone_cache_status=cache_status,
            clone_cache_path=cache_path_str,
        )

    # Execute deletions.
    for key, _entry in to_delete:
        dest_dir = skills_dir / key
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        manifest.pop(key, None)
        logger.info("REMOVE: %s", key)

    for key in kept:
        logger.warning("KEEP (user-modified): %s", key)

    # Clean up the plugin's now-empty parent dirs under skills/. Two levels
    # because agents live one level deeper (skills/<plugin>/agents/<name>/).
    plugin_dir = skills_dir / plugin_name
    if plugin_dir.exists():
        agents_dir = plugin_dir / "agents"
        if agents_dir.exists() and not any(agents_dir.iterdir()):
            agents_dir.rmdir()
        if not any(plugin_dir.iterdir()):
            plugin_dir.rmdir()

    # Drop _plugins meta only if no on-disk entries remain for this plugin
    # (i.e., everything got force-deleted or no user-modified kept).
    if not kept and plugin_name in plugins_index:
        del plugins_index[plugin_name]
        if not plugins_index:
            manifest.pop("_plugins", None)

    # Delete clone cache when found.
    if cache_status == "removed" and cache_path is not None:
        if cache_path.exists():
            shutil.rmtree(cache_path)
        else:
            cache_status = "already_missing"
    elif cache_status == "skipped_unfindable":
        logger.warning(
            "Clone cache for %s could not be located; skipping cache deletion.",
            plugin_name,
        )
    elif cache_status == "skipped_path_outside_anchor":
        logger.warning(
            "Clone cache for %s resolved outside %s; refusing to delete.",
            plugin_name,
            home / "plugins" / "cc-import" / "clones",
        )

    converter.save_manifest(manifest_path, manifest)

    return RemoveResult(
        plugin=plugin_name,
        dry_run=False,
        removed_skills=removed_skills,
        removed_agents=removed_agents,
        kept_user_modified=kept,
        clone_cache_status=cache_status,
        clone_cache_path=cache_path_str,
    )
