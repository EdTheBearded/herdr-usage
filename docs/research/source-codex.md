# Source research: OpenAI Codex CLI usage / rate-limit data (local, read-only)

Machine: Linux, user `ed`. Codex CLI **0.156.1** (standalone musl build at
`~/.codex/packages/standalone/releases/0.156.1-x86_64-unknown-linux-musl/bin/codex`,
`~/.local/bin/codex` on PATH). Auth mode `chatgpt` (ChatGPT OAuth), no `OPENAI_API_KEY`.
Plan observed: `team`.

All commands below were actually run on this machine; outputs are verbatim (account id /
email left in only where the CLI itself printed them; no tokens are reproduced anywhere).

---

## 1. Non-interactive CLI command that prints rate limits as JSON

### 1.1 Survey of the command surface

`codex --help` subcommands: `agents exec review login logout mcp plugin app-server
remote-control completion update doctor sandbox debug apply resume queue archive delete
migrate-rollouts unarchive fork cloud exec-server features help`.

Checked the help of every plausible candidate. Results:

| Command | Usage/rate-limit data? |
|---|---|
| `codex doctor --json` | **No.** 21 checks (`app_server.status`, `auth.credentials`, `config.load`, `network.*`, …). Grep for `usage/limit/credit/plan` in the JSON: all false. Only useful for *auth mode*, *model*, *CODEX_HOME*, install paths. |
| `codex debug models` | Model catalog only (same content as `models_cache.json`). |
| `codex debug prompt-input` | Prompt rendering. |
| `codex debug app-server send-message-v2` | Sends a message → **would consume quota**. Not used. |
| `codex features list` | Feature flags only. `analytics_plan_history` = `experimental / false`. |
| `codex agents` / `codex remote-control` | TUI / daemon lifecycle; `remote-control --json` prints pairing/daemon status, not usage. |
| `codex exec` | Runs the model. Consumes quota. |
| `codex cloud status/list` | Cloud task status, not account limits. |
| `codex app-server` | **Yes — this is the one.** JSON-RPC over stdio. |

**There is no `codex usage` / `codex status --json`.** The `/status` slash command exists only
inside the interactive TUI (binary strings: `"Token usage"`, `"limits may be stale - run
/status again shortly."`). The machine-readable path is `app-server`.

### 1.2 Discovering the app-server method list

`codex app-server generate-json-schema --out DIR --experimental` writes the full protocol
schema with **no network and no quota use**:

```
$ codex app-server generate-json-schema --out /tmp/codexschema --experimental
$ python3 -c "...extract method consts from ClientRequest.json..."
Account/rateLimits/readRequest          -> ['account/rateLimits/read']
Account/usage/readRequest               -> ['account/usage/read']
Account/readRequest                     -> ['account/read']
Account/rateLimitResetCredit/consumeRequest -> ['account/rateLimitResetCredit/consume']   # WRITE - do not call
Account/workspaceMessages/readRequest   -> ['account/workspaceMessages/read']
Model/listRequest                       -> ['model/list']
```

(483 method-ish identifiers total; the account family is the relevant one.)

### 1.3 Real stdio exchange — live output

Minimal driver (`/tmp/codex_rl_min.py`): spawn `codex app-server --stdio`, write
`initialize` then `account/rateLimits/read`, read until the matching id, terminate.
**`initialized` notification is not required**; the server answers immediately.

```
$ python3 /tmp/codex_rl_min.py
elapsed_s=0.58
{
 "id": 2,
 "result": {
  "ordinaryUsageAllowed": true,
  "rateLimits": {
   "limitId": "codex",
   "limitName": null,
   "normalModelSlug": null,
   "primary":   {"usedPercent": 7,  "windowDurationMins": 300,   "resetsAt": 1790301885},
   "secondary": {"usedPercent": 26, "windowDurationMins": 10080, "resetsAt": 1790691275},
   "credits": {"hasCredits": false, "unlimited": false, "balance": null},
   "individualLimit": null,
   "spendControlReached": false,
   "planType": "team",
   "rateLimitReachedType": null
  },
  "rateLimitsByLimitId": {"codex": { ...identical snapshot... }},
  "rateLimitResetCredits": {"availableCount": 3, "credits": [...]},
  "accountId": "f00379ab-...",
  "rateLimitUpsell": null
 }
}
```

**0.58 s wall clock, reproducible.** No model call — verified in `logs_2.sqlite`:

