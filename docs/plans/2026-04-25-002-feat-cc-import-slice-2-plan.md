---
title: "feat: cc-import Hermes plugin — slice 2 (agent tools + symmetric slash subcommands)"
type: feat
status: active
date: 2026-04-25
deepened: 2026-04-25
origin: docs/plans/2026-04-25-001-feat-cc-import-slice-1-plan.md
---

# feat: cc-import Hermes plugin — slice 2 (agent tools + symmetric slash subcommands)

## Overview

Slice 2 of three for `cc-import`. Slice 1 shipped the `/cc-import install
<git-url>` slash command and a battle-tested converter that imported 36
skills + 48 agents from EveryInc/compound-engineering on the first run.
Slice 2 makes the plugin agent-callable by exposing three tools to
Hermes's tool registry — `cc_import_install`, `cc_import_list`,
`cc_import_remove` — and by symmetrizing the slash surface so a human
user can also run `/cc-import list` and `/cc-import remove`.

The work is shaped by three deliberate choices:

1. **Symmetric scope** (Q's choice). Anything an agent can do via
   `register_tool`, a human can do via the slash command, with shared
   pure-logic core in a new `state.py`. Industry consensus on
   agent-friendly CLI design (per 2026 Medium/Reinhard/Firecrawl
   coverage) is "symmetric logic + surface specialization": same core,
   tailored output formats. CLI `--json` is *complementary* to agent
   tools, not a duplicate (CLI+JSON is ~33% more token-efficient than
   equivalent MCP calls per Reinhard benchmarks because agents can
   `--query` selectively). So the slash surface keeps `--json` on
   `list`.

2. **Defense in depth against prompt-injected installs**. Slice 1's
   install was human-typed; slice 2's is agent-callable, which opens a
   git-clone-RCE surface (a malicious URL passed by a prompt-injected
   agent can trigger `post-checkout` hooks, submodule recursion, or
   `core.fsmonitor` payloads — see CVE-2017-1000117). Slice 2 ships
   four mitigations recommended by Trail of Bits' MCP hardening guide,
   Microsoft's CVE-2017-1000117 writeup, and OpenAI's "Safe URL"
   pattern: URL allowlist, git-hook suppression at clone time,
   `plugin_name` + `subdir` traversal validation, and removal of
   `--force` from the tool schema (force stays on the slash surface
   for human-in-the-loop control).

3. **Typed-flag deferred-state contract**. Hermes does not reload
   skills mid-session, so an installed skill is not callable until
   the next session. Plan responses surface this with both a typed
   flag (`available_now: false`, `available_after: "next_session"`)
   and a tool-description warning visible to the LLM at planning
   time. Industry research (dev.to "agent looping" patterns) shows
   string-only notices are loop-prone; the typed-flag upgrade is a
   ~5-line cost for real robustness.

The manifest at `$HERMES_HOME/plugins/cc-import/state.json` gains an
optional top-level `_plugins` index reframed as the *install cache*:
when slice 3 ships `sources.yaml`, that file becomes the canonical
user-intent store and `_plugins` becomes a derived cache of what's
currently on disk. Backward-compat with v1 manifests is preserved.

## Problem Frame

Slice 1's `/cc-import install` works for humans in `hermes chat` and
gateway adapters, but a Hermes agent itself cannot import plugins,
inspect what's already installed, or remove them. The promised contract
in the README is *"Slice 2 — Agent-callable tools so Hermes itself can
install plugins on its own initiative"* — a single-surface restriction
that breaks down the moment an agent is asked to do plugin maintenance.

Adding the tool surface alone would create a worse asymmetry: agents
could `list` and `remove`, humans could not. So slice 2 closes both gaps
in one slice — three `register_tool` calls plus two new slash subcommands
that share their core with the tools.

A subtle correctness requirement falls out of slice 1: the user-modified
preservation matrix (origin_hash, prior_origin_hash, local_hash) must
extend to `remove`. Deleting a skill that the user has hand-edited would
silently lose work. The matrix is already implemented for `prune_removed`;
slice 2 reuses the same logic in `remove_import`.

A new requirement enters with slice 2: agent-callable git clone is a
qualitatively different trust model than human-typed git clone. The
former can be driven by prompt injection from any text the agent has
ingested. Slice 2 hardens the install path accordingly.

## Requirements Trace

- R1. `cc_import_install`, `cc_import_list`, `cc_import_remove` are
  registered with Hermes's tool registry under toolset `cc_import` and
  callable by an agent via `dispatch_tool`
- R2. `/cc-import list` and `/cc-import remove` are dispatched by the
  existing slash-command router in `cli.handle_command` and produce
  human-readable output. `/cc-import list` accepts `--json` for
  agent-friendly machine-readable output (per 2026 CLI-for-agents
  consensus: CLI `--json` complements rather than competes with
  agent tools)
- R3. `cc_import_install` calls the same `converter.import_plugin()` as
  the slash command and returns a structured success summary or
  structured error via `tool_result` / `tool_error`
- R4. `cc_import_list` returns one entry per installed plugin with
  skill count, agent count, and source URL/branch when available; both
  surfaces share a `state.list_imports()` core
- R5. `cc_import_remove` deletes `$HERMES_HOME/skills/<plugin>/`, the
  matching clone cache directory, and all manifest entries scoped to
  that plugin. User-modified files are preserved by default. The tool
  schema does **not** expose `force` (slash-command-only, for
  human-in-the-loop control). `--dry-run` is available on both surfaces
- R6. The state.json manifest gains an optional top-level `_plugins`
  index recording `{url, branch, subdir, imported_at}` per plugin. This
  index is the *install cache* — a derived view of what's currently on
  disk. When slice 3 ships `sources.yaml`, that file becomes the
  canonical user-intent store; `_plugins` remains the install cache.
  Reading a v1 manifest (no index) succeeds; on next save the index is
  populated from the install call's parameters
- R7. Tool handlers defensively validate their `args` dict (Hermes's
  registry does no schema validation before dispatch, verified at
  `~/.hermes/hermes-agent/tools/registry.py:292-309` — the registry
  passes the raw `function_args` dict straight through) and convert
  all exceptions to `tool_error`. Exception messages are
  path-redacted before return to avoid leaking filesystem layout
- R8. Install and remove tool responses include a typed deferred-state
  contract: `available_now: false` and
  `available_after: "next_session"` fields, plus a human-readable
  `notice` string. Tool descriptions register an explicit
  `IMPORTANT:` line warning the calling LLM at planning time that
  imported skills are not callable in the current session
- R9. `plugin.yaml` declares `provides_tools: [cc_import_install,
  cc_import_list, cc_import_remove]`. This field is parsed into
  `PluginManifest` but not consumed by Hermes at runtime (verified by
  reading `hermes_cli/plugins.py:797` and `hermes_cli/plugins_cmd.py
  cmd_list` — neither reads `provides_tools`); declaration exists for
  static documentation parity with bundled plugins like spotify, and a
  drift-guard test keeps it in sync with `tools.TOOLS`
- R10. **Security: agent-callable install hardening.**
  `cc_import_install` validates `git_url` against an allowlist of
  trusted hosts (default: github.com, gitlab.com, bitbucket.org,
  codeberg.org), rejects `file://`, `git://`, and bare-IP URLs.
  `git clone` runs with `GIT_CONFIG_NOSYSTEM=1`,
  `GIT_CONFIG_GLOBAL=/dev/null`, `--config core.hooksPath=/dev/null`,
  and `--no-recurse-submodules` to suppress hook execution and
  recursive-clone vectors per CVE-2017-1000117 mitigation guidance
- R11. **Security: traversal validation.** `plugin_name` (from
  `plugin.json` or git URL basename) is validated to match
  `^[A-Za-z0-9._-]+$`; values containing `..`, `/`, `\`, or absolute
  paths are rejected. `subdir` is resolved against the clone root and
  asserted to be a child via `Path.resolve().is_relative_to(clone_root.resolve())`
- R12. **Slice 1 invariants preserved unchanged:** R6 ($HERMES_HOME-derived
  paths, no hardcoded user/host/project values), R7 (plugin layout
  mirrors `plugins/disk-cleanup/` for upstream-PR readiness), R8 (no
  core Hermes file modified — Teknium's "no core mods" rule, AGENTS.md
  line 478) all carry forward verbatim from slice 1's plan

## Scope Boundaries

- No `update` / `sync` / `sources` subcommands or tools — those depend
  on `sources.yaml` which is slice 3's territory
- No `on_session_start` hook — slice 3
- No tool deregistration on plugin disable — Hermes's `PluginManager`
  doesn't call `registry.deregister`, and slice 2 should not paper over
  that gap
- No top-level `hermes cc-import list` CLI subcommand —
  `register_cli_command` remains unwired in `hermes_cli/main.py` (see
  slice 1's README note); slash + tool surfaces are sufficient
- No live skill-scanner reload after install/remove — surfaced via
  R8's typed deferred-state contract
- No support for multiple Hermes plugin layouts beyond `skills/` +
  `agents/` (still no `commands/`, `hooks/`, `.mcp.json`)
- No multi-threaded / concurrent install support. Slice 2 documents
  the single-threaded assumption; concurrent invocations from a single
  Hermes session may lose-update `state.json` (see Risks). Atomic-rename
  save mitigates the worst case but does not serialize concurrent calls
- `--force` on `cc_import_remove` tool surface — explicitly out of
  scope (R5, R10). `--force` remains on the slash command

### Deferred to Separate Tasks

- `update <plugin>` and `sync` (re-fetch all tracked sources): slice 3
- `sources.yaml` declarative source list + `/cc-import sources` /
  `cc_import_sources_*` tools: slice 3 (becomes canonical user-intent
  store; `_plugins` index reframes as derived cache at that point)
- `on_session_start` hook for opportunistic auto-sync: slice 3
- User-configurable URL allowlist (slice 2 hardcodes the default; a
  config file like `cc-import.toml` would override): slice 3
- RFC issue or PR proposing cc-import as a bundled Hermes plugin:
  separate workstream after slice 3
- Skill-scanner live-reload integration with Hermes core: out of scope
  permanently; would violate the no-core-mods rule

## Context & Research

### Relevant Code and Patterns

- `~/.hermes/hermes-agent/plugins/spotify/__init__.py:45-66` — the
  canonical `register_tool` registration loop. Iterates a tuple of
  `(name, schema, handler, emoji)` and calls `ctx.register_tool` for
  each. Mirror this exactly in cc-import's `__init__.py`.
- `~/.hermes/hermes-agent/plugins/spotify/tools.py` — handler
  structure. Each handler is a top-level function with signature
  `(args: dict, **kwargs) -> str`. Defensive arg parsing
  (`str(args.get("query") or "").strip()`). Returns `tool_result(...)`
  on success, `tool_error(...)` on validation or backend failure.
  Schemas are inline OpenAI-style function-call dicts. Verified import
  pattern: `from tools.registry import tool_error, tool_result`
  (NOT `hermes_agent.tools.registry`).
- `~/.hermes/hermes-agent/plugins/spotify/plugin.yaml:6-13` — example
  of `provides_tools:` block listing all tools.
- `~/.hermes/hermes-agent/plugins/disk-cleanup/__init__.py:230-274` —
  pattern for a slash command that dispatches subcommands and formats
  state-introspection output as text. Slice 1's `cli.handle_command`
  already follows the dispatch part; slice 2 adds two more
  subcommands.
- `~/.hermes/hermes-agent/hermes_cli/plugins.py:205-232` —
  `register_tool(name, toolset, schema, handler, check_fn=None,
  requires_env=None, is_async=False, description="", emoji="")`
  signature. Slice 2 uses positional + keyword form matching spotify.
- `~/.hermes/hermes-agent/tools/registry.py:456-482` —
  `tool_result(...)` and `tool_error(...)` JSON-wrapping helpers.
  Both return strings. Use unconditionally.
- `~/.hermes/hermes-agent/tools/registry.py:292-309` —
  `registry.dispatch` catches handler exceptions and returns
  `{"error": "Tool execution failed: <Type>: <msg>"}`. So raised
  exceptions lose structure; convert to `tool_error` explicitly.
- `~/.hermes/hermes-agent/tests/hermes_cli/test_plugins.py:545-572` —
  test pattern for `register_tool`: write a fake plugin into
  `tmp_path`, set `HERMES_HOME` via `monkeypatch`, load via
  `PluginManager().discover_and_load()`, assert against
  `tools.registry.registry._tools`. Slice 2 tests won't go through the
  full plugin loader — direct mock-`ctx` assertions match slice 1's
  pattern in `tests/test_cli.py` and stay simpler.
- `tests/conftest.py` (slice 1) — `_isolate_env` fixture is reused
  unchanged. Manifest fixtures for `state.py` tests build on it.
- `converter.py` (slice 1, this repo) —
  `load_manifest`/`save_manifest`/`sha256_file`/`sha256_bytes` are
  imported by `state.py`. `_resolve_hermes_home` and `_repo_basename`
  are slice-1 helpers re-exported for `state.py`'s use.
  `import_plugin` orchestrator is extended (Unit 1) to populate the
  new `_plugins` index, validate `plugin_name` per R11, and run
  `clone_or_update` with the hardened git env per R10.

### Institutional Learnings

- *Slice 1 plan, R6 + decision "All paths derive from `$HERMES_HOME`"* —
  carried forward verbatim. Slice 2's tool schemas and output strings
  must not bake `~/.hermes` literally.
- *Slice 1 plan, "user-modified detection decision matrix"* (origin
  doc lines 475-487) — the same three-hash logic governs `remove`.
  Files where `local_hash != entry["origin_hash"]` are kept by
  default; `--force` (slash-command-only) overrides.
- *Slice 1 plan, "Conversion logic ported verbatim from
  plugin_sync.py"* — slice 2 does not touch the conversion algorithm.
  All new logic is state-introspection, removal, and security
  hardening.
- *Q's `~/.claude/rules/no-hardcoding.md`* — applies to tool schemas
  and the URL allowlist. The allowlist hosts (github.com, gitlab.com,
  etc.) are protocol-stable identifiers, not user/host/project values,
  so they're legitimate hardcodes per the rule's "stable protocol
  constants" carve-out. A future config-file override (slice 3) would
  parameterize them.
- *Q's `~/.claude/rules/python-tooling.md`* — Astral toolchain stays.
  No new dependencies expected; existing `pyyaml` covers all parsing.

### External References

- **Trail of Bits, MCP security analysis** (https://trailofbits.com/mcp/)
  — URL allowlist + hook suppression as canonical hardening pattern
  for MCP servers and similar agent-callable surfaces
- **Microsoft DevBlogs, CVE-2017-1000117 writeup**
  (https://devblogs.microsoft.com/devops/git-vulnerability-with-submodules/)
  — explicit recommendation of `core.hooksPath=/dev/null`,
  `--no-recurse-submodules`, `GIT_CONFIG_NOSYSTEM` for
  agent-callable git operations
- **OpenAI, "Designing agents to resist prompt injection"**
  (https://openai.com/index/designing-agents-to-resist-prompt-injection/)
  — "Safe URL" pattern; destructive operations require explicit
  confirmation. Slice 2's drop-`--force`-from-tool-schema decision
  matches this pattern
- **2026 CLI-for-agents design pattern** (Reinhard,
  https://jannikreinhard.com/2026/02/22/why-cli-tools-are-beating-mcp-for-ai-agents/;
  Medium dminhk) — symmetric logic + surface specialization is the
  consensus pattern; CLI `--json` is complementary to (not competing
  with) agent tools; benchmarks show 33% token-efficiency advantage
  for CLI+JSON over equivalent MCP calls
- **dev.to "7 patterns that stop your AI agent from going rogue"**
  (https://dev.to/pockit_tools/...) — string-only deferred-state
  notices are loop-prone; typed flags + Budget Governors + explicit
  exit conditions reduce agent-loop failure modes

## Key Technical Decisions

- **New module split: `state.py` (pure logic) + `tools.py` (handler
  wrappers)**. Rationale: `state.py` has no dependency on Hermes core,
  so it's testable in isolation; `tools.py` requires `tools.registry`
  imports that should not infect `__init__.py`. Three-tool spotify
  pattern is precedent but not load-bearing — the testability
  isolation is. `state.py` owns `list_imports()` and `remove_import()`
  as pure functions returning dataclasses; `tools.py` owns the three
  `register_tool` schemas and handler wrappers that adapt those
  dataclasses to JSON. Both modules use slice 1's
  try-relative-then-absolute import idiom.
- **Manifest schema v2 with optional `_plugins` index, reframed as
  install cache**. Rationale: industry-standard additive-evolution
  pattern (creekservice JSON schema evolution guide; Confluent Schema
  Registry; Iceberg). The index stores `{url, branch, subdir,
  imported_at}` per plugin. Slice 3's `sources.yaml` becomes the
  canonical user-intent store; `_plugins` reframes as a derived cache
  of what's installed. This avoids the double-state risk surfaced in
  document review (sources.yaml + `_plugins` overlapping) by giving
  them clear roles. Backward-compat: missing `_plugins` index →
  derive by walking `source_path` upward (anchored to
  `$HERMES_HOME/plugins/cc-import/clones/`, not arbitrary `clones/`
  segments) to find the clone root.
- **Toolset name: `cc_import`**. Rationale: matches the convention
  `register_tool(toolset="cc_import", ...)`. All three tools group
  cleanly in `hermes tools list`.
- **Sync handlers**. Rationale: slice 1's converter is sync (subprocess
  + file I/O); typical clone of a Claude Code plugin (~50 skills) is
  O(seconds) on github.com over residential bandwidth.
  `is_async=False` matches. If long-running clones become an issue
  in practice, flip to `is_async=True` with `asyncio.to_thread(import_plugin, ...)`
  in a follow-up.
- **Defensive arg validation in every handler**. Rationale: the
  Hermes registry does not validate `args` against `parameters`
  before calling the handler (verified at `tools/registry.py:292-309`
  — the registry passes raw `function_args` straight to the handler).
  Each handler validates with explicit type coercion + checks,
  returning `tool_error("missing_arg", ...)` for shape failures.
- **Explicit exception → `tool_error` conversion with path
  redaction**. Rationale: `registry.dispatch` catches handler
  exceptions and JSON-wraps them with a generic message, losing
  structure. Wrap each handler's body in a try/except (`except
  Exception:` only — never bare `except:`; `KeyboardInterrupt` and
  `SystemExit` propagate intentionally) that calls `tool_error` with
  a domain-specific code and a path-redacted message (regex-replace
  absolute path-like substrings with `<path>` to avoid leaking
  filesystem layout via exception text).
- **Security: URL allowlist** (R10). Rationale: Trail of Bits MCP
  guide flags arbitrary-URL git clone as a primary attack surface
  for agent-driven installers. `cc_import_install` rejects URLs whose
  hostname is not on a small allowlist (github.com, gitlab.com,
  bitbucket.org, codeberg.org). Slash command bypasses the allowlist
  (human typed it explicitly). User-configurable override is slice 3.
- **Security: git hook suppression** (R10). Rationale:
  CVE-2017-1000117 mitigation per Microsoft's writeup. `clone_or_update`
  passes `GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null` in env
  and `--config core.hooksPath=/dev/null --no-recurse-submodules` as
  flags. Eliminates `post-checkout`, `post-clone`, `core.fsmonitor`,
  and submodule-recursion vectors at clone time.
- **Security: `plugin_name` + `subdir` validation** (R11). Rationale:
  even with allowlisted URLs, a malicious `plugin.json` could specify
  `name: "../core"` to write outside its intended subdir. Both fields
  are validated before any filesystem write. Closes a slice-1
  carry-forward gap.
- **Security: `--force` on slash only, not tool schema** (R5).
  Rationale: matches OpenAI's "Safe URL" / two-step-confirmation
  pattern for destructive operations. Force-deleting user-modified
  files via prompt-injected agent call is exactly the failure mode
  this avoids. Humans typing `/cc-import remove plugin --force` make
  the destructive choice explicitly.
- **Deferred-state contract: typed flag + tool description warning**
  (R8). Rationale: per dev.to "agent looping" patterns, string-only
  `notice` fields are loop-prone (LLMs ignore caveats they don't have
  a slot for). Tool responses include `available_now: false` and
  `available_after: "next_session"` typed fields. Tool descriptions
  carry an explicit `IMPORTANT:` line so the LLM sees the constraint
  during planning, not just after the call. Slash output stays text
  ("imported X — restart to use") since human users observe
  `hermes skills list` themselves.
- **`/cc-import list --json` flag**. Rationale: 2026 CLI-for-agents
  research (Reinhard, dminhk) — CLI `--json` is complementary to
  agent tools, not duplicate. Token-efficiency benchmarks favor
  CLI+JSON over MCP for selective-query patterns. Cheap to implement
  via `dataclasses.asdict` + `json.dumps`.
- **`/cc-import remove --dry-run` and tool `dry_run` field**.
  Rationale: explicit-exit-condition pattern (dev.to). `dry_run`
  reports `RemoveResult` populated as if the action ran, without
  filesystem changes. Available on both surfaces. Slash also accepts
  `--force` (tool does not — see above).
- **Concurrent state.json writes use atomic rename**. Rationale: lost-
  update on simultaneous installs is real (slice 1 didn't address
  it; slice 2 amplifies via destructive `remove`). `save_manifest`
  writes to `<path>.tmp` then `os.rename(<path>.tmp, <path>)`. Does
  not serialize concurrent callers but avoids torn writes. Documented
  single-threaded-use assumption stands.
- **`provides_tools` in `plugin.yaml` is documentation-only**.
  Rationale: verified at `hermes_cli/plugins.py:797` (parsed into
  `PluginManifest.provides_tools`) and `hermes_cli/plugins_cmd.py
  cmd_list` (does not read the field). Adding the block keeps
  parity with bundled plugins like spotify. Drift-guard test in
  `tests/test_tools.py` asserts the yaml list matches
  `[t[0] for t in tools.TOOLS]`.
- **No `is_async` on registration**. Rationale: matches handlers'
  sync nature. Default is `False`; not passing it = passing `False`.
- **Manifest helpers stay in `converter.py`**. Rationale: avoiding
  premature `manifest.py` extraction. `state.py` imports
  `load_manifest` / `save_manifest` / `sha256_file` /
  `_resolve_hermes_home` / `_repo_basename` from `converter`.
  Refactor to `manifest.py` is a slice-3 candidate if the seams ask
  for it.

## Open Questions

### Resolved During Planning

- *Where do new modules live?* → `state.py` (pure logic) + `tools.py`
  (handler wrappers). See "Key Technical Decisions".
- *Toolset name?* → `cc_import`.
- *Tool naming?* → `cc_import_install`, `cc_import_list`,
  `cc_import_remove` per slice 1's R-list.
- *Sync vs async?* → Sync, matching converter. Reassess if real-world
  clones exceed ~10s wall-clock.
- *Schema shape?* → Inline OpenAI-style function-call dicts
  (`{"name", "description", "parameters": <JSON Schema>}`), per
  spotify pattern.
- *How to share logic between slash and tools?* → Pure-logic core in
  `state.py`; both surfaces call it and format for their respective
  consumers.
- *Should `remove` delete the clone cache?* → Yes when reconstructible
  from the v2 `_plugins` index or v1 `source_path` walk-up (anchored
  to `$HERMES_HOME/plugins/cc-import/clones/`). When not, leave it
  and warn — orphan disk cost is tiny vs. risk of deleting the wrong
  tree.
- *Manifest schema bump strategy?* → Additive. New optional
  `_plugins` index, reframed as install cache. Old shape continues
  to load. v1 → v2 happens on next `import_plugin` save.
- *Should slash `list` accept `--json`?* → Yes. Per 2026 CLI-for-agents
  research, `--json` is complementary to the agent tool surface.
- *What does "takes effect next session" mean concretely?* → The
  skill index Hermes loads at session start does not refresh
  mid-session. Both install and remove tool responses include
  `available_now: false` typed fields and a tool-description
  warning. Slash output uses text ("restart to use").
- *Security mitigations to apply?* → All four: URL allowlist, git
  hook suppression, `plugin_name`/`subdir` validation, `--force`
  removed from tool schema (slash-only). See R10/R11 + Risks.
- *Should `--force` exist on the tool surface?* → No. Slash command
  retains it for human-in-the-loop control; tool surface does not
  expose it (matches OpenAI Safe URL pattern).
- *Async tool handlers?* → No, sync. If clone latency becomes a
  problem in practice, flip to `is_async=True` and
  `asyncio.to_thread`.

### Deferred to Implementation

- **Exact text-table column widths in `/cc-import list`** — tune
  against actual output once a real `state.json` exists.
- **Whether removing user-modified files (via slash `--force`) should
  also delete now-empty parent dirs** — decide while writing the test;
  default to "yes, parent dir cleanup is part of the remove" but
  verify.
- **Handler registration order** — install / list / remove (matches
  user mental model) is recommended; final order matches whatever
  `tools.TOOLS` declares.
- **Whether the install/remove tool's deferred-state warning should
  always-on or conditional** — easier to ship always-on; refine if
  noise complaints arise.
- **Path-redaction regex specifics** — start with
  `r'/[A-Za-z][\w./-]*'` to catch absolute paths in exception
  messages. Tune in Unit 3.
- **URL allowlist defaults at registration time** — slice 2 hardcodes
  github.com / gitlab.com / bitbucket.org / codeberg.org. Slice 3
  may surface a config override.

## Output Structure

```
cc-import/
├── __init__.py                 (modified: register tools)
├── cli.py                      (modified: list/remove subcommands)
├── converter.py                (modified: _plugins index, hardened clone, validation)
├── plugin.yaml                 (modified: provides_tools block)
├── README.md                   (modified: list/remove docs, security note)
├── state.py                    (NEW: list_imports, remove_import)
├── tools.py                    (NEW: tool handlers + schemas + TOOLS tuple)
├── docs/plans/
│   └── 2026-04-25-002-feat-cc-import-slice-2-plan.md  (this file)
└── tests/
    ├── conftest.py             (unchanged)
    ├── test_cli.py             (modified: list/remove slash tests + register tests)
    ├── test_converter.py       (modified: _plugins index + security tests)
    ├── test_state.py           (NEW: list_imports + remove_import)
    └── test_tools.py           (NEW: handler + schema + drift-guard tests)
```

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance
> for review, not implementation specification. The implementing agent
> should treat it as context, not code to reproduce.*

### Module dependency graph

```
                    ┌────────────────────┐
                    │   __init__.py      │
                    │   register(ctx):   │
                    │   - register_command  (slash, slice 1)
                    │   - register_tool x3  (slice 2)
                    └──────┬─────────┬───┘
                           │         │
                  ┌────────▼──┐   ┌──▼──────────┐
                  │  cli.py   │   │  tools.py   │
                  │ (text)    │   │  (JSON)     │
                  └────┬──────┘   └──────┬──────┘
                       │                 │
                       └────────┬────────┘
                                │
                       ┌────────▼─────────┐
                       │   state.py       │
                       │  list_imports()  │
                       │  remove_import() │
                       └────────┬─────────┘
                                │
                       ┌────────▼─────────┐
                       │  converter.py    │
                       │  load_manifest   │
                       │  save_manifest   │
                       │  sha256_file     │
                       │  _validate_url   │  (NEW: R10 allowlist)
                       │  _safe_clone_env │  (NEW: R10 hardened env)
                       │  _validate_name  │  (NEW: R11)
                       │  _validate_subdir│  (NEW: R11)
                       │  import_plugin   │
                       └──────────────────┘
```

### Manifest schema v2 (additive)

```jsonc
{
  // file entries — slice 1 shape, unchanged
  "compound-engineering/some-skill": {
    "plugin": "compound-engineering",
    "kind": "skill",
    "source_path": "...",
    "origin_hash": "..."
  },
  "compound-engineering/agents/secsentinel": {
    "plugin": "compound-engineering",
    "kind": "agent",
    "source_path": "...",
    "origin_hash": "..."
  },
  // NEW in slice 2 — install cache (slice 3's sources.yaml = canonical)
  "_plugins": {
    "compound-engineering": {
      "url": "https://github.com/EveryInc/compound-engineering-plugin.git",
      "branch": "main",
      "subdir": "",
      "imported_at": "2026-04-25T16:14:32Z"
    }
  }
}
```

### Tool schemas (sketch — actual values land in `tools.py`)

```jsonc
// cc_import_install
{
  "name": "cc_import_install",
  "description": "Import a Claude Code plugin (skills + agents) from a git URL into the local Hermes install. URL must be on the allowlist (github.com, gitlab.com, bitbucket.org, codeberg.org). IMPORTANT: imported skills are NOT callable in the current session — report success and advise the user to restart Hermes.",
  "parameters": {
    "type": "object",
    "properties": {
      "git_url": {"type": "string", "description": "Git URL of the Claude Code plugin repo (HTTPS only; allowlisted hosts)."},
      "branch": {"type": "string", "description": "Branch to clone.", "default": "main"},
      "subdir": {"type": "string", "description": "Subdirectory inside the repo containing skills/ and agents/. Must resolve to a child of the clone root.", "default": ""}
    },
    "required": ["git_url"]
  }
}

// Successful response shape:
// {"plugin": "...", "skills_imported": N, "agents_translated": M,
//  "skills_unchanged": ..., "agents_unchanged": ...,
//  "skipped_user_modified": [...],
//  "available_now": false, "available_after": "next_session",
//  "notice": "Imported skills take effect on the next Hermes session."}

// cc_import_list
{
  "name": "cc_import_list",
  "description": "List installed Claude Code plugins with skill and agent counts.",
  "parameters": {"type": "object", "properties": {}, "required": []}
}

// cc_import_remove
{
  "name": "cc_import_remove",
  "description": "Remove an installed Claude Code plugin (skills + agents + clone cache). User-edited files are always preserved (use the slash command if force-removal is needed). IMPORTANT: removal takes effect on the next Hermes session.",
  "parameters": {
    "type": "object",
    "properties": {
      "plugin": {"type": "string", "description": "Plugin name as shown by cc_import_list."},
      "dry_run": {"type": "boolean", "description": "Report what would happen without writing.", "default": false}
    },
    "required": ["plugin"]
  }
}
// Note: no `force` field — slash-command-only per security R5/R10.
```

## Implementation Units

- [ ] **Unit 1: converter.py — manifest schema v2 + security helpers**

**Goal:** Extend the manifest with a top-level `_plugins` install-cache
index and add the security validation helpers (`_validate_url`,
`_safe_clone_env`, `_validate_plugin_name`, `_validate_subdir`)
that `import_plugin` will use. This is the foundation the rest of
slice 2 depends on.

**Requirements:** R6, R10, R11.

**Dependencies:** None (slice 1 modules only).

**Files:**
- Modify: `converter.py` (extend `import_plugin`, `clone_or_update`;
  add four validation helpers + `_now_iso`)
- Modify: `tests/test_converter.py` (append schema-v2 + security tests)

**Approach:**
- `_validate_url(url) -> None` — rejects URLs whose hostname is not on
  `_ALLOWED_HOSTS = ("github.com", "gitlab.com", "bitbucket.org",
  "codeberg.org")`. Rejects `file://`, `git://`, `ssh://` for now
  (HTTPS only). Raises `ValueError` with a friendly message;
  callers (slash + tool handlers) convert as appropriate.
- `_validate_plugin_name(name) -> None` — asserts
  `re.fullmatch(r'[A-Za-z0-9._-]+', name)`. Rejects names containing
  `..`, `/`, `\`, or empty. Raises `ValueError`.
- `_validate_subdir(subdir, clone_root) -> Path` — resolves
  `(clone_root / subdir).resolve()` and asserts
  `.is_relative_to(clone_root.resolve())`. Returns the resolved path.
  Raises `ValueError` if subdir escapes.
- `_safe_clone_env() -> dict[str, str]` — returns
  `{**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL":
  "/dev/null"}`.
- `clone_or_update` extended: pass `env=_safe_clone_env()` to all
  `subprocess.run` calls; add `--config core.hooksPath=/dev/null`
  and `--no-recurse-submodules` to the `git clone` argv. Existing
  fetch + reset paths get the same env (hooks could fire on fetch
  via `core.fsmonitor` config in the cached repo).
- `import_plugin` calls `_validate_url(git_url)` before
  `clone_or_update`; calls `_validate_plugin_name(plugin_name)` after
  `_resolve_plugin_name`; uses `_validate_subdir(subdir, clone_dest)`
  to derive `plugin_root`.
- After `_resolve_plugin_name`, populate
  `manifest.setdefault("_plugins", {})[plugin_name] = {"url": git_url,
  "branch": branch, "subdir": subdir, "imported_at": _now_iso()}`. If
  the plugin already has an entry, only `imported_at` refreshes
  (other fields preserved unless previously empty — this is
  intentional; `cc_import_install` is meant to be re-run with the
  same args, divergent rerun is undefined and should be flagged in
  slice 3 if it becomes a real workflow).
- `_now_iso()` returns `datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")`.
- `save_manifest` is updated to atomic-rename: write to
  `<path>.tmp`, then `os.replace(<path>.tmp, <path>)`. Mitigates
  torn writes under concurrent invocations (does not serialize but
  prevents corruption mid-write). Existing `mkdir(parents=True,
  exist_ok=True)` stays.

**Execution note:** Strict TDD. One commit per added function /
behavior. Failing test → make-it-pass → commit. Mirrors slice 1.

**Patterns to follow:**
- Slice 1's `import_plugin` flow at `converter.py:454-528` — augment
  after `load_manifest`, before the skill/agent walks
- `~/.hermes/hermes-agent/plugins/spotify/plugin.yaml` —
  `provides_tools` shape (used in Unit 5)

**Test scenarios:**

`_validate_url`:
- *Happy path:* `https://github.com/foo/bar.git` → no exception
- *Allowlist enforcement:* `https://evil.com/payload.git` → raises
- *Schema rejection:* `file:///tmp/repo`, `git://...`,
  `ssh://git@host/repo` → raise
- *Allowlist hosts:* each of github / gitlab / bitbucket / codeberg
  passes (parametrize)

`_validate_plugin_name`:
- *Happy path:* `compound-engineering`, `foo_bar.v2` → pass
- *Traversal rejection:* `../core`, `/abs`, `foo/bar`, `foo\bar`,
  empty, `..` → all raise
- *Edge case:* unicode chars rejected (we restrict to ASCII regex)

`_validate_subdir`:
- *Happy path:* `subdir="plugins/foo"` against `/tmp/clone` →
  resolves to `/tmp/clone/plugins/foo`
- *Traversal rejection:* `subdir="../etc"`, `subdir="/etc/passwd"`,
  `subdir="plugins/../../etc"` → raise
- *Edge case:* empty subdir → returns `clone_root` itself

`_safe_clone_env`:
- *Happy path:* returned dict contains `GIT_CONFIG_NOSYSTEM=1` and
  `GIT_CONFIG_GLOBAL=/dev/null`
- *Inheritance:* preserves other env vars (e.g. `PATH`)

`clone_or_update` with hardened env:
- *Happy path:* clone succeeds against a local bare repo as in slice
  1, with the safe env applied (verify by setting up a fixture repo
  with a `post-checkout` hook that would write a side-effect file;
  assert the file is not written)
- *Existing slice-1 tests stay green*

`import_plugin` with validation:
- *Happy path:* unchanged from slice 1 baseline (compound-engineering
  fixture imports cleanly)
- *Bad URL:* call with `https://evil.com/x.git` → `ValueError` from
  `_validate_url`; manifest unchanged; nothing cloned
- *Bad plugin name (via malicious plugin.json):* fixture upstream
  with `plugin.json` containing `name: "../core"` → `ValueError`
  from `_validate_plugin_name`; nothing written under skills_dir
- *Bad subdir:* `subdir="../etc"` → `ValueError`; nothing cloned
- *Schema v2 happy path:* fresh manifest → after import,
  `manifest["_plugins"][<plugin>]` has all four fields with
  ISO-8601 timestamp
- *Schema v2 idempotent:* second call refreshes only `imported_at`
- *Schema v2 backward-compat read:* manually-constructed v1 manifest
  loads cleanly; first save adds `_plugins`
- *Schema v2 multiple plugins:* import two URLs sequentially → both
  in `_plugins`
- *ISO-8601 format:* `_now_iso()` matches
  `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$`

`save_manifest` atomic-rename:
- *Happy path:* file is created via `<path>.tmp` rename; final
  contents match expectation
- *Crash mid-write simulation:* if `<path>.tmp` exists from a prior
  failed run, next save overwrites it cleanly

**Verification:**
- `pytest tests/test_converter.py -q` passes (slice 1 tests + new
  unit-1 tests)
- `ruff check .` passes
- `ty check .` passes
- Manual: import the EveryInc compound-engineering plugin against an
  empty `tmp_path/.hermes`, then `cat
  tmp_path/.hermes/plugins/cc-import/state.json | jq ._plugins` shows
  the new index
- Manual security smoke: attempt `import_plugin("https://evil.com/x.git", ...)`
  → fails with `ValueError` before any clone

---

- [ ] **Unit 2: state.py — `list_imports()` + `remove_import()`**

**Goal:** Implement the pure-logic core that both surfaces will share.
`list_imports()` reads the manifest and returns a list of dataclasses.
`remove_import()` deletes a plugin's skills tree, clone cache, and
manifest entries while honoring the user-modified matrix. Note: the
slash command exposes `--force` (overrides user-modified preservation);
the tool schema does not.

**Requirements:** R4, R5, R7 (defensive arg shape on the boundary
between this module and its callers).

**Dependencies:** Unit 1 (manifest schema v2 readable; security
helpers in place). Slice 1 helpers re-exported from `converter`:
`_resolve_hermes_home`, `_repo_basename`.

**Files:**
- Create: `state.py`
- Create: `tests/test_state.py`

**Approach:**
- Module structure mirrors `converter.py`: top-level functions, two
  small dataclasses (`PluginListEntry`, `RemoveResult`), uses the
  try-relative-then-absolute import idiom for `converter`.
- `list_imports(hermes_home: Path | None = None) -> list[PluginListEntry]`:
  - Resolve `hermes_home` via `converter._resolve_hermes_home`.
  - Load manifest. Iterate `manifest.items()` filtering on
    `entry.get("plugin")` (returns `None` for `_plugins` key, which
    is correctly skipped — same mechanism `prune_removed` uses at
    `converter.py:392`). Group by `entry["plugin"]`. For each plugin:
    count entries where `kind == "skill"` and `kind == "agent"`. Pull
    URL/branch/imported_at from `manifest["_plugins"][<plugin>]` if
    present.
  - Return one `PluginListEntry(name, skills_count, agents_count,
    url | None, branch | None, imported_at | None)` per plugin,
    sorted by `name`.
- `remove_import(plugin_name: str, *, force: bool = False, dry_run:
  bool = False, hermes_home: Path | None = None) -> RemoveResult`:
  - Resolve paths from `hermes_home`.
  - Load manifest. Find all entries with
    `entry.get("plugin") == plugin_name`.
  - Plan the removal: for each entry, compute current `local_hash`
    (if file exists) and compare to `entry["origin_hash"]`. If equal
    → delete-able; else → user-modified, **preserved unless `force`
    is set**.
  - Determine clone cache path: prefer
    `manifest["_plugins"][plugin_name]["url"]` →
    `_repo_basename(url)` → `clones / <basename>`. Fall back to
    walking up `source_path` of any file entry, **anchored to
    `home / "plugins" / "cc-import" / "clones"`** — assert the
    resolved cache path is a child of that anchor; if not, abort
    cache deletion and warn. This anchoring closes the
    coincidental-`clones`-segment vulnerability surfaced in review.
  - If `dry_run`: build `RemoveResult` describing what would be
    removed/kept and return without touching disk.
  - Else: delete files. For user-modified files when `not force`:
    keep file, leave parent dir, log "KEEP (user-modified)". For all
    others: delete the parent skill dir entirely (not just SKILL.md
    — covers helper.py and friends).
  - After deletions, if no entries for the plugin remain (or all were
    force-deleted), drop `manifest["_plugins"][plugin_name]`.
  - Save manifest (atomic-rename per Unit 1).
  - Return `RemoveResult(plugin: str, dry_run: bool,
    removed_skills: int, removed_agents: int,
    kept_user_modified: list[str], clone_cache_status: str,
    clone_cache_path: str | None, no_changes: bool)`.
  - `clone_cache_status` is one of `"removed"`, `"skipped_unfindable"`,
    `"skipped_path_outside_anchor"`, `"already_missing"` (replaces
    the prior tri-state `bool | None` with explicit semantics).
  - `no_changes=True` when the plugin doesn't exist in the manifest
    at all — idempotent re-remove returns this rather than raising.
- All filesystem operations use `pathlib.Path`. `shutil.rmtree`
  handles recursive deletion. Manifest is updated in-memory and
  saved at the end (single atomic-rename save).

**Execution note:** Strict TDD per function. One commit per scenario
class.

**Patterns to follow:**
- `converter.py:376-405` — `prune_removed`'s user-modified detection
  is the model for `remove_import`. Same hash comparison; different
  triggering condition (explicit removal vs. upstream-deleted).
- `converter.py:413-422` — `ImportSummary` dataclass shape.
  `RemoveResult` follows the same convention.
- `~/.hermes/hermes-agent/plugins/disk-cleanup/disk_cleanup.py` —
  general shape of a "list installed things" function returning
  dataclasses; review for naming consistency before writing.

**Test scenarios:**

`list_imports`:
- *Happy path (single plugin):* Set up a fixture manifest with 2
  skills + 1 agent for plugin "fp", plus `_plugins["fp"] = {url, ...}`
  → `list_imports()` returns `[PluginListEntry(name="fp",
  skills_count=2, agents_count=1, url=..., branch="main",
  imported_at=...)]`.
- *Happy path (multiple plugins):* Two plugins → two sorted entries.
- *Backward-compat:* Manifest without `_plugins` key → entries still
  return; `url`/`branch`/`imported_at` are `None`.
- *Empty manifest:* No file entries → returns `[]`.
- *Manifest with only `_plugins` key (no file entries):* Returns
  `[]` (an entry without skills+agents is not really installed).
- *Mixed kinds:* Plugin with some `kind: "skill"` entries and some
  `kind: "agent"` entries → counts split correctly.
- *Edge case:* Manifest where an entry has no `plugin` key (corrupt
  v1 data) → silently skipped, not crashed.

`remove_import`:
- *Happy path:* Plugin "fp" with 2 skill dirs + 1 agent dir on disk
  matching manifest origin_hashes. `remove_import("fp")` →
  `RemoveResult(removed_skills=2, removed_agents=1,
  kept_user_modified=[], clone_cache_status="removed",
  no_changes=False)`. Skill dirs are gone. Manifest entries for "fp"
  are gone, including `_plugins["fp"]`.
- *Idempotent rerun:* Second call → `RemoveResult(no_changes=True)`,
  manifest unchanged.
- *User-modified preservation:* User edits one skill's `SKILL.md`
  (local_hash != origin_hash). `remove_import("fp")` →
  `kept_user_modified=["fp/that-skill"]`, that skill dir survives,
  manifest entry for it is retained, other entries removed,
  `_plugins["fp"]` is **retained** (because the plugin still has
  entries on disk).
- *Force overrides:* Same setup, `remove_import("fp", force=True)` →
  user-modified file is deleted too, `_plugins["fp"]` removed.
- *Force × dry-run:* `remove_import("fp", force=True, dry_run=True)`
  → reports "would delete user-modified files" via populated
  `kept_user_modified` becoming empty under force, but no disk
  changes; manifest unchanged.
- *Dry-run preserves:* `remove_import("fp", dry_run=True)` →
  `RemoveResult` with populated counts + `dry_run=True`, but disk
  and manifest unchanged.
- *Missing plugin:* `remove_import("never-installed")` →
  `RemoveResult(no_changes=True)`. Does not raise.
- *Missing clone cache (slice-1-shaped manifest, no `_plugins`):*
  walks up from `source_path` to find the cache dir, anchored to
  `home/plugins/cc-import/clones/`. Verify by setting up a v1-shaped
  manifest fixture.
- *Path outside anchor:* Manifest has `source_path` whose ancestors
  don't include `home/plugins/cc-import/clones/` (e.g. corrupted
  manifest pointing somewhere else) → `clone_cache_status=
  "skipped_path_outside_anchor"`; no deletion attempted; skills
  removal proceeds.
