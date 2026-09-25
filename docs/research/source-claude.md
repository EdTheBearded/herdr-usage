# Source research: Claude Code usage / rate-limit data (local, read-only)

Investigated on: 2026-09-24, Linux, user `ed`.
Claude Code **v2.1.282**, launcher `/home/ed/.local/bin/claude`, bundle
`/home/ed/.local/share/claude/versions/2.1.282` (238 MB single Bun executable).
Auth: Claude.ai OAuth (`~/.claude/.credentials.json`), no `ANTHROPIC_API_KEY`.

All findings below were reproduced on this machine. `~/.claude` was never written to.
Token values are never printed anywhere in this document.

---

## TL;DR

There are **three** working sources, in descending order of quality:

1. **`get_usage` SDK control request** — full server truth (5h, weekly, per-model weekly,
   extra-usage credits, reset timestamps, plan tier), zero model tokens burned. **Best.**
2. **`GET https://api.anthropic.com/api/oauth/usage`** with the OAuth access token from
   `~/.claude/.credentials.json` — the same server payload, no Claude Code process needed.
3. **statusLine hook stdin `rate_limits`** — a *subset* (5h + 7d percent + epoch reset only),
   but free and push-based; only arrives while a session is actually running.

Local token math (`stats-cache.json`, `projects/*.jsonl`) **cannot** produce limit
percentages — the plan denominators are server-side and not published anywhere local.

---

## Q1 — Non-interactive CLI path to usage limits

### `claude --help` — no usage subcommand

Full subcommand list in v2.1.282:

```
agents  attach  auth  auto-mode  doctor  gateway  import  install  logs  mcp
plugin  project  respawn  rm  setup-token  stop  ultrareview  update
```

There is **no** `claude usage`, `claude status`, `claude cost`, or `claude limits` command.
`/usage` and `/status` exist only as TUI slash commands.

### `claude auth status` — plan/identity, no limits (free, instant)

```console
$ claude auth status
{
  "loggedIn": true,
  "authMethod": "claude.ai",
  "apiProvider": "firstParty",
  "analyticsDisabled": false,
  "projectsDirectory": "/home/ed/.claude/projects",
  "configDirectory": "/home/ed/.claude",
  "email": "user@example.com",
  "orgId": "00000000-0000-0000-0000-000000000000",
  "orgName": "Toradex AI",
  "subscriptionType": null
}
```

Note `subscriptionType: null` — on this account (org/Enterprise seat) the plan tier is
**not** exposed here. Useful for identity + auth-method detection, useless for limits.

### ✅ The real answer: the `get_usage` SDK control request

The bundle registers a **control-protocol** request `get_usage`, described in its own
schema as *"Requests the structured /usage data: session cost/usage totals plus claude.ai
plan rate-limit utilization when available."* It is reachable non-interactively over
`--input-format stream-json`, and — critically — it is answered **before any model turn**,
so it costs **zero tokens and zero quota**.

```bash
printf '%s\n' '{"type":"control_request","request_id":"r1","request":{"subtype":"get_usage","skip_behaviors":true}}' \
  | claude -p --verbose --safe-mode \
      --input-format stream-json --output-format stream-json \
      --no-session-persistence \
  | grep -o '"type":"control_response".*'
```

Real output captured on this machine (whitespace added):

