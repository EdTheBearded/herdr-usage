"""Provider probes and normalizers. Read-only on every agent's state."""

import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time

from .model import AccountUsage, Gauge, window_label

# A plugin process inherits Herdr's PATH, not an interactive shell's, so user
# installs under ~/.local/bin or a version manager are invisible (verified:
# Errno 2 for both `codex` and `claude` when invoked as a plugin action).
_EXTRA_PATHS = (
    "~/.local/bin",
    "~/.local/share/claude/bin",
    "~/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
)


def resolve_binary(name: str) -> str:
    """Absolute path to an agent CLI, searching the usual user install dirs."""
    found = shutil.which(name)
    if found:
        return found
    for base in _EXTRA_PATHS:
        candidate = os.path.join(os.path.expanduser(base), name)
        if os.access(candidate, os.X_OK):
            return candidate
    return name  # let the caller fail with a clear Errno 2

_MODEL_FAMILIES = [
    (re.compile(r"opus", re.I), "Opus"),
    (re.compile(r"sonnet", re.I), "Sonnet"),
    (re.compile(r"haiku", re.I), "Haiku"),
    (re.compile(r"terra", re.I), "Terra"),
    (re.compile(r"luna", re.I), "Luna"),
    (re.compile(r"astra", re.I), "Astra"),
    (re.compile(r"\bsol\b|-sol", re.I), "Sol"),
]


def short_model(slug: str | None) -> str | None:
    if slug is None:
        return None
    for pat, name in _MODEL_FAMILIES:
        if pat.search(slug):
            return name
    return slug[:20]


