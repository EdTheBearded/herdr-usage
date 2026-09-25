# Provider usage limits & local usage sources

Source-of-truth reference for the Herdr per-agent usage-gauge plugin.

**All retrieval dates in this document are 2026-09-24** unless a row says otherwise.
Limits change often — re-verify before shipping new defaults.

Conventions used below:

- **Window kind**: `5h rolling`, `7d rolling`, `7d fixed-reset`, `30d rolling`, `calendar month`, `RPM`, `RPD`.
- **% direct?** — does the provider hand us a *percentage consumed* (so a gauge needs no denominator)?
- **UNVERIFIED** — no official documentation found; the best community source is cited separately and marked unofficial.

---

## 0. Executive answer: percentage vs denominator

This is the single most important distinction for the plugin's data model.

### Group A — percentage + reset timestamp (gauge is trivial and plan-agnostic)

| Provider | Field shape | Where we read it |
|---|---|---|
| Anthropic Claude Code | `rate_limits.five_hour.used_percentage` (0–100) + `resets_at` (unix s); same for `seven_day` and `spend_limit` | status-line JSON on stdin ([statusline docs][cc-statusline]) |
| OpenAI Codex CLI | `used_percent` + `window_minutes`/`windowDurationMins` + `resets_at` for `primary` and `secondary` | session rollout JSONL under `~/.codex/sessions/**`; app-server `account/rateLimits/read` |
| xAI Grok (consumer/SuperGrok, incl. Grok Build CLI) | "usage pool is shown as a percentage used", weekly reset date | Settings → Usage UI only; no documented local/CLI read ([xAI FAQ][xai-faq]) |

For Group A we **never** need to know the plan. We store `{window_id, used_percent, resets_at, window_seconds}` and render directly.
Note the trap: the window identity must come from the payload's own duration field, not from the key name (`primary` is not always 5h — see Surprise #2).

### Group B — denominator required (we must know the plan tier)

| Provider | Unit | Denominator source |
|---|---|---|
| GitHub Copilot | GitHub AI Credits / month | plan table: Pro 1,500 · Pro+ 7,000 · Max 20,000 ([docs][gh-individual]) |
| Google Gemini CLI / Code Assist | model requests / user / day | tier table: 1,000 / 1,500 / 2,000 / 250 ([docs][gca-quotas], [geminicli][gcli-quota]) |
| Factory Droid | "Standard Usage" in 5h/7d/30d rolling windows | no published numeric allowance; only relative multipliers (~5x, ~10x) ([docs][factory-ind]) |
| Devin | daily + weekly quota, or ACUs | no published numeric allowance ([docs][devin-selfserve]) |
| Cursor | USD-denominated usage pools per billing month | plan price implies pool; no published token/request count ([docs][cursor-pricing]) |
| Kimi Code | 5h rolling window + monthly total quota | no published numeric allowance ([docs][kimi-membership]) |
| Amp | USD credits / monthly tier | no published request allowance ([docs][amp-pricing]) |
| OpenCode Zen | USD balance, optional monthly USD cap | user-configured cap ([docs][oc-zen]) |

**Design consequence:** the plugin's core model should be
`Gauge { window: Duration, used_fraction: f64, resets_at: Option<Instant>, source: Percent | Counted{used, limit} }`.
Group A fills `Percent` with no config. Group B needs `limit` from the config file, keyed by `provider + plan`, and must degrade to "unknown plan → show raw count, no bar" rather than guessing a denominator.

---

## 1. Anthropic — Claude Code

### Windows and reset semantics

| Plan | Window kind | Documented quantity/unit | % direct? | Reset semantics | Source |
|---|---|---|---|---|---|
| Pro ($20/mo) | 5h session | No published message/token count — "more usage per session than Free"; varies by message length, conversation length, model, effort | Yes (via CLI status line) | "Your session-based usage limit will reset every five hours" | [Pro plan][cc-pro] |
| Pro | 7d, fixed reset | No published quantity; "weekly usage limit that applies across all models" | Yes | "Weekly limits reset at a fixed time each week that is assigned to your account… your reset day and time stay the same" | [Pro plan][cc-pro] |
| Max 5x ($100/mo) | 5h session | "five times the Pro plan's per-session usage allowance" | Yes | rolling 5h | [Max plan][cc-max] |
| Max 20x ($200/mo) | 5h session | "20 times the Pro plan's per-session usage allowance" | Yes | rolling 5h | [Max plan][cc-max] |
| Max 5x / 20x | 7d, fixed reset | weekly limit across all models; no published quantity | Yes | fixed weekly reset assigned to the account | [Max plan][cc-max] |
| Team (Standard / Premium seat) | 5h + weekly | per-seat allowance, size depends on Standard vs Premium seat; shared with Claude chat and Cowork | **Not in status line** (see below) | "resets on a rolling five-hour window and a weekly window" | [Claude Code costs][cc-costs], [Team/Enterprise][cc-team] |
| Enterprise, seat-based | 5h + weekly | per-seat allowance by seat tier | Not in status line | same as Team | [cc-costs][cc-costs] |
| Enterprise, usage-based (incl. self-serve Enterprise) | none | "no per-seat usage limits — usage is based on consumption and billed at API rates" | n/a | n/a | [Team/Enterprise][cc-team] |
| API key (Console/Bedrock/Vertex/Foundry) | none | pay-as-you-go per token; `/cost` shows session spend | n/a | n/a | [models-usage-limits][cc-models-usage] |

**Anthropic publishes no absolute numbers** for the 5h or weekly allowance on any subscription plan. Every figure circulating ("X messages", "Y Opus hours") is unofficial. Do not put such numbers in config defaults.

### The model-family limits (the "separate weekly Opus limit")

Claude Code's documented error strings are:

```
You've hit your session limit · resets 3:45pm
You've hit your weekly limit  · resets Mon 12:00am
You've hit your Opus limit    · resets 3:45pm
You've hit your Sonnet limit  · resets 3:45pm
```

> "The session and weekly limits are shared across all models, so switching models doesn't restore access. The Opus and Sonnet limits each apply only to requests to that model family, so switching to a model outside the family with `/model` keeps you working."
> — [Claude Code error reference][cc-errors]

So there are **four** distinct limit concepts, not two. The *Opus* and *Sonnet* limits are model-family limits with their own reset times; the docs do **not** state their window length or quantity. **UNVERIFIED**: whether the Opus limit is weekly, 5-hourly, or something else — the error message only shows a reset clock time. The status-line JSON exposes only `five_hour`, `seven_day` and `spend_limit`, so the plugin **cannot** gauge the Opus/Sonnet family limits from local data today.

