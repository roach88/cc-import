---
title: "feat: cc-import Hermes plugin — slice 1 (CLI install + converter)"
type: feat
status: active
date: 2026-04-25
---

# feat: cc-import Hermes plugin — slice 1 (CLI install + converter)

## Overview

Slice 1 of three for `cc-import`, a Hermes Agent plugin that imports Claude
Code plugins (skills + agents) into a local Hermes install. This slice
delivers the minimum end-to-end vertical: `hermes cc-import install <git-url>`
clones a Claude Code plugin repo, copies its skills verbatim into
`$HERMES_HOME/skills/<plugin>/`, and translates each Claude Code agent
markdown file into a Hermes "delegation skill" written under
`$HERMES_HOME/skills/<plugin>/agents/<name>/SKILL.md`. Per-skill SHA-256
hashes are recorded in a manifest at `$HERMES_HOME/plugins/cc-import/state.json`
so subsequent re-imports are idempotent and preserve user edits.

The conversion algorithm is a faithful port of the existing battle-tested
converter at `~/Dev/hermes-depracted/scripts/plugin_sync.py` (which
just imported 36 skills + 48 agents from the EveryInc/compound-engineering
repo on this machine). What changes is the packaging: the script becomes a
Hermes plugin so `hermes plugins install roach88/cc-import` works, and the
plugin then exposes `hermes cc-import install <url>` as a CLI subcommand.

## Problem Frame

`hermes plugins install` expects a Hermes-native plugin shape (`plugin.yaml`
at repo root, optional `__init__.py`). Claude Code plugins use a completely
different layout (`skills/`, `agents/`, `commands/`, `hooks/`, `.mcp.json`).
A user who points `hermes plugins install` at a Claude Code plugin repo gets
a silent partial install: the tree lands in `$HERMES_HOME/plugins/<name>/`
but no skills are registered, no agents are translated, the plugin is inert.

This plugin closes that gap. Once installed, any Hermes user can run a
single command to import a Claude Code plugin and have its skills appear in
`hermes skills list` and its sub-agents appear as agent-callable
"delegation skills" mapped to the appropriate Hermes toolsets (`file`,
`terminal`, `web`).

## Requirements Trace

- R1. `hermes cc-import install <git-url>` clones the URL and imports its
  Claude Code skills + agents in one command
- R2. Skills land at `$HERMES_HOME/skills/<plugin>/<skill>/` (so
  `hermes skills list` discovers them) without modification
- R3. Agents land at `$HERMES_HOME/skills/<plugin>/agents/<agent>/SKILL.md`
  with frontmatter rewritten to a Hermes delegation skill (toolsets mapped
  per `TOOL_MAP`, body wrapped to invoke `delegate_task`)
- R4. Re-importing the same URL is idempotent — unchanged files are not
  rewritten; upstream-updated files are refreshed; user-modified files are
  preserved with a warning
- R5. State lives at `$HERMES_HOME/plugins/cc-import/state.json`; clone
  cache at `$HERMES_HOME/plugins/cc-import/clones/<plugin>/`
- R6. All paths derive from `$HERMES_HOME` (default `$HOME/.hermes`); no
  user-, host-, or project-specific values are baked in
- R7. The plugin layout exactly mirrors `plugins/disk-cleanup/` in the
  Hermes Agent repo, so a future `git mv` into `plugins/cc-import/` would
  slot in with no restructuring (upstream-PR readiness)
- R8. No core Hermes file is modified — Teknium's "no core mods" rule
  (AGENTS.md line 478)

## Scope Boundaries

- No agent-callable tools (`ctx.register_tool`) in this slice — the install
  flow is CLI-only
- No `on_session_start` hook — automatic re-sync is slice 3
- No `list`, `remove`, `update`, `sync`, or `sources` subcommands — only
  `install`
- No multi-source `sources.yaml` — one URL per invocation
- No CC-plugin-level features other than skills + agents: `commands/`,
  `hooks/`, `.mcp.json` are out of scope (would need a separate slice if
  ever wanted)

### Deferred to Separate Tasks

- Agent tools (`cc_import_install`, `cc_import_list`,
  `cc_import_remove`): slice 2 of this project
- `on_session_start` hook + `sources.yaml` + `sync` / `sources` /
  `list` / `remove` subcommands: slice 3 of this project
- RFC issue or PR to NousResearch/hermes-agent proposing this as a
  bundled plugin: separate workstream after slice 3

## Context & Research

### Relevant Code and Patterns

- `~/Dev/hermes-depracted/scripts/plugin_sync.py` — battle-tested
  source of the conversion algorithm. Functions to port: `clone_or_update`,
  `parse_frontmatter`, `render_frontmatter`, `translate_tools`,
  `build_delegation_skill`, `migrate_skill`, `migrate_agent`,
  `prune_removed`, `load_manifest`, `save_manifest`. `TOOL_MAP` and
  `TOOL_DROP` constants port verbatim.