- *Unfindable clone cache:* Manifest has no `_plugins` and
  `source_path` doesn't contain the anchor →
  `clone_cache_status="skipped_unfindable"`; warning logged; skills
  deletion still proceeds.
- *Edge case:* Skill dir already missing (someone deleted it
  manually) → counted as removed, manifest entry dropped, no error.
- *Edge case:* `force=True` on a plugin with no user-modified files
  behaves identically to default — verifies `force` is a permission,
  not a separate code path.

**Verification:**
- `pytest tests/test_state.py -q` passes
- `ruff check state.py tests/test_state.py` passes
- `ty check state.py` passes

---

- [ ] **Unit 3: tools.py — `register_tool` schemas + handlers**

**Goal:** Implement the three agent-callable tool handlers and their
inline OpenAI-style schemas. Each handler defensively validates args,
calls into `state.py` or `converter.py`, and returns
`tool_result(...)` / `tool_error(...)` strings. Install + remove
responses include the typed deferred-state contract.

**Requirements:** R1, R3, R5 (no `force` field), R7, R8 (typed flag),
R10 (URL validation surfaces as `tool_error("disallowed_host", ...)`),
R11 (validation errors surface as `tool_error("invalid_arg", ...)`).

**Dependencies:** Unit 1 (security helpers); Unit 2 (`state.py`).