Fable models are a separate, documented carve-out:

> "On Max plans, premium seats on Team plans, and premium seats on seat-based Enterprise plans … **You can use up to 50% of your weekly usage limits on Fable models** at no extra cost… you can never use more than your weekly limit."
> — [Claude Fable models on your plan][cc-fable]

On Pro plans and Team/Enterprise *standard* seats, Fable 5 / 5.1 are **not** included in plan limits at all and bill to usage credits from the first token ([cc-fable]).

### Overage / extra usage

- **Usage credits**: Pro, Max, Team and seat-based Enterprise can enable usage credits to keep working past the included limit, billed at standard API rates ([cc-pro], [cc-max], [cc-team]).
- `/usage-credits` in the CLI opens claude.ai Settings → Usage (Pro/Max), or org admin usage settings, or files a request to admins ([cc-costs]).
- `/usage` shows a usage-credits row: Pro/Max → current-month spend vs monthly spend limit; Team/Enterprise → own spend vs any limit set for you ([cc-costs]).
- **Limit resets** (a consumable) can restore either the 5h or the weekly limit to full ([cc-max]).
- API credits via Claude Console are a *separate* system from plan usage; Claude Code prompts before switching ([cc-pro-code]).

### How limits are surfaced to the user

| Surface | What it shows | Source |
|---|---|---|
| `/usage` in CLI | Plan usage bars for session + weekly, activity stats, attribution breakdown (skills/subagents/plugins/MCP), behavior flags, loops; `d`/`w` toggles 24h vs 7d | [cc-costs] |
| Status line JSON (stdin) | `rate_limits.five_hour.used_percentage`, `.resets_at`; `rate_limits.seven_day.*`; `rate_limits.spend_limit.*` | [cc-statusline] |
| Error text | "You've hit your … limit · resets \<time\>" | [cc-errors] |
| claude.ai Settings → Usage | progress bars for the 5h session and the weekly limits (all models, and Fable separately) | [cc-bestpractices] |
| VS Code extension | Account & usage dialog with Day/Week toggle | [cc-costs] |

### Local, non-scraping read path (works for OAuth-only users)

The documented mechanism is the **status line**: Claude Code pipes a JSON object to a user-configured shell script on stdin. Fields ([cc-statusline]):

```
rate_limits.five_hour.used_percentage    0–100
rate_limits.five_hour.resets_at          unix epoch seconds
rate_limits.seven_day.used_percentage    0–100
rate_limits.seven_day.resets_at          unix epoch seconds
rate_limits.spend_limit.used_percentage  (behind a Claude apps gateway spend limit)
rate_limits.spend_limit.resets_at
```

Critical caveats, all documented on the same page:

- `rate_limits` **"appears only for claude.ai Pro and Max subscribers, or behind a Claude apps gateway that sets a spend limit for you, and only after the first API response in the session."** → Team and Enterprise seats get **no** `rate_limits` block even though they *have* 5h/weekly windows. The plugin must handle "authenticated, has limits, no data" as a first-class state.
- Each window may be independently absent.
- Claude Code re-runs the status line when a window reaches its `resets_at`.
- API-key sessions have no `rate_limits`.

There is **no documented CLI subcommand or public REST endpoint** for an OAuth subscriber to query their own plan usage. Programmatic paths that exist are all org/API-side and need an admin key: the [Enterprise Analytics API][cc-analytics-api] (Enterprise plan, `read:analytics` scope) and the [Claude Code Analytics API][cc-cc-analytics-api] (Console/API orgs, Admin API key). Neither is reachable by a Pro/Max OAuth user. OpenTelemetry export ([monitoring-usage][cc-otel]) gives per-user token/cost metrics but not plan-limit percentages.

**Local files inspected on this machine (Claude Code 2.1.282):** `~/.claude/` contains `stats-cache.json` (daily message/session/tool counts — *not* limits), `policy-limits.json` (org policy restrictions — *not* usage), `statusline-command.sh`, `projects/`, `sessions/`. A grep for `rate_limit|rateLimit|five_hour|seven_day` across `~/.claude/**.json` returned nothing. Conclusion: **the status line is the only local rate-limit channel**; it must be installed as a hook-like script, and the plugin should ship one rather than expect to read a cache file.

### Model identifiers (for the icon/short-label table)

Current Claude API model IDs ([models overview][cc-models], retrieved 2026-09-24):

| Model | API ID | API alias | Suggested label | Suggested glyph |
|---|---|---|---|---|
| Claude Fable 5.1 | `claude-fable-5-1` | `claude-fable-5-1` | `FAB` | `✦` |
| Claude Fable 5 | `claude-fable-5` | — | `FAB` | `✦` |
| Claude Opus 5.5 | `claude-opus-5-5` | `claude-opus-5-5` | `OPU` | `◆` |
| Claude Opus 5 / 4.8 / 4.7 / 4.6 | `claude-opus-5`, `claude-opus-4-8`, … | — | `OPU` | `◆` |
| Claude Sonnet 5 | `claude-sonnet-5` | `claude-sonnet-5` | `SON` | `◇` |
| Claude Sonnet 4.6 / 4.5 | `claude-sonnet-4-6`, `claude-sonnet-4-5-20250929` | — | `SON` | `◇` |
| Claude Haiku 4.5 | `claude-haiku-4-5-20251001` | `claude-haiku-4-5` | `HAI` | `·` |

Claude Code also accepts **aliases** rather than IDs, and the status line may report either. Aliases ([model-config][cc-modelconfig]): `default`, `best`, `fable`, `sonnet`, `opus`, `haiku`, `sonnet[1m]`, `opus[1m]`, `opusplan`. On the Anthropic API, `opus` → Opus 5.5, `sonnet` → Sonnet 5, `fable` → Fable 5.1; on Bedrock/Google Agent Platform `sonnet` → Sonnet 4.5; on Microsoft Foundry `opus` → Opus 4.6. The `default` model on Pro/Max/Team/Enterprise/API is Opus 5.5 as of v2.1.280.

→ **Mapping rule for the plugin:** match on the family substring (`fable`, `opus`, `sonnet`, `haiku`) after stripping provider prefixes (`us.anthropic.`, `anthropic.`) and the `[1m]` suffix. Do not hard-code dated IDs.

---

## 2. OpenAI — Codex CLI

