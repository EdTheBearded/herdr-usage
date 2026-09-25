"""Refresh orchestration: probe once per account, fan out to every pane."""

import copy
import time

from . import cache, herdrctl, render
from .model import Gauge, burn_eta
from .render import fmt_duration


def _gauges_of(usage):
    return usage.gauges if hasattr(usage, "gauges") else (usage.get("gauges") or [])


def _attr(obj, name, default=None):
    if hasattr(obj, name):
        return getattr(obj, name)
    if isinstance(obj, dict):
        return obj.get(name, default)
    return default


def _as_dict(usage) -> dict:
    """AccountUsage (or an already-plain dict) -> a JSON-safe dict."""
    if isinstance(usage, dict):
        return copy.deepcopy(usage)
    out = {
        "provider": usage.provider,
        "account_id": usage.account_id,
        "plan": usage.plan,
        "credits": usage.credits,
        "cost_usd": usage.cost_usd,
        "fetched_at": usage.fetched_at,
        "error": usage.error,
        "gauges": [],
    }
    for g in usage.gauges:
        out["gauges"].append({
            "key": g.key, "label": g.label, "pct": g.pct,
            "resets_at": g.resets_at, "severity": g.severity,
            "blocked": g.blocked,
        })
    return out


def _extras(cfg, entry, gauges, now):
    display = cfg.get("display") or {}
    extras = {}

    if display.get("show_plan") and entry.get("plan"):
        extras["plan"] = str(entry["plan"])

    bits = []
    if display.get("show_burn"):
        for g in gauges:
            eta = burn_eta(
                g.get("pct"), g.get("prev_pct"),
                (g.get("fetched_at") or entry.get("fetched_at") or now)
                - (g.get("prev_fetched_at") or 0)
                if g.get("prev_fetched_at") else None,
                g.get("resets_at"), now,
            )
            if eta:
                bits.append(f"⚡ {fmt_duration(eta)}")
                break
    if display.get("show_resets_in"):
        soon = [g.get("resets_at") for g in gauges if g.get("resets_at")]
        if soon:
            bits.append(f"↻ {fmt_duration(min(soon) - now)}")
    if bits:
        extras["burn"] = " · ".join(bits)

    tail = []
    if display.get("show_credits") and entry.get("credits"):
        tail.append(f"★{entry['credits']}")
    if display.get("show_cost") and entry.get("cost_usd"):
        tail.append(f"${entry['cost_usd']:.2f}")
    if tail:
        extras["extra"] = " · ".join(tail)
    return extras


def _provider_of(pane) -> str | None:
    """Which account's quota this pane consumes.

    Usually the agent itself, but a wrapper agent (Hermes) routes to a provider
    chosen per session, so the caller resolves that and sets `provider`. A pane
    pointed at a local or custom endpoint has no subscription quota and
    resolves to None -- it must show nothing, not a borrowed gauge.
    """
    if "provider" in pane:
        return pane["provider"]
    return pane.get("agent")


def refresh(cfg, cache_data, panes, probes, now=None, force: bool = False) -> dict:
    """Probe stale providers once each, then write tokens on every agent pane.

    The cache TTL is the rate limiter: with a fresh cache this makes zero
    probe calls no matter how many panes or events triggered it.
    """
    now = time.time() if now is None else now
    cfg = copy.deepcopy(cfg)
    display = cfg.get("display") or {}
    thresholds = cfg.get("thresholds") or {}
    ttl = (cfg.get("refresh") or {}).get("interval_secs", 300)
    stale_after = ttl * thresholds.get("stale_multiplier", 3)

    data = copy.deepcopy(cache_data) if isinstance(cache_data, dict) else {}
    # A pane's quota provider is not always its agent: a Hermes pane routes to
    # whichever provider that session was configured with.
    wanted = {p for p in (_provider_of(x) for x in panes) if p}
    fresh = cache.is_fresh(data, ttl, now)

    for provider in sorted(wanted):
        if not (cfg.get("providers") or {}).get(provider, {}).get("enabled", True):
            continue
        probe = probes.get(provider)
        if probe is None or (fresh and not force):
            continue
        try:
            data[provider] = _as_dict(probe())  # one probe per ACCOUNT
        except Exception as exc:  # a dead provider must not hide a live one
            entry = data.get(provider) or {"provider": provider, "gauges": []}
            entry["error"] = str(exc)
            data[provider] = entry
    if not fresh or force:
        if wanted:
            data["fetched_at"] = now

    results = {}
    for pane in panes:
        provider = _provider_of(pane)
        entry = data.get(provider) or {}
        selected = [
            g for g in entry.get("gauges") or []
            if not display.get("gauges") or g.get("key") in display["gauges"]
            or (display.get("show_scoped") and str(g.get("key", "")).startswith("scoped:"))
        ]
        gauges = [
            Gauge(
                key=g.get("key", "?"), label=g.get("label", "?"), pct=g.get("pct"),
                resets_at=g.get("resets_at"), severity=g.get("severity"),
                blocked=bool(g.get("blocked")),
            )
            for g in selected
        ]
        extras = _extras(cfg, entry, selected, now)
        if display.get("show_model") and pane.get("model"):
            extras["model"] = pane["model"]
        if display.get("show_icon", True):
            # Icon follows the billed provider, falling back to an explicit
            # vendor hint for panes that consume no quota (local endpoints).
            icon = render.icon_for(
                provider or pane.get("vendor"),
                display.get("icon_set", "brand"),
            )
            if icon:
                extras["model"] = f"{icon} {extras['model']}" if extras.get("model") else icon

        stamp = entry.get("fetched_at")
        stale = bool(stamp) and (now - stamp) > stale_after
        tokens, clear = render.build_tokens(
            gauges, extras=extras,
            cells=display.get("bar_cells", 10),
            bar_set=display.get("bar_set", "blocks"),
            warn=thresholds.get("warn", 70), crit=thresholds.get("crit", 90),
            stale=stale,
        )
        pane_id = pane.get("pane_id") or pane.get("id")
        results[pane_id] = {"tokens": tokens, "clear": clear, "provider": provider}
        try:
            herdrctl.report_metadata(pane_id, herdrctl.SOURCE, tokens, clear)
        except Exception:
            pass  # one unreachable pane must not abort the rest

    data["panes"] = results
    return data
