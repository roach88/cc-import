"""cc-import — Hermes plugin entry point.

Wires the ``/cc-import`` slash command and the agent-callable tools
``cc_import_install`` / ``cc_import_list`` / ``cc_import_remove`` via
:meth:`PluginContext.register_tool`. Slice 3 will add an
``on_session_start`` hook that opportunistically re-syncs configured
sources.

Why a slash command instead of a top-level CLI subcommand: Hermes's
``register_cli_command`` stores registrations but does not yet wire them
into ``hermes <plugin>`` argparse — only memory-plugin CLI registrations
are consumed. ``register_command`` (slash commands) IS consumed and
works in interactive sessions and gateway adapters.
"""

from __future__ import annotations

# Three-tier import for ``cli`` and ``tools``:
#
# 1. Hermes loads this plugin as a namespace-package member (sets
#    ``module.__package__`` and ``module.__path__`` before exec), so the
#    relative import succeeds in production.
# 2. Tests load ``__init__.py`` via ``importlib.util`` after inserting
#    the repo root into ``sys.path``, so the absolute fallback finds
#    ``cli.py`` / ``tools.py``.
# 3. Pytest's :class:`_pytest.python.Package` setup eagerly imports any
#    ``__init__.py`` it finds at the rootpath without package context
#    AND without inserting the repo root into ``sys.path``. Both prior
#    fallbacks fail; the modules are set to ``None`` so load completes
#    cleanly. ``register()`` is never invoked in that context, so the
#    ``None`` values are harmless.
try:
    from . import cli, tools
except ImportError:
    try:
        import cli  # type: ignore[no-redef]
        import tools  # type: ignore[no-redef]
    except ModuleNotFoundError:
        cli = None  # type: ignore[assignment]
        tools = None  # type: ignore[assignment]


def register(ctx) -> None:
    """Register cc-import's slash command and agent tools with Hermes."""
    # Defense-in-depth: the third-tier import fallback (cli=None, tools=None)
    # exists for pytest's eager Package.setup() and should never fire in
    # production. If it ever does — say, a future commit adds a sub-dependency
    # to cli.py or tools.py that's missing from the Hermes runtime — fail
    # loudly with the actual cause instead of a cryptic AttributeError later.
    if cli is None or tools is None:
        raise RuntimeError(
            "cc-import failed to load its cli/tools modules — likely a missing "
            "Python dependency in the Hermes environment. Check the Hermes "
            "logs around plugin discovery for the underlying ImportError."
        )
    ctx.register_command(
        name="cc-import",
        handler=cli.handle_command,
        description=(
            "Import Claude Code plugins (skills + agents) into Hermes. "
            "Subcommands: install <git-url> [--branch BRANCH] [--subdir SUBDIR], "
            "list [--json], remove <plugin> [--force] [--dry-run]."
        ),
        args_hint="install <git-url> | list | remove <plugin>",
    )
    for name, schema, handler, emoji in tools.TOOLS:
        ctx.register_tool(
            name=name,
            toolset="cc_import",
            schema=schema,
            handler=handler,
            description=schema["description"],
            emoji=emoji,
        )