Note: OpenAI's Codex docs moved to `learn.chatgpt.com/docs/*`; `developers.openai.com/codex/pricing/` redirects there. ChatGPT Work and Codex share one usage pool ([pricing][oai-pricing]).

### Plans and windows

| Plan | Window kind | Documented quantity/unit | % direct? | Reset semantics | Source |
|---|---|---|---|---|---|
| Free ($0) | — | GPT-6 Luna at Standard speed, desktop app, subject to rollout; no Codex CLI row in the table | Yes (CLI reports whatever windows the account has) | — | [pricing][oai-pricing] |
| Go ($8/mo) | — | lightweight tasks; no per-window numbers | Yes | — | [pricing][oai-pricing] |
| Plus ($20/mo) | 5h primary | **estimated** local messages per 5-hour period, per model: GPT-6 Astra 5–45 · GPT-6 Sol 15–150 · GPT-6 Luna 350–3,000 · GPT-5.6 Sol 10–100 · GPT-5.6 Terra 25–200 · GPT-5.6 Luna 250–2,000 · GPT-5.5 15–80 · GPT-5.4 20–100 · GPT-5.4 mini 60–350 | Yes | rolling; "check your usage dashboard for current limits and reset times" | [pricing][oai-pricing] |
| Pro 5x ($100/mo tier) | 5h primary | 5x the Plus estimates (e.g. GPT-6 Sol 70–700) | Yes | rolling | [pricing][oai-pricing] |
| Pro 20x ($200/mo tier) | 5h primary | 20x the Plus estimates (e.g. GPT-6 Sol 300–3,000) | Yes | rolling | [pricing][oai-pricing] |
| Standard Business ($20–25/user/mo) | 5h primary | same estimates as Plus | Yes | rolling | [pricing][oai-pricing] |
| Business ($100 tier) | 5h primary | "Business ($100) uses the Pro 5x estimates" | Yes | rolling | [pricing][oai-pricing] |
| Enterprise / Edu **with** flexible pricing | none | "no fixed rate limits — usage scales with credits" | n/a | n/a | [pricing][oai-pricing] |
| Enterprise / Edu **without** flexible pricing | 5h primary | "same per-seat usage limits as Plus for most features" | Yes | rolling | [pricing][oai-pricing] |
| All subscription plans | weekly secondary | "**Weekly limits may also apply**" — no published quantity | Yes | rolling | [pricing][oai-pricing] |
| API key | none | usage-based API pricing | No | n/a | [pricing][oai-pricing] |

The per-model numbers above are explicitly **"estimates … not fixed message limits"**. They are usable as *rough* denominators for a tooltip, never as gauge denominators — and you do not need them, because the CLI gives percentages.

Note the "5h" and "weekly" framing comes from the observed payloads (`window_minutes: 300` and `window_minutes: 10080`), not from a prose statement in the docs; the docs say "per five-hour period" and "weekly limits may also apply".

### Credits / overage

- Plus and Pro users who hit the limit can **purchase additional credits** without upgrading ([pricing][oai-pricing]).
- Business/Edu/Enterprise with flexible pricing can purchase additional **workspace credits**.
- Any user can run extra local chats with an **API key** at standard API rates.
- An in-flight turn is allowed to finish when the limit is reached ("subject to fair use limits").
- Credit token rates are published per model (e.g. GPT-6 Sol 50 credits/1M input, 250/1M output; GPT-6 Astra 250/1,250) ([pricing][oai-pricing]).

### Local, non-scraping read path

Two documented surfaces plus one verified local file:

1. **`/status`** inside an interactive Codex CLI session — "If you want to see your remaining limits during an active Codex CLI session, you can use `/status`." ([pricing][oai-pricing]). Works for ChatGPT-OAuth users.
2. **`codex app-server`** JSON-RPC method **`account/rateLimits/read`**, plus the notification `account/rateLimits/updated`. Verified present in the shipped binary (codex-cli 0.156.1) by symbol inspection: both method names and the response struct `GetAccountRateLimitsResponse { ordinaryUsageAllowed, rateLimits, rateLimitsByLimitId, rateLimitResetCredits, accountId, rateLimitUpsell }` with `RateLimitWindow { usedPercent, windowDurationMins, resetsAt }` appear in the binary's protocol strings. `codex app-server generate-json-schema` will emit the authoritative schema. **This is the cleanest programmatic source and it works with OAuth-only auth**, since the daemon uses the stored `~/.codex/auth.json` credentials.
3. **Session rollout files** — `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` carry a `rate_limits` snapshot on token-count events. Verified live on this machine (2026-09-24):

```json
"rate_limits": {
  "limit_id": "codex", "limit_name": null,
  "primary":   {"used_percent": 7.0,  "window_minutes": 300,   "resets_at": 1790301885},
  "secondary": {"used_percent": 26.0, "window_minutes": 10080, "resets_at": 1790691275},
  "credits": {"has_credits": false, "unlimited": false, "balance": null},
  "individual_limit": null, "spend_control_reached": null,
  "plan_type": "team", "rate_limit_reached_type": null
}
```

Field-name drift is real: the wire/app-server form uses camelCase (`usedPercent`, `windowDurationMins`, `resetsAt`) while the rollout JSONL uses snake_case (`used_percent`, `window_minutes`, `resets_at`), and community reports also show `reset_after_seconds` and `reset_at` variants. **Parse defensively: accept both cases and both reset spellings.**

### `plan_type` strings

**No official documentation lists the `plan_type` values.** Observed/credible values:

