"""``/cc-import`` slash-command handler.

Slice 2 adds ``list`` and ``remove`` subcommands alongside the slice 1
``install`` command. ``update``, ``sync``, and ``sources`` land in
slice 3 alongside ``sources.yaml`` and the auto-sync hook.

The slash surface keeps ``--force`` on ``remove`` (human-typed,
explicit destructive override) and ``--json`` on ``list`` (per the
2026 CLI-for-agents pattern: CLI ``--json`` is complementary to the
agent tool surface, often more token-efficient).
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
from dataclasses import asdict

# Try-relative-then-absolute import — see ``__init__.py`` for the rationale.
try:
    from . import converter, state
except ImportError:
    import converter  # type: ignore[no-redef]
    import state  # type: ignore[no-redef]


# Slash-surface path redaction. The agent tool surface uses tools._redact_paths;
# slash output may also reach an agent (gateway sessions, transcript replay),
# so apply the same discipline before returning exception text.
_PATH_RE = re.compile(r"/[\w.][\w./-]*")


def _redact_paths(text: str) -> str:
    return _PATH_RE.sub("<path>", text or "")


_USAGE = (
    "Usage: /cc-import install <git-url> [--branch BRANCH] [--subdir SUBDIR]\n"
    "       /cc-import list [--json]\n"
    "       /cc-import remove <plugin> [--force] [--dry-run]"
)


def handle_command(raw_args: str) -> str:
    """Dispatch a ``/cc-import`` invocation. Returns a result string for display."""
    tokens = shlex.split((raw_args or "").strip())
    if not tokens:
        return _USAGE

    subcommand, rest = tokens[0], tokens[1:]
    if subcommand == "install":
        return _cmd_install(rest)
    if subcommand == "list":
        return _cmd_list(rest)
    if subcommand == "remove":
        return _cmd_remove(rest)
    return f"Unknown subcommand: {subcommand!r}. Available: install, list, remove\n{_USAGE}"


def _cmd_install(argv: list[str]) -> str:
    parser = _make_install_parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit:
        # argparse calls sys.exit on -h / parse errors; convert to a return
        # string so the slash-command dispatcher can show the message.
        return f"Error parsing arguments.\n{_USAGE}"

    try:
        summary = converter.import_plugin(ns.git_url, branch=ns.branch, subdir=ns.subdir)
    except subprocess.CalledProcessError as exc:
        return f"Error: failed to clone {ns.git_url} (git exit {exc.returncode})."
    except Exception as exc:
        return _redact_paths(f"Error importing {ns.git_url}: {exc}")

    return _format_summary(summary)


def _make_install_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="/cc-import install",
        description="Import a Claude Code plugin (skills + agents) from a git URL.",
        add_help=False,
    )
    parser.add_argument("git_url")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--subdir", default="")
    return parser


def _format_summary(summary: converter.ImportSummary) -> str:
    head = (
        f"Imported {summary.plugin}: "
        f"{summary.skills_imported} skills imported, "
        f"{summary.agents_translated} agents translated"
    )
    parts = [head]
    if summary.skills_unchanged or summary.agents_unchanged:
        parts.append(
            f"  ({summary.skills_unchanged} skills + {summary.agents_unchanged} agents unchanged)"
        )
    if summary.skipped_user_modified:
        parts.append("\nUser-modified files preserved (not overwritten):")
        for key in summary.skipped_user_modified:
            parts.append(f"  - {key}")
    return "\n".join(parts)


def _cmd_list(argv: list[str]) -> str:
    parser = _make_list_parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit:
        return f"Error parsing arguments.\n{_USAGE}"

    try:
        entries = state.list_imports()
    except Exception as exc:
        return _redact_paths(f"Error: list failed: {exc}")

    if ns.json:
        # Wrap in {plugins: [...]} to match the cc_import_list tool envelope
        # so a single agent harness can parse either surface uniformly.
        return json.dumps({"plugins": [asdict(e) for e in entries]}, indent=2)
    return _format_list_text(entries)


def _make_list_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="/cc-import list",
        description="List installed Claude Code plugins.",
        add_help=False,
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    return parser


def _format_list_text(entries: list[state.PluginListEntry]) -> str:
    if not entries:
        return "No plugins installed."
    name_w = max(len(e.name) for e in entries)
    name_w = max(name_w, len("NAME"))
    lines = [f"{'NAME':<{name_w}}  SKILLS  AGENTS  URL"]
    for e in entries:
        url = e.url or "-"
        lines.append(f"{e.name:<{name_w}}  {e.skills_count:>6}  {e.agents_count:>6}  {url}")
    return "\n".join(lines)


def _cmd_remove(argv: list[str]) -> str:
    parser = _make_remove_parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit:
        return f"Error parsing arguments.\n{_USAGE}"

    try:
        result = state.remove_import(ns.plugin, force=ns.force, dry_run=ns.dry_run)
    except Exception as exc:
        return _redact_paths(f"Error removing {ns.plugin}: {exc}")

    return _format_remove(result)


def _make_remove_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="/cc-import remove",
        description="Remove an installed Claude Code plugin.",
        add_help=False,
    )
    parser.add_argument("plugin")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete user-modified files too (slash command only).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would happen without writing.",
    )
    return parser


def _format_remove(result: state.RemoveResult) -> str:
    if result.no_changes:
        return f"No changes: {result.plugin} is not installed."
    verb = "Would remove" if result.dry_run else "Removed"
    head = f"{verb} {result.plugin}: {result.removed_skills} skills, {result.removed_agents} agents"
    parts = [head]
    if result.kept_user_modified:
        parts.append("\nUser-modified files preserved (use --force to delete them):")
        for key in result.kept_user_modified:
            parts.append(f"  - {key}")
    status = result.clone_cache_status
    if status == "removed":
        parts.append(f"  clone cache: removed ({result.clone_cache_path})")
    elif status == "already_missing":
        parts.append(f"  clone cache: already missing ({result.clone_cache_path})")
    elif status == "skipped_path_outside_anchor":
        parts.append("  clone cache: skipped (path outside cc-import state dir)")
    elif status == "skipped_unfindable":
        parts.append("  clone cache: skipped (could not locate)")
    elif status == "not_attempted":
        # Reachable when all entries are kept user-modified (no deletions ran)
        parts.append("  clone cache: not modified (no deletions occurred)")
    return "\n".join(parts)