- `~/.hermes/hermes-agent/plugins/disk-cleanup/` — closest analogue Hermes
  plugin. Mirror its file layout: `__init__.py` (with `register(ctx)`),
  `plugin.yaml`, `README.md`, supporting modules (`disk_cleanup.py` →
  `converter.py` for us).
- `~/.hermes/hermes-agent/plugins/spotify/__init__.py` — second example of
  `register(ctx)` shape; useful for verifying the registration call
  signatures.
- `~/.hermes/hermes-agent/hermes_cli/plugins.py` lines 196-460 —
  `PluginContext` API surface. For slice 1 we use only
  `ctx.register_cli_command(name, help, setup_fn, handler_fn, description)`
  (line 264).
- `~/.hermes/hermes-agent/AGENTS.md` lines 436-482 — official plugin
  authoring guidance and Teknium's "plugins MUST NOT modify core files"
  rule.
- `~/.hermes/hermes-agent/tests/plugins/test_disk_cleanup_plugin.py` —
  test scaffold pattern: `_isolate_env` fixture monkeypatches
  `HERMES_HOME` to `tmp_path / ".hermes"`; `_load_lib()` and
  `_load_plugin_init()` use `importlib.util` to import plugin modules
  directly from their repo path. Slice 1 mirrors this pattern.
- `~/.hermes/hermes-agent/.github/workflows/tests.yml` — CI shape for
  Hermes itself: `astral-sh/setup-uv@v5` + `uv venv` + `uv pip install -e
  ".[dev]"` + `python -m pytest`. Our CI follows the same shape (minus the
  Hermes-specific `--ignore=tests/integration` flag).

### Institutional Learnings

- The local converter has already proven correct against EveryInc's
  compound-engineering repo (36 skills + 48 agents imported). The algorithm
  is settled; this slice is about *packaging* it as a plugin, not
  rediscovering the algorithm.
- Hermes plugins use `importlib.util` dynamic loading in tests because the
  `plugins/<name>/` layout is not a Python package on `sys.path`. Our
  tests must adopt the same pattern even though our plugin is in a
  standalone repo, because the eventual upstream `git mv` into
  `plugins/cc-import/` must keep the test suite working.
- Hermes's `pyproject.toml` skips ruff (`[tool.ruff]` with
  `exclude = ["*"]`). For our standalone repo we configure ruff strictly
  — a Hermes maintainer accepting the upstream PR can choose to retain or
  relax our config.

### External References

None used. Local Hermes source is authoritative for plugin conventions.

## Key Technical Decisions

- **Plugin layout = identical to `plugins/disk-cleanup/`.** Repo root
  contains `plugin.yaml`, `__init__.py`, `README.md`, `cli.py`,
  `converter.py`, `tests/`. Rationale: future upstream PR is one `git mv`.
- **State + clone cache live under `$HERMES_HOME/plugins/cc-import/`,
  not `$HERMES_HOME/.plugin-sync-cache/`.** Rationale: keeps the
  plugin's runtime state inside its own dir, making it owner-clear.
  Skill output still lands at `$HERMES_HOME/skills/<plugin>/` because
  that's where Hermes discovers skills.
- **One URL per `install` invocation.** Rationale: matches `hermes
  plugins install`'s shape (1 URL → 1 plugin). Multi-source batching
  via `sources.yaml` is slice 3 (where it earns its complexity).
- **Conversion logic ported verbatim from `plugin_sync.py`.** Rationale:
  the algorithm is correct; rewriting it would risk regressions. Tests
  freeze the behavior so refactors later don't drift.
- **Astral toolchain.** `uv` for env + deps, `ruff` for lint+format, `ty`
  for typecheck, `pytest` for tests. Per Q's `~/.claude/rules/python-tooling.md`
  rule: default to Astral for any new Python project.
- **Strict TDD.** Every feature-bearing implementation unit lands as one
  failing-test commit, then one make-it-pass commit. Pure helpers
  (frontmatter, tool translation, delegation skill builder) get
  table-driven tests. I/O units (clone, migrate, manifest) use a tmp
  directory `_isolate_env` fixture.
- **Test isolation strategy for git operations.** Use a real local bare
  git repo as a `file://` upstream URL. No subprocess monkeypatching —
  exercises the real `git clone`/`git fetch`/`git reset` paths.
- **Skill registration model.** We write `SKILL.md` files to
  `$HERMES_HOME/skills/<plugin>/<skill>/`, *not* via
  `ctx.register_skill()`. Reason: file-on-disk skills survive plugin
  uninstall and Hermes restarts; ephemeral in-process registration would
  require re-importing on every Hermes startup.

## Open Questions

### Resolved During Planning

- *Where does the manifest live?* → `$HERMES_HOME/plugins/cc-import/state.json`
  (decided in the design conversation; reflected in R5).