| Value | Plan | Evidence |
|---|---|---|
| `team` | ChatGPT Business/Team workspace | Observed live in `~/.codex/sessions/.../rollout-*.jsonl` on this machine, 2026-09-24 |
| `plus` | ChatGPT Plus | Unofficial: [pi-cliproxyapi-quota issue #1][unof-plantype] (live-account payload) |

**UNVERIFIED** for `free`, `go`, `pro`, `enterprise`, `edu`, `business`. Treat `plan_type` as an opaque display string; do not branch behaviour on it, and never derive a denominator from it — the percentages are authoritative.

The binary also exposes `rateLimitReachedType` with values `rate_limit_reached`, `workspace_member_credits_depleted`, `workspace_owner_usage_limit_reached`, `workspace_member_usage_limit_reached` (from binary symbol inspection, codex-cli 0.156.1) — useful for a "why is this blocked" label.

### Model identifiers

Current Codex model slugs ([models][oai-models], retrieved 2026-09-24):

| Model | Slug | Label | Glyph |
|---|---|---|---|
| GPT-6 Astra | `gpt-6-astra` | `AST` | `★` |
| GPT-6 Sol | `gpt-6-sol` | `SOL` | `☀` |
| GPT-6 Luna | `gpt-6-luna` | `LUN` | `☾` |
| GPT-5.6 Sol / Terra / Luna | `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna` | `SOL`/`TER`/`LUN` | `☀`/`⊕`/`☾` |
| GPT-5.5 (retires from Codex 2026-10-14) | `gpt-5.5` | `5.5` | `∘` |
| GPT-5.4 / 5.4 mini (retired from ChatGPT sign-in 2026-08-31) | `gpt-5.4`, `gpt-5.4-mini` | `5.4` | `∘` |

Deprecated/removed in Codex with ChatGPT sign-in: `gpt-5.2`, `gpt-5.3-codex`, and (from 2026-10-14) `gpt-5.5`. **The `gpt-5.1-codex-max` slug named in the task brief is historical** — it is no longer in OpenAI's current model list; it survives only as a third-party gateway ID (e.g. OpenCode Zen's `gpt-5.1-codex-max`, deprecated 2026-07-23 [oc-zen]).

Reasoning-effort levels affect consumption but not the slug: `low`/`medium`/`high`/`xhigh`(Extra High)/`max`/`ultra` ([oai-models]).

---

## 3. Google — Gemini CLI / Gemini Code Assist

⚠️ **Status change:** "Unpaid tier and Google One users: Gemini CLI was replaced by **Antigravity CLI** on June 18th, 2026." ([geminicli quota page][gcli-quota], banner; page last updated 2026-06-18). Herdr lists both `qwen`-style agents *and* an `antigravity-cli` integration, so the plugin should treat Gemini CLI and Antigravity CLI as related-but-distinct providers.

| Tier / auth | Window kind | Documented quantity | % direct? | Reset semantics | Source |
|---|---|---|---|---|---|
| Google account — Gemini Code Assist for individuals | RPD | **1,000** model requests / user / day | No | daily | [gcli-quota], [gca-quotas] |
| Google AI Pro | RPD | **1,500** requests / user / day | No | daily | [gcli-quota] |
| Google AI Ultra | RPD | **2,000** requests / user / day | No | daily | [gcli-quota] |
| Gemini API key, free tier | RPD | **250** requests / user / day, Flash model only | No | "RPD quotas reset at midnight Pacific time" ([gemini-api-rl]) | [gcli-quota] |
| Gemini API key, pay-as-you-go | RPD | varies by tier | No | daily | [gcli-quota] |
| Vertex AI Express mode | — | varies; 90 days before billing required | No | — | [gcli-quota] |
| Code Assist **Standard** edition | RPD | **1,500** max requests / user / day (agent mode + Gemini CLI combined) | No | daily | [gca-quotas] |
| Code Assist **Enterprise** edition | RPD | **2,000** max requests / user / day | No | daily | [gca-quotas] |
| Workspace AI Ultra | RPD | **2,000** requests / user / day | No | daily | [gcli-quota] |
| Gemini for Google Cloud (all) | RPS | **2** requests per second per user per project | No | per-second | [gca-quotas] |
| Gemini for Google Cloud | RPD | 6,000 code requests/day; 960 chat/visualization/etc. requests/day | No | daily | [gca-quotas] |
| Code Assist on GitHub | RPD | ≥100 PR reviews/day per installation | No | aligned with Google Cloud quota resets | [gca-quotas] |

Explicitly documented behaviour worth encoding:

> "When in agent mode or when using the Gemini CLI, **one prompt might result in multiple model requests**… These daily request limits are aggregated across all interactions with any model version or family (for example, Pro, Flash)… Once the maximum number of requests per day is reached, no further requests can be made through these interfaces to any model until the quota resets."
> — [gca-quotas]

Unsupported tiers are called out: Google AI Plus, Workspace AI Standard/Plus, and AI Expanded are **not** supported for Gemini CLI ([gcli-quota]).

### Local read path

- `/stats model` — "You can check your current token usage and applicable limits using the `/stats model` command… a snapshot of your current session's token usage, as well as information about the limits associated with your current quota." ([gcli-quota]); `/stats` also has `session` and `tools` subcommands ([gcli-commands]). A usage summary is printed on session exit.
- `/upgrade` opens the Code Assist upgrade page (Google login only) ([gcli-commands]).
- **No documented percentage field, no documented local JSON file, no documented usage API for an end user.** The plugin must count requests itself or read `/stats` output, and needs the tier from config to compute a denominator.

### Model identifiers

Google does not publish a Gemini-CLI-specific model list on the quota page. From the current Gemini model IDs seen in first-party partner docs (Cursor, OpenCode Zen, both retrieved 2026-09-24): `gemini-3.1-pro`, `gemini-3.8-flash`, `gemini-3.7-flash`, `gemini-3.6-flash`, `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3-flash`. **UNVERIFIED as an official Gemini CLI list** — these are third-party gateway catalogues ([cursor-pricing], [oc-zen]), not Google documentation.

Suggested labels: `G-P` / `◈` for Pro, `G-F` / `◇` for Flash, `G-FL` for Flash Lite.

---

## 4. GitHub — Copilot CLI / Copilot

**Major change:** premium requests are legacy. "On June 1, 2026, GitHub replaced request-based billing with usage-based billing built on GitHub AI Credits." The premium-request docs are now under `.../request-based-billing-legacy/` and are explicitly scoped to "Copilot Pro and Copilot Pro+ subscribers on an existing annual plan who remained on legacy premium request-based billing after June 1, 2026" ([gh-legacy-requests]).

### Current model — AI Credits (monthly)

| Plan | Window kind | Documented quantity | % direct? | Reset semantics | Source |
|---|---|---|---|---|---|
| Copilot Free | calendar month | "an allowance of AI credits" (amount not stated) + 2,000 code completions/month; auto model selection only | No | 1st of month 00:00:00 UTC | [gh-individual] |
| Copilot Student | calendar month | allowance of AI credits + unlimited code completions | No | 1st of month 00:00:00 UTC | [gh-individual] |
| Copilot Pro ($10/mo) | calendar month | **1,500** AI credits (1,000 base + 500 flex) | No | 1st of month 00:00:00 UTC, fixed, independent of billing date | [gh-individual] |
| Copilot Pro+ ($39/mo) | calendar month | **7,000** AI credits (3,900 base + 3,100 flex) | No | same | [gh-individual] |
| Copilot Max ($100/mo) | calendar month | **20,000** AI credits (10,000 base + 10,000 flex) | No | same | [gh-individual] |
| Business / Enterprise | calendar month | "each assigned Copilot license comes with a monthly amount of included AI credits, which can be pooled at the billing entity level" (amount not stated on this page) | No | monthly | [gh-billing] |