```
codex_app_server::message_processor | app-server request: account/rateLimits/read connection_id=ConnectionId(0) request_id=Integer(3)
codex_http_client::custom_ca        | app_server.request{otel.name="account/rateLimits/read" rpc.transport="stdio" ...}
```

Only an HTTPS GET to the backend usage endpoint is made. **No quota is consumed.**

Companion calls in the same session (all quota-free):

```
account/read    -> {"account":{"type":"chatgpt","email":"user@example.com","planType":"team"},
                    "requiresOpenaiAuth":true,
                    "workspaceRouting":{"chatgptAccountId":"f00379ab-...","backendOrigin":"https://chatgpt.com",
                                        "accountRoutingOverride":"NO_CONSTRAINT"}}

account/usage/read -> {"summary":{"lifetimeTokens":96679740,"peakDailyTokens":36612343,
                                  "longestRunningTurnSec":13581,"currentStreakDays":3,"longestStreakDays":4},
                       "dailyUsageBuckets":[{"startDate":"2026-09-15","tokens":1088138}, ... ],
                       "threadUsage":null}
```

Note `account/usage/read` gives **lifetime/daily token counts, not window percentages** — it
is not a substitute for `account/rateLimits/read`.

Unsolicited notification observed right after `initialize`:

```
NOTIF: account/updated {"authMode":"chatgpt","planType":"team"}
```

