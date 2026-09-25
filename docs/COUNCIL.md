# Assumption council — Herdr agent usage plugin

Status: **open**. Nothing here is decided until Eduardo signs off.
Everything below is grounded in `docs/research/` and re-verified live on this box.

---

## 1. What the investigation actually proved

### Both providers hand us a percentage. No token math.

Codex, quota-free, ~0.2s to first byte:

```
printf '<initialize>\n<account/rateLimits/read>\n' | codex app-server
→ primary  {usedPercent:10, windowDurationMins:300,   resetsAt:1790301885}
  secondary{usedPercent:26, windowDurationMins:10080, resetsAt:1790691275}
  planType:"team", credits{hasCredits,unlimited,balance},
  rateLimitResetCredits{availableCount:3, credits:[...]}
```

Claude, zero tokens billed, ~1.6s:

```
printf '{"type":"control_request",...,"subtype":"get_usage","skip_behaviors":true}' \
  | claude -p --verbose --safe-mode --input-format stream-json \
      --output-format stream-json --no-session-persistence
→ rate_limits.five_hour {utilization:11, resets_at:"2026-09-25T01:59:59Z"}
  rate_limits.seven_day {utilization:9,  resets_at:"..."}
  limits[] {kind, group, percent, severity, resets_at, scope, is_active}
  model_scoped[] {display_name:"Fable", utilization:0, resets_at}
  extra_usage{...}, spend{...}, subscription_type
```

Consequence: **we never derive a percentage from local token counting.** The
denominators are server-side and unpublished; `ccusage` and friends can only
approximate. We read the server's own number or we show nothing.

### Three things that break the obvious design

**(a) Quota is account-level, not pane-level.** Two Claude panes side by side
share one 5h window. A per-pane gauge that looks per-pane is a lie.

**(b) Herdr does not know the model.** `herdr pane list` gives `agent: "claude"`
and a session id — never a model. Live proof: the Claude pane's title is just
`"✳ Claude Code"`. (Hermes panes happen to put the model in the title —
`✓ chameleon · gpt-5.6-luna · ~/chameleon` — but that is Hermes's own
convention, not a Herdr feature.)

**(c) The sidebar strips ANSI.** Colour in the Agents sidebar comes only from
the *user's* `ui.sidebar.agents` rules in their own `config.toml`. A plugin
cannot set colour. Unicode block characters do survive byte-exact, 80 chars per
token, 32 tokens per pane, sidebar width 18–36 columns.

---

## 2. Proposed design

### 2.1 One poll per account, fanned out to panes

```
provider probe ──> AccountUsage{ provider, account_id, gauges[], plan, fetched_at }
                        │
                        └─> every pane whose agent == provider gets the same numbers
```

A "gauge" is deliberately not three hardcoded slots:

```python
Gauge(key="5h"|"7d"|"30d"|"weekly:Fable"|...,
      label, used_fraction, resets_at, severity, source)
```

Renderer draws whatever the provider returned. Copilot's calendar-month gauge
and Claude's server-invented `model_scoped` rows then need no new code.

### 2.2 Sidebar tokens

Per pane, emitted via `herdr pane report-metadata`:

| token | example | purpose |
|-------|---------|---------|
| `g5h`  | `███░░░░░░░ 11%` | the bar (unicode, no colour) |
| `p5h`  | `11`            | bare numeric, so the user's `gt`/`lt` rules can colour it |
| `g7d` / `p7d` | same | weekly |
| `model` | `S5` / `opus` | short model label |
| `stale` | `!` | set when data is older than N minutes |

Colour needs a managed block in the user's own `config.toml`. The plugin ships
a `setup-sidebar` action that resolves the one config file, prints its absolute
path, asks for confirmation, and writes only a marked block. Never silent.

### 2.3 Model resolution — degrade honestly

| agent | source | when unknown |
|-------|--------|--------------|
| codex | `state_5.sqlite:threads.model` keyed by the pane's thread id | `—` |
| claude | last `message.model` in the session transcript jsonl | `—` until first turn |
| other | config-file-declared command | `—` |

A fresh Claude pane has no transcript yet, so the model is genuinely unknown
for the first turn. Show `—`, never a guess.

### 2.4 Config