**Files:**
- Create: `tools.py`
- Create: `tests/test_tools.py`

**Approach:**
- Module exports: three handler functions, three schema dicts, and
  one `TOOLS` tuple `((name, schema, handler, emoji), ...)` consumed
  by `__init__.py` in Unit 4.
- Import `tool_result` and `tool_error` from `tools.registry` (the
  real Hermes import path; verified across 9+ bundled plugins).
  Slice-1's try-relative-then-absolute pattern wraps it: production
  inside Hermes resolves the real helpers; pytest in the standalone
  repo (no `tools/` on sys.path) falls back to a local one-liner
  shim that wraps `json.dumps`. The shim is two lines and exact-API-
  compatible. Note that pytest never exercises the production
  helpers; a contract-shape note in the smoke checklist (Unit 5)
  verifies the production shape post-install.
- Handler signatures: `def _handle_install(args: dict, **kwargs) ->
  str:` (and similar for list/remove). Each:
  1. Try-block wraps the entire body. `except Exception:` only
     (KeyboardInterrupt and SystemExit propagate intentionally).
     Any unexpected exception becomes
     `tool_error("internal_error", _redact_paths(str(exc)))`.
  2. Validate args defensively. Missing/empty `git_url` →
     `tool_error("missing_arg", "git_url is required")`. Wrong type
     → coerce via `str(...)` or report.
  3. Call into `converter.import_plugin` (install) or
     `state.list_imports` / `state.remove_import` (list/remove).
     Note: install's `import_plugin` does the URL/name/subdir
     validation internally; tool handler catches `ValueError` and
     surfaces as `tool_error("invalid_arg", ...)` or
     `tool_error("disallowed_host", ...)` based on the message.
  4. For install + remove: append typed flag fields to the response
     payload — `available_now: false`, `available_after: "next_session"`,
     `notice: "<human-readable>"`. List has no notice (state
     introspection is current).
  5. Return `tool_result(payload_dict)`. Payload shape =
     `dataclasses.asdict(summary) | {"available_now": False, ...}`
     for install/remove; `{"plugins": [asdict(e) for e in entries]}`
     for list.