and, for long-lived connections, `account/rateLimits/updated` (sparse rolling updates — merge,
don't replace; see `AccountRateLimitsUpdatedNotification` in §4).

### 1.4 Useful `initialize` parameters

`initialize` requires `clientInfo {name, version}`. `account/rateLimits/read` params are
optional (`null` works) but accept:

```jsonc
{"excludeResetCreditDetails": true,   // skip the extra reset-credit detail lookup (faster background polls)
 "supportsLunaReserve": false}        // do NOT set true: it lets the backend record experiment exposure
```

Use `excludeResetCreditDetails: true` for polling; `availableCount` is still returned.

### 1.5 Caveats of the app-server path

- Spawning `codex app-server` makes Codex **write** to `~/.codex/logs_2.sqlite` (its own
  tracing sink) and touch `app-server-daemon/` state. This is Codex writing its own logs, not
  us writing to `~/.codex`, but it is not a byte-for-byte read-only operation on the dir.
- It launches a full Codex process (~0.6 s, non-trivial RSS). Do not poll faster than ~60 s.
- Requires network + valid/refreshable OAuth token. Codex refreshes the token itself.
- `--stdio` is equivalent to the default `--listen stdio://`.

---

## 2. Local state files

### 2.1 `~/.codex/.codex-global-state.json`

Electron desktop-app atom state: onboarding flags, thread bindings, sidebar prefs, prompt
history, permission profiles per thread. **No rate-limit or usage data.** Not useful.

### 2.2 SQLite databases

```
$ python3 -c "sqlite_master over each db (URI mode=ro)"
goals_1:           _sqlx_migrations, thread_goals, thread_goal_continuation_deferrals
memories_1:        _sqlx_migrations, stage1_outputs, jobs, consolidation_progress
queue_1:           _sqlx_migrations, queued_items, queued_thread_revisions
state_5:           _sqlx_migrations, threads, thread_dynamic_tools, backfill_state,
                   thread_spawn_edges, remote_control_enrollments, external_agent_config_imports,
                   thread_sections, rollout_migration_state, rollout_migration_skipped_rollouts,
                   projects, project_roots, project_idempotency_keys, thread_attachments
thread_history_1:  _sqlx_migrations, thread_turns, thread_items,
                   thread_history_projection_state, thread_realtime_items
logs_2:            _sqlx_migrations, logs          (3.4 GB!)
```

**No table anywhere caches a rate-limit snapshot.** Verified:
`select count(*) from thread_items where item_json like '%rate_limit%'` → 58 rows, all
incidental (agent text mentioning the phrase), none a structured snapshot.
`item_type` distribution has no usage/limit type: `reasoning, commandExecution, agentMessage,
fileChange, userMessage, webSearch, subAgentActivity, collabAgentToolCall, imageView,
contextCompaction, mcpToolCall, plan, imageGeneration`.

**`state_5.threads` IS useful — per-thread model + token totals:**

```sql
CREATE TABLE threads (
  id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, created_at INTEGER, updated_at INTEGER,
  source TEXT, model_provider TEXT, cwd TEXT, title TEXT, sandbox_policy TEXT,
  approval_mode TEXT, tokens_used INTEGER DEFAULT 0, has_user_event INTEGER, archived INTEGER,
  archived_at INTEGER, git_sha TEXT, git_branch TEXT, git_origin_url TEXT,
  cli_version TEXT, first_user_message TEXT, agent_nickname TEXT, agent_role TEXT,
  memory_mode TEXT, model TEXT, reasoning_effort TEXT, agent_path TEXT,
  created_at_ms INTEGER, updated_at_ms INTEGER, thread_source TEXT, preview TEXT,
  recency_at INTEGER, recency_at_ms INTEGER, history_mode TEXT, name TEXT, is_pinned INTEGER,
  thread_section_id TEXT, section_position INTEGER, section_entered_at_ms INTEGER,
  project_id TEXT, originator TEXT, daybreak_enabled BOOLEAN)
```

Newest row (real):

```
id            = 00000000-0000-0000-0000-000000000000
rollout_path  = /home/ed/.codex/sessions/2026/09/24/rollout-2026-09-24T18-04-37-00000000-...jsonl
model         = gpt-5.6-terra
reasoning_effort = high
tokens_used   = 1542012
cwd           = /home/ed/chameleon
source        = vscode      originator = hermes      cli_version = 0.156.1
updated_at_ms = 1790283877925
```

**This is the cheapest way to map a live Codex process/cwd → model + rollout file.**

Fast per-thread model lookup:

```sql
SELECT id, model, reasoning_effort, cwd, rollout_path, tokens_used, updated_at_ms
FROM threads ORDER BY updated_at_ms DESC LIMIT 5;
```

Open with `sqlite3.connect("file:...?mode=ro", uri=True)` to guarantee read-only. **Note the
WAL:** `-wal`/`-shm` files exist and are live; `mode=ro` can read a stale view if the WAL
can't be replayed. `mode=ro` worked here. Avoid `immutable=1`.

`logs_2.sqlite` (`logs(id, ts, ts_nanos, level, target, feedback_log_body, module_path, file,
line, thread_id, process_uuid, estimated_bytes)`, 39 433 rows but 3.4 GB on disk) contains
tracing lines mentioning `account/rateLimits/updated` events but **no snapshot values**. Not a
data source; queries against it are slow. Ignore.

### 2.3 `~/.codex/models_cache.json` (354 KB)

```json
{"fetched_at":"2026-09-24T21:19:10.633943652Z","etag":"...","client_version":"0.156.1",
 "identity":"d4d5b0aa...","models":[ ... 9 entries ... ]}
```

Model entries use **snake_case** (`slug`, `display_name`, `context_window`,
`default_reasoning_level`, `supported_reasoning_levels`, `service_tiers`, `visibility`, …).
No rate-limit/plan/credit data. Useful for `context_window` per model and for the model list
without any network call.

### 2.4 Other files

- `~/.codex/session_index.jsonl` — **7 lines only, stale since 2026-09-16**, holds only
  `{id, thread_name, updated_at}` for a handful of legacy threads. **Do not use it**;
  `state_5.threads` supersedes it (80 rollout files exist, index lists 7).
- `~/.codex/version.json` — `{"latest_version":"0.156.1","last_checked_at":...}`.
- `~/.codex/auth.json` — `auth_mode`, `tokens.{id_token, access_token, refresh_token,
  account_id}`, `last_refresh` (`2026-09-18T22:44:25Z`). `OPENAI_API_KEY` is `null`.
  Useful *only* for `auth_mode` and `last_refresh`. Never read the token values.
- `~/.codex/config.toml` — `model = "gpt-5.6-terra"`, `model_reasoning_effort = "high"`.

---

## 3. Rollout files: the offline path

### 3.1 Which events carry `rate_limits`

Line-type census of the newest rollout file:

```
session_meta 1 | event_msg 106 | response_item 106 | world_state 1 | turn_context 2 | token_usage_record 26
  event_msg:task_started 2  item_completed 77  token_count 26  task_complete 1
RATE_LIMIT CARRIERS: {'event_msg:token_count': 26}
```

**Exactly one carrier: `type=="event_msg"` && `payload.type=="token_count"`.**
`token_usage_record` lines carry token counts but **no** `rate_limits`. Confirmed across all
80 files: 3 703 snapshots, every one from `token_count`.

Shape:

```json
{"timestamp":"2026-09-24T21:19:46.145Z","ordinal":237,"type":"event_msg",
 "payload":{"type":"token_count",
            "info":{"total_token_usage":{...},"last_token_usage":{...},"model_context_window":258400},
            "rate_limits":{...}}}
```

### 3.2 Corpus statistics (all 80 files, 128 MB)

```
snapshots:        3703
plan_type:        {'plus': 2510, 'team': 1188, None: 5}
null counts:      limit_name 3703/3703   individual_limit 3703/3703   spend_control_reached 3703/3703
                  rate_limit_reached_type 3688/3703   secondary 2261/3703   primary 19/3703   plan_type 5/3703
windows observed: primary(10080) 2242 | primary(300) 1442 | secondary(10080) 1442
rate_limit_reached_type: null 3688 | "workspace_member_credits_depleted" 15
credits variants: {"has_credits":false,"unlimited":false,"balance":"0"}   2512
                  {"has_credits":false,"unlimited":false,"balance":null}  1191
info keys:        total_token_usage, last_token_usage, model_context_window (all 3694)
```

**Critical gotcha:** `primary` is not always the 5 h window. In 2 242 of 3 703 snapshots the
`primary` window is **10080 min (weekly)** with `secondary` absent. **Never assume
primary==5h.** Always key off `window_minutes` (300 = 5 h, 10080 = weekly).

### 3.3 Staleness

Rollout files only advance while a Codex turn is running. Current mtimes:

```
   0.0 h ago    1700544 B  rollout-2026-09-24T18-04-37-...
   4.6 h ago    2282125 B  rollout-2026-09-24T07-57-49-...
   9.6 h ago    2446617 B  rollout-2026-09-24T08-40-17-...
oldest: 57.2 days ago
```

So the freshest offline snapshot can be **minutes or days old**. Median file 744 KB,
max 25.7 MB, total 128 MB. You must report the snapshot's age alongside its values.

### 3.4 Fast tail-reader (verified: 0.019 s total)

`/tmp/codex_rollout_tail.py`:

```python
#!/usr/bin/env python3
"""Newest rate_limits snapshot from Codex rollout files, reading as few bytes as possible."""
import glob, json, os, time

SESSIONS = os.path.expanduser("~/.codex/sessions")
TAIL_CHUNKS = (64 * 1024, 512 * 1024, 4 * 1024 * 1024)


def _scan_tail(path, chunk):
    size = os.path.getsize(path)
    start = max(0, size - chunk)
    with open(path, "rb") as fh:
        fh.seek(start)
        blob = fh.read()
    if start:                      # drop the partial first line
        nl = blob.find(b"\n")
        if nl < 0:
            return None
        blob = blob[nl + 1:]
    for line in reversed(blob.split(b"\n")):
        if b'"rate_limits"' not in line:      # cheap prefilter before json.loads
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        p = o.get("payload") or {}
        rl = p.get("rate_limits")
        if o.get("type") == "event_msg" and p.get("type") == "token_count" and rl:
            return {"rate_limits": rl, "info": p.get("info"),
                    "timestamp": o.get("timestamp"), "rollout": path}
    return None


def latest_rate_limits(max_files=5):
    files = glob.glob(os.path.join(SESSIONS, "*", "*", "*", "rollout-*.jsonl"))
    files.sort(key=os.path.getmtime, reverse=True)
    for path in files[:max_files]:
        for chunk in TAIL_CHUNKS:
            hit = _scan_tail(path, chunk)
            if hit:
                return hit
            if chunk >= os.path.getsize(path):
                break              # whole file scanned; move to the next-newest file
    return None


def demo():
    snap = latest_rate_limits()
    assert snap, "no rate_limits snapshot found in ~/.codex/sessions"
    rl = snap["rate_limits"]
    assert "primary" in rl and "secondary" in rl, rl
    age = time.time() - time.mktime(time.strptime(snap["timestamp"][:19], "%Y-%m-%dT%H:%M:%S"))
    print(json.dumps({"age_seconds_utc": round(age), **snap}, indent=1)[:1500])


if __name__ == "__main__":
    demo()
```

Real run (`time python3 codex_rollout_tail.py` → `real 0m0.019s`) returned:

```json
{"rate_limits":{"limit_id":"codex","limit_name":null,
  "primary":{"used_percent":8.0,"window_minutes":300,"resets_at":1790301885},
  "secondary":{"used_percent":26.0,"window_minutes":10080,"resets_at":1790691275},
  "credits":{"has_credits":false,"unlimited":false,"balance":null},
  "individual_limit":null,"spend_control_reached":null,
  "plan_type":"team","rate_limit_reached_type":null},
 "info":{"total_token_usage":{...},"last_token_usage":{...},"model_context_window":258400},
 "timestamp":"2026-09-24T21:19:46.145Z"}
```

Costs: one `glob` + `stat` per file (80 stats), then a single 64 KB `pread` on the newest
file. `timestamp` is **UTC with a `Z` suffix** — the demo's `time.mktime` is local-time and
therefore wrong by the UTC offset; use `datetime.datetime.strptime(...).replace(tzinfo=utc)`
in production.

---

## 4. Full observed schema

### 4.1 Rollout JSON (`payload.rate_limits`, snake_case)

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `limit_id` | string | yes | always `"codex"` here |
| `limit_name` | string | **always null** in 3 703/3 703 | non-null implies a non-Codex metered limit |
| `primary` | window | yes (19/3703 null) | **may be the weekly window** |
| `secondary` | window | yes (2261/3703 null) | absent when primary is already weekly |
| `credits` | object | yes | `{has_credits: bool, unlimited: bool, balance: string|null}` — `balance` is a **string** ("0") or null |
| `individual_limit` | object | **always null** here | spend-control; `{limit, used, remaining_percent, resets_at}` |
| `spend_control_reached` | bool | **always null** in rollouts | app-server returns `false` |
| `plan_type` | string | yes (5/3703) | see enum below |
| `rate_limit_reached_type` | string | yes | observed `workspace_member_credits_depleted` 15× |

Window object: `{"used_percent": float, "window_minutes": int, "resets_at": int}` —
`used_percent` is a **float** in rollouts, `resets_at` is a **Unix epoch second**.

`payload.info` (present on every `token_count`): exactly three keys —
`total_token_usage`, `last_token_usage`, `model_context_window` (int). Each usage breakdown:
`{input_tokens, cached_input_tokens, cache_write_input_tokens, output_tokens,
reasoning_output_tokens, total_tokens}` (all ints).

### 4.2 app-server JSON-RPC (camelCase, from the generated schema)

`GetAccountRateLimitsResponse`:

| Field | Type | Required |
|---|---|---|
| `rateLimits` | `RateLimitSnapshot` | **yes** |
| `rateLimitsByLimitId` | `{[limitId]: RateLimitSnapshot} \| null` | no |
| `rateLimitResetCredits` | `RateLimitResetCreditsSummary \| null` | no |
| `ordinaryUsageAllowed` | `bool \| null` | no — *"Null means unavailable; clients must not infer recovery from percentages or reset times."* |
| `accountId` | `string \| null` | no |
| `rateLimitUpsell` | backend-owned banner (snake_case inside) | no |

`RateLimitSnapshot` adds a field the rollouts don't have: **`normalModelSlug`**
(*"Normal model whose display name and reasoning options describe this quota alias"*).
`RateLimitWindow` = `{usedPercent: i32 (required), windowDurationMins: i64|null, resetsAt: i64|null}` —
note `usedPercent` is an **integer** here vs a float in rollouts, and the field is
`windowDurationMins`, not `window_minutes`.

`CreditsSnapshot` = `{hasCredits: bool, unlimited: bool, balance: string|null}`.
`SpendControlLimitSnapshot` = `{limit: string, used: string, remainingPercent: i32, resetsAt: i64}` (all required).
`RateLimitResetCreditsSummary` = `{availableCount: i64, credits: RateLimitResetCredit[]|null}` —
`null` credits means *only the count is known*; `[]` means *fetched, none available*; the list may be capped below `availableCount`.
`RateLimitResetCredit` = `{id, resetType, status, grantedAt, expiresAt|null, title|null, description|null}`.

Enums:

```
RateLimitReachedType : rate_limit_reached | workspace_owner_credits_depleted |
                       workspace_member_credits_depleted | workspace_owner_usage_limit_reached |
                       workspace_member_usage_limit_reached
RateLimitResetType   : codexRateLimits | unknown
RateLimitResetCreditStatus : available | redeeming | redeemed | unknown
```

### 4.3 `plan_type` values — two different lists

The **protocol `PlanType` enum** (authoritative for app-server responses):

```
free, go, plus, pro, prolite, team, self_serve_business_prolite,
self_serve_business_usage_based, business, ent26, enterprise_cbp_automation,
enterprise_cbp_usage_based, enterprise, edu, edu_plus, edu_pro, unknown
```

The **binary's `KnownPlan` string table** (wire values the backend may send, superset):

```
guest, go, pro, prolite, free_workspace, self_serve_business_prolite,
self_serve_business_usage_based, ent26, enterprise_cbp_automation,
enterprise_cbp_usage_based, education, quorum, k12, enterprise, edu, edu_pro, hc
```

Note the binary carries `"data did not match any variant of untagged enum PlanType"` —
**unknown plan strings are tolerated, not rejected.** Treat `plan_type` as an open string;
map known values, fall through to the raw string. Observed locally: `plus`, `team`.

---

## 5. Model identity

Four sources, best first:

1. **`state_5.threads.model` + `.reasoning_effort`** (per thread, joined to `cwd` /
   `rollout_path`). `→ gpt-5.6-terra` / `high`. Cheapest, per-session accurate.
2. **Rollout `turn_context` event** — `payload.model` (`"gpt-5.6-terra"`), plus
   `effort`, `summary`, `comp_hash`, `collaboration_mode`, `multi_agent_version`.
   **`session_meta` does NOT carry the model** — it has only `model_provider: "openai"`,
   plus `cwd`, `originator`, `source`, `cli_version`, `context_window`, `git`.
   So read `turn_context`, not `session_meta`.
3. **`~/.codex/config.toml`** — `model = "gpt-5.6-terra"`, `model_reasoning_effort = "high"`.
   This is the *default*, overridable by `-m` / profile / mid-session `/model`. Fallback only.
4. **`codex debug models` / `models_cache.json` / app-server `model/list`** — the catalog,
   not the active model.

**Model slugs offered on this install** (from `models_cache.json`, confirmed by app-server
`model/list`):

| slug | display name | notes |
|---|---|---|
| `gpt-6-sol` | GPT-6-Sol | `isDefault: true`; efforts low→ultra |
| `gpt-6-astra` | GPT-6-Astra | frontier; default effort `low` |
| `gpt-6-luna` | GPT-6-Luna | fast/cheap; no `ultra` effort |
| `gpt-reserve` | GPT-Reserve | fallback/reserve tier |
| `gpt-5.6-sol` | GPT-5.6-Sol | older coding model |
| `gpt-5.6-terra` | GPT-5.6-Terra | **currently configured** |
| `gpt-5.6-luna` | GPT-5.6-Luna | |
| `gpt-5.5` | GPT-5.5 | |
| `codex-auto-review` | Codex Auto Review | internal review model |

`models_cache.json` entries also carry `context_window` / `max_context_window`, and the
rollout `token_count.info.model_context_window` gave `258400` for `gpt-5.6-terra`.

`model/list` (camelCase) adds `supportedReasoningEfforts` with descriptions, `serviceTiers`
(`priority` = "Fast, 1.5x speed"), `defaultServiceTier`, `inputModalities`, `isDefault`.

---

## 6. HTTP fallback (documented, **not executed**)

### 6.1 Endpoints (from binary strings, `strings -n 4` on the ELF)

Base: `https://chatgpt.com/backend-api` (+ `/codex`). Two path families exist — the newer
`/api/codex/...` and the legacy `/wham/...`; the binary carries both, paired:

```
/api/codex/usage                              /wham/usage                       <- RATE LIMITS
/api/codex/usage/thread_usage/query           /wham/usage/thread_usage/query
/api/codex/rate-limit-reset-credits           /wham/rate-limit-reset-credits
/api/codex/rate-limit-reset-credits/consume   /wham/rate-limit-reset-credits/consume   (WRITE)
/api/codex/profiles/me                        /wham/profiles/me
/api/codex/settings/user                      /wham/settings/user
/api/codex/accounts/check                     /wham/accounts/check
/api/codex/workspace-messages                 /wham/workspace-messages
/api/codex/config/bundle                      /wham/config/bundle
/usage/plan_limit_history?days=7              (backend-client/src/client/plan_history.rs — analytics_plan_history flag)
/usage/thread_usage/query_v2                  (backend-client/src/client/analytics.rs)
```

Rust source files named in the binary: `backend-client/src/client.rs`,
`client/rate_limit_resets.rs`, `client/plan_history.rs`, `client/thread_usage.rs`,
`client/turn_usage.rs`, `client/task_usage.rs`, `client/profile.rs`, `client/analytics.rs`.

So the rate-limit read is **`GET https://chatgpt.com/backend-api/codex/usage`**
(the `/api/codex/usage` path joined onto the `/backend-api` base; `/wham/usage` is the legacy
alias). This matches an independent third-party finding: *"OpenAI's own move to a dedicated
usage endpoint (`GET /backend-api/codex/usage` in CodexApi mode)"* —
<https://github.com/r00tedbrain-backup/headroom-compresor-tokens-agente-ia/commit/8c00f7103cf0288991d703cc002ac354e6266534>

### 6.2 Auth the call would require

- `Authorization: Bearer <tokens.access_token>` from `~/.codex/auth.json`.
- `ChatGPT-Account-Id: <tokens.account_id>` — the binary logs `auth: set ChatGPT-Account-Id header:`.
- `originator` header (Codex sets a thread originator header; `codex_tui` / `codex_exec` values appear).
- Token refresh: OAuth `POST /oauth/token` at `https://auth.openai.com` with
  `grant_type=refresh_token`, `client_id`, `Content-Type: application/x-www-form-urlencoded`
  (binary: `"starting oauth token exchange"`, `"oauth token exchange succeeded"`).
  `auth.json.last_refresh` here is **2026-09-18**, i.e. the stored access token is likely
  expired; a raw HTTP call would have to implement refresh **and then write the new tokens
  back to `auth.json`** — which violates the read-only rule and can race the CLI.

### 6.3 Response shape

The binary's serde struct table for the HTTP payload (distinct from both the rollout and the
JSON-RPC shapes):

