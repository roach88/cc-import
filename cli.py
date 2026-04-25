"""``hermes cc-import`` CLI subcommand handlers.

Slice 1 ships only ``install``. The ``list``, ``remove``, ``update``,
``sync``, and ``sources`` subcommands land in slice 3 alongside
``sources.yaml`` and the auto-sync hook.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

# Try-relative-then-absolute import — see ``__init__.py`` for the rationale.
try:
    from . import converter
except ImportError:
    import converter  # type: ignore[no-redef]


def setup_parser(parser: argparse.ArgumentParser) -> None:
    """Wire the ``install`` subcommand onto the ``hermes cc-import`` parser."""
    sub = parser.add_subparsers(dest="cc_import_subcommand", required=True)

    install_p = sub.add_parser(
        "install",
        help="Import a Claude Code plugin (skills + agents) from a git URL.",
        description=(
            "Clone the plugin repo, copy its skills into "
            "$HERMES_HOME/skills/<plugin>/, and translate each Claude Code "
            "agent into a Hermes delegation skill."
        ),
    )
    install_p.add_argument(
        "git_url",
        help="Git URL of the Claude Code plugin repo (HTTPS, SSH, or file://).",
    )
    install_p.add_argument(
        "--branch",
        default="main",
        help="Branch to clone (default: main).",
    )
    install_p.add_argument(
        "--subdir",
        default="",
        help=(
            "Subdirectory within the repo where the plugin lives "
            "(e.g. 'plugins/compound-engineering'). Empty for repo-root layouts."
        ),
    )
    install_p.set_defaults(func=cmd_install)


def cmd_install(args: argparse.Namespace) -> int:
    """Execute ``hermes cc-import install <git-url>``.

    Returns 0 on success, non-zero on any error (clone failure, parse
    failure, unexpected exception). Errors are printed to stderr; success
    output goes to stdout.
    """
    try:
        summary = converter.import_plugin(args.git_url, branch=args.branch, subdir=args.subdir)
    except subprocess.CalledProcessError as exc:
        print(
            f"Error: failed to clone {args.git_url} (git exit {exc.returncode}).",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"Error importing {args.git_url}: {exc}", file=sys.stderr)
        return 1

    parts = [
        f"Imported {summary.plugin}:",
        f"{summary.skills_imported} skills imported",
        f"{summary.agents_translated} agents translated",
    ]
    if summary.skills_unchanged or summary.agents_unchanged:
        parts.append(f"({summary.skills_unchanged} + {summary.agents_unchanged} unchanged)")
    print(" ".join(parts))

    if summary.skipped_user_modified:
        print("\nUser-modified files preserved (not overwritten):")
        for key in summary.skipped_user_modified:
            print(f"  - {key}")

    return 0
