# SPEC — `herdr-usage`

Herdr plugin rendering AI-agent account usage limits as colored gauges in the
Agents sidebar.

Status: **closed** after two council rounds. Everything here is either verified
live on Herdr 0.9.1 / Codex 0.156.1 / Claude Code 2.1.282, or explicitly marked
as a decision. Evidence lives in `docs/research/`, reasoning in `docs/COUNCIL.md`.

---

## 1. Scope

**In v1:** Codex CLI and Claude Code, both OAuth-only; 5h and weekly gauges;
model label; plan badge; burn-rate ETA; reset countdown; Codex reset credits;
session cost; stale / no-data / blocked states; a popup settings TUI; automatic
sidebar configuration with an undo route; declarative user-added providers.

**Out of v1:** context-window gauge (held by Eduardo), multi-account per
provider, any write to agent state, any network call the plugin makes itself.

---

## 2. Non-negotiables

1. **Never invent a percentage.** Both providers report one; we never derive it
   from local token math. No data → `⋯ no data`, never `0%`.
2. **Quota is account-level.** The same numbers appear on every pane of that
   provider. Gauge labels say what the window is, never "this pane's usage".
3. **Read-only on agent state.** `~/.codex` and `~/.claude` are opened read-only
   (`mode=ro` for SQLite). No login/logout, no writes, ever.
4. **Never log or print a token value.** Probes handle credentials only inside
   the vendor CLI's own process.
5. **The user's `config.toml` is sacred.** Backup, marked block, `herdr config
   check` on a temp copy before the real write, rollback on failure.

---

## 3. Data model

```python
Gauge(key, label, pct, resets_at, severity, blocked, source)
#  key      "5h" | "wk" | "mo" | "scoped:Fable"     - provider-chosen, not fixed
#  label    "5h" | "wk"                             - what renders in the bar
#  pct      float 0..100 | None                     - None = unknown, never 0
#  resets_at epoch seconds | None
#  severity "ok"|"warn"|"crit"|None                 - provider's own, if it sends one
#  blocked  bool                                    - hard wall hit

AccountUsage(provider, account_id, plan, gauges, credits, cost_usd,
             fetched_at, error)
```

Gauges are a **list**, never three fixed slots. A provider that returns a
calendar-month window gets a monthly gauge with no new code. This was council
decision #3.

---

## 4. Providers

### 4.1 Codex (built-in)

```
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{...}}}
{"jsonrpc":"2.0","id":2,"method":"account/rateLimits/read","params":{}}
```
piped to `codex app-server`, read until `id == 2`. Verified: 0.5 s CPU, no model
turn, no quota consumed.

| Field | → |
|---|---|
| `rateLimits.primary` / `.secondary` | a gauge each |
| `.windowDurationMins` | **the window identity** — 300 → `5h`, 10080 → `wk`, else derived |
| `.usedPercent`, `.resetsAt` | `pct`, `resets_at` |
| `.planType` | `plan` |
| `.rateLimitReachedType` non-null | `blocked` |
| `rateLimitResetCredits.availableCount` | `credits` |
| `accountId` | `account_id` |

**Trap (from research):** `primary` is the *weekly* window in 2242/3703
historical rollout snapshots. Key off `windowDurationMins`, never the key name.
Parse both camelCase (app-server) and snake_case (rollout JSONL) spellings.

Model identity: `~/.codex/state_5.sqlite` → `threads.model`, matched to the pane
by `cwd` + most recent `updated_at_ms`.

### 4.2 Claude Code (built-in)

```
{"type":"control_request","request_id":"r1",
 "request":{"subtype":"get_usage","skip_behaviors":true}}