1 AI credit = **$0.01 USD**. Credits **do not roll over**. Code completions and next-edit suggestions are **not** billed in credits and stay unlimited on paid plans. Copilot CLI prompts *do* consume credits. Paid plans get a 10% model-cost discount when using auto model selection. ([gh-individual])

Overage: upgrade (charged only the price difference, prior usage counts against the larger allowance) or set a **budget for additional usage** in USD; additional usage may be capped. Business/Enterprise users see a banner to request more budget from an admin. ([gh-individual])

Minimum Copilot CLI version for correct usage-based billing display: **1.0.48** ([gh-individual]).

### Legacy premium requests (still live for some annual subscribers)

| Plan | Window | Quantity | Source |
|---|---|---|---|
| Copilot Pro (legacy annual) | calendar month | 300 premium requests/month | [gh-legacy-requests] |
| Copilot Pro+ (legacy annual) | calendar month | 1,500 premium requests/month | [gh-legacy-requests] |
| additional | — | $0.04 per premium request | [gh-legacy-requests] |

Counters reset on the 1st of each month at 00:00:00 UTC; unused requests do not carry over. Each Copilot CLI prompt = 1 premium request × the model multiplier; Copilot code review = 13 premium requests. ([gh-legacy-requests])

**This monthly calendar window is precisely why the plugin needs a 30-day/monthly gauge kind distinct from the rolling windows.** Note it is *calendar month, fixed UTC reset*, not a 30-day rolling window — a `30d rolling` gauge would be wrong for Copilot and right for Factory Droid.

### Local read path

`/usage` inside an interactive Copilot CLI session shows ([gh-cli-usage]):

- AI Credits used in the current **session**
- session duration
- total lines of code edited
- per-model token breakdown

Plus `/context` (token usage overview) and `/compact`.

⚠️ `/usage` is documented as **session** statistics, not the monthly allowance remaining. The monthly allowance/remaining view is documented only in the web UI (github.com/settings/billing → Metered usage → Copilot) and in IDE status-bar widgets ([gh-legacy-monitor] for the legacy view). **No documented CLI command or public API returns "credits remaining this month" for a Copilot CLI user.** Config dir is `~/.copilot` (override `COPILOT_HOME`); inspection on this machine found `config.json`, `settings.json`, `session-state/*/events.jsonl`, `session-store.db` — no documented usage/entitlement file.

### Model identifiers

GitHub routes to third-party models and publishes multipliers/pricing at `docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing` (not enumerated here — the list churns). The CLI's default is "auto model selection". For the plugin, the practical labels are the underlying vendor families (Claude / GPT / Gemini), so reuse the mappings from §1, §2 and §3 plus `AUTO` / `⊙` for auto selection.

---

## 5. Short entries — the rest of Herdr's supported agents

Herdr v0.9.1 lists these integrations: Pi, OMP, Claude Code, Codex, GitHub Copilot CLI, Devin CLI, Droid, Kimi Code CLI, OpenCode, Kilo Code CLI, Hermes Agent, Qoder CLI, Qwen Code, Letta Code, Cursor Agent CLI, MastraCode, Antigravity CLI, Grok CLI ([herdr-integrations]).

### Cursor (CLI / agent)

| Plan | Window kind | Quantity | % direct? | Source |
|---|---|---|---|---|
| Start (India, ₹649/mo) | monthly billing cycle | Cursor Models pool only; no Other Models pool | No | [cursor-pricing] |
| Pro ($20/mo) | monthly billing cycle | both pools included; USD-denominated | No | [cursor-pricing] |
| Pro Plus ($60/mo) | monthly billing cycle | both pools | No | [cursor-pricing] |
| Ultra ($200/mo) | monthly billing cycle | both pools | No | [cursor-pricing] |
| Teams Standard ($40/user/mo) / Premium ($120) | monthly | Premium = "5x the Standard limits on Agent" | No | [cursor-pricing] |

Two separate pools ("Cursor Models" for Grok/Composer, "Other Models" for third-party), each "resetting with your monthly billing cycle". Overage = on-demand usage at the same API rates, or upgrade. Usage is visible "in your editor settings and on your usage dashboard" (`cursor.com/dashboard/usage`). **No documented CLI usage command and no documented local usage file** — the CLI docs cover modes, MCP, worktrees, resume, but never usage ([cursor-cli]). Config dir `~/.cursor` (empty on this machine). Models: `claude-fable-5-1`, `claude-opus-5-5`, `claude-sonnet-5`, `composer-2.5`, `gemini-3.1-pro`, `gemini-3.8-flash`, `gpt-5.6-{sol,terra,luna}`, `grok-4.{5,6,7}`, `muse-spark-1.3` ([cursor-pricing]).

### OpenCode (incl. OpenCode Zen)

Zen is an **optional** pay-as-you-go gateway; OpenCode itself imposes no plan quota — you bring a provider. Zen: charged per request against a USD balance; auto-reload $20 when the balance drops below $5; **monthly usage limits can be set per workspace and per member** (USD, user-chosen) ([oc-zen]). So the denominator is a user setting, not a plan constant. No documented CLI usage command; balance is in the Zen console. **UNVERIFIED** (unofficial, community): an "OpenCode Go" free tier of 100 requests/day and a $9.99 Pro tier is claimed by [opencode.asia][unof-oczen] — this is a third-party site, not opencode.ai, and the official [Go docs][oc-go] describe fallback to a Zen balance without stating the free quota. Do not ship those numbers.

Zen model IDs are prefixed `opencode/` in config (e.g. `opencode/gpt-6-sol`) ([oc-zen]).

### Qwen Code

**No official quota documentation found.** The official docs ([qwen-overview], retrieved 2026-09-24) describe provider connection (Alibaba ModelStudio Token Plan / Standard API key, third-party providers, custom provider) but publish no request/day or window figures; `docs/` in the GitHub repo has no quota page. Limits therefore come from whichever provider you connect (ModelStudio token plan, DeepSeek, Kimi, OpenRouter…). Repo README documents only harness limits (`max_iterations=500`, `runtime_timeout_sec=7200`) and context limits (1M context, 64K max tokens) — not usage quotas. **UNVERIFIED**: the widely-cited "2,000 requests/day on Qwen OAuth" figure has no current official source; do not ship it. No documented local usage file or `/usage` command. Config dir `~/.qwen` (or `QWEN_HOME`) ([herdr-integrations]).

