"""Data model: gauges, severity bands, burn-rate ETA."""

from dataclasses import dataclass, field

# Provider-reported severities -> our bands. Anything else falls back to thresholds.
_SEVERITY = {"normal": "ok", "warning": "warn", "critical": "crit"}

_WINDOWS = {300: "5h", 10080: "wk", 43200: "mo"}


@dataclass
class Gauge:
    key: str
    label: str
    pct: float | None = None
    resets_at: float | None = None
    severity: str | None = None  # provider's own band, if it sends one
    blocked: bool = False


@dataclass
class AccountUsage:
    provider: str
    account_id: str | None = None
    plan: str | None = None
    gauges: list[Gauge] = field(default_factory=list)
    credits: int | None = None
    cost_usd: float | None = None
    fetched_at: float | None = None
    error: str | None = None


def window_label(minutes: int | None) -> str:
    """Window identity comes from its duration, never from a field name.

    Codex reports the weekly window under `primary` in ~60% of real snapshots.
    """
    if not minutes:
        return "?"
    if minutes in _WINDOWS:
        return _WINDOWS[minutes]
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def band(g: Gauge, warn: float = 70, crit: float = 90) -> str:
    """ok | warn | crit | none. A provider's own severity wins over thresholds."""
    if g.blocked:
        return "crit"
    if g.severity and g.severity.lower() in _SEVERITY:
        return _SEVERITY[g.severity.lower()]
    if g.pct is None:
        return "none"
    if g.pct >= crit:
        return "crit"
    if g.pct >= warn:
        return "warn"
    return "ok"


def burn_eta(pct, prev_pct, dt, resets_at, now, min_dt: float = 60):
    """Seconds until this window is exhausted at the current rate, or None.

    Suppressed when the sample is too short, usage isn't climbing, or the
    window resets before we'd run out (in which case the ETA is meaningless).
    """
    if prev_pct is None or pct is None or dt is None or dt < min_dt:
        return None
    if pct <= prev_pct or pct >= 100:
        return None
    rate = (pct - prev_pct) / dt  # percent per second
    if rate <= 0:
        return None
    eta = (100 - pct) / rate
    if resets_at is not None and now + eta > resets_at:
        return None
    return eta