```
RateLimitStatusDetails { allowed, limit_reached, primary_window, ... }   (4 elements)
RateLimitWindowSnapshot { used_percent, limit_window_seconds, reset_after_seconds, source }
CreditStatusDetails { has_credits, unlimited, balance, approx_local_messages, approx_cloud_messages }
SpendControlLimitDetails { limit, remaining, remaining_percent, ... }    (8 elements)
AdditionalRateLimitDetails { limit_name, metered_feature, rate_limit }
```

Note the HTTP layer uses **`limit_window_seconds` / `reset_after_seconds`** (relative), which
Codex converts to `window_minutes` / absolute `resets_at`. Conversion (per the third-party
analysis, matching codex-rs): `window_minutes = (limit_window_seconds + 59) // 60`.
`CreditStatusDetails` here also exposes `approx_local_messages` / `approx_cloud_messages`,
which neither the rollout nor the JSON-RPC snapshot surfaces.

### 6.4 Recommendation

**Do not implement the raw HTTP path.** `codex app-server` gives the same data in 0.6 s, does
its own token refresh, tracks endpoint changes across CLI versions, and requires no credential
handling by us. Keep §6 as reference only.

### 6.5 Upstream citations (openai/codex is public)

- `codex-rs/app-server-protocol/src/protocol/v2/account.rs` — `GetAccountRateLimitsResponse`,
  `RateLimitSnapshot`, `AccountRateLimitsUpdatedNotification`:
  <https://github.com/openai/codex/blob/27c05a52/codex-rs/app-server-protocol/src/protocol/v2/account.rs>
