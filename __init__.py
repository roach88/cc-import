"""cc-import — Hermes plugin entry point.

Slice 1 wires only the ``/cc-import`` slash command. Slice 2 will add
agent-callable tools (``cc_import_install``, ``cc_import_list``,
``cc_import_remove``) via :meth:`PluginContext.register_tool`. Slice 3
will add an ``on_session_start`` hook that opportunistically re-syncs
configured sources.

Why a slash command instead of a top-level CLI subcommand: Hermes's
``register_cli_command`` stores registrations but does not yet wire them
into ``hermes <plugin>`` argparse — only memory-plugin CLI registrations
are consumed. ``register_command`` (slash commands) IS consumed and
works in interactive sessions and gateway adapters.
"""

from __future__ import annotations

# Hermes loads this plugin as a namespace-package member (sets
# ``module.__package__`` and ``module.__path__`` before exec), so the
# relative import succeeds in production. Pytest, by contrast, imports
# ``__init__.py`` at the repo root without any package context, so the
# relative form raises ImportError; the absolute fallback then finds
# ``cli.py`` via the repo root on ``sys.path``.
try:
    from . import cli
except ImportError:
    import cli  # type: ignore[no-redef]


def register(ctx) -> None:
    """Register cc-import's slash command with the Hermes plugin loader."""
    ctx.register_command(
        name="cc-import",
        handler=cli.handle_command,
        description=(
            "Import Claude Code plugins (skills + agents) into Hermes. "
            "Subcommand: install <git-url> [--branch BRANCH] [--subdir SUBDIR]."
        ),
        args_hint="install <git-url>",
    )
