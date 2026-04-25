# cc-import

Import [Claude Code](https://claude.com/claude-code) plugins (skills + agents) into [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Translates Claude Code sub-agent personas into Hermes delegation skills, and copies skill bundles into Hermes's skills tree. User edits to translated skills survive re-imports.

## Installation

Install into a local Hermes:

```sh
hermes plugins install roach88/cc-import
hermes plugins enable cc-import
```

## Usage

cc-import exposes a `/cc-import` slash command, available in `hermes chat` and gateway sessions:

```
/cc-import install <git-url> [--branch BRANCH] [--subdir SUBDIR]
```

Example — import EveryInc's compound-engineering plugin:

```
/cc-import install https://github.com/EveryInc/compound-engineering-plugin.git
```

Output:

```
Imported compound-engineering: 36 skills imported, 48 agents translated
```

The skills appear in `hermes skills list` immediately. Restart the gateway (`hermes gateway restart`) for them to take effect in active chat sessions.

> **Note on top-level CLI:** Hermes's `register_cli_command` API is documented as wiring `hermes <plugin>` subcommands at startup, but the consuming code that would iterate plugin registrations into the top-level argparse is not yet present in `hermes_cli/main.py`. Until that gap is closed upstream, cc-import uses the slash-command surface, which is fully wired.

## How it works

Each upstream plugin produces two kinds of output in `$HERMES_HOME/skills/<plugin>/`:

| Source | Becomes |
|---|---|
| `skills/<name>/SKILL.md` and siblings | Copied verbatim to `$HERMES_HOME/skills/<plugin>/<name>/` |
| `agents/<name>.md` (Claude Code sub-agent) | Translated to a Hermes delegation skill at `$HERMES_HOME/skills/<plugin>/agents/<name>/SKILL.md` |

Agent translation rewrites the frontmatter (new `name`, `description`, `metadata.hermes.toolsets`) and wraps the original persona under a `## Persona` heading preceded by instructions on how Hermes should invoke `delegate_task`. Tool-name mapping:

| Claude Code tool | Hermes toolset |
|---|---|
| `Read`, `Grep`, `Glob`, `Edit`, `Write`, `NotebookEdit` | `file` |
| `Bash` | `terminal` |
| `WebFetch`, `WebSearch` | `web` |
| `Task` | (dropped — Hermes sub-agents cannot delegate further) |

## State

State lives at `$HERMES_HOME/plugins/cc-import/`:

```
state.json     SHA-256 manifest keyed by relative skill path
clones/        git-clone cache, one subdir per imported repo
```

Re-running `install` for the same URL is idempotent: unchanged files are skipped, upstream-updated files are refreshed, and locally edited files are preserved with a warning. The `state.json` manifest tracks which files came from which upstream plugin so the conversion can stay incremental.

## Roadmap

- **Slice 1** *(this release)* — `hermes cc-import install` CLI subcommand
- **Slice 2** — Agent-callable tools (`cc_import_install`, `cc_import_list`, `cc_import_remove`) so Hermes itself can install plugins on its own initiative
- **Slice 3** — `on_session_start` hook + `sources.yaml` for opportunistic auto-sync of tracked plugins

## Contributing

```sh
git clone https://github.com/roach88/cc-import.git
cd cc-import
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install pyyaml pytest pytest-mock ruff ty

pytest        # 92 tests
ruff check .
ty check .
```

Tests use real local bare git repos as fixtures (no subprocess mocking), so `git` must be on `$PATH`.

## License

[MIT](LICENSE).