- `codex-rs/app-server-protocol/src/protocol/common.rs` — method-name table
  (`GetAccountRateLimits => "account/rateLimits/read"`, `GetAccountTokenUsage => "account/usage/read"`,
  `ConsumeAccountRateLimitResetCredit => "account/rateLimitResetCredit/consume"`):
  <https://github.com/openai/codex/blob/53b50197/codex-rs/app-server-protocol/src/protocol/common.rs>
- PR that introduced the API — *"Adds a `GET account/rateLimits/read` API to app-server. This
  calls the codex backend to fetch the user's current rate limits. This would be helpful in
  checking rate limits without having to send a message."*:
  <https://github.com/openai/codex/pull/5302>
- Reference client usage (`send_get_account_rate_limits_request`):
  <https://github.com/openai/codex/blob/f1affbac/codex-rs/app-server/tests/common/test_app_server.rs>
- `codex-rs/protocol/src/error_tests.rs` — `RateLimitReachedType` → user-facing message mapping:
  <https://github.com/openai/codex/blob/f1affbac/codex-rs/protocol/src/error_tests.rs>

---

## 7. Recommended source priority

### P0 — `codex app-server` JSON-RPC `account/rateLimits/read` ✅ primary

- **Data:** everything — both windows, integer `usedPercent`, absolute `resetsAt`, `planType`,
  `credits`, `rateLimitResetCredits.availableCount`, `ordinaryUsageAllowed`,
  `spendControlReached`, `individualLimit`, `rateLimitsByLimitId` (multi-bucket), `accountId`.
