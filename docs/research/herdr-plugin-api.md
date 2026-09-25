# Herdr 0.9.1 plugin extension surface — verified reference

**Binary under test:** `/home/ed/.local/bin/herdr`, `herdr 0.9.1`, protocol 22, schema_version 1.
**Server:** running, socket `/home/ed/.config/herdr/herdr.sock`.

Sources, in order of authority:

1. The installed CLI/binary (`--help`, live API calls, `herdr api schema --json`, `strings` on the binary). **Wins any disagreement.**
2. Docs pinned to the release, from `https://herdr.dev/llms.txt`:
   `https://raw.githubusercontent.com/herdrdev/herdr/v0.9.1/docs/next/website/src/content/docs/{plugins,configuration,cli-reference,socket-api,agents,marketplace}.mdx`
   and `.../src/data/config-reference.json`.

Everything below labelled **VERIFIED** was produced by running a command against the live
0.9.1 session. **DOC-ONLY** means the docs state it and nothing in the CLI contradicts it,
but it was not exercised (usually because doing so requires linking a plugin, which was out
of scope). **UNVERIFIED** means genuinely unknown.

Probing was done in a disposable scratch tab (`w7:t3` / pane `w7:pC`), since removed
(`herdr tab close w7:t3` → `{"type":"ok"}`). No config was modified; config experiments used
`HERDR_CONFIG_PATH` against a copy in `/tmp`.

---

## 1. `herdr-plugin.toml` manifest

### Top-level scalar fields

| Field | Required | Notes |
|---|---|---|
| `id` | yes | ASCII letters, digits, `.`, `:`, `_`, `-`; ≤120 chars. Binary error string: *"plugin id must be non-empty, <= 120 characters, and contain only ASCII letters, digits, colon, dot, underscore, or hyphen"* (`invalid_plugin_id`). |
| `name` | yes | Display name. |
| `version` | yes | Plugin's own version string. |
| `min_herdr_version` | yes | Semver. Binary error: *"plugin min_herdr_version must be a semantic version like …"* (`invalid_plugin_min_herdr_version`). Link is refused if newer than the running binary. |
| `description` | no | |
| `platforms` | no (warn) | `["linux","macos","windows"]`. Omitting it links with a warning. Item-level `platforms` override the top-level list; mismatch at invoke time → `platform_unsupported`. |

### Array-of-table sections

The authoritative list comes from the binary's `InstalledPluginInfo` serde field list:

```
plugin_id  name  version  min_herdr_version  manifest_path  plugin_root  enabled
description  platforms  build  startup  actions  events  panes  link_handlers  source  warnings
```

So the manifest tables are exactly: **`[[build]]`, `[[startup]]`, `[[actions]]`, `[[events]]`,
`[[panes]]`, `[[link_handlers]]`**.

