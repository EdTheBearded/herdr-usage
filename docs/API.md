# API contract — `herdr_usage`

Signature-and-behaviour contract derived from `docs/SPEC.md`. This file is the
**only** description of the implementation given to independent test authors:
it carries signatures and observable behaviour, never bodies.

Python 3.11+ (`tomllib`). Stdlib only. Tests use stdlib `unittest`.

Import root is the package `herdr_usage` (repo root on `sys.path`).

---

## `herdr_usage.model`

```python
@dataclass
class Gauge:
    key: str                      # "5h" | "wk" | "mo" | "scoped:Fable"
    label: str                    # what renders in the bar, e.g. "5h", "wk Fable"
    pct: float | None = None      # 0..100. None = unknown. NEVER 0-as-unknown.
    resets_at: float | None = None      # epoch seconds
    severity: str | None = None   # provider's own band, verbatim
    blocked: bool = False         # hard wall hit

@dataclass
class AccountUsage:
    provider: str
    account_id: str | None = None
    plan: str | None = None
    gauges: list[Gauge] = []      # default_factory=list
    credits: int | None = None
    cost_usd: float | None = None
    fetched_at: float | None = None
    error: str | None = None
```

```python
def window_label(minutes: int | None) -> str
```
Window identity comes from its **duration**, never a field name.
`300 -> "5h"`, `10080 -> "wk"`, `43200 -> "mo"`. Other exact multiples derive:
`60 -> "1h"`, `120 -> "2h"`, `1440 -> "1d"`. Anything else is minutes
(`77 -> "77m"`). `None` or `0` -> `"?"`. Result is always short (<= 6 chars for
sane inputs).

```python
def band(g: Gauge, warn: float = 70, crit: float = 90) -> str
```
Returns `"ok" | "warn" | "crit" | "none"`. Precedence, in order:
1. `g.blocked` -> `"crit"` (regardless of `pct`, including `pct is None`)
2. `g.severity` in `{normal, warning, critical}` (case-insensitive) ->
   `{ok, warn, crit}` — **a provider's own band wins over the thresholds**
3. unrecognised `severity` -> fall through to thresholds
4. `pct is None` -> `"none"`
5. `pct >= crit` -> `"crit"`; `pct >= warn` -> `"warn"`; else `"ok"`

```python
def burn_eta(pct, prev_pct, dt, resets_at, now, min_dt: float = 60) -> float | None
```
Seconds until this window is exhausted at the current rate.
`rate = (pct - prev_pct) / dt`, `eta = (100 - pct) / rate`.
Returns `None` when: `prev_pct is None`, `pct is None`, `dt is None`,
`dt < min_dt`, `pct <= prev_pct`, `pct >= 100`, or
`resets_at is not None and now + eta > resets_at` (an ETA past the reset is
meaningless).

---

## `herdr_usage.render`

```python
BAR_SETS: dict[str, tuple[str, str]]   # name -> (full_char, empty_char)
# keys: blocks ██░░ | shade ▓░ | squares ■□ | dots ●○ | half ▮▯ | ascii #-
BLOCKED = "⛔️"
STALE = "~"
NO_DATA = "⋯ no data"
TOKEN_MAX = 80
```

```python
def bar(pct: float, cells: int = 10, bar_set: str = "blocks") -> str
```
Always exactly `cells` characters. Fill rule:
- `pct >= 100` -> `cells` full chars (**only** 100 may render full)
- `pct <= 0` -> 0 full chars
- otherwise -> `min(cells-1, max(1, ceil(pct/100*cells)))` — any non-zero
  percentage shows at least one cell and never a full bar

Out-of-range clamps. Unknown `bar_set` falls back to `blocks`.

```python
def fmt_duration(secs: float | None) -> str | None
```
`None -> None`. Negative clamps to 0. `74 -> "1m"`, `4940 -> "1h22m"`,
`90000 -> "1d1h"`, `59 -> "0m"`.

```python
def gauge_text(g, cells=10, bar_set="blocks", stale=False) -> str
```
- `pct is None` and not blocked -> `f"{label} {NO_DATA}"` — contains no bar
  characters and no `"0%"`
- blocked -> bar rendered full, tail is `BLOCKED` instead of a percentage
- otherwise -> `f"{label} {bar} {pct:3.0f}%"` (e.g. `"5h ████░░░░░░  31%"`)
- `stale=True` appends `" ~"`
- result is truncated to `TOKEN_MAX`

```python
def build_tokens(gauges, extras=None, cells=10, bar_set="blocks",
                 warn=70, crit=90, stale=False) -> tuple[dict[str,str], list[str]]
```
Returns `(tokens_to_set, token_names_to_clear)`.

Per gauge, token base name is `f"uq_g{slug(g.key)}"` where `slug` replaces every
character outside `[A-Za-z0-9_-]` with `_` **and bounds the result so the full
token name stays within 32 characters** (Herdr rejects longer keys). An
over-long key keeps a readable head plus a short hash tail, so two distinct long
keys never collide into the same token. Exactly one of
`{base}_ok|_warn|_crit|_none` is present in `tokens` (chosen by `band`); the
other three appear in `clear`.