```json
{"type":"control_response","response":{"subtype":"success","request_id":"r1","response":{
  "session": {"total_cost_usd":0,"total_api_duration_ms":0,"total_duration_ms":506,
              "total_lines_added":0,"total_lines_removed":0,"model_usage":{}},
  "subscription_type": null,
  "rate_limits_available": true,
  "rate_limits": {
    "five_hour": {"utilization":5,"resets_at":"2026-09-25T01:59:59.972429+00:00",
                  "limit_dollars":null,"used_dollars":null,"remaining_dollars":null,"locked_reason":null},
    "seven_day": {"utilization":8,"resets_at":"2026-09-25T02:59:58.972454+00:00",
                  "limit_dollars":null,"used_dollars":null,"remaining_dollars":null,"locked_reason":null},
    "seven_day_oauth_apps": null,
    "seven_day_opus": null,
    "seven_day_sonnet": null,
    "seven_day_cowork": null,
    "seven_day_omelette": null,
    "tangelo": null, "iguana_necktie": null, "omelette_promotional": null,
    "nimbus_quill": {"utilization":0,"resets_at":null, ...},
    "cinder_cove": null, "copper_kite": null, "brass_thimble": null,
    "harbor_lantern": null, "wattle_ember": null, "amber_ladder": null,
    "amber_cistern": null, "juniper_tide": null, "cedar_ember": null, "amber_gauge": null,
    "extra_usage": {"is_enabled":false,"monthly_limit":null,"used_credits":null,
                    "utilization":null,"currency":"USD","decimal_places":2,
                    "disabled_reason":"org_level_disabled_until","user_disabled":false,
                    "spend_limit_reached":true,"credits_ever_enabled":true,
                    "daily":null,"weekly":null},
    "limits": [
      {"kind":"session","group":"session","percent":5,"severity":"normal",
       "resets_at":"2026-09-25T01:59:59.972429+00:00","scope":null,"is_active":false},
      {"kind":"weekly_all","group":"weekly","percent":8,"severity":"normal",
       "resets_at":"2026-09-25T02:59:58.972454+00:00","scope":null,"is_active":true},
      {"kind":"weekly_scoped","group":"weekly","percent":0,"severity":"normal",
       "resets_at":"2026-09-25T03:00:00+00:00",
       "scope":{"model":{"id":null,"display_name":"Fable"},"surface":null},"is_active":false}
    ],
    "spend": {"used":{"amount_minor":0,"currency":"USD","exponent":2},"limit":null,
              "percent":0,"severity":"normal","enabled":false,
              "disabled_reason":"org_level_disabled_until","cap":null,"balance":null,
              "auto_reload":null,"can_purchase_credits":false,"can_toggle":false},
    "member_dashboard_available": false,
    "seven_day_breakdown": null,
    "model_scoped": [{"display_name":"Fable","utilization":0,"resets_at":"2026-09-25T03:00:00+00:00"}]
  },
  "behaviors": null
}}}
```

Flag notes:

- `--verbose` is **mandatory** with `-p --output-format stream-json` (hard error otherwise).
- `--safe-mode` is strongly recommended: it skips hooks/plugins/CLAUDE.md, which on this
  machine otherwise dumped multi-KB SessionStart hook output into the stream. It does
  **not** disable auth. `--bare` would be wrong here — it forbids OAuth entirely.
- `--no-session-persistence` keeps `~/.claude/projects` untouched.
- `skip_behaviors: true` avoids a full scan of every transcript touched in the last 7 days
  (the schema says the scan "reads every transcript touched in the last seven days").
- Latency observed: ~0.5–3 s wall (`total_duration_ms: 506` for the handler itself).

The schema (extracted verbatim from the bundle) documents the contract:

> `rate_limits_available` — *"False when plan rate limits do not apply (API key, Bedrock,
> Vertex, or missing profile scope) — rate_limits will be null."*
> `subscription_type` — *"Claude.ai subscription type ('pro', 'max', 'team', 'enterprise')
> or null for API key / 3P provider sessions."*
> `model_scoped` — *"Per-model weekly windows from the server `limits[]` array, filtered by
> the overage-included-models allowlist."*
> The whole response is marked **"Experimental — the shape may change."**

### Bonus: `get_status` control request (the `/status` screen, non-interactively)

Same transport, also zero-quota:

```bash
printf '%s\n' '{"type":"control_request","request_id":"r2","request":{"subtype":"get_status"}}' \
  | claude -p --verbose --safe-mode --input-format stream-json --output-format stream-json --no-session-persistence
```

Real output (abridged): sections `Session` (Version `2.1.282`, Session ID, Session kind
`interactive`, cwd, Login method `Claude API account`, Organization `Toradex AI`, Email)
and `Environment` (Model `claude-opus-5-5`, Setting sources, Auto mode server). **No limit
data** — `/status` is not a usage source in this version.

Other control subtypes seen in the bundle's "safe to answer" set:
`initialize`, `file_suggestions`, `read_file`, `get_workspace_diff`, `get_context_usage`,
`get_usage`, `mcp_status`, plus `get_session_cost`, `list_models`, `get_binary_version`.

**No `-p` prompt was ever sent; zero quota was consumed for any of this.**

---

## Q2 — statusLine hook stdin schema in v2.1.282

`~/.claude/settings.json` on this machine:

```json
"statusLine": { "type": "command", "command": "~/.claude/statusline/statusline.sh",
                "padding": 0, "refreshInterval": 10 }
```