- There is **no `[[commands]]` table** and **no plugin config schema section** in 0.9.1.
  Plugin config is whatever files you choose to put under `HERDR_PLUGIN_CONFIG_DIR`; Herdr
  neither parses, validates, nor migrates them (`plugins.mdx`: *"There is no Herdr-managed
  plugin storage API in v1"*).
- Per-table serde field lists (from the binary):
  - `PluginManifestBuild`: 2 fields → `command`, `platforms`
  - `PluginManifestStartup`: 2 fields → `command`, `platforms`
  - `PluginManifestAction`: 6 fields → `id`, `title`, `description`, `contexts`, `command`, `platforms`
  - `PluginManifestEventHook`: 3 fields → `on`, `command`, `platforms`
  - `PluginManifestPane`: 8 fields → `id`, `title`, `description`, `placement`, `width`, `height`, `command`, `platforms`
  - `PluginManifestLinkHandler`: 5 fields → `id`, `title`, `pattern`, `action`, `platforms`
- `contexts` values (binary enum blob `globalworkspacetabselection`): **`global`, `workspace`, `tab`, `selection`**.
- `placement` values (binary enum blob `overlaypopupsplitzoomed`): **`overlay`, `popup`, `split`, `zoomed`**
  — plus `tab`, accepted by `plugin.pane.open` (see §5).
- Action/pane/link-handler ids are *local* ids: ASCII letters, digits, `:`, `_`, `-`, **no dots**;
  unique per id-kind. Herdr qualifies actions as `<plugin.id>.<action>`.
- `command` is an **argv array**, not a shell string. No shell, no expansion, no globbing.

### Live proof that these are the fields Herdr actually stores

**VERIFIED** — `herdr plugin list --json` (existing local plugin, real output, reformatted):

```json
{
  "plugin_id": "herdbake",
  "name": "Herdbake",
  "version": "0.3.0",
  "min_herdr_version": "0.9.0",
  "description": "Routes bitbake's menuconfig/devshell/ccmake terminal spawns into Herdr popups, splits, or tabs instead of external terminal windows.",
  "platforms": ["linux", "macos"],
  "enabled": true,
  "manifest_path": "/home/ed/herdbake/herdr-plugin.toml",
  "plugin_root": "/home/ed/herdbake",
  "source": { "kind": "local" },
  "actions": [
    { "id": "setup",  "title": "Herdbake: set up BitBake terminal routing",
      "contexts": ["workspace"], "command": ["scripts/setup-action.sh"] },
    { "id": "status", "title": "Herdbake: show status",
      "contexts": ["workspace"], "command": ["scripts/status.sh"] }
  ],
  "panes": [
    { "id": "chooser",       "title": "Herdbake: choose split direction",
      "placement": "popup",   "command": ["scripts/chooser.sh"] },
    { "id": "setup-confirm", "title": "Herdbake setup",
      "placement": "overlay", "command": ["scripts/setup-confirm.sh"] },
    { "id": "terminal",      "title": "Herdbake",
      "placement": "popup",   "command": ["scripts/pane-entry.sh"] }
  ]
}
```

Note the second registered plugin in the same output carries
`"warnings": ["manifest unavailable: No such file or directory (os error 2)"]` — a stale
registry entry whose manifest file no longer exists. Herdr keeps the entry and surfaces the
warning rather than dropping it.

### Full annotated example manifest

```toml
# ---- package metadata (id/name/version/min_herdr_version are REQUIRED) ----
id   = "herdr-usage"                 # ASCII [A-Za-z0-9.:_-], <=120 chars, globally unique
name = "Usage"                       # display name in plugin list / marketplace card
version = "0.1.0"                    # your plugin's version (free-form string)
min_herdr_version = "0.9.0"          # semver; link refused if > running herdr
description = "Per-agent usage-limit gauges"      # optional
platforms = ["linux", "macos"]       # omit => links with a warning; every item inherits this

# ---- build: only runs for `herdr plugin install` from GitHub, never for `plugin link` ----
[[build]]
command = ["python3", "-m", "pip", "install", "-r", "requirements.txt"]
platforms = ["linux", "macos"]       # optional per-item override

# ---- startup: one-shot, after session restore + API socket ready ----
#     Also re-runs on live-handoff server takeover. NOT on plugin link/enable/config reload.
[[startup]]
command = ["python3", "main.py", "startup"]
# env extra: HERDR_PLUGIN_EVENT=startup

# ---- actions: invocable by CLI, keybinding, or link handler ----
[[actions]]
id = "open"                          # local id: [A-Za-z0-9:_-], NO dots, unique per plugin
title = "Usage: open panel"          # shown in UI/command list
description = "Open the usage dashboard"   # optional
contexts = ["workspace"]             # global | workspace | tab | selection
command = ["python3", "main.py", "open"]
# qualified id becomes "herdr-usage.open"

# ---- events: subscribe to Herdr lifecycle events (see §3 for the full name list) ----
[[events]]
on = "pane.agent_status_changed"     # validated at link time; unknown name = warning, not error
command = ["python3", "main.py", "on-event"]

# ---- panes: terminal entrypoints openable via plugin.pane.open ----
[[panes]]
id = "panel"
title = "Usage"
placement = "split"                  # overlay (default) | popup | split | zoomed  (tab only via request)
command = ["python3", "main.py", "panel"]

[[panes]]
id = "settings"
title = "Usage settings"
placement = "popup"
width  = "80%"                       # popup ONLY: cells (int) or "NN%" string
height = 20                          # popup ONLY
command = ["python3", "main.py", "settings"]

# ---- link handlers: Ctrl+click on a matching terminal URL routes to an action ----
[[link_handlers]]
id = "usage-docs"
title = "Open usage docs"
pattern = "^https://example\\.com/usage/.*$"   # Rust regex, matched against the clicked URL
action = "open"                      # must name an action in THIS plugin
```

**VERIFIED** — `width`/`height` are popup-only: the binary carries the error string
*"width and height are only supported when placement is popup"*.

### Keybinding to an action (in `~/.config/herdr/config.toml`, not the manifest)

```toml
[[keys.command]]
key = "prefix+u"
type = "plugin_action"
command = "herdr-usage.open"
description = "open usage panel"
```

---

## 2. Runtime environment variables

Injected into **every** runtime command Herdr launches (actions, panes, event hooks,
startup hooks) — from `plugins.mdx` and confirmed by the binary's env-injection string blobs
(`HERDR_PLUGIN_ROOTHERDR_PLUGIN_CONFIG_DIRHERDR_PLUGIN_STATE_DIR`, `HERDR_PLUGIN_ACTION_ID`
`HERDR_PLUGIN_EVENT` `HERDR_PLUGIN_EVENT_JSON` …):

| Variable | Present for | Meaning |
|---|---|---|
| `HERDR_ENV=1` | all runtime | marker that you're inside Herdr |
| `HERDR_BIN_PATH` | all runtime | absolute path to the running herdr binary — **always call Herdr through this** |
| `HERDR_SOCKET_PATH` | all runtime | raw socket / named pipe (OS-specific transport) |
| `HERDR_PLUGIN_ID` | all runtime | your plugin id |
| `HERDR_PLUGIN_ROOT` | all runtime | installed/linked plugin dir; also the cwd of the command. **Do not write durable state here** (GitHub installs are managed checkouts) |
| `HERDR_PLUGIN_CONFIG_DIR` | all runtime | user-editable config dir (created by Herdr) |
| `HERDR_PLUGIN_STATE_DIR` | all runtime | plugin runtime state dir (created by Herdr) |
| `HERDR_PLUGIN_CONTEXT_JSON` | all runtime | full invocation context (see below) |
| `HERDR_WORKSPACE_ID` | when available | |
| `HERDR_TAB_ID` | when available | |
| `HERDR_PANE_ID` | when available — **never for popup panes** | |
| `HERDR_PLUGIN_ACTION_ID` | **actions only** | |
| `HERDR_PLUGIN_EVENT` | **startup + event hooks** | `startup` for startup hooks; event name for event hooks |
| `HERDR_PLUGIN_EVENT_JSON` | **event hooks only** | the event payload |
| `HERDR_PLUGIN_ENTRYPOINT_ID` | **pane commands only** | the `[[panes]] id` |
| `HERDR_PLUGIN_CLICKED_URL` | **link-handler-invoked actions** | |
| `HERDR_PLUGIN_LINK_HANDLER_ID` | **link-handler-invoked actions** | |

Per-kind matrix:

| | action | pane | event hook | startup hook | build |
|---|---|---|---|---|---|
| core 8 (`HERDR_ENV`, `_BIN_PATH`, `_SOCKET_PATH`, `_PLUGIN_ID`, `_ROOT`, `_CONFIG_DIR`, `_STATE_DIR`, `_CONTEXT_JSON`) | ✅ | ✅ | ✅ | ✅ | ❌ |
| `HERDR_WORKSPACE_ID` / `_TAB_ID` / `_PANE_ID` | when available | when available (popup: no `_PANE_ID`) | when available | when available | ❌ |
| `HERDR_PLUGIN_ACTION_ID` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `HERDR_PLUGIN_EVENT` | ❌ | ❌ | ✅ | ✅ (`=startup`) | ❌ |
| `HERDR_PLUGIN_EVENT_JSON` | ❌ | ❌ | ✅ | ❌ | ❌ |
| `HERDR_PLUGIN_ENTRYPOINT_ID` | ❌ | ✅ | ❌ | ❌ | ❌ |

**Build commands get NO runtime plugin context and no Herdr socket env** (`plugins.mdx`).

### `HERDR_ACTIVE_*` — a different family, NOT for plugins

**VERIFIED (binary + `configuration.mdx:236`)**: `HERDR_ACTIVE_WORKSPACE_ID`,
`HERDR_ACTIVE_TAB_ID`, `HERDR_ACTIVE_PANE_ID`, `HERDR_ACTIVE_PANE_CWD` are injected into
**custom `[[keys.command]]` commands**, not into plugin commands. `configuration.mdx:201`
adds that a popup (which has no `HERDR_PANE_ID`) can use `HERDR_ACTIVE_PANE_ID` for the
underlying tiled pane — in the keybinding-command context. **Plugin popups should read the
underlying pane from `HERDR_PLUGIN_CONTEXT_JSON` instead** (`plugins.mdx` says exactly that).
Do not assume `HERDR_ACTIVE_PANE_ID` in a plugin command.

### `HERDR_PLUGIN_CONTEXT_JSON` shape

Binary serde field list for `PluginInvocationContext`:

```
workspace_id  workspace_label  workspace_cwd  tab_id  tab_label
focused_pane_id  focused_pane_agent  focused_pane_status
selected_text  invocation_source  correlation_id  clicked_url  link_handler_id
```

`invocation_source` values seen in the binary: `cli`, `keybinding`, `link_click`, `activation`.

**VERIFIED** — real context returned from `herdr plugin action invoke herdbake.status`
(read-only status action of an already-linked plugin):

```json
{
  "correlation_id": "cli:plugin",
  "focused_pane_agent": "claude",
  "focused_pane_cwd": "/home/ed/herdr-usage",
  "focused_pane_id": "w7:p9",
  "focused_pane_status": "idle",
  "invocation_source": "cli",
  "tab_id": "w7:t1",
  "tab_label": "1",
  "workspace_cwd": "/home/ed/herdr-usage",
  "workspace_id": "w7",
  "workspace_label": "herdr-usage"
}
```

> **CLI vs binary discrepancy (minor):** the live response contains `focused_pane_cwd`, which
> is not in the binary's `PluginInvocationContext` field-name blob and is not documented.
> Trust the live output: `focused_pane_cwd` exists.

### Env caveat that matters for this project

A plugin script invoked *by a third-party program* (not by Herdr) does **not** inherit any of
these. Herdr only injects into processes it launches directly.

---

## 3. Event hooks

### Complete `on` name list — VERIFIED from the binary's event-name enum blob

`[[events]] on = "..."` accepts these **dotted** names (Herdr validates at link time; an
unknown name is a *warning* in the `plugin.link` / `plugin.list` `warnings` field, not a
failure):

```
workspace.created            workspace.updated        workspace.metadata_updated
workspace.renamed            workspace.moved          workspace.reordered
workspace.focused            workspace.closed
worktree.created             worktree.opened          worktree.removed
tab.created                  tab.closed               tab.renamed
tab.moved                    tab.focused
pane.created                 pane.closed              pane.updated
pane.focused                 pane.moved               pane.output_changed
pane.exited                  pane.agent_detected      pane.agent_status_changed
layout.updated
```

Additional names exist for **`events.subscribe` subscriptions only** (not manifest event
hooks): `pane.output_matched`, `pane.scroll_changed`.

> ⚠ **`workspace.metadata_updated` never invokes plugin event hooks.** `socket-api.mdx:790,820`:
> it is emitted to API subscribers only. There is no `pane.metadata_updated` event at all —
> pane token changes surface as `pane.updated` (the `PaneInfo` carries `tokens`).

### Payload shape

The JSON payload is an internally-tagged union keyed by `type`, using the **underscore** form
of the name. Verified from `herdr api schema --json` (`.schemas.event.$defs.EventKind`):

```
workspace_created workspace_updated workspace_metadata_updated workspace_closed
workspace_renamed workspace_moved workspace_reordered workspace_focused
worktree_created worktree_opened worktree_removed
tab_created tab_closed tab_renamed tab_moved tab_focused
pane_created pane_closed pane_updated pane_focused pane_moved
pane_output_changed pane_exited pane_agent_detected pane_agent_status_changed
layout_updated
```

The ones a usage-gauge plugin cares about — **VERIFIED**, straight from `api schema --json`:

```jsonc
// pane_created / pane_updated
{ "type": "pane_created", "pane": PaneInfo }          // required: type, pane

// pane_closed / pane_exited / pane_focused
{ "type": "pane_closed", "pane_id": "w7:p9", "workspace_id": "w7" }

// pane_agent_detected
{ "type": "pane_agent_detected",
  "pane_id": "w7:p9", "workspace_id": "w7",           // required
  "agent": "claude"|null, "released": true|false,
  "final_status": "idle"|"working"|"blocked"|"done"|"unknown"|null }

// pane_agent_status_changed   <-- the one to subscribe to
{ "type": "pane_agent_status_changed",
  "pane_id": "w7:p9", "workspace_id": "w7", "agent_status": "working",   // required
  "agent": "claude"|null, "display_agent": "..."|null,
  "title": "..."|null, "state_labels": { "working": "..." } }
```

`AgentStatus` enum: `idle | working | blocked | done | unknown`.

`PaneInfo` (the payload of `pane_created`/`pane_updated`), full schema:

```
required: pane_id, terminal_id, workspace_id, tab_id, focused, agent_status, revision
optional: agent, agent_session, cwd, foreground_cwd, display_agent, label, scroll,
          state_labels, terminal_title, terminal_title_stripped, title, tokens
tokens:   object, maxProperties 32, key pattern ^[A-Za-z0-9_-]{1,32}$, string values
```

### How the payload reaches the hook

**Via environment, not argv and not stdin.** `plugins.mdx:256-261` + the binary's env blob:
event hook processes get `HERDR_PLUGIN_EVENT` = the event name and `HERDR_PLUGIN_EVENT_JSON` =
the serialized payload. The manifest `command` argv is passed through unchanged — Herdr appends
nothing. (**DOC-ONLY**: not exercised live, since that requires linking a plugin.)

---

## 4. Sidebar `ui.sidebar.agents` and `herdr pane report-metadata`

### THE ANSWER FOR THE GAUGE PLUGIN

- **ANSI / colour escapes in a token value: IMPOSSIBLE. Herdr strips them at storage time.**
- **Unicode block characters: FULLY SUPPORTED, stored byte-exact.**
- **Colour comes from config, not from the value** — via `fg`/`bold`/`dim` and up to 16
  ordered `rules` per token occurrence, evaluated against the token's *string value*.
- **Hard cap: 80 characters (Unicode scalars, not bytes) per token value.**
- **Sidebar width: default 26 columns, config range clamped by `ui.sidebar_min_width` (18)
  and `ui.sidebar_max_width` (36).** So a gauge has roughly 20–34 usable columns after the
  state icon and separators — design for ~16–24 cells of bar.

#### VERIFIED: ANSI is stripped

```console
$ herdr pane report-metadata w7:pC --source probe.usage --token colored=$'\x1b[32m████\x1b[0m 40%'
$ herdr pane get w7:pC | python3 -c "import sys,json; v=json.load(sys.stdin)['result']['pane']['tokens']['colored']; print(repr(v)); print('has ESC:', '\x1b' in v)"
repr: '[32m████[0m 40%'
has ESC: False
```

Hexdump of the stored value confirms the `0x1b` byte is gone but the literal `[32m` text
survives:

```
00000000  5b 33 32 6d e2 96 88 e2  96 88 e2 96 88 e2 96 88  |[32m............|
00000010  5b 30 6d 20 34 30 25 0a                           |[0m 40%.|
```

This matches `cli-reference.mdx:321`: *"Herdr trims surrounding whitespace, removes control
characters, and caps … token values at 80 characters."* ESC is a control character.
**Conclusion: never embed SGR in a token; you get visible garbage (`[32m…[0m`) eating your
80-character budget.**

#### VERIFIED: Unicode blocks pass through unchanged

```console
$ herdr pane report-metadata w7:pC --source probe.usage \
    --token gauge5h='█████░░░░░ 52%' --token model='claude-opus-5'
$ herdr pane get w7:pC | jq -c '.result.pane.tokens'
{"gauge5h":"█████░░░░░ 52%","model":"claude-opus-5"}
```

#### VERIFIED: 80-char cap is in *characters*, silent truncation

```console
$ herdr pane report-metadata w7:pC --source probe.usage --token longv="$(python3 -c "print('A'*100)")"
$ herdr pane get w7:pC | jq -r '.result.pane.tokens.longv | length'
80

$ herdr pane report-metadata w7:pC --source probe.usage --token uni="$(python3 -c "print('█'*100)")"
$ # -> chars: 80  bytes: 240
```

So the cap is 80 Unicode scalars — a 100-block bar is silently cut to 80 blocks, and the byte
length is irrelevant. No error is returned; the value is just shorter. Plan your format
accordingly.

### `herdr pane report-metadata` — exact flags (VERIFIED)

```
Usage: herdr pane report-metadata [OPTIONS] --source <ID> <PANE_ID>

Arguments:
  <PANE_ID>
Options:
      --source <ID>                 (required)
      --agent <LABEL>
      --applies-to-source <ID>
      --title <TEXT>
      --clear-title
      --display-agent <TEXT>
      --clear-display-agent
      --state-label <STATUS=TEXT>
      --clear-state-labels
      --token <NAME=VALUE>
      --clear-token <NAME>
      --seq <N>
      --ttl-ms <N>
```

Workspace equivalent (for Space rows) is a strict subset — **no presentation flags at all**:

```
Usage: herdr workspace report-metadata [OPTIONS] --source <ID> <WORKSPACE_ID>
Options: --source <ID>  --token <NAME=VALUE>  --clear-token <NAME>  --seq <N>  --ttl-ms <N>
```

### Verified limits and semantics

| Behaviour | Result | Evidence |
|---|---|---|
| Token key charset | `^[A-Za-z0-9_-]{1,32}$` | `api schema` `PaneInfo.tokens.propertyNames` |
| Dot in key | rejected | `--token 'bad.name=x'` → `{"code":"invalid_metadata_token","message":"invalid metadata token key: bad.name"}` |
| 33-char key | rejected | same error code |
| Max tokens per pane | **32** | filling 35 keys → `{"code":"metadata_token_limit","message":"pane metadata may contain at most 32 tokens"}`; `jq '.tokens|length'` = 32 |
| Max distinct `--source` per pane | 32 (lifetime; clearing does not free a slot) | `cli-reference.mdx:325` (DOC-ONLY) |
| Value cap | 80 chars, silent truncate | measured above |
| Empty value | does **not** clear on this build | `--token longv=` left `longv` untouched — see discrepancy below |
| `--clear-token NAME` | clears reliably | verified on 36 keys |
| `--ttl-ms 0` | rejected | `{"code":"invalid_metadata_ttl","message":"metadata ttl_ms must be at least 1"}` |
| `--ttl-ms 86400001` | rejected | `{"code":"invalid_metadata_ttl","message":"metadata ttl_ms must be 86400000 or less"}` |
| TTL expiry | works | `--token ttltest=hello --ttl-ms 1500`; present immediately, gone after 2.2 s |
| `--seq` staleness | works | `--seq 5` value `first`, then `--seq 3` value `stale` → stored value stays `first` (call returns success) |
| `--source` charset | ≤80 chars, ASCII letters/digits/`:`/`.`/`_`/`-` | `cli-reference.mdx:325` (DOC-ONLY) |

> **Doc vs CLI discrepancy — trust the CLI.** `cli-reference.mdx:321` claims *"Empty normalized
> token values clear that key."* On 0.9.1 `--token longv=` returned success and **left the old
> value in place**. Use `--clear-token NAME` to remove a token; do not rely on the empty-value
> form.

### Value lifetime and persistence

- Tokens live on the **pane**, in memory. They survive reloads of the client and `server
  reload-config`, and they persist until replaced, cleared, TTL-expired, or the pane closes.
- **They do NOT survive a server restart.** `socket-api.mdx:796`: *"Token metadata is not
  restored after a server restart."* A usage plugin therefore needs a `[[startup]]` hook to
  re-report every pane's tokens after Herdr restarts — and note that `[[startup]]` only fires
  on server (re)start / live handoff, **not** on `plugin link` or `plugin enable`.
- Tokens are display-only. They never affect `agent_status`, waits, notifications, or rollups.
- `--agent` / `--applies-to-source` guard **only** `--title`, `--display-agent`, `--state-label`.
  They do **not** guard `--token`; token reporters own their own clearing and TTL.

### Built-in tokens

**Agent rows** (`ui.sidebar.agents.rows`):

| Token | Meaning |
|---|---|
| `state_icon` | colored icon for semantic agent state (fixed styles only, **no `rules`**) |
| `state_text` | `idle`/`working`/`blocked`/`done`/`unknown`, incl. a reported display label |
| `machine` | machine label when the client has >1 machine; omitted for a single local machine |
| `workspace` | workspace name |
| `tab` | tab name when available |
| `pane` | pane name when available |
| `agent` | detected or reported agent display name |
| `terminal_title` | latest OSC 0/2 title after safety normalization |
| `terminal_title_stripped` | same, minus one leading activity/spinner glyph + whitespace |
| `$name` | custom pane metadata token `name` |

**Space rows** (`ui.sidebar.spaces.rows`): `state_icon`, `state_text`, `workspace`, `branch`,
`git_status` (fixed styles only), `$name` (workspace metadata).

Defaults (from `config-reference.json`):

```toml
[ui.sidebar.agents]
row_gap = 0
rows = [["state_icon", "machine", "workspace", "tab"], ["agent"]]

[ui.sidebar.spaces]
row_gap = 0
rows = [["state_icon", "workspace"], ["branch", "git_status"]]
```

Layout limits: **max 16 rows per layout, max 16 tokens per row.** Adjacent values are joined
with ` · `; `state_icon` is followed by a single space. Missing values drop themselves *and
their separator*; a row with no values disappears entirely.

### Styling and rules — how to colour a gauge

```toml
[ui.sidebar.agents]
row_gap = 0
rows = [
  ["state_icon", "agent", "$model"],
  [{ token = "$g5h",   fg = "#a6e3a1",
     rules = [{ starts_with = "9", fg = "#f38ba8", bold = true },
              { contains = "!",    fg = "#fab387" }] }],
  [{ token = "$pct5h", fg = "#a6e3a1",
     rules = [{ gt = 90, fg = "#f38ba8", bold = true },
              { gt = 70, fg = "#fab387" }] }],
  ["workspace", "tab"],
]
```

**VERIFIED** — `HERDR_CONFIG_PATH=<copy> herdr config check` → `config: ok` for exactly the
above. And regex rules are rejected, as documented:

```console
$ # rows = [[{ token = "$g", rules = [{ regex = "^9" }] }]]
$ HERDR_CONFIG_PATH=/tmp/bad.toml herdr config check
config: issues found
config parse error: TOML parse error at line 431, column 9
data did not match any variant of untagged enum RawSidebarToken
; using defaults
```

Rule mechanics (`configuration.mdx:405-424`):

- `fg` must be strict `#RGB` or `#RRGGBB`. `bold`/`dim` are booleans; omitted preserves the
  contextual style, explicit `false` removes the modifier.
- A rule holds **exactly one** condition: `equals`, `contains`, `starts_with`, `gt`, or `lt`.
  Text conditions are case-sensitive unless `ignore_case = true` (ASCII only). `gt`/`lt` need
  the whole value to parse as a finite number — `"90"` matches `gt = 80`; `"90%"` does not.
- **First matching rule wins**, overriding only the fields it names.
- `hide = true` removes the token and its separator; a row losing all tokens disappears.
- Matching uses the full value **before** display truncation.
- Rules are unavailable on `state_icon` and composite `git_status`.
- **Design implication:** if you want numeric thresholds to drive colour, report the raw number
  as its own bare token (`$pct5h = "92"`) and the bar glyphs as a separate token
  (`$g5h = "█████████░"`), because a value like `"92%"` or a bar string will never satisfy
  `gt`/`lt`.

Per-agent layout override (replaces `rows` wholesale, not extends):

```toml
[ui.sidebar.agents.rows_by_agent]
claude = [["state_icon", "agent", "state_text"], ["$g5h"], ["workspace", "tab"]]
```

Keys are **strict canonical agent ids** (`claude`, `codex`, `pi`, …). Detection aliases like
`claude-code` are rejected. Custom reported agents fall back to `rows`.

### Sidebar width

**VERIFIED** from `config-reference.json`:

| Key | Type | Default | Note |
|---|---|---|---|
| `ui.sidebar_width` | integer | **26** | "Default expanded sidebar width in columns. Auto-scales based on workspace names." |
| `ui.sidebar_min_width` | integer | **18** | |
| `ui.sidebar_max_width` | integer | **36** | |
| `ui.sidebar_start_collapsed` | boolean | false | takes effect next launch |
| `ui.sidebar_collapsed_mode` | enum | `compact` | `compact` \| `hidden` |

Sidebar row layouts affect **only the expanded desktop sidebar**; collapsed and mobile views
keep their own compact layouts and ignore `rows`.

**UNVERIFIED:** the actual on-screen rendering of a block-character gauge (glyph width, whether
the client truncates mid-bar at narrow widths) was not visually confirmed — that needs a human
looking at the sidebar. Storage-side behaviour is proven; render-side is inferred.

---

## 5. Panes

### `herdr plugin pane open` — exact CLI surface (VERIFIED)

```
Usage: herdr plugin pane open [OPTIONS]

Options:
      --plugin <ID>
      --entrypoint <ID>
      --placement <PLACEMENT>   [possible values: overlay, split, tab, zoomed]
      --workspace <ID>
      --target-pane <PANE>
      --direction <DIRECTION>   [possible values: right, down]
      --cwd <PATH>
      --env <KEY=VALUE>         Set an environment variable for the launched process
      --focus
      --no-focus

herdr plugin pane focus <PANE_ID>
herdr plugin pane close <PANE_ID>
```

> **CLI vs docs discrepancy — important.** `plugins.mdx` and `socket-api.mdx` say
> `plugin.pane.open` accepts `overlay | popup | split | tab | zoomed`. The **CLI's
> `--placement` clap enum omits `popup`**. Passing it is accepted by clap but the CLI's own
> parser rejects it:
>
> ```console
> $ herdr plugin pane open --plugin zz.nope --entrypoint x --placement bogus
> invalid pane placement: bogus
> $ herdr plugin pane open --plugin zz.nope --entrypoint x --placement popup
> {"error":{"code":"plugin_not_found","message":"plugin not found"},"id":"cli:plugin"}
> ```
>
> `popup` got *past* placement parsing and reached plugin resolution, so the CLI **does**
> accept `popup` at runtime despite not advertising it in `--help`. Likewise the CLI advertises
> `tab`, which the manifest `placement` field does not accept. Net: **declare `popup` in the
> manifest's `[[panes]] placement` (proven working — `herdbake` does exactly that), and treat
> `--placement popup` on the CLI as working-but-undocumented.**

There are **no `--width` / `--height` CLI flags**; popup sizing is manifest-only from the CLI.
Over the raw socket, `plugin.pane.open` accepts `width`/`height` in params. Binary error
string: *"width and height are only supported when placement is popup"*.

### Placement semantics

| Placement | Behaviour | Pane ID? | Targeting |
|---|---|---|---|
| `overlay` (manifest default) | temporary zoomed overlay over the active pane; restores previous focus+zoom on close | ✅ | uses active tiled pane |
| `split` | normal split | ✅ | `--target-pane` (+ `--direction right\|down`) |
| `tab` | new tab | ✅ | `--workspace` only; **not** `--target-pane`/`--direction` |
| `zoomed` | zoomed pane | ✅ | `--target-pane` |
| `popup` | session-modal terminal popup; receives all input incl. Escape; doesn't change layout | ❌ **none** | uses active tiled pane |

Binary error strings confirming targeting rules:
*"overlay and popup plugin panes target the active pane"*,
*"tab plugin panes support workspace_id but not target_pane_id or direction"*,
*"split and zoomed plugin panes target an existing pane; use target_pane_id"*.

### Popup lifecycle — the trap

A popup is a **singleton session resource**, not a pane:

- no pane ID; invisible to `pane list`, `pane read`, `pane process-info`, `pane close`
  (all return `pane_not_found`)
- emits **no** pane lifecycle events; does not participate in layout/persistence/agent APIs
- does **not** export `HERDR_PANE_ID` to its process — read the underlying tiled pane from
  `HERDR_PLUGIN_CONTEXT_JSON`
- opening one while Settings / Copy mode / another modal is active → `ui_busy`
  (binary: *"a popup pane is already open"*, *"terminal area too small for popup"*)
- closes when the command exits or on a `popup.close` request; `popup.close` with none open →
  `popup_not_open`
- `plugin.pane.open` returns `{"type":"ok"}` **as soon as the process is launched**, not when
  it finishes. A fast success proves nothing about whether the command worked.
- Sizing: `width`/`height` as ints (terminal cells) or `"NN%"` strings; omit for a default
  half-size popup; too-small values clamp to the popup minimum.

Split/tab/zoomed/overlay panes are ordinary Herdr panes after opening: `pane.move`, `pane.swap`,
`pane.resize`, `pane.zoom` all work, and plugin-pane ownership follows the pane across tabs and
workspaces. `plugin.pane.focus` / `plugin.pane.close` operate on those pane IDs.

**Debugging tip (from the local herdr skill, consistent with everything above):** to verify a
popup entrypoint, temporarily switch it to `split` so it gets a pane ID you can `pane read`,
then switch back.

---

## 6. Reading which agent/model runs in a pane

### `herdr agent list` — VERIFIED, real output

> `--json` is **not** accepted (`usage: herdr agent list`). Output is already JSON on stdout.

```json
{"id":"cli:agent:list","result":{"type":"agent_list","agents":[
 {"agent":"hermes",
  "agent_session":{"agent":"hermes","kind":"id","source":"herdr:hermes","value":"20260917_111043_d6be5f"},
  "agent_status":"working","cwd":"/home/ed/chameleon","focused":false,
  "foreground_cwd":"/home/ed/chameleon","pane_id":"w4:p1","revision":7,"state_change_seq":16,
  "tab_id":"w4:t1","terminal_id":"term_65c40ed5fd8772",
  "terminal_title":"⏳ chameleon · gpt-5.6-luna · ~/chameleon",
  "terminal_title_stripped":"⏳ chameleon · gpt-5.6-luna · ~/chameleon","workspace_id":"w4"},
 {"agent":"claude",
  "agent_session":{"agent":"claude","kind":"id","source":"herdr:claude","value":"11111111-1111-1111-1111-111111111111"},
  "agent_status":"idle","cwd":"/home/ed/herdr-usage","focused":true,
  "foreground_cwd":"/home/ed/herdr-usage","pane_id":"w7:p9","revision":8,"state_change_seq":15,
  "tab_id":"w7:t1","terminal_id":"term_65c40f38d3c8ff",
  "terminal_title":"✳ Claude Code","terminal_title_stripped":"Claude Code","workspace_id":"w7"},
 {"agent":"hermes",
  "agent_session":{"agent":"hermes","kind":"id","source":"herdr:hermes","value":"20260924_112008_2b6787"},
  "agent_status":"idle","cwd":"/home/ed/torizoncore-builder","focused":false,
  "foreground_cwd":"/home/ed/torizoncore-builder","pane_id":"wF:p3","revision":4,"state_change_seq":4,
  "tab_id":"wF:t2","terminal_id":"term_65c40ed6014a6c",
  "terminal_title":"✓ Investigate TCB device-tree… · claude-sonnet-5 · ~/torizoncore-builder",
  "terminal_title_stripped":"✓ Investigate TCB device-tree… · claude-sonnet-5 · ~/torizoncore-builder",
  "workspace_id":"wF"}
]}}
```

Exact key union across all live agents (**VERIFIED**):

```
agent  agent_session  agent_status  cwd  focused  foreground_cwd  pane_id  revision
state_change_seq  tab_id  terminal_id  terminal_title  terminal_title_stripped  workspace_id
```

### **Herdr exposes NO model field.** This is the critical finding.

`agent` is the **agent kind** (`claude`, `codex`, `hermes`, `pi`, …), never the model.
`agent_session` is a native session *reference* (`{agent, kind, source, value}`), not a model.
There is no `model` key anywhere in `AgentInfo`, `PaneInfo`, or `SessionSnapshot`.

The only place a model name appears is inside the agent's **own OSC title**, e.g.
`"⏳ chameleon · gpt-5.6-luna · ~/chameleon"` or
`"✓ Investigate TCB device-tree… · claude-sonnet-5 · ~/torizoncore-builder"` — and that is
purely a convention of the Hermes agent's title format. Claude Code's own title is just
`"✳ Claude Code"` with **no model at all**. So:

> **A usage plugin cannot get the running model from Herdr.** It must either parse the
> agent-specific terminal title (fragile, agent-specific, absent for Claude Code) or read the
> agent CLI's own local state/config files. Do not build on `terminal_title`.

### `herdr api snapshot` — VERIFIED

`Usage: herdr api snapshot` (no flags). Returns `{"id":…,"result":{"type":"session_snapshot"|…,"snapshot":{…}}}`
with top-level keys:

```
version  protocol  focused_workspace_id  focused_tab_id  focused_pane_id
workspaces  tabs  panes  layouts  agents
```

Live: `version 0.9.1`, `protocol 22`. Agent record identical to `agent list`. Workspace record:

```json
{"active_tab_id":"w6:t2","agent_status":"unknown","focused":false,
 "label":"vpn","number":1,"pane_count":1,"tab_count":1,"workspace_id":"w6"}
```

### `herdr pane list` / `pane get` — VERIFIED

`pane list [--workspace <ID>]` (no `--json`). Two real records:

```json
{"agent":"claude",
 "agent_session":{"agent":"claude","kind":"id","source":"herdr:claude","value":"11111111-1111-1111-1111-111111111111"},
 "agent_status":"idle","cwd":"/home/ed/herdr-usage","focused":true,
 "foreground_cwd":"/home/ed/herdr-usage","pane_id":"w7:p9","revision":8,
 "scroll":{"max_offset_from_bottom":0,"offset_from_bottom":0,"viewport_rows":31},
 "tab_id":"w7:t1","terminal_id":"term_65c40f38d3c8ff",
 "terminal_title":"✳ Claude Code","terminal_title_stripped":"Claude Code","workspace_id":"w7"}

{"agent_status":"unknown","cwd":"/home/ed/herdr-usage","focused":false,
 "foreground_cwd":"/home/ed/herdr-usage","pane_id":"w7:pB","revision":1,
 "scroll":{"max_offset_from_bottom":0,"offset_from_bottom":0,"viewport_rows":31},
 "tab_id":"w7:t1","terminal_id":"term_65c4115b1f31411",
 "terminal_title":"ed@host: ~/herdr-usage",
 "terminal_title_stripped":"ed@host: ~/herdr-usage","workspace_id":"w7"}
```

Panes carrying plugin tokens include a `tokens` map — real output from the scratch pane during
the metadata probe:

```json
{"pane_id":"w7:pC","workspace_id":"w7","tab_id":"w7:t3","agent_status":"unknown",
 "display_agent":"Usage Probe","title":"probe title","state_labels":{"working":"crunching"},
 "tokens":{"colored":"[32m████[0m 40%","gauge5h":"█████░░░░░ 52%",
           "model":"claude-opus-5","s":"first",
           "uni":"████████████████████████████████████████████████████████████████████████████████"},
 "cwd":"/home/ed/herdr-usage","foreground_cwd":"/home/ed/herdr-usage","revision":63,
 "scroll":{"max_offset_from_bottom":0,"offset_from_bottom":0,"viewport_rows":66},
 "terminal_id":"term_65c4123da91e712","terminal_title":"ed@host: ~/herdr-usage",
 "terminal_title_stripped":"ed@host: ~/herdr-usage","focused":false}
```

### `herdr pane process-info` — VERIFIED, the reliable identity source

```
Usage: herdr pane process-info [OPTIONS]
Options:  --pane <ID>   --current
```

Agent pane:

```json
{"id":"cli:pane:process_info","result":{"type":"pane_process_info","process_info":{
  "pane_id":"w7:p9","shell_pid":13816,"foreground_process_group_id":20763,
  "foreground_processes":[
    {"pid":20763,"name":"claude","cmdline":"claude","argv":["claude"],"cwd":"/home/ed/herdr-usage"},
    {"pid":20860,"name":"lava-mcp",
     "cmdline":"/home/ed/lava-mcp/.venv/bin/python3 /home/ed/lava-mcp/.venv/bin/lava-mcp",
     "argv":["/home/ed/lava-mcp/.venv/bin/python3","/home/ed/lava-mcp/.venv/bin/lava-mcp"],
     "cwd":"/home/ed/herdr-usage"}]}}}
```

Idle shell pane (the "safe to inject" shape — exactly one foreground process whose `pid` equals
`shell_pid`):

```json
{"process_info":{"pane_id":"w7:pC","shell_pid":25239,"foreground_process_group_id":25239,
 "foreground_processes":[{"pid":25239,"name":"bash","cmdline":"/bin/bash",
                          "argv":["/bin/bash"],"cwd":"/home/ed/herdr-usage"}]}}
```

`PaneProcessInfoProcess` serde fields: `pid`, `name`, `argv0`, `argv`, `cmdline`, `cwd`.

**This is the best hook for identity**: the real argv and cwd of the agent process, which is what
you need to locate that agent's own credential/usage state on disk.

### `herdr agent explain` — detection introspection

```
Usage: herdr agent explain [OPTIONS] [TARGET]
Options: --file <PATH>  --agent <LABEL>  --json  --format text|json  -v/--verbose
```

Returns the full detection rule evaluation: `agent`, `cached_remote_version`, and
`evaluated_rules[]` each with `id`, `priority`, `region`, `state`, `matched`, and `evidence`
(including a `region_preview` of the live screen text). Real excerpt:

```json
{"agent":"claude","cached_remote_version":"2026.09.11.1","evaluated_rules":[
 {"id":"osc_title_working","priority":1100,"region":"osc_title","state":"working","matched":false,
  "evidence":{"regex":["^[\\x{2800}-\\x{28FF}\\x{25D0}-\\x{25D3}] "],
              "region_bytes":15,"region_preview":"✳ Claude Code"}},
 {"id":"live_turn_working","priority":970,"region":"bottom_non_empty_lines(12)","state":"working",
  "matched":false,"evidence":{"any_count":2,"region_bytes":657,"region_preview":"…"}}]}
```

Useful for debugging detection; **not** a source of model information.

### Agent metadata Herdr actually exposes — summary

| Want | Available? | How |
|---|---|---|
| agent kind | ✅ | `agent` field (`claude`, `codex`, `hermes`, …) |
| semantic state | ✅ | `agent_status` ∈ `idle\|working\|blocked\|done\|unknown` |
| native session id | ✅ when an integration reports it | `agent_session` = `{agent, kind:"id", source:"herdr:<kind>", value:"<session id>"}` |
| pane/tab/workspace ids, cwd | ✅ | `pane_id`, `tab_id`, `workspace_id`, `cwd`, `foreground_cwd` |
| agent process argv/pid | ✅ | `pane process-info` |
| **model** | ❌ | **not exposed anywhere** — see above |
| account / plan / quota | ❌ | not a Herdr concept at all |

Supported `agent start --kind` values (`cli-reference.mdx:348`): `pi claude codex gemini cursor
devin agy cline omp mastracode opencode copilot kimi kiro droid amp grok hermes kilo qodercli
qwen letta maki muse`. Installable integrations (**VERIFIED** from `herdr integration --help`):
`pi omp claude codex copilot devin droid kimi opencode kilo hermes qodercli qwen letta cursor
mastracode antigravity-cli grok`.

---

## 7. Configuration and UI primitives

### Where plugin files live — VERIFIED

```console
$ herdr plugin config-dir herdbake
/home/ed/.config/herdr/plugins/config/herdbake
```

Actual on-disk layout:

```
~/.config/herdr/plugins/config/<plugin-id>/     <- HERDR_PLUGIN_CONFIG_DIR   (user-editable)
~/.config/herdr/plugins/github/                 <- managed GitHub checkouts
~/.local/state/herdr/plugins/<plugin-id>/       <- HERDR_PLUGIN_STATE_DIR    (runtime state)
~/.config/herdr/config.toml                     <- Herdr's own config (HERDR_CONFIG_PATH overrides)
~/.config/herdr/plugins.json                    <- plugin registry (next to session.json)
```

Binary symbol confirming the state path: `herdr::plugin_paths::plugin_state_dir`.

> **Side effect worth knowing:** `herdr plugin config-dir <unknown-id>` **creates** both the
> config dir and the state dir for that id and prints the path. It does not validate that the
> plugin exists. (Observed during probing; the stray `example.nope` dirs were removed.)

Herdr creates these directories and nothing else — no schema, no validation, no sync, no
cleanup, no migration. File format and lifecycle are entirely the plugin's problem
(`socket-api.mdx:628`: *"path discovery only"*).

### Native modal / menu / prompt primitives: NONE

**VERIFIED** against the complete socket method table (`herdr api schema --json`,
`socket-api.mdx:95-113`, and the binary's `Method` enum blob). The full method list contains
no picker, form, prompt, menu, dialog, or input primitive. The only UI-ish methods are:

- `notification.show` — fire-and-forget toast (`title`, `body`, `sound`, …); no return value
  from the user
- `client.window_title.set` / `client.window_title.clear` — outer terminal title
- `popup.close` — closes the single active popup
- `pane.graphics.{set,clear,info,stream}` — Kitty-graphics images into a pane
- `agent.view.set` / `agent.view.clear` — declarative filter/sort of the **Agents sidebar list**
  (`AgentViewFilter` supports `Eq`/`Any`/`All`/`Not`/`In`/`Exists`, plus `AgentViewSort{field,order}`);
  this reorders/filters which agents are listed, it is **not** a widget API

**Conclusion: a plugin that needs a picker or a form must implement its own TUI and run it in a
pane** — `placement = "popup"` for a modal (gets all keys including Escape) or `split`/`tab` for
something inspectable and long-lived. There is no native widget toolkit and no runtime
registration of UI.

Caveats for building a popup-based config UI:

- a popup has no pane ID → an agent/script cannot drive it or verify a human keypress
  end-to-end; test the underlying script by piping stdin, and test the caller's wait/poll logic
  separately
- `ui_busy` is returned if Settings / Copy mode / another modal is open
- the launch call returns `ok` immediately; it tells you nothing about the command's fate —
  cross-check `~/.config/herdr/herdr-server.log` for `pane.spawn.start` → `pane.spawned` →
  `pane.exit` and the exit status

### Validating config changes

```console
$ herdr config check         # -> "config: ok" or "config: issues found" + parse errors
$ herdr server reload-config # apply config.toml without restarting the session
```

`herdr config` has exactly two subcommands: `check` and `reset-keys`.

---

## 8. Marketplace publishing (0.9.1)

There is **no submission form and no review**. The marketplace is an automatic index over public
GitHub repos (`marketplace.mdx`). Requirements:

1. **Public** GitHub repository. Forks and archived repos are excluded.
2. One or more `herdr-plugin.toml` at the repo root or in subdirectories of the **default
   branch**, each with parseable required metadata: `id`, `name`, `version`,
   `min_herdr_version`. Malformed manifest metadata → excluded.
3. **GitHub topic `herdr-plugin` on the repository.** This is the actual trigger.
   `gh repo edit <owner>/<repo> --add-topic herdr-plugin`
4. The index refreshes **every 30 minutes**, and rescans when the default-branch head changes.
   Nothing to poll or force; don't claim it's live immediately after tagging.

A listing card shows GitHub repo metadata (name, owner, description, stars, primary language,
last push). Each plugin row shows the manifest's `name` and `version` and links to its exact
source directory. The index records `manifest path, id, name, version, platforms,
min_herdr_version` plus the exact default-branch commit. One repo card can host multiple
separately installable plugins.

Users install with:

```bash
herdr plugin install <owner>/<repo>[/subdir...]    # GitHub shorthand ONLY; no URLs
herdr plugin install <owner>/<repo> --ref <REF>    # pin a revision
herdr plugin install <owner>/<repo> --yes          # skip the interactive preview
```

Install clones with `git`, shows a preview of source and commands in interactive terminals, runs
`[[build]]` commands (a build failure aborts the install and the plugin is not registered),
stores the checkout under `~/.config/herdr/plugins/github/`, and registers it. Changing
`herdr-plugin.toml` after the install preview aborts the install.

Other lifecycle facts (VERIFIED from `--help`):

```
herdr plugin uninstall <PLUGIN>        # id OR owner/repo[/subdir]; removes managed checkout
herdr plugin link <PATH> [--disabled|--enabled]
herdr plugin unlink <PLUGIN_ID>        # unregister only; leaves files alone
herdr plugin enable|disable <PLUGIN_ID>
herdr plugin list [--plugin <ID>] [--json]
herdr plugin action list [--plugin <ID>]         # no --json
herdr plugin action invoke <ACTION_ID> [--plugin <ID>]
herdr plugin log list [--plugin <ID>] [--limit <N>]
```

- There is **no `plugin update`** in v1; reinstall from GitHub to refresh.
- Installing over a locally linked plugin is refused — unlink first.
- Installed/linked plugins and their enabled state are **global to the user**, available in
  every session. Both `install` and `link` work with no server running.
- `plugin link` does **not** run `[[build]]` commands.

**Before declaring a plugin published, round-trip the *install* path**, not just `link` — they
are different code paths (a file you forgot to commit only breaks `install`):

```bash
herdr plugin unlink <id>
herdr plugin install <owner>/<repo> --yes
herdr plugin action invoke <id>.<action>
herdr plugin uninstall <id>
herdr plugin link ~/<local-checkout>
```

`plugin log list` is how you see what a command actually did — **VERIFIED**, real output:

```json
{"id":"cli:plugin","result":{"type":"plugin_log_list","logs":[
 {"log_id":"plugin-log-1","plugin_id":"herdbake","action_id":"status",
  "command":["scripts/status.sh"],"status":"succeeded","exit_code":0,
  "started_unix_ms":1790284830757,"finished_unix_ms":1790284830759,
  "stderr":"",
  "stdout":"herdbake: routing is configured per Yocto build directory\nrun 'Herdbake: set up BitBake terminal routing' from a build pane\n\nconfig: /home/ed/.config/herdr/plugins/config/herdbake/bitbake-terminal.toml\n(falls back to bundled config/bitbake-terminal.default.toml if absent)\n"}]}}
```

`PluginCommandLogInfo` fields: `log_id`, `plugin_id`, `action_id`, `event`, `command`, `status`
(`running`|`succeeded`|`failed`), `started_unix_ms`, `finished_unix_ms`, `exit_code`, `stdout`,
`stderr`, `error`.

---

## Consolidated doc-vs-CLI discrepancies (CLI wins)

| # | Docs say | CLI/binary does | Verdict |
|---|---|---|---|
| 1 | Empty token value clears the key (`cli-reference.mdx:321`) | `--token k=` returns success and leaves the old value | **Use `--clear-token`** |
| 2 | `plugin.pane.open` placement includes `popup` | `--placement` clap enum lists only `overlay, split, tab, zoomed`, but `popup` is still accepted at runtime | popup works; undocumented in `--help` |
| 3 | Manifest `placement` = `overlay` default, docs imply same set | manifest enum is `overlay\|popup\|split\|zoomed`; `tab` is request-only | declare `popup` in the manifest |
| 4 | `PluginInvocationContext` field list (binary) omits `focused_pane_cwd` | live invoke returns `focused_pane_cwd` | field exists |
| 5 | `agent list` shown with `--json` in some agent docs/skills | `herdr agent list` takes no options; already emits JSON | don't pass `--json` |

## Blockers for the usage-gauge plugin

1. **No model field.** Herdr exposes agent *kind*, not model. Terminal-title parsing is the only
   in-Herdr signal and Claude Code's title (`"✳ Claude Code"`) contains no model at all.
2. **No ANSI in sidebar tokens.** Colour must come from `fg` + `rules` in the user's
   `config.toml`, which means the plugin's install docs must instruct the user to edit
   `ui.sidebar.agents.rows`. Herdr offers no way for a plugin to write sidebar styling itself.
3. **80-character value cap + ~20–34 usable sidebar columns.** Gauges must be short.
4. **Numeric `rules` require a bare numeric token.** Split the numeric value and the bar glyphs
   into separate tokens if you want threshold colouring.
5. **Tokens vanish on server restart.** A `[[startup]]` hook must re-report everything — and it
   does not fire on `plugin link`/`enable`, only on server (re)start and live handoff.
6. **No modal/form primitive.** Configuration UI = your own TUI in a popup or split pane.