def _num(v):
    """Percentages only. A non-number is unknown, never 0."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _epoch(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return datetime.datetime.fromisoformat(v).timestamp()
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------- codex

def _codex_window(w):
    """camelCase (app-server) and snake_case (rollout jsonl) spellings."""
    if not isinstance(w, dict):
        return None, None, None
    mins = w.get("windowDurationMins", w.get("window_minutes"))
    pct = _num(w.get("usedPercent", w.get("used_percent")))
    resets = w.get("resetsAt", w.get("resets_at"))
    return mins, pct, _epoch(resets)


def normalize_codex(doc: dict) -> AccountUsage:
    rl = doc.get("rateLimits") or doc.get("rate_limits") or {}
    blocked = bool(rl.get("rateLimitReachedType") or rl.get("rate_limit_reached_type"))
    gauges = []
    # Never key off the field name: `primary` is the WEEKLY window in ~60% of
    # real snapshots. The duration is the identity.
    for slot in ("primary", "secondary"):
        mins, pct, resets = _codex_window(rl.get(slot))
        if mins is None:
            continue
        label = window_label(mins)
        gauges.append(Gauge(key=label, label=label, pct=pct, resets_at=resets,
                            blocked=blocked and pct is not None and pct >= 100))
    if blocked and gauges and not any(g.blocked for g in gauges):
        gauges[0].blocked = True

    credits = (doc.get("rateLimitResetCredits") or {}).get("availableCount")
    return AccountUsage(
        provider="codex",
        account_id=doc.get("accountId") or doc.get("account_id"),
        plan=rl.get("planType") or rl.get("plan_type"),
        gauges=gauges,
        credits=credits if isinstance(credits, int) else None,
        fetched_at=time.time(),
    )


def probe_codex(timeout: float = 20) -> dict:
    """Ask the app-server for rate limits over stdio JSON-RPC.

    stdin is held open while we read: closing it immediately (what
    subprocess.run(input=...) does) makes the server exit before it answers.
    """
    req = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientInfo": {"name": "herdr-usage", "title": "herdr-usage",
                                   "version": "0.1.0"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": {}},
    ]
    proc = subprocess.Popen(
        [resolve_binary("codex"), "app-server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True,
    )
    try:
        assert proc.stdin and proc.stdout
        for r in req:
            proc.stdin.write(json.dumps(r) + "\n")
        proc.stdin.flush()
        deadline = time.time() + timeout
        for line in proc.stdout:
            if time.time() > deadline:
                break
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("id") == 2 and "result" in d:
                return d["result"]
    finally:
        proc.kill()
        proc.wait(timeout=5)
    raise RuntimeError("codex app-server returned no rateLimits result")


# -------------------------------------------------------------------- claude

def _claude_gauge(key, label, raw, severity=None):
    if not isinstance(raw, dict):
        return None
    return Gauge(
        key=key,
        label=label,
        pct=_num(raw.get("utilization")),
        resets_at=_epoch(raw.get("resets_at")),
        severity=severity,
        blocked=bool(raw.get("locked_reason")),
    )


def normalize_claude(doc: dict) -> AccountUsage:
    rl = doc.get("rate_limits") or {}
    sev = {}
    for entry in rl.get("limits") or []:
        if isinstance(entry, dict):
            sev[entry.get("kind")] = entry.get("severity")

    gauges = []
    for src, key, label, kind in (("five_hour", "5h", "5h", "session"),
                                  ("seven_day", "wk", "wk", "weekly_all")):
        g = _claude_gauge(key, label, rl.get(src), sev.get(kind))
        if g:
            gauges.append(g)

    # Which model gets its own weekly bucket is server-driven -- iterate,
    # never assume Opus.
    for scoped in rl.get("model_scoped") or []:
        if not isinstance(scoped, dict):
            continue
        name = scoped.get("display_name") or "?"
        gauges.append(Gauge(
            key=f"scoped:{name}", label=f"wk {name}",
            pct=_num(scoped.get("utilization")),
            resets_at=_epoch(scoped.get("resets_at")),
            severity=sev.get("weekly_scoped"),
        ))

    session = doc.get("session") or {}
    return AccountUsage(
        provider="claude",
        account_id=None,
        plan=doc.get("subscription_type"),
        gauges=gauges,
        cost_usd=session.get("total_cost_usd"),
        fetched_at=time.time(),
    )


def probe_claude(timeout: float = 30) -> dict:
    req = {"type": "control_request", "request_id": "r1",
           "request": {"subtype": "get_usage", "skip_behaviors": True}}
    out = subprocess.run(
        [resolve_binary("claude"), "-p", "--verbose", "--safe-mode",
         "--input-format", "stream-json", "--output-format", "stream-json",
         "--no-session-persistence"],
        input=json.dumps(req) + "\n", capture_output=True, text=True, timeout=timeout,
    ).stdout
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "control_response":
            resp = d.get("response") or {}
            if resp.get("subtype") == "success":
                return resp.get("response") or {}
    raise RuntimeError("claude returned no get_usage response")


# --------------------------------------------------------------- declarative

_INDEX = re.compile(r"^(.*?)\[(\d+)\]$")


def resolve_path(doc, path: str):
    """Dotted subset of JSONPath: $.a.b[0].c. No expressions, no code."""
    if not isinstance(path, str) or not path.startswith("$"):
        return None
    cur = doc
    for part in path.lstrip("$").lstrip(".").split("."):
        if not part:
            continue
        m = _INDEX.match(part)
        idx = None
        if m:
            part, idx = m.group(1), int(m.group(2))
        if part:
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        if idx is not None:
            if not isinstance(cur, list) or idx >= len(cur):
                return None
            cur = cur[idx]
    return cur


def run_declarative(name: str, spec: dict, doc: dict) -> AccountUsage:
    gauges = []
    entries = spec.get("gauges")
    # A user's config.toml is untrusted input: a scalar here must degrade to
    # "no data", never crash the provider.
    if not isinstance(entries, (list, tuple)):
        entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        label = entry.get("label") or "?"
        gauges.append(Gauge(
            key=entry.get("key") or label,
            label=label,
            pct=_num(resolve_path(doc, entry.get("pct", ""))),
            resets_at=_epoch(resolve_path(doc, entry.get("resets_at", ""))),
        ))
    return AccountUsage(
        provider=name,
        account_id=resolve_path(doc, spec.get("account_id", "")),
        plan=resolve_path(doc, spec.get("plan", "")),
        gauges=gauges,
        fetched_at=time.time(),
    )


def probe_declarative(spec: dict, timeout: float = 30) -> dict:
    cmd = spec.get("command")
    if not isinstance(cmd, list) or not cmd:
        raise RuntimeError("provider command must be a non-empty list")
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    return json.loads(out)


# ----------------------------------------------------------- model identity

def codex_model(cwd: str, state_db: str) -> str | None:
    """Newest thread for this cwd. Read-only: mode=ro, never written."""
    try:
        con = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        row = con.execute(
            "SELECT model FROM threads WHERE cwd = ? ORDER BY updated_at_ms DESC LIMIT 1",
            (cwd,),
        ).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def claude_model(transcript) -> str | None:
    """Last assistant model in the session transcript. None until the first turn."""
    try:
        lines = transcript.read_text().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        model = (d.get("message") or {}).get("model")
        if model:
            return model
    return None


# Hermes routes each session to a provider of its own choosing, so a hermes
# pane's quota depends on that session's billing_provider -- never on the agent
# name. Only these hosts are the accounts our probes actually read; anything
# else (a local llama.cpp, a proxy, a custom endpoint) consumes no subscription
# quota and must show no gauge rather than a borrowed one.
_HERMES_PROVIDERS = {
    "openai-codex": ("codex", "chatgpt.com"),
    "anthropic": ("claude", "api.anthropic.com"),
}


def hermes_session(session_id, state_db) -> dict | None:
    """Model + effective provider for one Hermes session. Read-only."""
    if not session_id:
        return None
    try:
        con = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        row = con.execute(
            "SELECT model, billing_provider, billing_base_url FROM sessions"
            " WHERE id = ?",
            (session_id,),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if not row:
        return None

    model, billing, base_url = row
    provider = None
    vendor = None
    mapped = _HERMES_PROVIDERS.get(billing or "")
    if mapped:
        name, host = mapped
        # An unexpected base_url means we cannot claim it is the same account.
        if not base_url or host in base_url:
            provider = name
    elif billing:
        # An endpoint we don't bill against at all (llama.cpp, a custom host):
        # no quota exists to show, and that is worth labelling. A *mapped*
        # provider behind an unknown base_url is deliberately NOT labelled --
        # it may well be the user's account, we just cannot prove it.
        vendor = "local"
    return {"model": model, "provider": provider, "vendor": vendor,
            "billing_provider": billing, "base_url": base_url}