- **Freshness:** live (backend round-trip).
- **Cost:** 0.58 s, one process spawn, one HTTPS GET. **No model quota.**
- **Failure modes:** no network → RPC error `failed to fetch codex rate limits`; expired +
  unrefreshable token → `chatgpt authentication required to read rate limits`; API-key auth
  mode → same error (the endpoint is ChatGPT-only); backend returns nothing →
  `failed to fetch codex rate limits: no snapshots returned`; method renamed on a CLI upgrade.
- **Mitigation:** hard timeout (~10 s), always `kill`/`terminate` the child, cache the last good
  snapshot, fall through to P1 on any error. Poll no faster than every 60 s.
- **Caveat:** Codex writes its own tracing rows into `~/.codex/logs_2.sqlite` while running.

### P1 — newest rollout `token_count.rate_limits` ✅ offline fallback

- **Data:** both windows, float `used_percent`, `resets_at`, `plan_type`, `credits`,
  `rate_limit_reached_type`, plus `info.model_context_window` and token totals.
  Missing: `ordinary_usage_allowed`, `rate_limit_reset_credits`, `normal_model_slug`;
  `spend_control_reached` / `individual_limit` / `limit_name` are always null.
- **Freshness:** **stale by design** — advances only during an active turn. Observed newest
  0 h, next 4.6 h, oldest file 57 days. Always surface the age.
