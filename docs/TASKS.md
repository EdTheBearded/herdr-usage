# Task breakdown — TDD

RED → GREEN → REFACTOR per task. Each task names its test file, the assertions
that must fail first, and the done condition. Pure logic is unit-tested; the
three subprocess seams (`codex`, `claude`, `herdr`) are tested against recorded
fixtures plus one opt-in live check.

Fixtures captured from the real CLIs live in `tests/fixtures/`:
`codex_ratelimits.json`, `claude_get_usage.json` (both already obtained live).

---

## T1 — `render.bar()`

`tests/test_render.py`

- 0 → no fill; 100 → full; 0.4 → exactly one cell (never rounds to empty)
- 99.9 → `cells-1`, **not** full — the rule that makes "full bar = exhausted" true
- all six sets produce `cells` characters; `bar_cells` respected
- `None` → the `⋯ no data` form, never a 0% bar

Done: every set × {0, 0.4, 31, 82, 99.9, 100, None} asserted.

## T2 — `render` durations and ETA

`tests/test_render.py`

- `fmt_duration`: 74 s → `1m`, 4940 s → `1h22m`, 90000 s → `1d1h`, negative → `0m`
- burn ETA suppressed when `Δt < 60`, `pct <= prev_pct`, or ETA > time-to-reset
- normal case: 31%→41% over 600 s ⇒ ~59 min

## T3 — severity band

`tests/test_model.py`

- thresholds warn=70 crit=90: 69→ok, 70→warn, 89→warn, 90→crit, 100→crit
- `blocked=True` ⇒ crit regardless of pct
- `pct=None` ⇒ `none`
- **a provider-supplied `severity` overrides the thresholds** (Claude's
  `normal|warning|critical`)

## T4 — token dict builder

`tests/test_render.py`

- exactly one of `_ok|_warn|_crit|_none` present per gauge; the other three are
  in the **clear** list
- gauge key slugified: `scoped:Fable` → `uq_gscoped_Fable_ok`, matches
  `^[A-Za-z0-9_-]{1,32}$`
- every value ≤ 80 characters (Herdr truncates silently above that)
- extras absent from the dict when their toggle is off
- 4 gauges + all extras ≤ 32 tokens

## T5 — Codex normalizer

`tests/test_providers.py`, fixture `codex_ratelimits.json`

- `windowDurationMins` 300 → `5h`, 10080 → `wk`; **a fixture with the windows
  swapped between `primary`/`secondary` still yields the same two gauges** (the
  2242/3703 trap)
- snake_case rollout spelling parses identically to camelCase
- `rateLimitReachedType` non-null ⇒ `blocked`
- `credits.balance` as a string, and as null, both survive
- unknown `windowDurationMins` (e.g. 43200) ⇒ a gauge labelled from the duration,
  not a crash

## T6 — Claude normalizer

`tests/test_providers.py`, fixture `claude_get_usage.json`

- `five_hour`/`seven_day` → two gauges; `model_scoped[]` → one gauge each
- **`seven_day_opus: null` does not produce a gauge** — the "don't hardcode Opus"
  rule
- `limits[].severity` is carried through
- `locked_reason` non-null ⇒ `blocked`
- `subscription_type: null` ⇒ plan absent, not the string `"None"`

## T7 — declarative provider

`tests/test_providers.py`

- dotted path resolves `$.a.b[0].c`
- missing path ⇒ `None` ⇒ `⋯ no data`, no exception
- a path that resolves to a non-number ⇒ `None`, logged once
- malformed provider table ⇒ that provider is skipped, the others still render

## T8 — config load / merge / write

`tests/test_config.py`

- missing file ⇒ defaults
- partial file ⇒ merged over defaults, unspecified keys keep defaults
- `interval_secs = 1` clamped to `min_interval_secs`
- **surgical writer preserves comments, key order and unrelated tables**
- a key absent from the file is appended under its table, not duplicated
- writer round-trips through `tomllib` (output always re-parses)

## T9 — cache

`tests/test_cache.py`

- fresh cache ⇒ no probe; expired ⇒ probe
- write is atomic (temp + replace); a partially-written file never loads
- a `.lock` older than 60 s is ignored
- previous sample retained for the burn-rate calculation

## T10 — sidebar managed block

`tests/test_sidebar.py`

- generated block re-parses and matches the enabled gauges/extras
- install into a config with no block; re-install replaces in place (never
  duplicates); `--undo` removes it and leaves surrounding content byte-identical
- **a deliberately broken block ⇒ `config check` fails ⇒ the original file is
  restored** — the data-loss guard, proven not asserted
- `--undo` writes the `uninstalled` marker; auto-install then does nothing

## T11 — model identity

`tests/test_providers.py`

- codex: `threads` rows → the row matching the pane `cwd` with the newest
  `updated_at_ms` wins; opened `mode=ro`
- claude: last `message.model` in the transcript; **no transcript ⇒ `None` ⇒ `—`**
- `claude-opus-5` → `Opus`, `gpt-5.6-terra` → `Terra`; unknown slug → the slug,
  truncated

## T12 — herdr seam

`tests/test_herdrctl.py`

- `pane_list` parses real recorded output; panes without `agent` are skipped
- `report_metadata` builds the right argv, one `--token`/`--clear-token` per entry
- a non-zero exit is surfaced, never swallowed

## T13 — refresh orchestration

`tests/test_main.py`

- cache hit ⇒ zero probe calls (the CPU guarantee, asserted with a fake probe)
- one probe per **account**, fanned out to every matching pane — not one per pane
- a failing provider doesn't block the other
- startup mode always probes

## T14 — settings TUI reducer

`tests/test_settings.py`

- pure `(state, key) → state`: navigation, toggle, cycle bar set, numeric edit
  with bounds, save/cancel
- cancel leaves the config file untouched
- save triggers exactly one config write + one sidebar regenerate

## T15 — live smoke (opt-in, `-m live`)

- real `codex` probe returns two gauges with sane percentages
- real `claude` probe returns `five_hour`
- `herdr config check` on the generated block returns `ok`
- skipped by default so CI never needs credentials

---

## Then

| Step | Gate |
|---|---|
| Manifest + wiring | `herdr plugin link` clean, no warnings |
| README | install, manual sidebar block, provider recipe, uninstall |
| Live render | **Eduardo confirms the sidebar visually** — no agent can verify this |
| Publish round-trip | `unlink → install from GitHub → invoke → uninstall → link` |
