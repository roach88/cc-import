"""``/cc-import`` slash-command handler.

Slice 1 ships only the ``install`` subcommand. ``list``, ``remove``,
``update``, ``sync``, and ``sources`` land in slice 3 alongside
``sources.yaml`` and the auto-sync hook.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess

# Try-relative-then-absolute import — see ``__init__.py`` for the rationale.
try:
    from . import converter
except ImportError:
    import converter  # type: ignore[no-redef]

_USAGE = "Usage: /cc-import install <git-url> [--branch BRANCH] [--subdir SUBDIR]"


def handle_command(raw_args: str) -> str:
    """Dispatch a ``/cc-import`` invocation. Returns a result string for display."""
    tokens = shlex.split((raw_args or "").strip())
    if not tokens:
        return _USAGE

    subcommand, rest = tokens[0], tokens[1:]
    if subcommand == "install":
        return _cmd_install(rest)
    return f"Unknown subcommand: {subcommand!r}. Available: install\n{_USAGE}"


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
        return f"Error importing {ns.git_url}: {exc}"

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


def _format_summary(summary) -> str:
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
