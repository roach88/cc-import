"""cc-import — Hermes plugin entry point.

Slice 1 wires only the ``hermes cc-import`` CLI subcommand. Slice 2 will
add agent-callable tools (``cc_import_install``, ``cc_import_list``,
``cc_import_remove``) via :meth:`PluginContext.register_tool`. Slice 3
will add an ``on_session_start`` hook that opportunistically re-syncs
configured sources.
"""

from __future__ import annotations

# Absolute imports rather than ``from . import cli``: at the repo-root layout
# Hermes uses for plugins, the plugin's directory is added to sys.path by
# the loader, and absolute imports work in both that context and pytest's
# default rootdir handling. Relative imports would require pytest to treat
# the repo root as a package, which conflicts with rootdir + pyproject.toml.
import cli


def register(ctx) -> None:
    """Register cc-import's CLI subcommand with the Hermes plugin loader."""
    ctx.register_cli_command(
        name="cc-import",
        help="Import Claude Code plugins (skills + agents) into Hermes.",
        setup_fn=cli.setup_parser,
        description=(
            "Manage Claude Code plugin imports. Translates Claude Code agent "
            "personas into Hermes delegation skills and copies skill bundles "
            "into the user's skills tree."
        ),
    )