```
piped to `claude -p --verbose --safe-mode --input-format stream-json
--output-format stream-json --no-session-persistence`. Verified: answered before
any model turn, zero tokens billed, 0.55 s CPU.

| Field | → |
|---|---|
| `rate_limits.five_hour` | `5h` gauge |
| `rate_limits.seven_day` | `wk` gauge |
| `model_scoped[]` | one `scoped:<display_name>` gauge each |
| `limits[].severity` | `severity` — **use the server's band, don't re-derive** |
| `*.locked_reason` non-null | `blocked` |
| `subscription_type` | `plan` |
| `session.total_cost_usd` | `cost_usd` |

**Don't hardcode "Opus".** On this account `seven_day_opus` is null and the only
scoped row is `"Fable"`. Iterate `model_scoped[]`.

Model identity: last `message.model` in
`~/.claude/projects/<slug>/<session_id>.jsonl`, where `session_id` comes from
Herdr's `agent_session.value`. Absent until the first assistant turn → `—`.

### 4.3 User-added (declarative — council decision #5)

```toml
[providers.mytool]
command = ["mytool", "usage", "--json"]
account_id = "$.account.id"
plan       = "$.plan"
[[providers.mytool.gauges]]
label      = "5h"
pct        = "$.limits.five_hour.used_percent"
resets_at  = "$.limits.five_hour.resets_at"
```

Paths are a dotted subset of JSONPath (`$.a.b[0].c`) — no expressions, no
filters, no code execution. Unresolvable path → that field is `None`, which
renders as `⋯ no data`, not as a crash.

---

## 5. Rendering

### 5.1 Glyphs (chosen)

| Element | Value |
|---|---|
| Window labels | `5h` / `wk` |
| Blocked | `⛔️` |
| Stale | `~` |
| No data | `⋯ no data` |
| Burn ETA · resets-in | `⚡ 1h20m · ↻ 2h14m` |
| Reset credits | `★3` |
| Model label | `Opus` (family name, capitalized) |

All verified to round-trip byte-exact through `pane report-metadata`, including
the `⛔️` variation selector (U+26D4 U+FE0F).

> Width note: `⛔️` is East-Asian *Ambiguous* in Unicode data but renders 2 cells
> in most terminals with emoji presentation. It is always the **last** glyph in
> its token, so worst case is one cell of overflow. Accepted.

### 5.2 Bar sets — all six, selectable in the menu

```
blocks   ████░░░░░░   shade    ▓▓▓▓░░░░░░   squares  ■■■■□□□□□□
dots     ●●●●○○○○○○   half     ▮▮▮▮▯▯▯▯▯▯   ascii    ####------
```
Default `blocks`. Width configurable (default 10 cells).

**Fill rule:** `cells` if `pct >= 100`, `0` if `pct <= 0`, else
`min(cells-1, max(1, ceil(pct/100*cells)))`. A full bar therefore means
exhausted and a non-zero percentage always shows at least one block.

### 5.3 Colour — severity in the token *name*

Herdr strips ANSI and its `gt`/`lt` rules need the whole value to parse as a
number, which a bar never will. So the plugin computes the band and emits
**exactly one** of four token names, clearing the other three:

```
uq_g5h_ok    #a6e3a1   green
uq_g5h_warn  #f9e2af   yellow     default band: pct >= 70
uq_g5h_crit  #f38ba8   red bold   default band: pct >= 90  (or blocked)
uq_g5h_none  #6c7086   dim        no data
```

Verified live — one token present per band, and `herdr config check` → `config:
ok` on the generated block. A row drops missing tokens *and* their separators, so
the three unset variants render nothing.

Thresholds are plain numbers in the plugin config. Where a provider sends its own
`severity`, that wins over the thresholds.

### 5.4 Token budget

| Token | Count |
|---|---|
| `uq_model`, `uq_plan`, `uq_burn`, `uq_extra` | 4 |
| `uq_g<slug>_{ok,warn,crit,none}` per gauge | 4 each |

Default (2 gauges) = 12. Worst realistic case (4 gauges) = 20. Limit is 32/pane.
Gauge keys are slugified to `^[A-Za-z0-9_-]{1,32}$` (`scoped:Fable` →
`scoped_Fable`).

### 5.5 Layout

Default — 2 gauges, extras off:

```
┌──────────────────────────┐
│✳ claude · Opus           │  state_icon · agent · $uq_model
│5h ████░░░░░░  31%        │  $uq_g5h_{ok,warn,crit,none}
│wk █████████░  82%        │  $uq_gwk_{...}
│herdr-usage · t1          │  workspace · tab
└──────────────────────────┘
```

All extras on:

```
┌──────────────────────────┐
│✳ claude · Opus · team    │
│5h ████░░░░░░  31%        │
│wk █████████░  82%        │
│wk Fable ░░░░░░  0%       │
│⚡ 1h20m · ↻ 2h14m         │
│★3 · $0.42                │
└──────────────────────────┘
```

Degraded states, all distinct from a real reading:

```
│5h ██████████ ⛔️          │  hard-blocked
│5h ████░░░░░░  31% ~      │  stale (cache older than 3× refresh interval)
│5h ⋯ no data              │  probe never succeeded
│✳ claude · —              │  model not yet known
```

### 5.6 Burn-rate ETA

From the previous cached sample: `eta = (100 - pct) / ((pct - prev_pct) / Δt)`.
Suppressed when `Δt < 60 s`, `pct <= prev_pct`, or the ETA exceeds the window
reset — an ETA later than the reset is meaningless.

---

## 6. Refresh — measured, not guessed

| Probe | wall | CPU | peak RSS |
|---|---|---|---|
| codex | 0.6–2.0 s | 0.5 s | 168 MB |
| claude | 1.2–1.4 s | 0.55 s | 274 MB |
| cache hit | 0.01 s | 0.01 s | 10 MB |

**The cache TTL is the rate limiter. Event hooks never probe.**

| Trigger | Probes? |
|---|---|
| `[[startup]]` | yes — a server restart wipes all pane tokens |
| `pane.agent_status_changed` | only if cache older than TTL |
| `pane.agent_detected` | only if cache older than TTL |
| `refresh` action | forced, ignores TTL |

At `interval_secs = 300`: ~12 CPU-seconds/hour (0.3% of one core), 12 API
calls/hour/provider. `min_interval_secs = 60` floor. No daemon, no sleep loop,
no live timer — council decision #4.

Concurrency: the cache write is atomic (temp file + `os.replace`) and a stale
`.lock` older than 60 s is ignored, so a burst of event hooks cannot corrupt it.

---

## 7. Sidebar installation

Automatic on install, reversible, and fully documented for manual use
(council round 2.1).

- Writes a marked block into `~/.config/herdr/config.toml`:
  `# >>> herdr-usage managed block` … `# <<< herdr-usage managed block`