### Kimi Code CLI (Moonshot)

| Group | Window kind | Quantity | % direct? | Source |
|---|---|---|---|---|
| New plans (Plus and above) | 5h rolling | not published | No | [kimi-membership] |
| New plans | monthly total | Kimi membership monthly total quota; not published | No | [kimi-membership] |
| New plans | weekly | **removed** ("the weekly quota limit is removed") | — | [kimi-membership] |
| Legacy plans (Andante and above) | 5h rolling + 7d refresh + monthly total | not published; quota "refreshes automatically every 7 days from your subscription date", no rollover | No | [kimi-membership] |

Kimi Code shares the Kimi membership quota with the web app; all devices and API keys share one quota. Overage = "Extra Usage" prepaid balance (shared with Kimi web), deducted last, optional monthly spending cap. The console shows a progress card and surfaces a "Buy Extra Usage" button at **>80% used** — implying a percentage is computed server-side, but it is only documented in the console UI.

**Local read path: `/usage` in the Kimi Code CLI** — "Kimi Code CLI: type `/usage` on the command line to check directly" (documented for the Extra Usage balance) ([kimi-membership]). Herdr uses `~/.kimi-code` / `KIMI_CODE_HOME` ([herdr-integrations]). Model families: `kimi-k3`, `kimi-k2.7-code`, `kimi-k2.6`, `kimi-k2.5` (IDs via [oc-zen]); K3 requires Plus+, K3 1M context and HighSpeed require Pro+ ([kimi-membership]). Label `K3` / glyph `◉`.

### Grok CLI (xAI "Grok Build")

Consumer plans (SuperGrok / X Premium+) since June 2026 use **one shared weekly usage pool across all Grok products** (Chat, Imagine, Voice, Build, API):

> "The usage pool is shown as a **percentage used** and will be broken down by product… The usage pool limit resets every week on a schedule shown in the Usage tab in Settings."
> — [xAI FAQ][xai-faq]

| Plan | Window kind | Quantity | % direct? | Reset | Source |
|---|---|---|---|---|---|
| SuperGrok / X Premium+ (all paid consumer tiers) | 7d, fixed per-account schedule | a weekly "usage allowance" measured in compute; **no numeric quantity published** | Yes, in the Settings → Usage UI | weekly, per-account schedule | [xai-faq] |
| xAI API (developer) | RPS + TPM per model per tier | e.g. `grok-4.6`: T0 150 RPS / 50M TPM … T4 500 RPS / 100M TPM | No | per-second / per-minute | [xai-ratelimits] |

Overage: Extra Usage Credits (min $5, web only, expire after 1 year) + Auto Top Up with a monthly cap.

**No documented local file or CLI usage command.** `grok --help` flags cover sessions, models, permissions, sandbox — nothing for usage ([xai-cli-ref]). **UNVERIFIED (unofficial)**: a community issue documents an undocumented gRPC-web endpoint `POST https://grok.com/grok_api_v2.GrokBuildBilling/GetGrokCreditsConfig` returning `credit_usage_percent` + reset timestamp, and an ACP JSON-RPC method `x.ai/billing` that returns `-32601 Method not found` on `grok agent stdio` in CLI ~0.1.210 ([unof-grok]). Both are reverse-engineered; do not depend on them in a shipped default. Herdr config dir: `~/.grok` / `GROK_HOME`. Models: `grok-4.7`, `grok-4.6`, `grok-4.5`, `grok-build-0.1` ([xai-ratelimits], [oc-zen]). Label `GR4` / glyph `✕`.

### Droid (Factory)

The best-structured of the small providers, and the closest match to Herdr's three-gauge design:

> "**Rate Limits:** three rolling windows, **5-hour, 7-day, and 30-day**. Each runs independently."
> — [Factory individual plans][factory-ind]

| Plan | Windows | Quantity | % direct? | Reset | Source |
|---|---|---|---|---|---|
| Pro ($20/mo) | 5h + 7d + 30d rolling | "Standard Usage"; no numeric allowance published | No | rolling, each window starts on first use and resets after it elapses | [factory-ind], [factory-pricing] |
| Plus ($100/mo) | same | "~5x Pro usage" | No | rolling | [factory-ind] |
| Max ($200/mo) | same | "~10x Pro usage" | No | rolling | [factory-ind] |
| Teams ($60/mo + $40/seat) | same | each seat includes Pro Rate Limits | No | rolling | [factory-orgs] |
| Business / Enterprise | custom | contract terms, not the Individual rate-limit model | No | contract | [factory-orgs] |

"You need headroom in all three to send a request." Standard Usage is consumed first; then **Droid Core** (free open-weight model pool with its own separate rate limits); then **Extra Usage** (prepaid USD credits, $10 minimum, never expire, roll over). Standard Usage does not roll over.

**Local read path: `/limits` in the Droid CLI** ("Monitor usage: open Settings > Usage or run `/limits` in Droid") plus `/cost` and `/stats [period]` for usage statistics ([factory-ind], [factory-cli-ref]). No documented output schema and no documented API. Herdr config dir: `~/.factory`. Label `DRO` / glyph `⚙`.

### Devin CLI

| Plan | Window kind | Quantity | % direct? | Source |
|---|---|---|---|---|
| Free | — | "light quota", limited model availability | No | [devin-pricing] |
| Pro ($20/mo) | **daily + weekly**, calendar-based | "a daily and weekly usage quota that covers Devin sessions, Devin CLI, and Devin Desktop"; daily allowance is >1/7 of the weekly; no numbers published | No | [devin-selfserve] |
| Max ($200/mo) | **weekly only**, no daily cap | "significantly larger weekly usage quota" | No | [devin-selfserve] |
| Teams ($80/mo minimum, $40/full seat) | daily + weekly per full seat | shared on-demand credits across the team | No | [devin-selfserve] |
| Enterprise | monthly, contract cycle | ACUs; optional per-user monthly ACU limits (beta), reset on the contract billing day | No | [devin-usage], [devin-usage-policies] |

Overage: prepaid **on-demand credits** (never expire, roll over, optional auto-reload). Enterprise per-user ACU limits count local (Desktop, CLI) and cloud usage against one limit.