Extras: keys `model`, `plan`, `burn`, `extra` map to tokens `uq_model`,
`uq_plan`, `uq_burn`, `uq_extra`. Present-and-truthy -> in `tokens`
(truncated to `TOKEN_MAX`); absent/falsy -> name in `clear`.

Every emitted token name matches `^[A-Za-z0-9_-]{1,32}$` and every value is
<= 80 characters (Herdr truncates silently above that).

---

## `herdr_usage.providers`

```python
def short_model(slug: str | None) -> str | None
```
Family name from a model slug: `claude-opus-5 -> "Opus"`,
`claude-sonnet-5 -> "Sonnet"`, `claude-haiku-4-5-20251001 -> "Haiku"`,
`gpt-5.6-terra -> "Terra"`, `gpt-6-sol -> "Sol"`. Unknown slug -> the slug
truncated to 20 chars. `None -> None`; `"" -> ""` (only `None` maps to `None`).

```python
def normalize_codex(doc: dict) -> AccountUsage
```
`doc` is the `result` of the app-server `account/rateLimits/read` response.
Reads `doc["rateLimits"]` (or `rate_limits`).

- Iterates slots `primary`, `secondary`. **The window label comes from
  `windowDurationMins` (camelCase) or `window_minutes` (snake_case), NEVER from
  the slot name** — `primary` carries the weekly window in ~60% of real
  snapshots. A fixture with the two slots swapped must yield an identical set of
  `(label, pct)` pairs.
- `usedPercent` / `used_percent` -> `pct`; `resetsAt` / `resets_at` -> `resets_at`
- a slot that is `None` or lacks a duration is skipped, not an error
- `rateLimitReachedType` / `rate_limit_reached_type` non-null -> a gauge is
  `blocked`
- `planType` / `plan_type` -> `plan`; `accountId` -> `account_id`
- `doc["rateLimitResetCredits"]["availableCount"]` -> `credits` (int or None)
- `credits.balance` may be a string or null; neither breaks parsing
- `provider == "codex"`

```python
def normalize_claude(doc: dict) -> AccountUsage
```
`doc` is the `get_usage` control-response payload.

- `rate_limits.five_hour` -> gauge key/label `"5h"`;
  `rate_limits.seven_day` -> `"wk"`
- each entry's `utilization` -> `pct`, `resets_at` (**ISO-8601 string**) ->
  epoch float, `locked_reason` non-null -> `blocked`
- `rate_limits.limits[]` entries carry `kind` + `severity`; severity is attached
  to the matching gauge (`kind=="session"` -> 5h, `kind=="weekly_all"` -> wk,
  `kind=="weekly_scoped"` -> scoped gauges) **verbatim**, not translated
- `rate_limits.model_scoped[]` -> one gauge each, key `f"scoped:{display_name}"`,
  label `f"wk {display_name}"`. **`seven_day_opus` is NOT read** — which model
  gets its own bucket is server-driven and is often null while a differently
  named model has a bucket
- `subscription_type` -> `plan` (a JSON null stays `None`, never the string
  `"None"`)
- `session.total_cost_usd` -> `cost_usd`
- `provider == "claude"`

```python
def resolve_path(doc, path: str)
```
Dotted subset of JSONPath: `$.a.b[0].c`. No expressions, no filters, no code.
A path that does not start with `"$"`, or that cannot be resolved (missing key,
index out of range, wrong container type), returns `None` — never raises.

```python
def run_declarative(name: str, spec: dict, doc: dict) -> AccountUsage
```
Builds an `AccountUsage` by resolving `spec` paths against `doc`.
`spec` keys: `account_id`, `plan`, `gauges` (list of
`{label, key?, pct, resets_at}`).

