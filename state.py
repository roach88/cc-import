"""State introspection + removal — the pure-logic core for ``cc_import_list``
and ``cc_import_remove`` (and their slash-command equivalents).

``list_imports`` reads the manifest and groups file entries by plugin,
augmenting each with metadata from the ``_plugins`` install-cache index
when present. ``remove_import`` (added in a follow-up commit) deletes a
plugin's skills tree, clone cache, and manifest entries while honoring
the user-modified preservation matrix from slice 1.

This module imports manifest helpers (``load_manifest``, ``save_manifest``,
``sha256_file``, ``_resolve_hermes_home``, ``_repo_basename``) from
``converter.py`` rather than re-implementing them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
