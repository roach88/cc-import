"""Agent-callable tool handlers for ``cc_import_install`` /
``cc_import_list`` / ``cc_import_remove``.

Registered with Hermes's tool registry from :mod:`__init__` via the
:data:`TOOLS` tuple. Each handler is a thin formatting wrapper around
the pure logic in :mod:`converter` (install) and :mod:`state`
(list, remove).

**Security posture:** unlike the slash command, ``cc_import_install``
applies the URL allowlist (``converter._validate_url``) before invoking
``converter.import_plugin``. ``cc_import_remove`` does not expose a
``force`` parameter — destructive override is slash-only per the
OpenAI Safe URL pattern.

**Deferred-state contract (R8):** install/remove responses include
``available_now: false`` and ``available_after: "next_session"`` typed
fields, plus a human-readable ``notice``. The schemas' descriptions
also carry an ``IMPORTANT:`` line so the calling LLM sees the constraint
at planning time, not just after the call.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict
from typing import Any

# Three-tier import for converter / state, mirroring slice-1's pattern.
try:
    from . import converter, state
except ImportError:
    try:
        import converter  # type: ignore[no-redef]
        import state  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover - pytest eager-import path
        converter = None  # type: ignore[assignment]
        state = None  # type: ignore[assignment]

# Hermes provides ``tool_result`` and ``tool_error`` JSON-wrapping helpers
# at ``tools.registry``. In the standalone repo (and under pytest), that
# path is absent — fall back to local one-liners with the same shape so
# tests cover handler logic via the fallback. Manual smoke (Unit 5)
# verifies the production helper resolves correctly.
try:
    from tools.registry import tool_error, tool_result  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - production-import shape covered by smoke

    def tool_result(payload: dict[str, Any]) -> str:
        return json.dumps(payload)

    def tool_error(code: str, message: str = "", **extra: Any) -> str:
        body: dict[str, Any] = {"error": code}
        if message:
            body["message"] = message
        body.update(extra)
        return json.dumps(body)


_PATH_RE = re.compile(r"/[A-Za-z][\w./-]*")


def _redact_paths(text: str) -> str:
    """Replace absolute path-like substrings with ``<path>``.

    Exception messages routinely embed filesystem paths
    (``FileNotFoundError: ...: '/Users/.../state.json'``); leaking those
    in ``tool_error`` lets a prompt-injected agent map the local layout.
    """
    return _PATH_RE.sub("<path>", text or "")


_NEXT_SESSION_NOTICE = (
    "Imported skills are not callable in this session. "
    "Stop after success and tell the user to restart Hermes."
)
_REMOVE_NOTICE = (
    "Removal takes effect on the next Hermes session. "
    "Stop after success and tell the user to restart Hermes."
)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _handle_install(args: dict[str, Any], **_kwargs: Any) -> str:
    """``cc_import_install`` — clone + import a CC plugin (R1, R3, R8, R10)."""
    try:
        git_url_raw = args.get("git_url")
        if not isinstance(git_url_raw, str) or not git_url_raw.strip():
            return tool_error("missing_arg", "git_url is required")
        git_url = git_url_raw.strip()
        branch = str(args.get("branch") or "main")
        subdir = str(args.get("subdir") or "")

        # R10: agent-callable surface enforces the URL allowlist before
        # any clone happens. Slash command bypasses (human-typed).
        try:
            converter._validate_url(git_url)
        except ValueError as exc:
            msg = str(exc).lower()
            code = "disallowed_host" if "host" in msg or "allowlist" in msg else "invalid_arg"
            return tool_error(code, str(exc))

        try:
            summary = converter.import_plugin(git_url, branch=branch, subdir=subdir)
        except subprocess.CalledProcessError as exc:
            return tool_error(
                "clone_failed",
                _redact_paths(f"git clone failed (exit {exc.returncode}) for {git_url}"),
            )
        except ValueError as exc:
            # Validators inside import_plugin raise ValueError for
            # plugin_name / subdir traversal.
            return tool_error("invalid_arg", _redact_paths(str(exc)))

        payload: dict[str, Any] = {
            **asdict(summary),
            "available_now": False,
            "available_after": "next_session",
            "notice": _NEXT_SESSION_NOTICE,
        }
        return tool_result(payload)
    except Exception as exc:
        return tool_error("internal_error", _redact_paths(f"{type(exc).__name__}: {exc}"))


def _handle_list(args: dict[str, Any], **_kwargs: Any) -> str:
    """``cc_import_list`` — JSON view of installed plugins (R1, R4)."""
    try:
        entries = state.list_imports()
        return tool_result({"plugins": [asdict(e) for e in entries]})
    except Exception as exc:
        return tool_error("internal_error", _redact_paths(f"{type(exc).__name__}: {exc}"))


def _handle_remove(args: dict[str, Any], **_kwargs: Any) -> str:
    """``cc_import_remove`` — delete a plugin (R1, R5, R8). No ``force`` field."""
    try:
        plugin_raw = args.get("plugin")
        if not isinstance(plugin_raw, str) or not plugin_raw.strip():
            return tool_error("missing_arg", "plugin is required")
        plugin = plugin_raw.strip()

        # R5: tool surface never accepts force. If an agent passes it, surface
        # an explicit error so the agent's planner learns the constraint
        # rather than silently dropping the flag.
        if "force" in args:
            return tool_error(
                "invalid_arg",
                "force is not supported via the agent tool surface; "
                "use '/cc-import remove <plugin> --force' instead",
            )

        dry_run = bool(args.get("dry_run", False))

        result = state.remove_import(plugin, force=False, dry_run=dry_run)
        payload: dict[str, Any] = {
            **asdict(result),
            "available_now": False,
            "available_after": "next_session",
            "notice": _REMOVE_NOTICE,
        }
        return tool_result(payload)
    except Exception as exc:
        return tool_error("internal_error", _redact_paths(f"{type(exc).__name__}: {exc}"))


# ---------------------------------------------------------------------------
# Schemas (inline OpenAI-style function-call dicts)
# ---------------------------------------------------------------------------


_INSTALL_SCHEMA: dict[str, Any] = {
    "name": "cc_import_install",
    "description": (
        "Import a Claude Code plugin (skills + agents) from a git URL into the "
        "local Hermes install. URL must be on the allowlist (github.com, "
        "gitlab.com, bitbucket.org, codeberg.org). "
        "IMPORTANT: imported skills are NOT callable in the current session. "
        "Report success and tell the user to restart Hermes; do not retry."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "git_url": {
                "type": "string",
                "description": "HTTPS git URL of the Claude Code plugin repo.",
            },
            "branch": {
                "type": "string",
                "description": "Branch to clone.",
                "default": "main",
            },
            "subdir": {
                "type": "string",
                "description": (
                    "Subdirectory inside the repo containing skills/ and "
                    "agents/. Must resolve to a child of the clone root."
                ),
                "default": "",
            },
        },
        "required": ["git_url"],
    },
}


_LIST_SCHEMA: dict[str, Any] = {
    "name": "cc_import_list",
    "description": (
        "List installed Claude Code plugins with skill and agent counts. "
        "Returns one entry per plugin with its source URL, branch, and "
        "import timestamp when available."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


_REMOVE_SCHEMA: dict[str, Any] = {
    "name": "cc_import_remove",
    "description": (
        "Remove an installed Claude Code plugin (skills + agents + clone "
        "cache). User-edited files are always preserved (use the slash "
        "command if force-removal is needed). "
        "IMPORTANT: removal takes effect on the next Hermes session. "
        "Stop after success and tell the user to restart Hermes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plugin": {
                "type": "string",
                "description": "Plugin name as shown by cc_import_list.",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Report what would happen without writing.",
                "default": False,
            },
        },
        "required": ["plugin"],
    },
}


# Tuple consumed by ``__init__.register(ctx)``. Order is install / list /
# remove so the agent's tool list reads in the natural verb order.
TOOLS: tuple[tuple[str, dict[str, Any], Any, str], ...] = (
    ("cc_import_install", _INSTALL_SCHEMA, _handle_install, "📦"),
    ("cc_import_list", _LIST_SCHEMA, _handle_list, "📋"),
    ("cc_import_remove", _REMOVE_SCHEMA, _handle_remove, "🗑️"),
)
