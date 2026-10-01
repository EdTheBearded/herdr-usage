# Herdr Usage Gauges

AI agent usage limits as coloured gauges in the [Herdr](https://herdr.dev)
Agents sidebar. The plugin ID is `herdr-usage`.

```
┌──────────────────────────┐
│✳ claude · Opus           │
│5h ████░░░░░░  31%        │   ← green
│wk █████████░  92%        │   ← red, bold
│herdr-usage · t1          │
└──────────────────────────┘
```

Works with **OAuth logins** — no API key needed. Reads the number the provider
itself reports; never estimates from local token counting.

---

## Why

Codex and Claude Code both know exactly how much of your 5-hour and weekly
allowance you've burned. Neither shows it while you work. This puts it in the
sidebar you already have open.

## Install

```bash
herdr plugin install EdTheBearded/herdr-usage --yes
herdr plugin action invoke herdr-usage.setup-sidebar
```

The second command writes a managed block into your `~/.config/herdr/config.toml`
so Herdr knows how to colour the gauges. It is reversible and validated:
the file is checked with `herdr config check` **before** anything is written,
and restored byte-identically if that fails.

Restart Herdr (or run `herdr plugin action invoke herdr-usage.refresh`) and the
gauges appear.

To remove the block:

```bash
herdr plugin action invoke herdr-usage.remove-sidebar
```

### Local development

```bash
git clone https://github.com/EdTheBearded/herdr-usage
herdr plugin link ~/herdr-usage
python3 ~/herdr-usage/main.py doctor    # verify both probes work
```

## Requirements

- Herdr ≥ 0.9.0
- Python 3.11+ (standard library only — **no dependencies**)
- At least one of: [Codex CLI](https://github.com/openai/codex),
  [Claude Code](https://docs.claude.com/en/docs/claude-code)

## What it shows

| | |
|---|---|
| `5h ████░░░░░░  31%` | rolling 5-hour window |
| `wk ██░░░░░░░░  13%` | rolling weekly window |
| `wk Fable ░░░░░░  0%` | model-scoped weekly limit, when the provider reports one |
| `Opus` | model running in that pane |
| `team` | plan tier |
| `⚡ 1h20m` | projected exhaustion at the current burn rate |
| `↻ 2h14m` | time until the window resets |
| `★3` | unused Codex rate-limit reset credits |
| `$0.42` | session cost |

Everything except the two gauges is **off by default**. Turn things on in the
settings popup.

Degraded states are visually distinct from a real reading — a stuck probe never
looks like `0%`:

```
5h ██████████ ⛔️      hard limit reached
5h ████░░░░░░  31% ~  data is stale
5h ⋯ no data          probe never succeeded
✳ claude · —          model not known yet (no turn in this session)
```

## Settings

Press **`prefix+u`** (`ctrl+b` then `u`), or:

```bash
herdr plugin action invoke herdr-usage.settings
```

A popup with every toggle: bar style (6 sets), vendor icon set, bar width,
which gauges to show, thresholds, refresh interval, per-provider enable.

`setup-sidebar` installs that shortcut as part of its managed block, so
`--undo` removes it again. If the key is already bound — by Herdr or by you —
the block installs **without** a shortcut rather than hijacking it, and says
so. Pick another with `display.settings_key`, or set it to `""` to skip the
binding entirely.

Or edit `~/.config/herdr/plugins/config/herdr-usage/config.toml` directly —
your comments and formatting survive; the plugin only rewrites the one line it
needs to.

```toml
[refresh]
interval_secs = 300        # probe at most this often
min_interval_secs = 60     # floor, so a typo can't hammer the API

[display]
bar_set = "blocks"         # blocks|shade|squares|dots|half|ascii
bar_cells = 10
gauges = ["5h", "wk"]
show_model = true
show_icon = true           # vendor icon before the model name
icon_set = "brand"         # brand|geometric|ascii
settings_key = "prefix+u"  # opens the settings popup; "" disables
show_plan = false
show_burn = false
show_resets_in = false
show_credits = false
show_cost = false
show_scoped = false

[thresholds]
warn = 70                  # yellow at/above
crit = 90                  # red at/above
stale_multiplier = 3       # mark stale after 3x the refresh interval

[providers.codex]
enabled = true
[providers.claude]
enabled = true
```

### Bar styles

```
blocks   ████░░░░░░      dots     ●●●●○○○○○○
shade    ▓▓▓▓░░░░░░      half     ▮▮▮▮▯▯▯▯▯▯
squares  ■■■■□□□□□□      ascii    ####------
```

### Vendor icons

The icon reflects **which account is billed**, not the agent's name — a Hermes
pane routed to Codex shows the OpenAI mark.

```
brand       Opus       Terra       Qwen      (Nerd Font required)
geometric  ◆ Opus      ◇ Terra      ⌂ Qwen
ascii      [A] Opus    [G] Terra    [L] Qwen  (any terminal)
```

`brand` uses Nerd Font private-use codepoints (`cod-claude` U+EC82,
`cod-openai` U+EC81, `cod-robot` U+EC20). They need a [Nerd
Font](https://www.nerdfonts.com/) — without one they render as boxes, so
switch to `geometric` or `ascii`.

Check your terminal before committing to a set:

```sh
python3 scripts/glyphcheck.py
```

An unrecognised provider gets **no icon** rather than a default one: showing
one vendor's logo next to another's model would be as misleading as showing
the wrong percentage.

## Adding another provider

Declaratively, no code. Give it a command that prints JSON and say where the
numbers live:

```toml
[providers.mytool]
enabled = true
command = ["mytool", "usage", "--json"]
account_id = "$.account.id"
plan = "$.plan"

[[providers.mytool.gauges]]
label = "5h"
pct = "$.limits.five_hour.used_percent"
resets_at = "$.limits.five_hour.resets_at"
```

Paths are a dotted subset of JSONPath (`$.a.b[0].c`). A path that doesn't
resolve, or resolves to something that isn't a number, renders as `⋯ no data`
— it never invents a percentage and never crashes the refresh.

## How it works

```
[[startup]] ─┐
events ──────┼─► main.py refresh ─► cache fresh? ─ yes ─► paint from cache (~10ms)
action ──────┘                           │
                                         no
                                         ▼
                              one probe per ACCOUNT
                                         │
                                         ▼
                            herdr pane report-metadata
                                (every matching pane)
```

**Data sources**, both local, read-only, and quota-free:

| Provider | Source |
|---|---|
| Codex | `codex app-server` → JSON-RPC `account/rateLimits/read` |
| Claude | `claude -p` → `get_usage` control request (answered before any model turn) |

Model identity comes from `~/.codex/state_5.sqlite` (opened `mode=ro`) and the
Claude session transcript. Nothing is ever written to `~/.codex` or `~/.claude`.

### Wrapper agents (Hermes)

Some agents are not providers. [Hermes](https://github.com/NousResearch/hermes)
routes each session to an account of its own choosing, so two Hermes panes side
by side can bill to different subscriptions — or to none at all.

For those panes the provider is resolved **per session**, from
`~/.hermes/state.db` (`sessions.billing_provider` + `billing_base_url`, opened
read-only), not from the agent name:

| Session routed to | Gauge shown |
|---|---|
| `anthropic` @ `api.anthropic.com` | your Claude quota |
| `openai-codex` @ `chatgpt.com` | your Codex quota |
| a local llama.cpp, a proxy, any other endpoint | **none** |

That last row matters: a session on a local model consumes no subscription
quota, so it gets no gauge rather than another account's numbers. An
`anthropic` session pointed at an unrecognised base URL is likewise not
claimed — we cannot prove it is the same account.

```
w4:p1  agent=hermes  provider=claude  model=Opus     5h ██████░░░░  57%
wA:p1  agent=hermes  provider=codex   model=Terra    5h ██░░░░░░░░  13%
```

### Cost

Measured on a Linux laptop:

| | wall | CPU | RSS |
|---|---|---|---|
| codex probe | 0.6–2.0 s | 0.5 s | 168 MB |
| claude probe | 1.2–1.4 s | 0.55 s | 274 MB |
| **cache hit** | **0.06 s** | **0.04 s** | 10 MB |

At the default 5-minute interval that's about **12 CPU-seconds per hour** —
0.3% of one core — and 12 API calls per provider. There is no daemon and no
background timer; event hooks only ever read the cache.

## Things worth knowing

**Quota is account-level, not per-pane.** Two Claude panes share one 5-hour
window. The gauges show the same numbers on every pane of that provider,
because that is the truth.

**Colour comes from your config, not from the plugin.** Herdr strips ANSI from
metadata token values, and its numeric `gt`/`lt` rules can't match a bar
string. So the plugin picks a severity band and emits one of
`uq_g5h_ok` / `_warn` / `_crit` / `_none`; the managed block gives each name a
fixed colour. That's why `setup-sidebar` needs to touch your config.toml.

**Tokens don't survive a Herdr server restart.** That's a Herdr property, not a
bug here — the `[[startup]]` hook repaints them. After `plugin link` (which
fires no startup hook), run the `refresh` action once.

**The model is unknown until the first turn.** A freshly opened Claude pane has
no transcript, so the label shows `—` rather than a guess.

## Remote clients (Windows, or any `herdr --remote` / saved-machine setup)

If you connect to the Herdr server from another machine, the work is **split
across both sides**, because Herdr draws the sidebar locally:

> "Your local Herdr draws the UI, including its sidebar, menus, and theme."
> "The UI uses the client's local theme, sidebar settings, and keybindings by
> default. … Herdr does not copy local command plugins, configuration,
> executables, or secrets onto SSH hosts."

| | Where |
|---|---|
| The plugin (probes, tokens) | **Server** — it needs the agent CLIs and their logins |
| The sidebar row layout | **Client** — it renders the tokens |

So install the plugin on the server as usual, then copy **only the managed
block** into the client's own `config.toml`:

| Client OS | Config path |
|---|---|
| Linux | `~/.config/herdr/config.toml` |
| macOS | `~/Library/Application Support/herdr/config.toml` |
| Windows | `%APPDATA%\herdr\config.toml` |

On Windows, CPython has no `_curses`, so the settings popup uses a line-mode
prompt with the same keys.

Print the block from the server and paste it there:

```bash
python3 main.py print-sidebar        # on the server
```

Then on the client, apply it without detaching — use the UI's `reload config`
action, or `herdr server reload-config`.

Do **not** install the plugin on the client: it would probe that machine's
agent logins, which are not the ones you are watching.

### Several machines at once

With [saved SSH machines](https://herdr.dev/docs/connecting-machines/), your
client shows agents from every connected machine in one list. Each machine's
own Herdr server runs its own copy of this plugin, reports tokens for its own
panes, and the client renders them all — **no extra configuration, and no code
here knows about machines at all.**

Install the plugin once per machine whose agents you want gauged, and put the
sidebar block in the client's config once.

The generated block includes Herdr's built-in `machine` token, which renders
only when more than one machine is connected and drops itself otherwise. Two
machines with different accounts therefore never show indistinguishable
gauges:

```
┌──────────────────────────┐
│✳ local · claude · Opus   │
│5h ████░░░░░░  31%        │
│✳ build-box · codex · Sol │
│5h █████████░  92%        │
└──────────────────────────┘
```

Each machine's quota is read on that machine, by that machine's own login.
Nothing is proxied and no credential crosses the SSH boundary.

Caveat: a disconnected machine's row stays visible but dimmed, showing its
last known values. That is Herdr's cached state, not live data — the `~`
stale marker only tracks the plugin's own cache, not the connection.

## Manual sidebar setup

If you'd rather not let the plugin edit your config, add this yourself:

```toml
# >>> herdr-usage managed block
[ui.sidebar.agents]
row_gap = 0
rows = [
  ["state_icon", "machine", "agent", "$uq_agent"],
  ["$uq_model", "$uq_plan"],
  [
    { token = "$uq_g5h_ok", fg = "#a6e3a1" },
    { token = "$uq_g5h_warn", fg = "#f9e2af" },
    { token = "$uq_g5h_crit", fg = "#f38ba8", bold = true },
    { token = "$uq_g5h_none", fg = "#6c7086", dim = true },
  ],
  [
    { token = "$uq_gwk_ok", fg = "#a6e3a1" },
    { token = "$uq_gwk_warn", fg = "#f9e2af" },
    { token = "$uq_gwk_crit", fg = "#f38ba8", bold = true },
    { token = "$uq_gwk_none", fg = "#6c7086", dim = true },
  ],
  ["$uq_burn"],
  ["workspace", "tab"],
]
# <<< herdr-usage managed block
```

Only one of the four variants per gauge is ever set, and Herdr drops missing
tokens along with their separators — so each row renders exactly one coloured
gauge, and panes with no usage data render nothing.

Add `"$uq_plan"` to the first row, or a `["$uq_burn", "$uq_extra"]` row, to
surface the extras.

## Troubleshooting

```bash
python3 main.py doctor
```

Prints every path, whether the sidebar block is installed, which agent panes
Herdr sees, and the live result of each probe.

| Symptom | Cause |
|---|---|
| No gauges at all | Sidebar block missing — run `setup-sidebar` |
| Gauges vanished after restarting Herdr | `[[startup]]` didn't fire; run the `refresh` action |
| `⋯ no data` | That provider's probe failed — check `doctor` |
| Model shows `—` | No assistant turn in that session yet |

## Tests

```bash
python3 -m unittest discover -s tests -t .          # authored alongside the code
python3 -m unittest discover -s tests_indep -t .    # written independently
```

`tests_indep/` is deliberately separate from the implementation and primary
suite. It caught three bugs the first suite missed.

## Licence

MIT