- *How are CC tools mapped to Hermes toolsets?* → Use `TOOL_MAP` and
  `TOOL_DROP` from `plugin_sync.py` verbatim. `Read/Grep/Glob/Edit/Write/NotebookEdit`
  → `file`; `Bash` → `terminal`; `WebFetch/WebSearch` → `web`; `Task`
  dropped.
- *Default toolsets for an agent with no `tools:` in frontmatter?* → `["file", "web"]`
  (matches the existing converter's behavior).

### Deferred to Implementation

- Exact name and signature of the orchestrator function
  (`import_plugin` vs `sync_plugin` vs `install_plugin`). Decide in
  Unit 5 once the helpers' shape is stable.
- Whether `migrate_agent` needs a separate `*.agent` directory suffix
  (current converter writes to `agents/<name>.agent/SKILL.md` — odd but
  works). Defer to Unit 4; adopt whatever the test fixtures dictate.
- Exit code conventions for the CLI handler on partial failure
  (e.g. clone OK but one agent's frontmatter is malformed). Decide in
  Unit 6 against test scenarios.

## Output Structure

```
cc-import/
├── .github/
│   └── workflows/
│       └── ci.yml
├── .gitignore
├── docs/
│   └── plans/
│       └── 2026-04-25-001-feat-cc-import-slice-1-plan.md  (this file)
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_converter.py
│   └── test_cli.py
├── LICENSE                      (already exists, MIT)
├── README.md                    (initially auto-created by gh, rewritten in Unit 7)
├── __init__.py                  (register(ctx) entry point)
├── cli.py                       (argparse setup + handler)
├── converter.py                 (core conversion logic)
├── plugin.yaml                  (Hermes plugin manifest)
└── pyproject.toml               (Astral toolchain config)
```

## Implementation Units

- [ ] **Unit 1: Project scaffolding**

**Goal:** Establish the repo's tooling and metadata so subsequent units
can run tests, lint, and CI immediately.

**Requirements:** R6, R7

**Dependencies:** None.

**Files:**
- Create: `pyproject.toml`
- Create: `plugin.yaml`
- Create: `.gitignore`
- Create: `.github/workflows/ci.yml`
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py` (fixture: `_isolate_env` matching the
  Hermes test pattern)
- Modify: `README.md` (replace gh-auto-generated stub with one-paragraph
  placeholder; full Ankane-style README is Unit 7)

**Approach:**
- `pyproject.toml`: project metadata (`name = "cc-import"`,
  `version = "0.1.0"`, `authors = [{ name = "Tyler Barstow" }]`,
  `license = { text = "MIT" }`, `requires-python = ">=3.11"`).
  Dependencies: `pyyaml>=6.0,<7`. Dev extras: `pytest>=8`, `pytest-mock`,
  `ruff`, `ty`. Build backend: `hatchling`.
- `plugin.yaml`: matches disk-cleanup's compact shape — `name`, `version`,
  `description`, `author: Tyler Barstow`. No `provides_tools` or `hooks`
  yet (those land in slices 2 and 3).
- `.github/workflows/ci.yml`: ubuntu-latest, `astral-sh/setup-uv@v5`,
  `uv venv .venv --python 3.11`, `uv pip install -e ".[dev]"`, then run
  `pytest -q`, `ruff check`, `ruff format --check`, `ty check`.
- `tests/conftest.py`: ports the `_isolate_env` fixture from
  `tests/plugins/test_disk_cleanup_plugin.py` (creates `tmp_path/.hermes`,
  `monkeypatch.setenv("HERMES_HOME", ...)`, yields the path).

**Patterns to follow:**
- `~/.hermes/hermes-agent/plugins/disk-cleanup/plugin.yaml` (manifest
  shape)
- `~/.hermes/hermes-agent/.github/workflows/tests.yml` (CI shape, minus
  Hermes-specific `--ignore` flags)
- `~/.hermes/hermes-agent/tests/plugins/test_disk_cleanup_plugin.py`
  (the `_isolate_env` fixture)

**Test scenarios:**
- Test expectation: none — scaffolding-only unit. Verification is
  "tooling works" (Unit 2's tests run + CI green when triggered), not
  behavior.

**Verification:**
- `uv venv .venv && uv pip install -e ".[dev]"` succeeds locally
- `ruff check` produces no errors against an empty `cc-import/` (because
  there's no Python yet — Unit 2 onward fills it in)
- `pytest -q` exits 0 (no tests collected yet, but no errors)
- `git push` triggers CI; CI passes

---

- [ ] **Unit 2: converter.py — pure helpers**

**Goal:** Implement and TDD the four pure helper functions of the
converter: frontmatter parse/render, CC-tool translation, delegation-skill
construction.

**Requirements:** R3 (delegation-skill shape)

**Dependencies:** Unit 1.

**Files:**
- Create: `converter.py` (only the four pure helpers in this unit;
  remaining converter functions land in Units 3-5)
- Create: `tests/test_converter.py` (only the tests for these four
  helpers in this unit)

**Approach:**
- Port `parse_frontmatter`, `render_frontmatter`, `translate_tools`,
  `TOOL_MAP`, `TOOL_DROP`, `build_delegation_skill` from
  `plugin_sync.py` lines 88-249.
- These are all pure functions: in → out, no I/O. Table-driven tests
  cover them efficiently.

**Execution note:** Strict TDD. For each function: write the failing
test class first, run pytest to see it fail, implement the function,
run pytest to see it pass, commit. One commit per function.

**Patterns to follow:**
- `parse_frontmatter` and `render_frontmatter` use a regex on the
  `---\n...\n---\n` boundary; preserve that exact pattern from
  `plugin_sync.py` so existing serialized SKILL.md files round-trip.
- `translate_tools` accepts `None`, `str` (comma-separated), or
  `list[str]`. Returns `(toolsets: list[str], unknown: list[str])`.
  Default toolsets when input is empty/missing: `["file", "web"]`.
- `build_delegation_skill` produces frontmatter with keys `name`,
  `description`, `version: "1.0.0"`, `metadata.hermes.{source,
  source_kind, upstream_name, toolsets}`. Body wraps the original CC
  body under a "## Persona" heading.

**Test scenarios:**

`parse_frontmatter`:
- Happy path: `"---\nname: foo\ndescription: bar\n---\nbody text"` →
  `({"name": "foo", "description": "bar"}, "body text")`
- Edge case: no frontmatter at all → `({}, full_text)`
- Edge case: empty frontmatter `"---\n---\nbody"` → `({}, "body")`
- Error path: malformed YAML inside frontmatter → `({}, original_text)`
  (graceful, not raise)
- Edge case: trailing newline after closing `---` is consumed correctly

`render_frontmatter`:
- Round-trip: `parse_frontmatter(render_frontmatter(fm, body))` returns
  equivalent `(fm, body)` for table of inputs
- Happy path: empty `body` → still produces valid `---\n...\n---\n\n`
  output

`translate_tools`:
- Happy path: `["Read", "Grep", "Bash"]` → `(["file", "terminal"], [])`
  (file deduped despite two source tools mapping to it)
- Edge case: `None` → `(["file", "web"], [])`
- Edge case: `""` → `(["file", "web"], [])`
- Edge case: `[]` → `(["file", "web"], [])`
- Edge case: comma-string `"Read, Bash, WebSearch"` →
  `(["file", "terminal", "web"], [])`
- Drops `Task`: `["Read", "Task"]` → `(["file"], [])` (Task neither in
  toolsets nor in unknown)
- Unknown tool: `["Read", "MagicTool"]` → `(["file"], ["MagicTool"])`
- All-unknown input: `["Magic", "Mystery"]` → `(["file", "web"],
  ["Magic", "Mystery"])` (default toolsets when nothing maps)

`build_delegation_skill`:
- Happy path: CC frontmatter `{"name": "secsentinel", "description":
  "Audit security", "tools": "Read,Bash"}` and body `"You are a
  security reviewer..."` → output frontmatter has
  `name: "compound-engineering/agent/secsentinel"`,
  `description: "Audit security"`, `metadata.hermes.toolsets: ["file",
  "terminal"]`, body has "Delegation skill" header + "## Persona" + the
  original body verbatim
- Edge case: CC frontmatter without `description` → output description
  falls back to `"Delegate to the <name> sub-agent persona."`
- Edge case: tools include unknown → output body includes a
  "⚠️ Upstream tools not mapped to Hermes toolsets: ..." note
- Edge case: empty CC body → output still well-formed (just a Persona
  heading with empty content)
- Output frontmatter has `metadata.hermes.source` set to the plugin
  argument, `source_kind: "agent"`, `upstream_name` set to the agent
  name argument

**Verification:**
- `pytest tests/test_converter.py -q` passes
- `ruff check converter.py tests/test_converter.py` passes
- `ty check converter.py` passes

---

- [ ] **Unit 3: converter.py — manifest I/O + clone_or_update**

**Goal:** Add the persistence layer and the git wrapper that the
migration functions in Unit 4 will depend on.

**Requirements:** R4 (manifest enables idempotency), R6 (paths from
`HERMES_HOME`)

**Dependencies:** Unit 2.

**Files:**
- Modify: `converter.py` (append `load_manifest`, `save_manifest`,
  `sha256_file`, `sha256_bytes`, `clone_or_update`)
- Modify: `tests/test_converter.py` (append manifest + clone tests)

**Approach:**
- Port `load_manifest`, `save_manifest`, `sha256_bytes`, `sha256_file`
  from `plugin_sync.py` lines 61-85.
- Port `clone_or_update` from `plugin_sync.py` lines 112-126.
- Manifest path is parameterized — accepted as an argument, not derived
  from a module-level global. (Departure from `plugin_sync.py`'s
  module-level `MANIFEST` constant. Reason: testability and avoiding the
  hardcoding that bit us in the original converter.)
- For tests, `clone_or_update` runs against a real local bare git repo
  on disk inside `tmp_path`. No subprocess monkeypatching.

**Execution note:** Strict TDD per function.

**Patterns to follow:**
- `plugin_sync.py:61-126` — verbatim except for parameterized paths
- For the bare-repo fixture in tests: `git init --bare`, then add a
  commit by cloning + committing + pushing

**Test scenarios:**

`load_manifest`:
- Happy path: existing valid JSON file → returns dict with parsed
  content
- Edge case: nonexistent path → returns `{}`
- Error path: corrupt JSON → returns `{}` and emits warning (assert
  `caplog`); does not raise

`save_manifest`:
- Happy path: writes JSON with `indent=2` and `sort_keys=True` so
  diffs are stable
- Round-trip: `load_manifest(save_manifest(...))` returns equivalent
  data
- Creates parent dirs if missing

`sha256_file` / `sha256_bytes`:
- Happy path: known input → known SHA-256 hex digest (use one
  hand-computed reference value to verify the implementation)
- `sha256_file` reads bytes (not text) so line-ending differences are
  preserved

`clone_or_update`:
- Happy path: dest does not exist → calls `git clone --depth=1
  --branch <branch> <url> <dest>`; dest exists with `.git/` afterward
- Happy path: dest already a checkout of the same upstream → calls
  `git fetch` + `git reset --hard origin/<branch>`; works after upstream
  commits (test makes a new commit in the bare repo, calls
  `clone_or_update` again, asserts dest's HEAD now matches the new
  commit)
- Edge case: dest exists but is a non-git directory → removed and
  re-cloned; original contents are gone
- Error path: invalid branch name → `subprocess.CalledProcessError`
  propagates; manifest is unchanged

**Verification:**
- `pytest tests/test_converter.py -q` (including new tests) passes
- All git operations exercised against a real local `file://` bare repo
  inside the test's `tmp_path`

---

- [ ] **Unit 4: converter.py — migration core**

**Goal:** Implement the three migration functions that perform the
real work: skill copy, agent translation, and prune-removed cleanup.
Each preserves user-modified files via the manifest.

**Requirements:** R2, R3, R4

**Dependencies:** Units 2 + 3.

**Files:**
- Modify: `converter.py` (append `migrate_skill`, `migrate_agent`,
  `prune_removed`)
- Modify: `tests/test_converter.py` (append migration tests)

**Approach:**
- Port from `plugin_sync.py` lines 133-321. Behavior is identical;
  signatures change to accept `skills_dir: Path` and `manifest: dict`
  as explicit args (instead of reading module-level globals).
- The "user-modified" detection uses three SHA hashes: `origin_hash`
  (current upstream), `entry["origin_hash"]` (last-known upstream from
  manifest), `local_hash` (file on disk). Decision matrix:

| `local == origin` | `local == prior_origin` | Action |
|---|---|---|
| true | (any) | UNCHANGED, refresh manifest entry |
| false | true | COPY (upstream updated, user hasn't edited) |
| false | false | SKIP with warning (user-modified) |

**Execution note:** Strict TDD. Each migration function gets its own
test class with the four cases (first-write, unchanged, upstream-update,
user-modified). `prune_removed` adds a fifth: stale-entry-removal vs.
stale-entry-kept-because-user-modified.

**Patterns to follow:**
- `plugin_sync.py:133-321` — port verbatim except for the
  parameterized `skills_dir`/`manifest` signatures
- Logging shape: `logger.info("COPY skill: %s", key)`,
  `logger.warning("SKIP (user-modified): %s", key)` etc., so output
  is grep-able

**Test scenarios:**

`migrate_skill`:
- Happy path (first write): src has `SKILL.md` + `helper.py` → dest gets
  the whole tree, manifest gains entry `{plugin, kind: "skill",
  source_path, origin_hash}`
- Idempotent rerun: re-call with same inputs → no file mtime change,
  manifest entry unchanged
- Upstream update: change `SKILL.md` content in src, re-call → dest
  reflects new content, manifest `origin_hash` updated
- User-modified preserved: user edits dest's `SKILL.md`, src changes
  too, re-call → SKIP logged, dest unchanged, manifest entry still
  points at prior origin_hash
- Edge case: src dir without `SKILL.md` → no-op, no manifest entry, no
  copy

`migrate_agent`:
- Happy path: src `.md` with valid CC frontmatter → dest
  `<plugin>/agents/<name>.agent/SKILL.md` exists with translated
  content (matches `build_delegation_skill` output for those inputs);
  manifest entry `{plugin, kind: "agent", source_path, origin_hash}`
- Idempotent rerun: same inputs → no rewrite, manifest unchanged
- Upstream update: change agent body in src → dest content reflects
  new body inside "## Persona", manifest origin_hash updated
- User-modified preserved: user edits dest's `SKILL.md`, src changes →
  SKIP logged
- Edge case: agent without `name:` in frontmatter → uses src filename
  stem
- Edge case: agent with no frontmatter at all → still produces output
  (defaults applied)

`prune_removed`:
- Happy path: manifest has entry not in `seen_keys`, file on disk
  unmodified (matches manifest origin_hash) → file deleted, manifest
  entry removed
- User-modified kept: stale entry where local hash differs from
  `entry["origin_hash"]` → KEEP logged, file remains, manifest entry
  retained
- Edge case: stale entry with file already gone → manifest entry
  removed cleanly (no error)
- Edge case: empty `seen_keys` → all entries for the plugin pruned
  (subject to user-modified check)

**Verification:**
- `pytest tests/test_converter.py -q` passes
- `ty check converter.py` passes
- `ruff check converter.py tests/test_converter.py` passes

---

- [ ] **Unit 5: converter.py — `import_plugin` orchestrator**

**Goal:** Wire the helpers into a single end-to-end function that the
CLI handler will call. This is the public API of `converter.py`.

**Requirements:** R1, R2, R3, R4, R5, R6

**Dependencies:** Units 2 + 3 + 4.

**Files:**
- Modify: `converter.py` (append `import_plugin` plus a small
  `ImportSummary` dataclass / TypedDict)
- Modify: `tests/test_converter.py` (append integration tests using a
  fixture upstream repo containing real skills + agents)

**Approach:**
- Signature (final name TBD — see "Deferred to Implementation"):

  ```
  def import_plugin(
      git_url: str,
      *,
      branch: str = "main",
      subdir: str = "",
      hermes_home: Path | None = None,
  ) -> ImportSummary:
  ```

  When `hermes_home is None`, derive from
  `os.environ.get("HERMES_HOME", Path.home() / ".hermes")`.

- Compute paths:
  - `skills_dir = hermes_home / "skills"`
  - `state_dir = hermes_home / "plugins" / "cc-import"`
  - `clone_dir = state_dir / "clones"`
  - `manifest_path = state_dir / "state.json"`
- Plugin name: pull from `plugin.json` at the cloned repo root if
  present (Claude Code convention); fall back to the trailing path
  component of the git URL (sans `.git`).
- Walk `<repo>/<subdir>/skills/*/SKILL.md` → `migrate_skill`
- Walk `<repo>/<subdir>/agents/*.md` → `migrate_agent`
- Call `prune_removed` with `seen_keys` accumulated during the walks
- Save manifest
- Return `ImportSummary(plugin: str, skills_imported: int,
  agents_translated: int, skills_unchanged: int, agents_unchanged: int,
  skipped_user_modified: list[str])`

**Execution note:** Strict TDD. Build the integration tests first
against a fixture upstream repo committed under `tests/fixtures/`.
The fixture repo has the minimum CC plugin shape: `skills/foo/SKILL.md`,
`skills/bar/SKILL.md`, `agents/baz.md`, `plugin.json` declaring
`name: "fixture-plugin"`. `conftest.py` builds a bare clone for each
test using `git init --bare` and pushes the fixture's content.

**Test scenarios:**
- Happy path (fresh import): fixture upstream has 2 skills + 1 agent →
  3 manifest entries, 2 SKILL.md under
  `$HERMES_HOME/skills/fixture-plugin/`, 1 SKILL.md under
  `$HERMES_HOME/skills/fixture-plugin/agents/baz.agent/`. Returned
  summary has `skills_imported == 2`, `agents_translated == 1`.
- Idempotent rerun: second invocation against same upstream → all
  files unchanged (compare mtimes), summary has `skills_unchanged == 2`,
  `agents_unchanged == 1`, `skills_imported == 0`.
- Upstream update: between invocations a skill is edited in the bare
  repo → second run picks up the edit, manifest origin_hash updates,
  summary reports 1 skill re-imported.
- User-modified preservation: user edits a translated agent's
  SKILL.md, upstream also changes → second run reports the agent in
  `skipped_user_modified`, file is not overwritten.
- Subdir support: fixture has the plugin nested under
  `plugins/fixture-plugin/`; `import_plugin(..., subdir="plugins/fixture-plugin")`
  imports correctly.
- HERMES_HOME override: passing `hermes_home=tmp_path/"alt"` writes
  there even when env var is set elsewhere.
- Error path: git clone fails (invalid branch) → raises
  `subprocess.CalledProcessError`; no partial state left in
  `$HERMES_HOME/skills/<plugin>/` (manifest unchanged from before the
  call).

**Verification:**
- `pytest tests/test_converter.py -q` passes (~25 tests across
  Units 2-5)
- A manual end-to-end against `https://github.com/EveryInc/compound-engineering-plugin.git`
  with `hermes_home=$(mktemp -d)/.hermes` produces the same 36+48
  result we already verified with `plugin_sync.py`

---

- [ ] **Unit 6: Plugin wiring — `__init__.py` register(ctx) + cli.py install handler**

**Goal:** Make `hermes plugins install file://$(pwd)` followed by
`hermes cc-import install <git-url>` end-to-end work on a developer
machine.

**Requirements:** R1, R7 (disk-cleanup-shape mirror)

**Dependencies:** Units 1-5.

**Files:**
- Create: `__init__.py` (registers the CLI subcommand only; tools and
  hooks land in slices 2 + 3)
- Create: `cli.py` (the argparse `setup_fn` and `cmd_install` handler)
- Create: `tests/test_cli.py`
- Modify: `tests/test_converter.py` (no changes — this unit's tests live
  in `test_cli.py`)

**Approach:**
- `__init__.py` defines `register(ctx)`. Body:

  ```
  def register(ctx) -> None:
      from . import cli
      ctx.register_cli_command(
          name="cc-import",
          help="Import Claude Code plugins (skills + agents) into Hermes.",
          setup_fn=cli.setup_parser,
          handler_fn=cli.cmd_install,  # default handler when no subcommand given
          description="..."
      )
  ```

  The `handler_fn` parameter is set as the default dispatcher via
  argparse's `set_defaults(func=...)` per `plugins.py:282`.

- `cli.py` defines:
  - `setup_parser(parser)` → adds an `install` subparser with one
    positional `git_url` and `--branch`/`--subdir` options.
  - `cmd_install(args)` → calls `converter.import_plugin(args.git_url,
    branch=args.branch, subdir=args.subdir)`, prints a single-line
    summary, returns 0 on success.
- Tests use a mock `ctx` (a simple class with `register_cli_command`
  capturing args). They do NOT spin up the real Hermes plugin loader.

**Execution note:** Strict TDD. Tests for `register(ctx)` go first
(verify wiring), then `cmd_install` (verify it dispatches correctly).

**Patterns to follow:**
- `~/.hermes/hermes-agent/plugins/disk-cleanup/__init__.py` — same
  shape: top-level `register(ctx)`, subcommand handlers in
  `cli.py` / submodule.
- `~/.hermes/hermes-agent/hermes_cli/plugins.py` line 264: signature
  of `register_cli_command` is `(name, help, setup_fn, handler_fn,
  description)`.

**Test scenarios:**

`__init__.register`:
- Happy path: `register(mock_ctx)` calls
  `mock_ctx.register_cli_command` exactly once with
  `name="cc-import"`, a callable `setup_fn`, and a callable
  `handler_fn`. No other `register_*` methods are called (no tools,
  no hooks in slice 1).
- The `setup_fn` is `cli.setup_parser`; the `handler_fn` is
  `cli.cmd_install`.

`cli.setup_parser`:
- Happy path: given a real `argparse.ArgumentParser`, after
  `setup_parser(p)`, `p.parse_args(["install", "URL"]).git_url ==
  "URL"`, `branch == "main"`, `subdir == ""`.
- Happy path: with explicit options,
  `p.parse_args(["install", "URL", "--branch", "dev", "--subdir",
  "plugins/foo"]).branch == "dev"` etc.
- Edge case: `p.parse_args(["install"])` raises `SystemExit` (missing
  positional).

`cli.cmd_install`:
- Happy path: `cmd_install(args)` calls
  `converter.import_plugin(args.git_url, branch=args.branch,
  subdir=args.subdir)` exactly once. Returns 0. Prints a summary
  containing the plugin name + counts. (Use `monkeypatch` to stub
  `converter.import_plugin` returning a known `ImportSummary`.)
- Error path: `import_plugin` raises
  `subprocess.CalledProcessError` → `cmd_install` returns non-zero
  exit code, prints a friendly error mentioning the URL, does not
  re-raise.
- Error path: `import_plugin` raises arbitrary exception → caught,
  printed, non-zero exit. Test verifies stderr content.

**Verification:**
- `pytest tests/test_cli.py -q` passes
- `pytest -q` (whole suite) passes
- Manual smoke: `hermes plugins install file:///$(pwd)` then
  `hermes cc-import install https://github.com/EveryInc/compound-engineering-plugin.git`
  reproduces the 36 skills + 48 agents we got from `plugin_sync.py`

---

- [ ] **Unit 7: README polish + manual smoke verification**

**Goal:** Replace the placeholder README with an Ankane-style document
that a Hermes user can read end-to-end. Run the full manual smoke that
slice 1 promises.

**Requirements:** R1, R7

**Dependencies:** Units 1-6.

**Files:**
- Modify: `README.md`

**Approach:**
- Sections (Ankane convention):
  1. One-paragraph hero ("Import Claude Code plugins into Hermes Agent.")
  2. Installation: `hermes plugins install roach88/cc-import`
  3. Usage: `hermes cc-import install <git-url> [--branch <name>] [--subdir <path>]` + a worked example with `EveryInc/compound-engineering-plugin`
  4. How it works: skills copied verbatim → `$HERMES_HOME/skills/<plugin>/`; agents translated to delegation skills → `$HERMES_HOME/skills/<plugin>/agents/`
  5. State: where the manifest lives, how user edits are preserved
  6. Roadmap: slice 2 (agent tools), slice 3 (auto-sync hook + sources.yaml)
  7. Contributing: `uv venv .venv && uv pip install -e ".[dev]"`, `pytest`
  8. License: MIT
- Avoid AI-slop tells (em-dashes overused, "Furthermore," "In conclusion,",
  generic blog-post tone). Imperative voice. Short sentences.

**Test scenarios:**
- Test expectation: none — README is documentation, not behavior.

**Verification:**
- Manual smoke (record output):
  - `hermes plugins install file://$(pwd)` from the repo root succeeds
  - `hermes cc-import install https://github.com/EveryInc/compound-engineering-plugin.git`
    runs and reports `skills_imported=36, agents_translated=48`
  - `hermes skills list | grep compound-engineering | wc -l` → 84
  - Re-run install → all unchanged
- README renders correctly on github.com (open the repo URL after push)
- `gh pr create` is NOT invoked (slice 1 lands on `main` directly per
  the small-repo-solo-author convention; slice 2+ may use PRs)

## System-Wide Impact

- **Interaction graph:** This plugin only adds a new CLI subcommand
  (`hermes cc-import`). It does not touch any existing Hermes tool,
  hook, or skill discovery path. It writes to
  `$HERMES_HOME/skills/<plugin>/` (which Hermes already discovers via
  its skill scanner) and to `$HERMES_HOME/plugins/cc-import/`
  (private state). No callbacks, no middleware.
- **Error propagation:** Errors from `git clone`, manifest I/O, or
  individual file conversion bubble up to `cmd_install`, which catches
  them, logs a friendly message, and returns a non-zero exit code. No
  partial-state cleanup needed because `prune_removed` only fires on
  success.
- **State lifecycle risks:** None new. The manifest at
  `$HERMES_HOME/plugins/cc-import/state.json` is the single source of
  truth for what's been imported. If it's deleted, the next run treats
  every existing skill on disk as "unmanaged" and the import becomes
  a fresh full import (with the user-modified detection still
  protecting locally-edited files via the
  `local_hash != entry["origin_hash"]` check).
- **API surface parity:** N/A — slice 1 introduces only one subcommand
  on one surface (CLI). Slices 2 + 3 add agent tools and a hook.
- **Integration coverage:** Unit 5's integration tests use real
  `git init --bare` repos in tmp dirs; Unit 7's manual smoke is the
  full end-to-end. Together these prove the cross-layer behavior unit
  tests alone don't.
- **Unchanged invariants:** Hermes core code (`run_agent.py`,
  `cli.py`, `gateway/run.py`, `hermes_cli/main.py`) is untouched —
  honors Teknium's "no core mods" rule.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Plugin discovery requires `~/.hermes/plugins/cc-import/` to be importable as Python; `hermes plugins install file://...` may copy without preserving `__init__.py` discovery semantics | Manual smoke test in Unit 7 explicitly exercises `hermes plugins install file://...` followed by a CLI subcommand call; failure here surfaces immediately |
| `clone_or_update` writes inside `$HERMES_HOME/plugins/cc-import/clones/` — name collision with a future user who runs `hermes plugins install <something-else>` is impossible (different parent dir) but worth flagging | Use a hidden subdir (`clones/` is already nested two levels deep inside our plugin's state dir; safe) |
| `git_url` is user input passed to `subprocess.run(["git", "clone", ...])` as a positional argument — argument injection is impossible (subprocess receives a list, not a shell), but a malicious git URL with hooks could execute arbitrary code at clone time | Documented in README's Security section: only install plugins from sources you trust, same as `hermes plugins install` itself |
| Astral `ty` is pre-1.0 (0.0.x); breaking config changes possible | Pin a lower-bound version in dev deps; CI will fail loudly on a breaking change so it's caught early |
| Test fixture's local bare repos rely on `git` being installed in CI | CI step `apt-get install -y git` if not already present (it is on `ubuntu-latest`) |

## Documentation / Operational Notes

- README polish is Unit 7. After slice 1 ships, update the README to
  preview slices 2 + 3 in the Roadmap section so a reader knows where
  this is going.
- No operational rollout needed — third-party plugin, opt-in install,
  no servers, no migrations.

## Sources & References

- Origin design conversation: see git history of this branch for the
  back-and-forth that produced the 4 design questions and Q's answers.
- Reference converter (battle-tested): `~/Dev/hermes-depracted/scripts/plugin_sync.py`
- Reference plugin (closest analogue): `~/.hermes/hermes-agent/plugins/disk-cleanup/`
- PluginContext API: `~/.hermes/hermes-agent/hermes_cli/plugins.py:196-460`
- Plugin authoring guide: `~/.hermes/hermes-agent/AGENTS.md:436-482`
- Test pattern: `~/.hermes/hermes-agent/tests/plugins/test_disk_cleanup_plugin.py`
- CI pattern: `~/.hermes/hermes-agent/.github/workflows/tests.yml`
- Upstream repo (eventual contribution target): `https://github.com/NousResearch/hermes-agent`