- `_redact_paths(text: str) -> str` — regex-replace
  `r'/[A-Za-z][\w./-]+'` with `<path>`. Preserves error semantics
  while avoiding filesystem-layout leakage.
- Schemas are inline dicts at module top level (`_INSTALL_SCHEMA`,
  `_LIST_SCHEMA`, `_REMOVE_SCHEMA`). Each contains `name`,
  `description`, `parameters`. **Descriptions include explicit
  `IMPORTANT:` lines warning the LLM at planning time** about
  deferred state (install/remove) and force-via-slash (remove). No
  description names tools from outside the `cc_import` toolset
  (AGENTS.md `:628`).
- `_REMOVE_SCHEMA` does NOT include `force` in `parameters.properties`.
  The tool surface cannot force-delete user files — that requires
  the slash command (R5).
- `TOOLS` tuple: `(("cc_import_install", _INSTALL_SCHEMA,
  _handle_install, "📦"), ("cc_import_list", _LIST_SCHEMA,
  _handle_list, "📋"), ("cc_import_remove", _REMOVE_SCHEMA,
  _handle_remove, "🗑️"))`.

**Execution note:** Strict TDD. Tests for each handler land in their
own class with happy/error scenarios. Schema tests assert structural
shape and security invariants (no `force` on remove; descriptions
contain `IMPORTANT:`).