Usage visibility: self-serve → Settings > Plans (current month usage, quota remaining, credit balance); Enterprise → Consumption / Consumption Analytics pages; per-session cost via Session Insights ([devin-usage]). A **user-level ACU-limits API** exists for Enterprise admins ([devin-usage-policies]). **No documented CLI usage command and no documented local usage file.** Herdr config dir: `$XDG_CONFIG_HOME/devin` or `~/.config/devin`. Label `DVN` / glyph `∇`.

### Amp (Sourcegraph)

| Tier | Window kind | Quantity | % direct? | Source |
|---|---|---|---|---|
| Hobby (free) | — | no included agent usage; pay-as-you-go credits; **"No Amp token fees or limits"** with BYOK/your own subs | No | [amp-pricing] |
| Individual ($20/mo, Megawatt / Gigawatt) | monthly billing period | included agent usage (USD-denominated, not published as a number) + **45,000 minutes of orb time**; "resets at the end of each monthly billing period and does not roll over" | No | [amp-pricing] |
| Teams | monthly | members on mixed tiers, shared billing, pooled credits | No | [amp-pricing] |
| Enterprise | custom | pooled credits, spend limits, higher quotas | No | [amp-pricing] |

Credits are charged at provider API cost with no markup for individuals; purchased credits expire 12 months after purchase. Orb time is a **separate** allowance billed per minute.

**Local read path: `amp usage`** — "You can buy credits and check your balance in user settings, in workspace settings, or by running `amp usage`." ([amp-pricing]). That is a balance, not a percentage. Amp is not in Herdr's integration list but is in scope for the plugin's config file.

### Agents with no published usage limits (bundled for completeness)

Pi, OMP, Kilo Code CLI, Qoder CLI, Letta Code, MastraCode, Hermes Agent, Antigravity CLI: no plan/quota documentation was located for a per-agent usage window as of 2026-09-24. These are either BYO-provider harnesses (limits belong to the upstream model provider) or have no published consumer plan. **UNVERIFIED** — treat as "no gauge" in the default config, and let users add a custom provider entry.

---

## 6. Summary table — what the plugin can actually gauge today

| Provider | 5h | 7d/weekly | 30d/monthly | Source of truth | OAuth-only reachable? |
|---|---|---|---|---|---|
| Claude Code | ✅ % + reset | ✅ % + reset | spend-limit % only, gateway-only | status-line JSON | ✅ Pro/Max only — **not** Team/Enterprise |
| Codex CLI | ✅ % + reset | ✅ % + reset | ⚠️ sometimes appears as `primary` with a 2,592,000 s window | rollout JSONL + `account/rateLimits/read` | ✅ |
| Gemini CLI | ✖ | ✖ | ✖ (RPD only) | `/stats model` text | ✅ but needs tier denominator |
| Copilot CLI | ✖ | ✖ | ⚠️ session-only via `/usage`; monthly remaining is web-only | `/usage` + web billing | partially |
| Droid | ✅ (no schema) | ✅ | ✅ | `/limits` text | ✅ |
| Kimi Code | ✅ (no schema) | legacy plans only | ✅ monthly total | `/usage` text | ✅ |
| Cursor / Devin / Amp / OpenCode / Grok / Qwen | ✖ | ✖ | ✖ | web dashboards (`amp usage` for balance) | no |

---

## 7. Citations

[cc-pro]: https://support.claude.com/en/articles/8325606-what-is-the-pro-plan — "What is the Pro plan?", retrieved 2026-09-24
[cc-max]: https://support.claude.com/en/articles/11049741-what-is-the-max-plan — "What is the Max plan?", retrieved 2026-09-24
[cc-pro-code]: https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan — "Use Claude Code with your Pro or Max plan", retrieved 2026-09-24
[cc-team]: https://support.claude.com/en/articles/11845131-use-claude-code-with-your-team-or-enterprise-plan — "Use Claude Code with your Team or Enterprise plan", retrieved 2026-09-24
[cc-bestpractices]: https://support.claude.com/en/articles/9797557-usage-limit-best-practices — "Usage limit best practices", retrieved 2026-09-24
[cc-limits-explainer]: https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work — "How do usage and length limits work?", retrieved 2026-09-24
[cc-fable]: https://support.claude.com/en/articles/15424964-claude-fable-models-on-your-plan — "Claude Fable models on your plan", retrieved 2026-09-24
[cc-models-usage]: https://support.claude.com/en/articles/14552983-models-usage-and-limits-in-claude-code — "Models, usage, and limits in Claude Code", retrieved 2026-09-24
[cc-costs]: https://code.claude.com/docs/en/costs — "Manage costs effectively", retrieved 2026-09-24
[cc-statusline]: https://code.claude.com/docs/en/statusline — "Status line", retrieved 2026-09-24
[cc-errors]: https://code.claude.com/docs/en/errors — "Error reference", retrieved 2026-09-24
[cc-modelconfig]: https://code.claude.com/docs/en/model-config — "Model configuration", retrieved 2026-09-24
[cc-models]: https://platform.claude.com/docs/en/about-claude/models/overview — "Models overview", retrieved 2026-09-24
[cc-otel]: https://code.claude.com/docs/en/monitoring-usage — referenced from [cc-costs], retrieved 2026-09-24
[cc-analytics-api]: https://platform.claude.com/docs/en/api/admin/analytics — referenced from [cc-costs], retrieved 2026-09-24
[cc-cc-analytics-api]: https://platform.claude.com/docs/en/build-with-claude/claude-code-analytics-api — referenced from [cc-costs], retrieved 2026-09-24

[oai-pricing]: https://learn.chatgpt.com/docs/pricing (via https://developers.openai.com/codex/pricing/) — "Pricing | ChatGPT Learn", retrieved 2026-09-24
[oai-models]: https://learn.chatgpt.com/docs/models — "Models | ChatGPT Learn", retrieved 2026-09-24
[oai-cli]: https://learn.chatgpt.com/docs/codex/cli — "Codex CLI", retrieved 2026-09-24

[gca-quotas]: https://docs.cloud.google.com/gemini/docs/quotas (via https://developers.google.com/gemini-code-assist/resources/quotas) — "Quotas and limits | Gemini for Google Cloud", page states "Last updated 2026-09-22 UTC", retrieved 2026-09-24
[gcli-quota]: https://geminicli.com/docs/resources/quota-and-pricing/ — "Gemini CLI: Quotas and pricing", page states "Last updated: Jun 18, 2026", retrieved 2026-09-24
[gcli-commands]: https://geminicli.com/docs/reference/commands/ — "CLI commands", page states "Last updated: Aug 17, 2026", retrieved 2026-09-24
[gemini-api-rl]: https://ai.google.dev/gemini-api/docs/rate-limits — "Rate limits | Gemini API", retrieved 2026-09-24 (search snippet: "Requests per day (RPD) quotas reset at midnight Pacific time")