- Sequence: backup → write to a temp copy → `HERDR_CONFIG_PATH=<tmp> herdr
  config check` → only on `ok`, replace the real file → `herdr server
  reload-config`. Any failure restores the backup and reports.
- `setup-sidebar --undo` removes the block and writes an `uninstalled` marker so
  auto-install never fights a deliberate removal.
- Triggered by `[[build]]` (GitHub installs) and by a one-time idempotent check
  at startup/refresh (covers `plugin link`, which fires neither `[[build]]` nor
  `[[startup]]`).
- README documents the exact block for hand-editing.

---

## 8. Settings popup

`[[panes]] placement = "popup"` — council decision #2. Herdr has no native
picker, so this is our own curses TUI. Toggles every extra, picks the bar set and
width, sets thresholds and refresh interval, enables/disables providers. On save:
rewrite the plugin config, regenerate the sidebar block, `config check`, reload.

**Testing constraint (from the herdr skill):** a popup has no pane ID and cannot
be driven end-to-end by an agent. Tests therefore exercise the TUI's pure
keypress→state reducer directly and the config writer separately; the live
keypress is Eduardo's to verify.

---

## 9. Config file

`~/.config/herdr/plugins/config/herdr-usage/config.toml`, read with `tomllib`,
written with a surgical scalar setter that preserves comments and layout.

```toml
[refresh]
interval_secs = 300
min_interval_secs = 60

[display]
bar_set = "blocks"        # blocks|shade|squares|dots|half|ascii
bar_cells = 10
gauges = ["5h", "wk"]     # which windows to render
show_model = true
show_plan = false
show_burn = false
show_resets_in = false
show_credits = false
show_cost = false
show_scoped = false

[thresholds]
warn = 70
crit = 90
stale_multiplier = 3

[providers.codex]
enabled = true
[providers.claude]
enabled = true
```

---

## 10. Files

```
herdr-plugin.toml     manifest
main.py               CLI dispatch: refresh | settings | setup-sidebar | doctor
config.py             defaults, tomllib load+merge, surgical scalar writer
providers.py          codex + claude probes, declarative runner, model identity
model.py              Gauge/AccountUsage, normalizers, severity band, burn ETA
render.py             bar(), durations, token-dict builder
sidebar.py            managed-block generate/install/undo, config check+rollback
herdrctl.py           subprocess seam: pane_list, report_metadata, config_check
settings_tui.py       curses popup + pure keypress reducer
tests/                pytest, stdlib only
README.md
```

Stdlib only — both payloads are JSON, `tomllib` is 3.11+. No dependencies.

---

## 11. Verification gates

Ship blocked until:

1. Unit tests green (§12 task list).
2. `herdr config check` returns `ok` on the generated block, and the rollback
   path is proven by feeding it a deliberately broken block.
3. Real gauges visible in Eduardo's sidebar for both a Codex and a Claude pane,
   confirmed by him looking at it — the render side is the one thing no agent
   can verify.
4. `plugin unlink` → `plugin install <owner>/<repo>` → action invoke →
   `uninstall` → `link` round-trip, because the install path differs from the
   link path used during development.