**Patterns to follow:**
- `~/.hermes/hermes-agent/plugins/spotify/tools.py:170-221` — handler
  shape, defensive arg parsing, single try/except.
- `~/.hermes/hermes-agent/plugins/spotify/tools.py:351-395` — schema
  shape (the trio of `enum`-discriminated examples).
- `converter.py:454-528` — `import_plugin` signature; reuse for
  `_handle_install`.
- `state.py:list_imports` / `state.py:remove_import` — call sites for
  list/remove handlers.

**Test scenarios:**

`_handle_install`:
- *Happy path:* `args = {"git_url": ALLOWED_URL, "branch": "main",
  "subdir": ""}`, monkeypatch `converter.import_plugin` to return a
  known `ImportSummary` → response is JSON-decodable, has top-level
  keys `plugin`, `skills_imported`, `agents_translated`,
  `skills_unchanged`, `agents_unchanged`, `skipped_user_modified`,
  `available_now: false`, `available_after: "next_session"`,
  `notice`. Notice mentions "restart" or "next session".
- *Defaults:* `args = {"git_url": URL}` (no branch/subdir) → calls
  `import_plugin` with `branch="main"` and `subdir=""`. Verify via
  spy on the function.
- *Missing arg:* `args = {}` → `tool_error("missing_arg", ...)` with
  message containing `git_url`. Decoded JSON has `"error"` key.