**A percentage must be a real JSON number.** A string (even `"42"`), a bool, or
an unresolvable path all yield `pct=None`. A malformed `gauges` value (not a
list, or entries that aren't dicts) yields no gauges rather than an exception.

```python
def probe_codex(timeout: float = 20) -> dict
def probe_claude(timeout: float = 30) -> dict
def probe_declarative(spec: dict, timeout: float = 30) -> dict
```
Subprocess probes. Raise `RuntimeError` when the expected response never
arrives. `probe_declarative` requires `spec["command"]` to be a non-empty list.

```python
def codex_model(cwd: str, state_db: str) -> str | None
def claude_model(transcript: pathlib.Path) -> str | None
```
`codex_model` opens the SQLite DB **read-only** (`file:...?mode=ro`, uri=True)
and returns `threads.model` for the newest row (`ORDER BY updated_at_ms DESC`)
matching `cwd`. Any `sqlite3.Error` or missing DB -> `None`.
`claude_model` returns the last `message.model` in the JSONL transcript, or
`None` when the file is missing or has no assistant turn yet.

---

## `herdr_usage.config` (to implement — T8)

```python
DEFAULTS: dict     # nested dict matching SPEC §9
def load(path) -> dict
def set_value(path, dotted_key: str, value) -> None
```
- missing file -> a deep copy of `DEFAULTS`
- a partial file is **deep-merged over** `DEFAULTS`; unspecified keys keep their
  default; unknown keys are preserved
- `refresh.interval_secs` is clamped up to `refresh.min_interval_secs`
- `set_value` is a **surgical** writer: it preserves comments, key order and
  unrelated tables, appends a key under its existing table rather than
  duplicating it, and its output always re-parses with `tomllib`

Config schema (SPEC §9): `[refresh] interval_secs=300 min_interval_secs=60`;
`[display] bar_set="blocks" bar_cells=10 gauges=["5h","wk"] show_model=true
show_plan=false show_burn=false show_resets_in=false show_credits=false
show_cost=false show_scoped=false`;
`[thresholds] warn=70 crit=90 stale_multiplier=3`;
`[providers.codex] enabled=true`; `[providers.claude] enabled=true`.

## `herdr_usage.cache` (to implement — T9)

```python
def load(path) -> dict
def save(path, data: dict) -> None
def is_fresh(data: dict, ttl: float, now: float | None = None) -> bool
```
- `save` is atomic: temp file + `os.replace`, so a partially written file is
  never observed by `load`
- a corrupt/partial file loads as `{}` rather than raising
- `is_fresh` compares `data["fetched_at"]` against `now - ttl`
- the previous sample (`pct`, `fetched_at`) is retained per gauge so
  `burn_eta` has a `prev_pct`
- a `.lock` file older than 60 s is ignored

## `herdr_usage.sidebar` (to implement — T10)

```python
BEGIN = "# >>> herdr-usage managed block"
END   = "# <<< herdr-usage managed block"
def generate(cfg: dict) -> str
def install(config_path, block: str, check=...) -> bool
def undo(config_path) -> bool
```
- `generate` emits a `[ui.sidebar.agents]` block whose rows reference the
  enabled gauges and extras, with one fixed-`fg` entry per severity variant
- `install` replaces an existing block **in place** (never duplicates) and
  appends when absent
- **data-loss guard:** the new content is validated (`herdr config check` on a
  temp copy) before the real file is touched; on failure the original file is
  restored byte-identically and `install` returns False
- `undo` removes the block, leaves surrounding content byte-identical, and
  records an `uninstalled` marker so auto-install does not re-add it

## `herdr_usage.herdrctl` (to implement — T12)

```python
def pane_list() -> list[dict]
def report_metadata(pane_id: str, source: str, tokens: dict, clear: list) -> None
def config_check(config_path=None) -> bool
```
- `pane_list` runs `herdr pane list`, which returns
  `{"id": ..., "result": {"panes": [...], "type": "pane_list"}}`. It returns
  only panes carrying an `agent` key. A bare list is also accepted.
- `report_metadata` builds one `--token NAME=VALUE` per token and one
  `--clear-token NAME` per cleared name, plus `--source <source>`
- a non-zero exit from `pane_list` / `report_metadata` raises
  `subprocess.CalledProcessError`, never swallowed
- `config_check` is the exception: it **returns `False`** for an invalid config
  rather than raising, because a bad config is an expected outcome there, not a
  failure of the call. `SOURCE` is the constant `"herdr-usage"`.

## `herdr_usage.sidebar` — marker location

The `undo` marker is a file named `uninstalled` inside the plugin's state dir
(`$HERDR_PLUGIN_STATE_DIR`, falling back to
`~/.local/state/herdr/plugins/herdr-usage/`).

## `herdr_usage.main` (to implement — T13)

```python
def refresh(cfg, cache_data, panes, probes, now, force: bool = False) -> dict
```
- `force=True` (startup mode, and the manual `refresh` action) always probes,
  ignoring the TTL
- a fresh cache performs **zero** probe calls
- one probe **per account**, fanned out to every matching pane (not one probe
  per pane)
- a failing provider does not prevent the others from rendering
- **returns the updated cache dict** and calls `herdrctl.report_metadata` once
  per pane. Tests may assert on either channel.

## `herdr_usage.settings_tui` (to implement — T14)

```python
def initial_state(cfg: dict) -> dict
def reduce(state: dict, key) -> dict
```
`initial_state` returns `{"cfg": <deep copy>, "cursor": 0, "done": None}`.

`reduce` is pure: it **never mutates** the state passed in, and returns a new
dict. `key` is a string; both single characters (`"j"`, `"k"`, `"q"`) and
curses key names (`"KEY_DOWN"`, `"KEY_UP"`) are accepted.

- navigation (`j`/`KEY_DOWN`, `k`/`KEY_UP`) moves `cursor` and **wraps** at both
  ends
- `space`/`enter` toggles a boolean field, cycles `bar_set` through all six
  `BAR_SETS` names (wrapping), or increments/decrements a numeric field
- numeric bounds: `bar_cells` 1..80; `warn`/`crit` 0..100 and `warn <= crit`;
  `interval_secs >= min_interval_secs`; nothing may go negative
- `"s"` sets `done = "save"`; `"q"`/escape sets `done = "cancel"`
- `done == "cancel"` leaves the config file untouched; `done == "save"` triggers
  exactly one config write and one sidebar regenerate (performed by the caller,
  not by `reduce`)