- **Cost:** ~0.02 s, ~64 KB read. Truly read-only. Works offline, works with no valid token.
- **Failure modes:** no session has ever run → nothing found; a brand-new session's tail has no
  `token_count` yet (mitigated by walking the 5 newest files); rollout format change.
- **Gotcha:** `primary` is the **weekly** window in 60 % of historical snapshots. Key off
  `window_minutes` (300 / 10080), never on field position.

### P2 — `state_5.sqlite` `threads` table ✅ model identity + per-thread tokens

- **Data:** `model`, `reasoning_effort`, `tokens_used`, `cwd`, `rollout_path`,
  `updated_at_ms`, `originator`, `source`, `cli_version`. **No account rate limits.**
- **Freshness:** updated per turn.
- **Cost:** microseconds (`mode=ro` URI).
- **Failure modes:** DB name is versioned (`state_5` → `state_6` on a future migration — glob
  `state_*.sqlite` and take the highest); WAL contents may not be visible in `mode=ro`.
- **Use:** join a Herdr agent pane's cwd/PID to a thread, then to the model and rollout file.

### P3 — `~/.codex/config.toml` ✅ last-resort model default

`model` + `model_reasoning_effort`. Wrong whenever `-m`, a profile, or an in-session `/model`
switch is in play. Use only when P2 yields nothing.