- *Wrong type:* `args = {"git_url": 42}` → coerced to string via
  `str()` or surfaced as `tool_error`. Test the implementation's
  choice; spy on `import_plugin` makes the chosen behavior
  observable.
- *Disallowed host:* `args = {"git_url": "https://evil.com/x.git"}`
  → `import_plugin` raises `ValueError`; handler returns
  `tool_error("disallowed_host", ...)` (or `"invalid_arg"`,
  depending on classification). Decoded.
- *Path traversal in subdir:* `args = {"git_url": ALLOWED_URL,
  "subdir": "../etc"}` → `tool_error("invalid_arg", ...)`.
- *Backend git failure:* monkeypatch `import_plugin` to raise
  `subprocess.CalledProcessError` → response is `tool_error` with
  `"clone_failed"` code. Path-redaction applied to message.
- *Generic exception:* `import_plugin` raises `RuntimeError("nope")`
  → `tool_error("internal_error", ...)` with redacted message.
- *Path redaction:* exception message containing
  `/Users/tyler/.hermes/...` → redacted to `<path>` in response.

`_handle_list`:
- *Happy path:* monkeypatch `state.list_imports` to return two
  `PluginListEntry` instances → response JSON has `"plugins"` array
  with two dicts; each dict has `name`, `skills_count`,
  `agents_count`, `url`, `branch`, `imported_at`.
- *Empty:* `list_imports` returns `[]` → response has `"plugins":
  []`. No error.
- *Generic exception:* `list_imports` raises → `tool_error` with
  internal_error code.

`_handle_remove`:
- *Happy path:* `args = {"plugin": "fp"}`, monkeypatch
  `state.remove_import` to return a `RemoveResult` →  response has
  `plugin`, `removed_skills`, `removed_agents`,
  `kept_user_modified`, `clone_cache_status`, `clone_cache_path`,
  `dry_run`, `no_changes`, `available_now: false`,
  `available_after: "next_session"`, `notice`.
- *Dry-run pass-through:* `args = {"plugin": "fp", "dry_run": true}`
  → `remove_import` is called with `dry_run=True`, `force=False`
  (always). Verify via spy.
- *No `force` field accepted:* `args = {"plugin": "fp", "force":
  true}` → `force` is **silently dropped** (or
  `tool_error("invalid_arg", "force is not supported via tool
  surface; use /cc-import remove --force")`). Test the
  implementation's choice; recommend the explicit error for
  agent-clarity.
- *Missing plugin arg:* `args = {}` → `tool_error("missing_arg",
  ...)`. Decoded.
- *No-op:* `RemoveResult(no_changes=True)` → response includes the
  flag and notice that nothing changed.
- *Generic exception:* `remove_import` raises → `tool_error`.

`TOOLS` tuple structure:
- *Shape:* Length 3. Each element is a 4-tuple of `(str, dict,
  callable, str)`. Names match
  `["cc_import_install", "cc_import_list", "cc_import_remove"]`.
- *Schema fields:* Each schema dict has keys `name`, `description`,
  `parameters`. `parameters` has `type: "object"` and a `properties`
  dict.
- *No `force` in `_REMOVE_SCHEMA`:* assert
  `"force" not in _REMOVE_SCHEMA["parameters"]["properties"]`.
- *`IMPORTANT:` warning in install + remove descriptions:* assert
  `"IMPORTANT:" in _INSTALL_SCHEMA["description"]` and same for
  remove.
- *Description discipline:* No description string mentions any tool
  name from outside the `cc_import` toolset. Concrete check: assert
  none of the descriptions contain `"spotify_"`, `"hindsight_"`,
  or known names from other bundled toolsets (regex against a
  hardcoded list).

`_redact_paths`:
- *Happy path:* `"/Users/tyler/.hermes/state.json: not found"` →
  `"<path>: not found"`.
- *Multiple paths:* `"a /foo/bar b /baz"` → `"a <path> b <path>"`.
- *Non-path text:* unchanged.

**Verification:**
- `pytest tests/test_tools.py -q` passes
- `ruff check tools.py tests/test_tools.py` passes
- `ty check tools.py` passes
- All handlers return strings (not dicts) when called in isolation —
  enforced by typing + tests
