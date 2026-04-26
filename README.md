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

cc-import exposes a `/cc-import` slash command and three agent-callable tools.

### Slash command

Available in `hermes chat` and gateway sessions:

```
/cc-import install <git-url> [--branch BRANCH] [--subdir SUBDIR]
/cc-import list [--json]
/cc-import remove <plugin> [--force] [--dry-run]
```

Example — import EveryInc's compound-engineering plugin:

```
/cc-import install https://github.com/EveryInc/compound-engineering-plugin.git
```

Output:

```
Imported compound-engineering: 36 skills imported, 48 agents translated
```

`/cc-import list` shows installed plugins; `--json` emits a parseable array. `/cc-import remove <plugin>` deletes a plugin's skills, agents, and clone cache. Hand-edited skill files are preserved by default — pass `--force` to delete them too. `--force` is slash-command-only; the agent tool surface deliberately doesn't expose it.

The skills appear in `hermes skills list` immediately, but **imported skills are not callable until you restart Hermes** (the gateway's skill index is loaded once per session). Run `hermes gateway restart` after install/remove to pick up changes.

### Agent tools

Three tools registered under toolset `cc_import`, callable via Hermes's tool dispatch:

- **`cc_import_install`** — clone and import a plugin from a git URL. Args: `git_url` (required, HTTPS, allowlisted host), `branch` (default `main`), `subdir` (default empty).
- **`cc_import_list`** — return a JSON array of installed plugins with skill/agent counts and source URL.
- **`cc_import_remove`** — remove a plugin. Args: `plugin` (required), `dry_run` (default false). User-modified files are always preserved on the tool surface; use the slash command's `--force` to override.

Install and remove tool responses include `available_now: false` and `available_after: "next_session"` typed flags. The tool descriptions also carry an explicit `IMPORTANT:` line warning the calling LLM that imported skills aren't callable in the current session — agents should report success and tell the user to restart Hermes, not retry.

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

## Security

Slice 2's agent-callable surface introduces a new trust model: an LLM, not a human, picks the URL passed to `cc_import_install`. cc-import hardens the install path accordingly:

- **URL allowlist.** `cc_import_install` rejects URLs whose host isn't on a small allowlist (`github.com`, `gitlab.com`, `bitbucket.org`, `codeberg.org`). HTTPS only — `file://`, `git://`, `ssh://`, plain `http://` are refused. The slash command bypasses this check (human-typed).
- **Hook suppression.** Every `git clone` runs with `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `--config core.hooksPath=/dev/null`, and `--no-recurse-submodules`. This eliminates `post-checkout` hook execution and CVE-2017-1000117-class submodule recursion attacks.
- **Path-traversal validation.** `plugin_name` (read from the cloned repo's `plugin.json`) and `subdir` (caller-supplied) are validated before any filesystem write. Names containing `..`, slashes, or absolute paths are rejected; subdirs must resolve to a child of the clone root.
- **`--force` is slash-only.** The agent tool surface deliberately doesn't expose `force` on `cc_import_remove`. A prompt-injected agent cannot force-delete user-modified files; that requires a human typing `/cc-import remove <plugin> --force`.

If you're installing from a host that isn't allowlisted, use the slash command — the human-in-the-loop UX for that case is intentional. A user-configurable allowlist override is planned for slice 3.

## State

State lives at `$HERMES_HOME/plugins/cc-import/`:

```
state.json     SHA-256 manifest keyed by relative skill path
clones/        git-clone cache, one subdir per imported repo
```

The manifest also includes a top-level `_plugins` install-cache index recording each imported plugin's source URL, branch, subdir, and import timestamp. Slice 3's `sources.yaml` will become the canonical user-intent store; until then `_plugins` is the derived view of what's installed.

Re-running `install` for the same URL is idempotent: unchanged files are skipped, upstream-updated files are refreshed, and locally edited files are preserved with a warning. The `state.json` manifest is written via atomic `os.replace` to avoid torn writes under concurrent invocations (single-threaded use is the documented assumption).

## Roadmap

- **Slice 1** *(0.1.0)* — `/cc-import install` slash command
- **Slice 2** *(0.2.0, this release)* — Agent-callable tools (`cc_import_install`, `cc_import_list`, `cc_import_remove`) + symmetric `/cc-import list` and `/cc-import remove` slash subcommands + URL allowlist + git-hook suppression + traversal validation
- **Slice 3** — `sources.yaml` declarative source list + `/cc-import sync` + `on_session_start` opportunistic auto-sync hook

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