[gh-individual]: https://docs.github.com/en/copilot/concepts/billing-and-usage/individuals/billing — "Usage-based billing for individuals", retrieved 2026-09-24
[gh-billing]: https://docs.github.com/en/billing/concepts/product-billing/github-copilot-billing — "GitHub Copilot billing", retrieved 2026-09-24
[gh-legacy-requests]: https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/copilot-requests (via /en/copilot/concepts/billing/copilot-requests) — "Requests in GitHub Copilot (legacy)", retrieved 2026-09-24
[gh-legacy-monitor]: https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/monitor-premium-requests — "Monitoring your GitHub Copilot usage and entitlements (legacy)", retrieved 2026-09-24
[gh-cli-usage]: https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/overview — "Using GitHub Copilot CLI", retrieved 2026-09-24

[cursor-pricing]: https://cursor.com/docs/models-and-pricing — "Models & Pricing", retrieved 2026-09-24
[cursor-cli]: https://cursor.com/docs/cli/using — "Using Agent in CLI", retrieved 2026-09-24

[oc-zen]: https://opencode.ai/docs/zen/ — "Zen | OpenCode", retrieved 2026-09-24
[oc-go]: https://opencode.ai/docs/go/ — "Go | OpenCode", retrieved 2026-09-24 (search snippet only)

[qwen-overview]: https://qwenlm.github.io/qwen-code-docs/en/users/overview/ — "Qwen Code overview", page states "Last updated on September 18, 2026", retrieved 2026-09-24

[kimi-membership]: https://www.kimi.com/code/docs/en/kimi-code/membership.html — "Membership Benefits | Kimi Code Docs", retrieved 2026-09-24

[xai-faq]: https://docs.x.ai/grok/faq — "FAQ - Grok Website / Apps", page states "Last updated: August 27, 2026", retrieved 2026-09-24
[xai-ratelimits]: https://docs.x.ai/developers/rate-limits — "Rate Limits | xAI Docs", retrieved 2026-09-24 (search snippet)
[xai-cli-ref]: https://docs.x.ai/build/cli/reference — "CLI Reference", retrieved 2026-09-24 (search snippet)

[factory-ind]: https://docs.factory.ai/pricing/individuals — "Individual Plans", retrieved 2026-09-24
[factory-orgs]: https://docs.factory.ai/pricing/organizations — "Organization Plans", retrieved 2026-09-24 (search snippet)
[factory-pricing]: https://factory.ai/pricing — "Factory Pricing", retrieved 2026-09-24 (search snippet)
[factory-cli-ref]: https://docs.factory.ai/droid-cli/cli-reference — "Droid CLI Reference", retrieved 2026-09-24 (search snippet)

[devin-selfserve]: https://docs.devin.ai/admin/billing/self-serve — "Self-serve plans", retrieved 2026-09-24 (search snippet)
[devin-usage]: https://docs.devin.ai/admin/billing/usage — "Usage", retrieved 2026-09-24 (search snippet)
[devin-usage-policies]: https://docs.devin.ai/enterprise/features/usage-policies — "Usage policies: per-user ACU limits", retrieved 2026-09-24 (search snippet)
[devin-pricing]: https://devin.ai/pricing — "Devin Plans and Pricing", retrieved 2026-09-24 (search snippet)

[amp-pricing]: https://ampcode.com/docs/pricing — "Pricing | Amp Docs", page states modified 2026-09-14, retrieved 2026-09-24

[herdr-integrations]: https://raw.githubusercontent.com/herdrdev/herdr/v0.9.1/docs/next/website/src/content/docs/integrations.mdx — Herdr v0.9.1 integrations page, retrieved 2026-09-24

### Unofficial / community sources (clearly marked — not authoritative)

[unof-plantype]: https://github.com/songhuiming2007-coder/pi-cliproxyapi-quota/issues/1 — community issue quoting a live Codex payload with `"plan_type": "plus"`, retrieved 2026-09-24. **Unofficial.**
[unof-codexwindow]: https://github.com/Sing3Rous/stream-deck-ai-limits/issues/11 — community issue showing a Codex payload where `primary_window.limit_window_seconds` = 2,592,000 (30 days) with `secondary_window: null`, retrieved 2026-09-24. **Unofficial.**
[unof-codexfields]: https://github.com/kevin-lee/token-watchroo/issues/53 — community issue showing `reset_at` vs `resets_at` field-name drift on `GET https://chatgpt.com/backend-api/wham/usage`, retrieved 2026-09-24. **Unofficial; the endpoint itself is undocumented — do not use it.**
[unof-grok]: https://github.com/diegosouzapw/OmniRoute/issues/6844 — community write-up of the undocumented `GetGrokCreditsConfig` gRPC-web endpoint and the ACP `x.ai/billing` method, retrieved 2026-09-24. **Unofficial and reverse-engineered.**
[unof-oczen]: https://www.opencode.asia/zen-models/ — third-party site claiming an OpenCode free tier of 100 requests/day and a $9.99 Pro tier, retrieved 2026-09-24. **Unofficial; contradicted by nothing on opencode.ai but also unconfirmed by it.**

### Local observations on this machine (2026-09-24, not documentation)

- Claude Code `2.1.282`; `~/.claude/` holds no rate-limit cache (grep for `rate_limit|rateLimit|five_hour|seven_day` across `~/.claude/**.json` → no matches).
- Codex `codex-cli 0.156.1`; `~/.codex/sessions/2026/09/24/rollout-*.jsonl` contains the `rate_limits` object quoted in §2 with `plan_type: "team"`.
- Codex binary symbol inspection confirms the app-server methods `account/rateLimits/read`, `account/usage/read`, `account/rateLimitResetCredit/consume` and the notification `account/rateLimits/updated`, plus the `RateLimitWindow { usedPercent, windowDurationMins, resetsAt }` shape.
- `~/.copilot/` contains `config.json`, `settings.json`, `session-state/*/events.jsonl`, `session-store.db` — no usage/entitlement file.
- `~/.cursor/` is empty; `~/.gemini/` contains only `antigravity-cli/settings.json`.