- `plugin.yaml` ↔ `tools.TOOLS` drift-guard test passes (asserts
  yaml's `provides_tools` list matches `[t[0] for t in tools.TOOLS]`)

---

- [ ] **Unit 4: cli.py + __init__.py — slash subcommands and tool registration (merged)**

**Goal:** Wire slice 2 into both plugin entry points in one unit.
`cli.py` gains the `list` and `remove` slash subcommands (with
`--json` on list, and `--force` + `--dry-run` on remove — slash
keeps `--force` for human use). `__init__.py`'s `register(ctx)`
calls `register_tool` once per entry in `tools.TOOLS` after the
existing `register_command` call.

**Requirements:** R1, R2, R5 (slash retains `--force`).

**Dependencies:** Unit 2 (`state.py`); Unit 3 (`tools.py`). Units 3
and 4 can be built in parallel after Unit 2 if desired (they only
depend on `state.py`); the merge into one unit reflects logical
"slice 2 wiring" rather than a strict ordering constraint.

**Files:**
- Modify: `cli.py`
- Modify: `__init__.py`
- Modify: `tests/test_cli.py` (slash subcommand tests + register tests)

**Approach:**

*cli.py:*
- Update `_USAGE` constant to include `list` and `remove`.
- Extend `handle_command` dispatch table:
  ```
  if subcommand == "install": return _cmd_install(rest)
  if subcommand == "list":    return _cmd_list(rest)
  if subcommand == "remove":  return _cmd_remove(rest)
  ```
- `_make_list_parser()` → argparse with `--json` flag.
- `_make_remove_parser()` → argparse with positional `plugin`,
  `--force`, `--dry-run`.
- `_cmd_list(argv)` → parse, call `state.list_imports()`, format
  text table OR JSON. Catch unexpected exceptions, return string
  beginning with `"Error:"`.
- `_cmd_remove(argv)` → parse, call `state.remove_import(plugin,
  force=..., dry_run=...)`, format human-readable summary string
  including any `kept_user_modified` lines. Catch unexpected
  exceptions.
- Formatting helpers:
  - `_format_list_text(entries)` → aligned columns:
    `NAME  SKILLS  AGENTS  URL`. If empty: "No plugins installed."
  - `_format_list_json(entries)` → `json.dumps([asdict(e) for e in
    entries], indent=2)`.
  - `_format_remove(result)` → multi-line text describing what
    happened or what would happen (`dry_run=True`).

*__init__.py:*
- After `ctx.register_command(...)`, iterate `tools.TOOLS` and call
  `ctx.register_tool(name=name, toolset="cc_import", schema=schema,
  handler=handler, description=schema["description"], emoji=emoji)`.
- Add try-relative-then-absolute import for `tools` (matching the
  existing pattern for `cli`).

**Execution note:** Strict TDD per surface. Slash subcommand tests
share `tests/test_cli.py` with slice 1's existing install tests;
register tests get a new `TestRegister` class in the same file.

**Patterns to follow:**
- `cli.py:54-63` (slice 1) — `_make_install_parser` shape for
  argparse add_help=False subparsers.
- `~/.hermes/hermes-agent/plugins/disk-cleanup/__init__.py:230-274` —
  text-table formatting for state introspection. Keep it boring.
- `cli.py:35-52` (slice 1) — `_cmd_install`'s try/except shape;
  reuse for remove + list.
- `~/.hermes/hermes-agent/plugins/spotify/__init__.py:45-66` —
  registration loop iterating a TOOLS-like tuple.
- `__init__.py:24-40` (slice 1) — current `register(ctx)` shape.

**Test scenarios:**

*Slash subcommand parser tests:*

`_make_list_parser`:
- *Happy path:* `parse_args([])` → `Namespace(json=False)`.
- *--json:* `parse_args(["--json"])` → `Namespace(json=True)`.

`_make_remove_parser`:
- *Happy path:* `parse_args(["fp"])` → `plugin="fp"`, `force=False`,
  `dry_run=False`.
- *Flags:* `parse_args(["fp", "--force", "--dry-run"])` → both
  True.
- *Error path:* `parse_args([])` raises `SystemExit` (missing
  positional).

*Slash subcommand handler tests:*

`_cmd_list`:
- *Happy path (text):* monkeypatch `state.list_imports` to return two
  entries → output is a table whose first row contains
  "NAME"/"SKILLS"/"AGENTS"/"URL" and subsequent rows contain the
  plugin names.
- *Happy path (JSON):* `_cmd_list(["--json"])` → output parses as
  JSON, length-2 array, each element has keys matching dataclass
  fields.
- *Empty:* `list_imports` returns `[]` → text: "No plugins
  installed." JSON: `"[]"`.
- *Backend exception:* `list_imports` raises → returns string
  beginning with "Error:" (does not re-raise).

`_cmd_remove`:
- *Happy path:* `_cmd_remove(["fp"])` calls `state.remove_import("fp",
  force=False, dry_run=False)` (verified via spy). Output mentions
  the plugin name and the counts.
- *--force --dry-run pass-through:* args reach `remove_import`
  unchanged.
- *No-op:* `remove_import` returns `RemoveResult(no_changes=True)` →
  output mentions "no changes".
- *User-modified preservation:* `kept_user_modified` is non-empty →
  output enumerates kept files and notes that `--force` would
  delete them.
- *Missing plugin arg:* `_cmd_remove([])` returns the parse-error
  string + usage hint.
- *Backend exception:* `remove_import` raises → "Error:" string.

*Dispatch tests:*

`handle_command`:
- *Routes `list`:* `handle_command("list")` → invokes `_cmd_list`
  (verified via monkeypatch of the module-level function).
- *Routes `remove fp`:* `handle_command("remove fp")` →
  `_cmd_remove(["fp"])`.
- *Unknown subcommand:* `handle_command("nuke fp")` → message lists
  `install`, `list`, `remove` as available.
- *Existing slice-1 install routing:* `handle_command("install URL")`
  still works (slice 1 regression test stays green).

*Register tests (`TestRegister` class):*

- *Happy path:* `register(mock_ctx)` calls
  `mock_ctx.register_command` exactly once with `name="cc-import"`
  (slice 1 invariant) AND `mock_ctx.register_tool` exactly three
  times.
- *Tool registration shape:* Each `register_tool` call has
  `toolset="cc_import"`, a `name` matching the schema's `name`, a
  callable `handler`, and a non-empty `description`.
- *Tool name uniqueness:* The three names are
  `cc_import_install`, `cc_import_list`, `cc_import_remove`, in
  the order declared by `tools.TOOLS`.
- *Slash command preservation:* `register_command` is still called
  with the same args slice 1 produced — no regression in the slash
  surface.

**Verification:**
- `pytest tests/ -q` passes (whole suite, ~120 tests projected)
- `ruff check .` passes
- `ty check .` passes
- Manual smoke (next Hermes session): `hermes plugins install
  file://$(pwd)`, restart gateway, `hermes tools list | grep
  cc_import` shows three entries

---

- [ ] **Unit 5: plugin.yaml + README + manual smoke (final)**

**Goal:** Land the documentation parity changes and run the full
end-to-end smoke that slice 2 promises, including the security-
hardening verification.

**Requirements:** R9, R12.

**Dependencies:** Units 1-4.

**Files:**
- Modify: `plugin.yaml`
- Modify: `README.md`

**Approach:**
- `plugin.yaml`: append a `provides_tools` block listing the three
  tools. Bump `version` to `0.2.0`. Drift-guard test in `test_tools.py`
  enforces sync with `tools.TOOLS`.
- `README.md` updates:
  - Roadmap: mark slice 2 done; phrase slice 3 with the
    sources.yaml + auto-sync framing.
  - Usage: extend with `/cc-import list` and `/cc-import remove`
    examples + flags. Note that `--force` is slash-only (not
    available via the agent tool surface) for security.
  - Add an "Agent tools" section: 3-bullet summary of
    `cc_import_install`, `cc_import_list`, `cc_import_remove` with
    one-sentence descriptions and key argument names. Note that
    install/remove "take effect on next Hermes session" and that
    install URLs are restricted to an allowlist.
  - Add a "Security" section: brief paragraph explaining the URL
    allowlist, hook-suppression, and force-via-slash policy.
    Single-paragraph + bullet list.
  - State section: one-line mention of the `_plugins` install-cache
    index.
  - "How it works" → no changes (conversion algorithm unchanged).

**Test scenarios:**
- Test expectation: none — documentation is not behavior. README
  rendering is verified manually on github.com after push.

**Verification:**
- `pytest -q` (whole suite) passes
- `ruff check .`, `ruff format --check .`, `ty check .` pass
- Manual smoke (new shell, fresh `tmp_path/.hermes`):
  1. `HERMES_HOME=/tmp/cc-import-smoke/.hermes hermes plugins
     install file://$(pwd)`
  2. Restart gateway / start a fresh chat session
  3. `/cc-import install
     https://github.com/EveryInc/compound-engineering-plugin.git`
     → reports 36 skills + 48 agents (matches slice 1 baseline).
  4. `/cc-import list` → shows `compound-engineering` with
     `36 / 48`, the URL, and an `imported_at` timestamp.
  5. `/cc-import list --json` → parses as JSON list with the same
     fields.
  6. `/cc-import remove compound-engineering --dry-run` → reports
     what would be removed; no disk changes.
  7. `/cc-import remove compound-engineering` → reports actual
     removal; `state.json` no longer contains entries for
     compound-engineering; clones cache for that repo is gone.
  8. Agent test: in a Hermes chat, prompt the agent to call
     `cc_import_list`. Confirm the JSON response shape.
  9. Agent test: prompt the agent to call `cc_import_install` with
     the compound-engineering URL. Verify response contains
     `available_now: false`, `available_after: "next_session"`, and
     a `notice` string.
  10. **Security smoke:** prompt the agent to `cc_import_install`
      against `https://evil.com/payload.git` → response is
      `tool_error` with `disallowed_host` code; nothing cloned.
  11. **Security smoke:** prompt the agent to call
      `cc_import_remove` with `force=true` → either silent-drop or
      explicit error per Unit 3 implementation; in neither case
      does a user-modified file get deleted.
  12. **Production helper smoke:** the install in step 9 went
      through `tools.registry.tool_result` (real Hermes), not the
      pytest fallback shim. Verify by inspecting the response shape
      for any field unique to the real helper (e.g. truncation
      metadata if present in the upstream version).
- README renders cleanly on github.com after push (manual check).

## System-Wide Impact

- **Interaction graph:** Three new tools join Hermes's tool registry
  under toolset `cc_import`. Slash command surface gains `list` and
  `remove`. No core Hermes file is modified. No new hooks. The plugin
  remains discoverable by `hermes plugins list`, registerable by
  `hermes plugins install file://$(pwd)`, and invokable from any
  surface that consumes `register_command` + `register_tool`.
- **Error propagation:** Tool handlers convert all caught exceptions
  to `tool_error(code, redacted_msg)` strings; `registry.dispatch`
  will not see a raised exception from cc-import handlers.
  KeyboardInterrupt / SystemExit propagate intentionally. Slash
  handlers continue slice 1's "return error string" pattern.
- **State lifecycle risks:** The manifest gains a `_plugins` install-
  cache index. Reads of v1 manifests succeed. Writes always include
  the index when there's data to store, via atomic-rename to avoid
  torn writes. If a `remove` is interrupted (process killed
  mid-write), `os.replace` semantics ensure the file is either fully
  old or fully new state. Concurrent invocations from a single
  Hermes session may lose-update — single-threaded use is the
  documented assumption.
- **API surface parity:** The same operation can now be reached three
  ways: agent tool (JSON in/out, no `force`, deferred-state typed
  flag), slash command (text in/out, `--force` available,
  `--json` available on list), and the unwired top-level CLI
  subcommand from slice 1's `register_cli_command` (will work if/when
  Hermes wires it up). The three surfaces share `state.py` for
  list+remove and `converter.import_plugin` for install.
