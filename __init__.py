"""cc-import — Hermes plugin entry point.

Slice 1 wires only the ``hermes cc-import`` CLI subcommand. Slice 2 will
add agent-callable tools (``cc_import_install``, ``cc_import_list``,
``cc_import_remove``) via :meth:`PluginContext.register_tool`. Slice 3
will add an ``on_session_start`` hook that opportunistically re-syncs
configured sources.
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