### P4 — `codex doctor --json` ⚠️ diagnostics only

Auth mode, configured model, CODEX_HOME, install paths, network reachability. **No usage
data.** Slow (~1.5 s, performs live network + WebSocket probes). Use for a one-time health
check, never for polling.

### P5 — raw HTTPS `GET /backend-api/codex/usage` ❌ do not implement

Requires reading the bearer token, implementing OAuth refresh, and writing refreshed tokens
back into `auth.json`. Violates read-only, races the CLI, and duplicates P0 for no gain.

### Dead ends (verified, do not revisit)

`.codex-global-state.json` (no usage data) · `logs_2.sqlite` (tracing text only, 3.4 GB) ·
`thread_history_1.sqlite` (58 incidental `rate_limit` string matches, zero snapshots) ·
`models_cache.json` (catalog only) · `session_index.jsonl` (7 stale rows, superseded) ·
`goals_1` / `memories_1` / `queue_1` (unrelated) · `account/usage/read` (lifetime + daily
tokens, no window percentages).

### Suggested composite strategy

```
1. P0 app-server, 10 s timeout, once per 60 s      -> live windows + plan + credits
2. on any P0 error: P1 rollout tail                -> same shape, report age_seconds
3. P2 threads table                                -> per-pane model + reasoning effort
4. normalise: key windows by window_minutes (300 / 10080), never by primary/secondary position
5. treat plan_type as an open string (unknown values are tolerated upstream)
```

---

## Appendix: reproduction scripts

- `/tmp/codex_rl_probe.py` — full app-server probe (`initialize`, `account/read`,
  `account/rateLimits/read`, `account/usage/read`, `model/list`).
- `/tmp/codex_rl_min.py` — minimal 2-message rate-limit read, 0.58 s.
- `/tmp/codex_rollout_tail.py` — offline tail-reader with `demo()` self-check, 0.019 s.
- `/tmp/codexschema/` — `codex app-server generate-json-schema --experimental` output
  (`ClientRequest.json` for methods, `codex_app_server_protocol.v2.schemas.json` for types).

Rules honoured: nothing written under `~/.codex` by this research (Codex's own app-server
process wrote its tracing rows); all SQLite opened via `file:...?mode=ro`; no
`codex login/logout`; no interactive session; no model turn; no token value printed.