(`~/.claude/statusline-command.sh` is a leftover stub that only prints `user@host:cwd`;
the active script is `~/.claude/statusline/statusline.sh`, a clone of
[daniel3303/ClaudeCodeStatusLine](https://github.com/daniel3303/ClaudeCodeStatusLine) @ `5da9695`.)

### Exact schema, extracted from the binary

The bundle embeds the schema as a doc comment (the text Claude Code itself reads when you
run `/statusline`). Verbatim excerpt of the usage-relevant parts:

```
{
  "session_id": "string",        // Unique session ID
  "session_name": "string",      // Optional: name set via /rename
  "prompt_id": "string",         // Optional: UUID of the prompt being processed
  "transcript_path": "string",
  "cwd": "string",
  "model": { "id": "string", "display_name": "string" },
  "workspace": { "current_dir", "project_dir", "added_dirs", "git_worktree",
                 "repo": { "host", "owner", "name" } },
  "version": "string",
  "output_style": { "name": "string" },
  "context_window": {
    "total_input_tokens": number,
    "total_output_tokens": number,
    "context_window_size": number,
    "current_usage": { "input_tokens", "output_tokens",
                       "cache_creation_input_tokens", "cache_read_input_tokens" } | null,
    "used_percentage": number | null,
    "remaining_percentage": number | null
  },
  "effort": { "level": "low"|"medium"|"high"|"xhigh"|"max" },
  "thinking": { "enabled": boolean },
  "rate_limits": {   // Optional: Claude.ai subscription usage limits, or a Claude gateway
                     // spend limit. Only present for subscribers, or behind a gateway that
                     // sets a spend limit for you, AFTER FIRST API RESPONSE, while at least
                     // one window is present.
    "five_hour":   { "used_percentage": number,  // 0-100
                     "resets_at": number },      // UNIX EPOCH SECONDS
    "seven_day":   { "used_percentage": number, "resets_at": number },
    "spend_limit": { "used_percentage": number, "resets_at": number }  // gateway only
  },
  "prompt_cache": { "warm": boolean, ... },
  "vim": { "mode": ... }, "agent": { "name": ... },
  "pr": { "number", "url", "review_state", "kind" },
  "worktree": { "name", "path", "branch", "original_cwd", "original_branch" }
}
```

Confirmed against the official docs (`https://code.claude.com/docs/en/statusline.md`,
"Available data" table + "Full JSON schema" accordion), which document exactly:

| Field | Meaning |
|---|---|
| `rate_limits.five_hour.used_percentage`, `rate_limits.seven_day.used_percentage` | Percentage of the 5-hour or 7-day rate limit consumed, 0–100 |
| `rate_limits.five_hour.resets_at`, `rate_limits.seven_day.resets_at` | Unix epoch **seconds** when the window resets |
| `rate_limits.spend_limit.*` | Claude apps gateway spend limit; can exceed 100. Requires v2.1.251+ |

### What statusline stdin carries — and what it does NOT

**Carries:** model id + display name, session id/name, cwd/workspace/repo, context window
usage (absolute tokens, size, pre-computed `used_percentage`), effort level, thinking flag,
prompt-cache warmth, git/PR/worktree info, and `rate_limits.{five_hour,seven_day}`
(`used_percentage` + epoch `resets_at`).

**Does NOT carry:**
- `seven_day_opus` / `seven_day_sonnet` / any **per-model weekly** window
- `extra_usage` (overage credits) — the installed statusline.sh comments this explicitly:
  *"extra_usage is only exposed through the OAuth usage endpoint (not stdin JSON)"*
- `subscription_type` / plan tier
- session cost in dollars (only `context_window` token counts)
- the rich `limits[]` array with `kind`/`group`/`severity`/`scope`

**Timing caveats:** `rate_limits` is absent until the session's first API response, absent
for non-subscribers/API-key sessions, and a window disappears from the object once its
`resets_at` has passed. Community reports of it going missing entirely exist
([anthropics/claude-code#45133](https://github.com/anthropics/claude-code/issues/45133)).
It was introduced in **v2.1.80**.

### Practical note for a Herdr plugin

The statusline is **push-only**: it runs when Claude Code decides (events +
`refreshInterval`). To *consume* it you must own or wrap the user's statusLine command —
which means **writing to `~/.claude/settings.json`**. That is a config mutation, not a
read. A plugin can instead tail the cache a statusline already writes (see below) or use
the `get_usage` control request, both of which stay read-only.

---

## Q3 — Local files caching server-reported limit windows

### Grep of `~/.claude` for limit keys

```console
$ grep -rlI -E "rate_limit|rateLimit|five_hour|fiveHour|resets_at|resetsAt|unified_rate_limit|usage_limit|limit_window" \
    ~/.claude --include="*.json"
(no matches)
```

Confirmed: **Claude Code itself persists no usage snapshot to disk.** `~/.claude/state/`
holds only `mcp-discover-verdicts.json`; `~/.claude/cache/` holds `changelog.md` and a
model catalog; `~/.claude/sessions/` holds a pid-keyed socket descriptor + key.

The binary confirms this is by design — its usage layer is an in-memory snapshot:
`"Usage read answered from a snapshot <N>s old; endpoint not asked"`.

### `~/.claude/policy-limits.json` — org policy, NOT usage

```json
{ "restrictions": { "allow_quick_web_setup": {"allowed": false},
                    "enforce_web_search_mcp_isolation": {"allowed": false},
                    "require_trusted_devices": {"allowed": true} },
  "compliance_taints": [], "monitoring_notice": null,
  "defaults": { "remote_control_at_startup": false } }
```

### `~/.claude/stats-cache.json` — lifetime token math, no limits

```
top keys: dailyActivity, dailyModelTokens, dailyModelTokensVersion, firstSessionDate,
          hourCounts, lastComputedDate, longestSession, modelUsage, totalMessages,
          totalSessions, version
lastComputedDate: 2026-09-23   version: 5   totalSessions: 36   totalMessages: 15062
modelUsage["claude-sonnet-5"] = {"inputTokens":152248, "outputTokens":10656956,
  "cacheReadInputTokens":3079901823, "cacheCreationInputTokens":84701391,
  "webSearchRequests":0, "costUSD":0, "contextWindow":0, "maxOutputTokens":0}
```

Note `lastComputedDate: 2026-09-23` while today is 2026-09-24 — it is recomputed lazily,
so it is **stale by up to a day**. `costUSD: 0` for OAuth sessions. No window, no reset,
no denominator. Unusable for limits.

### `~/.claude/projects/*/*.jsonl` transcripts

Normal assistant envelopes carry a per-response `usage` block and **no** rate-limit
metadata. Real sample (model `claude-sonnet-5`):

```json
{"type":"assistant","message":{"id":"...","model":"claude-sonnet-5","role":"assistant",
 "usage":{"input_tokens":2,"cache_creation_input_tokens":15562,
   "cache_read_input_tokens":29610,"output_tokens":1236,
   "server_tool_use":{"web_search_requests":0,"web_fetch_requests":0},
   "service_tier":"standard",
   "cache_creation":{"ephemeral_1h_input_tokens":15562,"ephemeral_5m_input_tokens":0},
   "inference_geo":"not_available","iterations":[...],"speed":"standard"}},
 "sessionId":"...","cwd":"...","version":"2.1.234","gitBranch":"HEAD","timestamp":"..."}
```

Envelope keys: `cwd, entrypoint, error, gitBranch, isApiErrorMessage, isSidechain, message,
parentUuid, sessionId, session_id, timestamp, type, userType, uuid, version`.

**⚠️ The one exception — `quotaLimits` on 429 error envelopes.** When a request is actually
rejected by a rate limit, Claude Code writes an error entry that *does* carry window data.
Real sample (4 transcripts on this machine contain these):

```json
{"...","requestId":"req_011CeAHKodi7drs4E2Nb4BzV",
 "quotaLimits":{"status":"rejected","resetsAt":1787069400,
   "unifiedRateLimitFallbackAvailable":false,"rateLimitType":"five_hour",
   "overageStatus":"rejected","overageDisabledReason":"org_level_disabled_until",
   "isUsingOverage":false},
 "error":"rate_limit","isApiErrorMessage":true,"apiErrorStatus":429,
 "sessionId":"421b8ef2-...","version":"2.1.234","cwd":"/home/ed/torizon-integration-scarthgap/..."}
```

Matching schema in the binary:

```js
status: ["allowed","allowed_warning","rejected"],
resetsAt: int,
rateLimitType: ["five_hour","seven_day","seven_day_opus","seven_day_sonnet",
                "seven_day_overage_included","overage"],
utilization: number,
unifiedWindows: { five_hour: {utilization, resetsAt},
                  seven_day: {utilization, resetsAt},
                  seven_day_overage_included: {utilization, resetsAt} }
```

So the transcripts **can** yield a definitive "you are hard-limited until `resetsAt`, on
window `rateLimitType`" signal — but only *after* you've been blocked, and only when the
API chose to include it. It is an excellent **hard-wall detector**, a useless gauge.

---

## Q4 — Binary strings: endpoints and field names

### OAuth API paths present in the bundle

```console
$ grep -a -o -E "/api/(oauth|organizations)[a-zA-Z0-9_/{}.-]*" <bundle> | sort -u
/api/oauth/account/grove_notice_viewed
/api/oauth/account/settings
/api/oauth/claude_cli/create_api_key
/api/oauth/claude_cli/roles
/api/oauth/cri
/api/oauth/files/
/api/oauth/file_upload
/api/oauth/local_pairing
/api/oauth/organizations/
/api/oauth/profile
/api/oauth/usage          <-- the /usage endpoint
/api/oauth/validate
/api/organizations/
```

### The exact call site (deminified excerpt from the bundle)

```js
var VV = {
  plain:       "/api/oauth/usage",
  at_wall:     "/api/oauth/usage?at_wall=1&skip_spend=1",
  cedar_ember: "/api/oauth/usage?cedar_ember=1&skip_spend=1"
};
function iC(e, n) {
  return Pr(n === "at_wall" ? "api_usage_fetch_at_wall" : ... "api_usage_fetch", async () => {
    let r = VV[n];
    let h = await At.get(r, { timeout: 5000,
                              headers: { "Content-Type": "application/json" },
                              refreshOAuth: !0, credentials: e });
    ...
  });
}
```

So: **`GET https://api.anthropic.com/api/oauth/usage`**, 5 s timeout, OAuth bearer with
automatic 401→refresh→retry. The `?at_wall=1&skip_spend=1` variant is what it hits when you
have actually hit a limit (cheaper, skips the spend block).

The keys it expects back:

```js
var UV = ["five_hour","seven_day","seven_day_oauth_apps","seven_day_opus",
          "seven_day_sonnet","cinder_cove","extra_usage","limits"];
```

The per-model weekly windows are filtered through a GrowthBook flag named
`tengu_usage_overage_included_models` (the "overage-included models allowlist"), which is
why `model_scoped` can be empty even when the server returns `limits[]` rows.

### The `/usage` TUI renderer (shows what the dialog actually displays)

```js
function y(s) {
  let { rate_limits: e, subscription_type: n } = s;
  if (!e) return null;
  let a = n === "max" || n === "team" || n === null;   // who gets the Sonnet-only row
  let r = [
    { title: "Current session",            limit: e.five_hour },
    { title: "Current week (all models)",  limit: e.seven_day },
    ...a ? [{ title: "Current week (Sonnet only)", limit: e.seven_day_sonnet }] : [],
    ...(e.model_scoped ?? []).map(o => ({ title: `Current week (${o.display_name})`,
                                          limit: { utilization: o.utilization,
                                                   resets_at: o.resets_at } }))
  ];
  ... `${title}: ${Math.floor(limit.utilization)}% used · resets ${...}`
}
```

This is the ground truth for how to *render* the data: **integer floor of `utilization`,
expressed as "% used", plus a reset timestamp.** Rows are: Current session, Current week
(all models), Current week (Sonnet only) [Max/Team/unknown-tier only], and one
`Current week (<Model>)` row per `model_scoped` entry.

### Verified live call (token read from credentials, never printed)

```bash
TOK=$(jq -r '.claudeAiOauth.accessToken' ~/.claude/.credentials.json)
curl -s --max-time 15 \
  -H "Authorization: Bearer $TOK" \
  -H "anthropic-beta: oauth-2025-04-20" \
  -H "Content-Type: application/json" \
  https://api.anthropic.com/api/oauth/usage
```

Real response (abridged):

```json
{"five_hour":{"utilization":6.0,"resets_at":"2026-09-25T01:59:59.644894+00:00",
              "limit_dollars":null,"used_dollars":null,"remaining_dollars":null,"locked_reason":null},
 "seven_day":{"utilization":8.0,"resets_at":"2026-09-25T02:59:59.644912+00:00", ...},
 "seven_day_opus":null,"seven_day_sonnet":null,
 "limits":[{"kind":"session","group":"session","percent":6,"severity":"normal",
            "resets_at":"2026-09-25T01:59:59.644894+00:00","scope":null,"is_active":false},
           {"kind":"weekly_all","group":"weekly","percent":8,"severity":"normal",
            "resets_at":"2026-09-25T02:59:59.644912+00:00","scope":null,"is_active":true},
           {"kind":"weekly_scoped","group":"weekly","percent":0,"severity":"normal",
            "resets_at":"2026-09-25T03:00:00+00:00",
            "scope":{"model":{"id":null,"display_name":"Fable"},"surface":null},"is_active":false}],
 "extra_usage":{"is_enabled":false, ...}}
```

Identical shape to what `get_usage` returns — `get_usage` is a thin wrapper over this call.
Note the `anthropic-beta: oauth-2025-04-20` header is what community tools send; the
bundle's own client sets it globally, not per-call.

`~/.claude/.credentials.json` structure (keys only, values never read out):
`claudeAiOauth.{accessToken, refreshToken, expiresAt, scopes}`. Note: on this machine
`expiresAt` and `scopes` are **null**, so you cannot pre-check token expiry from the file —
you must handle a 401 and let Claude Code refresh it. **Never refresh the token yourself**;
a concurrent rotation would break the user's session.

---

## Q5 — Community prior art

| Tool | Data source | Notes |
|---|---|---|
| **[ccusage](https://github.com/ccusage/ccusage)** (`ryoppippi` → org `ccusage`) | **Local JSONL only.** *"Coding CLIs generate local usage files → ccusage reads these files from your local machine → analyzes and aggregates → calculates estimated costs using model pricing information."* | `ccusage blocks` reconstructs "Claude Code 5-hour billing windows" **heuristically** from transcript timestamps (rolling block anchored on first activity after a gap). It has **no** access to the server's real percentage and **never** calls an OAuth API. Good for cost/token attribution, wrong tool for limits. |
| **[Maciek-roboblog/Claude-Code-Usage-Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor)** (`claude-monitor` on PyPI) | **Hybrid, explicitly provenance-labelled.** `--statusline` installs itself as the statusLine hook and *captures Claude Code's official `rate_limits`*; `--api` is an opt-in *"experimental Anthropic OAuth usage API"*. Everything else is a labelled local estimate. | Its README states the design rule this research confirms: *"Official Claude Code statusline `rate_limits` are treated as the live source of truth when fresh"*, and *"Official Weekly: Seven-day percentages render only from official statusline data."* Provenance labels: `official` / `local_estimate` / `experimental` / `unknown`. |
| **[daniel3303/ClaudeCodeStatusLine](https://github.com/daniel3303/ClaudeCodeStatusLine)** (installed here at `~/.claude/statusline/`) | **Both, stdin-first.** Reads `.rate_limits.{five_hour,seven_day}.{used_percentage,resets_at}` from stdin; falls back to `GET https://api.anthropic.com/api/oauth/usage` with `Authorization: Bearer <token>` + `anthropic-beta: oauth-2025-04-20`, token from `~/.claude/.credentials.json` (or keychain). | Caches the API response for **60 s** at `/tmp/claude/statusline-usage-cache-<hash>.json`, shared across concurrent Claude Code instances. Its own comment: *"Refresh API cache when stale — runs regardless of builtin rate_limits because `extra_usage` is only exposed through the OAuth usage endpoint (not stdin JSON)."* |
| **[Atarit0/claude-code-limits-statusline](https://github.com/Atarit0/claude-code-limits-statusline)** | **stdin `rate_limits` only, zero network.** | *"Claude Code's statusLine hook sends a JSON payload on stdin. For Pro/Max subscribers … it includes `rate_limits.five_hour` and `rate_limits.seven_day`, each with `used_percentage` and `resets_at` (Unix epoch seconds)."* Degrades to model-name-only when absent. Cleanest reference implementation of the stdin path. |
| **[ohugonnot/claude-code-statusline](https://github.com/ohugonnot/claude-code-statusline)** | Same hybrid, stated bluntly in its CLAUDE.md: *"Native stdin first: `rate_limits.five_hour` / `.seven_day` (epoch `resets_at`) feed the session + weekly-all blocks with no network call. The API is hit only when the field is absent (older CC / non-subscriber) **or for the Sonnet weekly quota (API-only)**."* | Independently confirms the per-model weekly window is unavailable via stdin. |
| **[quota-monitor](https://pypi.org/project/quota-monitor/)** | statusLine wrapper: *"When the statusLine payload contains valid `rate_limits`, this gives you precise 5-hour and 7-day usage percentages and exact reset times — without making any extra API calls."* | Same wrap-the-statusline trick (writes settings.json). |
| **[Summertimechi/claude-usage-monitor](https://github.com/Summertimechi/claude-usage-monitor)** | Pure OAuth API menubar app: *"updated every 60 seconds via the Claude OAuth API. The app reads Claude Code's OAuth tokens from your system's credential store, then polls an undocumented Claude usage endpoint."* | Confirms 60 s as the community-accepted poll floor. |

**Consensus across all of them:** the only two real sources are the statusLine `rate_limits`
object and `GET /api/oauth/usage`. Nobody derives limit *percentages* from local token math —
because the denominators are server-side and unpublished. `get_usage` via the control
protocol appears to be used by **none** of them; it is the newest and cleanest path.

---

## Q6 — Claude Code's actual limit model (official docs)

### The three windows

| Window | Key(s) | Expressed as | Reset |
|---|---|---|---|
| **5-hour session** | `five_hour`, `limits[].kind == "session"` | `utilization` / `percent`, 0–100 | rolling; `resets_at` ISO 8601 (control/API) or epoch seconds (statusline) |
| **7-day / weekly, all models** | `seven_day`, `limits[].kind == "weekly_all"` | same | rolling 7-day |
| **Weekly, model-scoped** | `seven_day_opus`, `seven_day_sonnet`, `model_scoped[]`, `limits[].kind == "weekly_scoped"` with `scope.model.display_name` | same | rolling 7-day |

Everything is a **percentage used (0–100) plus a reset timestamp**. Anthropic never exposes
the absolute token/message denominator, which is exactly why local token counting cannot
reproduce these numbers. (`limit_dollars` / `used_dollars` / `remaining_dollars` exist in
the payload but are `null` for subscription accounts — they are for gateway/spend-limit
setups.)

`limits[]` rows additionally carry `severity` (`normal` / …) and `is_active` (which window
is currently the binding constraint) — both useful for gauge coloring without inventing
your own thresholds.

### Who gets what

- **Pro, Max 5x, Max 20x** all have the 5-hour window **and** the weekly window.
- The **Sonnet-only weekly row is rendered only when `subscription_type` is `max`, `team`,
  or `null`** — straight from the binary: `let a = n === "max" || n === "team" || n === null`.
  Pro does not get that row.
- The **Opus weekly limit** is the `seven_day_opus` key / a `weekly_scoped` row. On this
  account both are `null`/`0` and the only scoped row is for model `"Fable"` — i.e. **which
  model gets its own weekly bucket is server-driven and changes over time**. Do not hardcode
  "Opus"; iterate `model_scoped[]` / `limits[]` where `kind == "weekly_scoped"` and read
  `scope.model.display_name`.

### Official sources

- [Customize your status line](https://code.claude.com/docs/en/statusline) — the only
  official documentation of any usage field; documents `rate_limits.five_hour`,
  `.seven_day`, `.spend_limit` with `used_percentage` (0–100) and `resets_at`
  (Unix epoch seconds). `spend_limit` requires v2.1.251+.
- [Models, usage, and limits in Claude Code](https://support.claude.com/en/articles/14552983-models-usage-and-limits-in-claude-code)
  (updated 2026-09-22) — *"A pool of usage included in your organization's plan, reset on a
  rolling window. A 'limit reached, resets at time' message."* Confirms Opus *"uses
  meaningfully more of your quota"*. Does **not** publish numeric caps.
- [Use Claude Code with your Pro or Max plan](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan)
  — *"Both Pro and Max plans offer usage limits that are shared across Claude and Claude
  Code, meaning all activity in both tools counts against the same usage limits."* Also
  confirms IDE usage shares the same pool.
- [Manage usage credits](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans)
  — the `extra_usage` / overage credits system.

**⚠️ Critical for a per-pane gauge:** the quota is **per account, shared across Claude web,
Claude Code, and IDE plugins on every device**. Two Claude Code panes on this machine share
one number; that number also moves when the user chats on claude.ai. A per-pane gauge must
present these as *account-level*, never as "this pane's usage" — which is exactly the
constraint already recorded in the task plan.

**Plan-tier detection is unreliable here:** both `claude auth status` and `get_usage` report
`subscription_type: null` on this (org/Enterprise-seat) account. Treat `null` as "unknown
tier" and render whatever windows the server actually returns rather than inferring rows
from a tier.

---

## Recommended source priority

| # | Source | Gets you | Freshness | Cost | Failure modes |
|---|---|---|---|---|---|
| **1** | **`get_usage` control request** (`claude -p --verbose --safe-mode --input-format stream-json --output-format stream-json --no-session-persistence`, `skip_behaviors:true`) | Everything: 5h, weekly-all, per-model weekly, `extra_usage`, `limits[]` with `severity`/`is_active`, `subscription_type`, session cost | Live server read (may be served from an in-memory snapshot a few seconds old) | **Zero tokens / zero quota.** ~0.5–3 s wall, one short-lived process per poll | Marked **experimental — shape may change**; spawning the 238 MB binary per poll is heavy, so cache ≥60 s; needs `--verbose` or it hard-errors; without `--safe-mode` user hooks pollute the stream; `rate_limits: null` when `rate_limits_available` is false (API key / Bedrock / Vertex) |
| **2** | **`GET https://api.anthropic.com/api/oauth/usage`** with the bearer from `~/.claude/.credentials.json` (+ `anthropic-beta: oauth-2025-04-20`) | Same payload as #1, minus session cost | Live | One cheap HTTPS request, no model tokens | Undocumented endpoint, can change without notice; token may be expired and **you must not refresh it yourself** (concurrent rotation would break the user's session) — treat 401 as "unknown" and fall back; credentials may live in a keychain instead of the file; rate-limitable if polled hard (community floor: 60 s) |
| **3** | **`/tmp/claude/statusline-usage-cache-<hash>.json`** — *only if* a statusline that writes it is already installed (it is, on this machine) | Full server payload, same shape | ≤60 s old | **Free, pure file read.** No process, no network | Entirely dependent on a third-party statusline being installed and a Claude Code session having run recently; path/format is that project's private contract; goes stale silently when no session is open. Great opportunistic fast path, never the only path |
| **4** | **statusLine hook stdin `rate_limits`** | 5h + 7d `used_percentage` and epoch `resets_at` only | Push, event-driven + `refreshInterval` | Free | Requires **writing `~/.claude/settings.json`** to hook it (not read-only); absent before the first API response, absent for non-subscribers, window vanishes after its `resets_at`; no per-model weekly, no `extra_usage`, no plan tier; has regressed before (issue #45133) |
| **5** | **`quotaLimits` on 429 envelopes in `~/.claude/projects/*/*.jsonl`** | Definitive hard-wall signal: `rateLimitType`, `resetsAt`, `status`, overage state | Only written when a request is actually rejected | Free file read | Tells you nothing until you are already blocked. Use as a **hard-limit override**, never as a gauge |
| **6** | **`claude auth status`** | `loggedIn`, `authMethod`, `apiProvider`, `orgId`/`orgName`, `email` | Instant | Free | No limit data at all; `subscriptionType` was `null` here. Use only to decide *whether* limits apply (`apiProvider: firstParty` + `authMethod: claude.ai` ⇒ subscription limits exist) |
| — | ~~`stats-cache.json`~~ / ~~transcript token math~~ / ~~ccusage~~ | Lifetime + per-day token/cost attribution | Up to 24 h stale (`lastComputedDate` lagged a day here) | Free | **Cannot produce limit percentages** — the denominators are server-side and unpublished. Use for "what did I spend", never for "how much is left" |

### Suggested strategy for the Herdr plugin

Poll **#1** (`get_usage`) on a ≥60 s timer, warm-starting from **#3** if that cache file
happens to be fresh, and fall back to **#2** if spawning the binary fails. Overlay **#5** as
a hard-blocked flag. Render exactly the rows the server returns — `five_hour`,
`seven_day`, plus one row per `model_scoped[]` / `weekly_scoped` entry keyed on
`scope.model.display_name` — using `Math.floor(utilization)` and `resets_at`, and label the
whole block **account-level, shared across all panes and all Claude surfaces**.