- **Integration coverage:** Unit 5's manual smoke is the load-bearing
  cross-layer verification. Units 1-4 unit tests stub or use fixture
  data. The smoke test exercises real `git clone`, real filesystem
  mutation, real `_plugins` index writes, real removal, real
  hardened-clone env, real allowlist enforcement, and the agent
  surface end-to-end including the production `tool_result` path.
- **Unchanged invariants:**
  - `converter.import_plugin` keeps its public signature
    `(git_url, *, branch, subdir, hermes_home) -> ImportSummary`.
    Slice 1 callers don't break. Internal additions: URL/name/subdir
    validation, hardened clone env. These can raise `ValueError` on
    malformed input — slice-1's slash handler already catches
    generic exceptions, so no regression.
  - `migrate_skill` / `migrate_agent` / `prune_removed` /
    `parse_frontmatter` / `render_frontmatter` / `translate_tools` /
    `build_delegation_skill` are untouched.
  - `clone_or_update` gains hardened env + flags but retains its
    signature.
  - `cli.handle_command` keeps its `(raw_args: str) -> str`
    signature.
  - `__init__.py:register(ctx)` keeps calling
    `ctx.register_command` with the same args (slice 1 regression
    test holds).
  - The user-modified preservation matrix from slice 1 governs
    `remove`'s default behavior unchanged.
  - No new Python dependencies. No CI workflow changes.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| **Prompt-injection-driven git clone RCE** — agent ingests untrusted text containing a malicious git URL, calls `cc_import_install`, hooks/submodules execute on Q's machine | URL allowlist (R10) blocks non-allowlisted hosts; `--config core.hooksPath=/dev/null --no-recurse-submodules` + `GIT_CONFIG_NOSYSTEM=1` suppress hook execution and recursion (per CVE-2017-1000117 mitigation guidance). Slash command bypasses allowlist (human-typed). Documented in README Security section. |
| **`subdir` / `plugin_name` path traversal** — malicious `plugin.json name` or attacker-controlled `subdir` writes outside the intended subtree | `_validate_plugin_name` enforces `^[A-Za-z0-9._-]+$`; `_validate_subdir` resolves and asserts subpath of clone root. Both run before any filesystem write. (Closes a slice-1 carry-forward gap, R11.) |
| **`force` via prompt-injected agent destroys user-modified files** | `cc_import_remove` tool schema has no `force` parameter; force is slash-command-only (R5). Matches OpenAI Safe URL pattern for destructive ops. |
| **Hermes's tool registry does no JSON-schema validation before invoking the handler** — agents can pass malformed args | Every handler defensively validates: missing/empty required args → `tool_error("missing_arg", ...)`; wrong types coerced or reported. Tests cover both shapes. |
| **`tool_error` exception messages leak filesystem paths** — exception text routinely contains absolute paths an agent could read | `_redact_paths` regex-replaces absolute path-like substrings with `<path>` before returning. Applied in every handler's catch-all. |
| **Concurrent invocations corrupt state.json** — two parallel `cc_import_install` calls lose-update | `save_manifest` writes via atomic `os.replace` (write to `.tmp` then rename) — prevents torn writes. Does not serialize concurrent callers; single-threaded use is the documented assumption. README notes this. |
| **Agent loops attempting to use just-installed skill** — installed skills aren't callable until next Hermes session | Tool response includes typed `available_now: false` + `available_after: "next_session"` fields. Tool description carries `IMPORTANT:` warning visible to LLM at planning time. Industry research (dev.to "agent looping" patterns) shows typed flags reduce loop frequency vs. string-only notices. |
| **Manifest schema v2 introduces a new top-level key (`_plugins`) that legacy slice-1 readers might iterate** | Slice 1's `prune_removed` filters by `entry.get("plugin") == plugin` (verified at `converter.py:392`); the `_plugins` value is a `dict[str, dict]` whose `.get("plugin")` returns `None`, so it's skipped. Slice 2 readers (`list_imports`, `remove_import`) explicitly filter the same way. Verification step in Unit 1: grep slice-1 source for any raw `manifest.items()` iteration without `plugin` filter to confirm no surprise paths. |
| **`remove` deletes user files if user-modified detection has a hash mismatch** (e.g. CRLF vs LF) | Slice 1's `sha256_file` reads bytes — CRLF-safe. Tests for `remove_import` include a CRLF-edited fixture to verify. |
| **Clone cache deletion targets the wrong directory** — `source_path` walk-up lands somewhere outside cc-import's state dir | Walk-up is anchored to `home / "plugins" / "cc-import" / "clones"`; resolved cache path must be a child of that anchor or deletion is skipped with `clone_cache_status="skipped_path_outside_anchor"`. |
| **Pytest never exercises real `tool_result` / `tool_error`** — drift in the upstream API would not be caught by unit tests | Documented limitation. Manual smoke step 12 inspects production response shape. If upstream Hermes changes the helper signature, drift will be caught at the smoke gate, not unit-test gate. Acceptable for slice 2; consider a CI-side integration test in slice 3 if drift becomes a recurring problem. |
| **Idempotent install with stale URL/branch/subdir silently retains old values** — second call with different args than first leaves manifest disagreeing with reality | Behavior is intentional (re-running install with same args should be a no-op); divergent re-run is undefined. README notes that `cc_import_install` should be called with the same URL/branch/subdir each time for a given plugin name. Slice 3's `sources.yaml` makes user intent canonical; this stops being a manifest concern. |
| **Partial tool registration on `register_tool` failure** — first tool registers, second fails, plugin half-registered | Hermes's `PluginManager` does not call `registry.deregister` on plugin-load failure; this is a Hermes-side limitation. Slice 2 doesn't paper over it. Documented in README. |
| **`provides_tools` drift between yaml and code** | Drift-guard test in `tests/test_tools.py` asserts yaml's `provides_tools` matches `[t[0] for t in tools.TOOLS]`. Catches name additions/removals; does not catch description drift. |

## Documentation / Operational Notes

- README updates land in Unit 5: Roadmap, Usage (with new flags),
  new "Agent tools" section, new "Security" section, and a
  one-liner about the `_plugins` install-cache index.
- No operational rollout — third-party plugin, opt-in install, no
  servers, no migrations. The only "migration" is an additive JSON
  key, applied lazily on next `import_plugin` save.
- `version: 0.2.0` in `plugin.yaml` marks the surface expansion.
- Slice 3's roadmap entry in README should now read: "sources.yaml
  + `/cc-import sync` + `on_session_start` opportunistic re-sync".
  Slice 3 will reframe `_plugins` as the install cache derived
  from sources.yaml.

## Sources & References

- **Origin document:** [docs/plans/2026-04-25-001-feat-cc-import-slice-1-plan.md](docs/plans/2026-04-25-001-feat-cc-import-slice-1-plan.md)
- Slice 1 README roadmap: `README.md:69-74`
- Reference plugin (tools): `~/.hermes/hermes-agent/plugins/spotify/`
  — `__init__.py:45-66`, `tools.py` (handlers + schemas, with
  verified `from tools.registry import …` import path), `plugin.yaml:6-13`
- Reference plugin (subcommand): `~/.hermes/hermes-agent/plugins/disk-cleanup/__init__.py:230-274`
- PluginContext API: `~/.hermes/hermes-agent/hermes_cli/plugins.py:205-232`
- Tool registry helpers: `~/.hermes/hermes-agent/tools/registry.py:292-309, 456-482`
- Plugin authoring guide (handler contract + prompt-cache rule):
  `~/.hermes/hermes-agent/AGENTS.md:287-291, 526-535, 628`
- Tool registration test pattern: `~/.hermes/hermes-agent/tests/hermes_cli/test_plugins.py:545-572`
- Slice 1 source: `__init__.py`, `cli.py`, `converter.py`,
  `plugin.yaml`, `tests/test_cli.py`, `tests/test_converter.py`
- Upstream (eventual contribution target): https://github.com/NousResearch/hermes-agent

### External research

- **Trail of Bits, MCP security analysis:** https://trailofbits.com/mcp/
- **Microsoft DevBlogs, CVE-2017-1000117 (git submodule recursion RCE):**
  https://devblogs.microsoft.com/devops/git-vulnerability-with-submodules/
- **OpenAI, "Designing agents to resist prompt injection":**
  https://openai.com/index/designing-agents-to-resist-prompt-injection/
- **Reinhard, "Why CLI tools are beating MCP for AI agents" (2026):**
  https://jannikreinhard.com/2026/02/22/why-cli-tools-are-beating-mcp-for-ai-agents/
- **Medium dminhk, "Designing CLIs for AI agents — patterns that work in 2026":**
  https://medium.com/@dminhk/designing-clis-for-ai-agents-patterns-that-work-in-2026-29ac725850de
- **JSON schema evolution (creekservice):**
  https://www.creekservice.org/articles/2024/01/08/json-schema-evolution-part-1.html
- **dev.to, "7 patterns that stop your AI agent from going rogue":**
  https://dev.to/pockit_tools/7-patterns-that-stop-your-ai-agent-from-going-rogue-in-production-5hb1