`~/.config/herdr/plugins/<id>/config.toml` — which gauges to show, bar width,
poll interval, thresholds, glyph set, per-provider enable, and a
`[providers.<name>]` table for adding a CLI that isn't built in.

---

## 3. Decisions from the council (round 1)

| # | Question | Decision |
|---|----------|----------|
| — | Extras (burn rate, countdown, reset credits, severity, ctx fill, blocked state, plan badge, staleness, cost) | **All in**, each individually toggleable in the config menu |
| 1 | Sidebar organisation | See §3.1 — labelled gauge per row, default 2 rows |
| 2 | Config menu | Herdr **popup** placement (`[[panes]] placement = "popup"`), our own TUI |
| 3 | Gauge model | Provider-driven windows, no hardcoded 5h/7d/30d slots |
| 4 | Restart behaviour | See §3.2 — one-shot refresh on events, no daemon by default |
| 5 | User-added providers | **Declarative**: command + JSON field paths in the config file |

### 3.1 Sidebar layout

The sidebar row layout is global (or per agent *kind* via `rows_by_agent`), never
per-pane. That works in our favour: **a missing token drops itself and its
separator, and a row with no tokens disappears entirely.** So one layout serves
every pane — a shell pane with no usage data simply doesn't render those rows.

Chosen default, at the default 26-column width:

```
┌──────────────────────────┐
│✳ claude · Opus           │   state_icon · agent · $uq_model
│5h ███████░░░ 71%         │   $uq_g5h
│7d █░░░░░░░░░  9%         │   $uq_g7d
│herdr-usage · t1          │   workspace · tab
└──────────────────────────┘
```

The window label lives *inside* the token (`5h `), not as a separate token —
separate tokens would render as `5h · ███████░░░` with Herdr's ` · ` joiner.

With every extra enabled (still fits 26 columns):

```
┌──────────────────────────┐
│✳ claude · Opus · team    │
│5h ███████░░░ 71%         │
│7d █░░░░░░░░░  9%         │
│wk Fable ░░░░░░ 0%        │
│↯ 1h20m · ⟳ 2h14m         │   burn-rate ETA · resets-in
│ctx ██░░░░ 34%            │   this pane's own context fill
│$0.42 · ⏱ 4m              │
│herdr-usage · t1          │
└──────────────────────────┘
```

Degraded states, all distinct from a real number:

```
│5h ⋯ no data              │   probe never succeeded
│5h ███████░░░ 71% ⌛      │   data older than staleness threshold
│5h ██████████ BLOCKED     │   429 / rateLimitReachedType / locked_reason
│✳ claude · —              │   model not yet known (fresh pane, no turn yet)
```

**Colour.** `gt`/`lt` rules need the whole token value to parse as a number,
which a bar never will, and `contains` on block runs is fragile (it couples the
colour thresholds to the bar width and glyph set). **Resolved instead by putting
severity in the token *name*:** the plugin computes the band and emits exactly
one of `$uq_g5h_ok` / `_warn` / `_crit` / `_none`, clearing the other three. Each
name gets a fixed `fg` in the config — no `rules` at all.

VERIFIED live on pane `w4:p7` — exactly one token present at each band:

```
OK  : {'uq_g5h_ok':   '5h ███░░░░░░░ 31%'}
WARN: {'uq_g5h_warn': '5h ████████░░ 82%'}
CRIT: {'uq_g5h_crit': '5h █████████░ 94%'}
```

and the matching config block → `config: ok` on 0.9.1:

```toml
rows = [
  ["state_icon", "agent", "$uq_model"],
  [{ token = "$uq_g5h_ok",   fg = "#a6e3a1" },
   { token = "$uq_g5h_warn", fg = "#f9e2af" },
   { token = "$uq_g5h_crit", fg = "#f38ba8", bold = true },
   { token = "$uq_g5h_none", fg = "#6c7086", dim = true }],
  ...
]
```

Because a row drops any missing token *and its separator*, the three unset
variants render nothing and the row shows one correctly-coloured gauge. Cost: 4
token slots per gauge out of 32 — 2 gauges = 8, well inside budget. Benefit:
thresholds live in the plugin config (numbers), bar width and glyph set become
free variables, and the `severity` a provider reports (Claude sends
`normal|warning|critical`) can drive the band directly instead of being
re-derived.

The bar still never renders full below 100% (fill is `ceil` clamped to
`cells-1`), so a full bar means exhausted.

### 3.2 Refresh model — measured, then decided

Herdr wipes pane metadata tokens on **server restart** (documented) and there is
no `pane.metadata_updated` event. Every trigger runs the same idempotent
one-shot `refresh`:

> read cache → if older than TTL, probe each account → write tokens on every
> matching pane → exit

**Measured cost of one probe on this box** (`/usr/bin/time`, includes children):

| Probe | wall | user+sys CPU | peak RSS |
|-------|------|--------------|----------|
| codex `app-server` | ~0.6–2.0 s | **0.5 s** | 168 MB |
| claude `get_usage` | ~1.2–1.4 s | **0.55 s** | 274 MB |
| cache-hit path (python, no probe) | 0.01 s | ~0.01 s | 10 MB |

So a probe is *not* free — ~1 CPU-second and a 270 MB transient for both
providers. That settles the design: **the cache TTL is the rate limiter, and the
event hooks only ever consult the cache.**

`refresh.interval_secs = 300` (5 min, configurable) →
**~12 CPU-seconds/hour, 0.3%** of one core, 12 API calls/hour/provider. Event
hooks firing 100× in that window cost 100 × 0.01 s = 1 CPU-second and zero API
calls, because they all hit the cache.

Triggers:

| Trigger | Probes? |
|---------|---------|
| `[[startup]]` | yes — server restart wiped the tokens |
| `[[events]] on = "pane.agent_status_changed"` | only if cache older than TTL |
| `[[events]] on = "pane.agent_detected"` | only if cache older than TTL |
| `refresh` action (+ optional keybind) | forces a probe, ignores TTL |

No daemon, no pidfile, no sleep loop, no live timer. The countdown values are
rendered from the cached `resets_at` at write time and go stale between
refreshes — that is what the `⌛` marker is for, and at a 5-minute TTL the
countdown is never more than 5 minutes off.

One guard worth having: a `refresh.min_interval_secs` floor (default 60) so a
misconfigured `interval_secs = 1` can't hammer the providers.

---

## 4. Decisions from the council (round 2)

| # | Question | Decision |
|---|----------|----------|
| 4 | CPU / API cost | 5-minute cache TTL, configurable, `min_interval_secs = 60` floor. Event hooks read cache only. No live timer. Measured in §3.2 |
| 1 | Colouring | Severity in the **token name** (`_ok`/`_warn`/`_crit`/`_none`), fixed `fg` per name. `LIMIT` label kept for the blocked state |
| R2.1 | Sidebar config | Written **automatically on install**, with a `setup-sidebar --undo` route and full manual instructions in the README |
| R2.2 | Default rows | 2 gauges (5h + weekly), extras off |
| R2.3 | Context gauge | **Held.** Not in v1 |

---

## 5. Extras — all accepted, all toggleable


1. **Countdown, not timestamp** — `resets in 2h14m` beats `resets_at 01:59Z`.
2. **Burn rate / projected exhaustion** — two samples give `out in ~1h20m`.
   The single most actionable number here, and nobody else shows it.
3. **Codex reset credits** — `availableCount: 3` free full resets sitting
   unused on this account right now. Worth a badge.
4. **Server-provided `severity`** — Claude returns `normal|warning|critical`
   per limit. Use it instead of inventing thresholds where available.
5. **Hard-blocked state** — `rateLimitReachedType`, `locked_reason`, and the
   429 `quotaLimits` envelope. A distinct visual state from "99%".
6. **Context-window fill for the pane's own session** — this one *is* genuinely
   per-pane (`threads.tokens_used` / model context window for codex). Arguably
   more useful moment-to-moment than the account quota.
7. **Plan badge** — `team` / `plus` / `max20x`, so the bar's meaning is legible.
8. **Data staleness** — when the last successful poll was; a stuck probe must
   look stuck, not look like 0%.
9. **Session cost** — Claude's `session.total_cost_usd`.
10. **Multi-account** — Eduardo runs more than one account per provider in
    other contexts; the model should key on `account_id`, not on the provider.
