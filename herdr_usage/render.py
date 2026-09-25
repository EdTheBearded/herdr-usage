"""Rendering: bars, durations, and the sidebar token dict."""

import hashlib
import math
import re

from .model import Gauge, band

BAR_SETS = {
    "blocks": ("█", "░"),
    "shade": ("▓", "░"),
    "squares": ("■", "□"),
    "dots": ("●", "○"),
    "half": ("▮", "▯"),
    "ascii": ("#", "-"),
}

# Vendor icons, keyed by the provider whose quota is being consumed.
# `brand` uses Nerd Font private-use codepoints (cod-claude U+EC82,
# cod-openai U+EC81, cod-robot U+EC20); `geometric` and `ascii` exist for
# terminals without a patched font. The hexagons U+2B22/U+2B21 are
# deliberately absent -- FiraCode Nerd Font lacks them, so they render tofu.
ICON_SETS = {
    "brand": {
        "anthropic": "\uec82",
        "openai": "\uec81",
        "copilot": "\uec1e",
        "local": "\uec20",
    },
    "geometric": {
        "anthropic": "◆",
        "openai": "◇",
        "copilot": "◉",
        "local": "⌂",
    },
    "ascii": {
        "anthropic": "[A]",
        "openai": "[G]",
        "copilot": "[C]",
        "local": "[L]",
    },
}

# Which icon a provider maps to. Providers absent here get no icon rather
# than a wrong one -- an unknown vendor must not borrow another's logo.
PROVIDER_ICONS = {
    "claude": "anthropic",
    "anthropic": "anthropic",
    "codex": "openai",
    "openai": "openai",
    "copilot": "copilot",
    "local": "local",
}

# Bare U+26D4: the VS16 emoji form (U+26D4 FE0F) is absent from FiraCode
# Nerd Font and pushed the token an extra cell in a live pane.
BLOCKED = "⛔"
STALE = "~"
NO_DATA = "⋯ no data"

TOKEN_MAX = 80  # Herdr silently truncates above this
NAME_MAX = 32  # Herdr rejects token keys longer than this
_SLUG = re.compile(r"[^A-Za-z0-9_-]")


def bar(pct: float, cells: int = 10, bar_set: str = "blocks") -> str:
    """Fill rule: only 100% may render full, any non-zero shows >=1 cell.

    That makes a full bar mean 'exhausted' unambiguously, which is what the
    crit colour and the blocked state rely on.
    """
    full, empty = BAR_SETS.get(bar_set, BAR_SETS["blocks"])
    if pct >= 100:
        n = cells
    elif pct <= 0:
        n = 0
    else:
        n = min(cells - 1, max(1, math.ceil(pct / 100 * cells)))
    return full * n + empty * (cells - n)


def fmt_duration(secs: float | None) -> str | None:
    if secs is None:
        return None
    secs = max(0, int(secs))
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m}m"
    return f"{m}m"


def gauge_text(g: Gauge, cells: int = 10, bar_set: str = "blocks",
               stale: bool = False) -> str:
    if g.pct is None and not g.blocked:
        text = f"{g.label} {NO_DATA}"
    else:
        b = bar(g.pct if g.pct is not None else 100, cells, bar_set)
        tail = BLOCKED if g.blocked else f"{g.pct:3.0f}%"
        text = f"{g.label} {b} {tail}"
    if stale:
        text += f" {STALE}"
    return text[:TOKEN_MAX]


def icon_for(provider: str | None, icon_set: str = "brand") -> str | None:
    """Vendor icon for a provider, or None when we don't know the vendor.

    Unknown providers get no icon rather than a default one: showing the
    Anthropic mark next to an unrecognised vendor would be a quiet lie, and
    the same rule already governs the gauges themselves.
    """
    if not provider:
        return None
    icons = ICON_SETS.get(icon_set) or ICON_SETS["brand"]
    vendor = PROVIDER_ICONS.get(str(provider).lower())
    return icons.get(vendor) if vendor else None


def _slug(key: str) -> str:
    """Sanitize and bound a gauge key so `uq_g{slug}_{band}` fits Herdr's
    32-char token-name limit (4 prefix + 5 worst-case suffix leaves 23).

    Over-long keys keep a readable head plus a hash tail, so two different
    long keys never collide into one token.
    """
    s = _SLUG.sub("_", key)
    budget = NAME_MAX - len("uq_g") - len("_warn")
    if len(s) > budget:
        digest = hashlib.sha1(key.encode()).hexdigest()[:6]
        s = s[: budget - 7] + "_" + digest
    return s


def build_tokens(gauges, extras=None, cells: int = 10, bar_set: str = "blocks",
                 warn: float = 70, crit: float = 90, stale: bool = False):
    """-> (tokens_to_set, token_names_to_clear).

    Severity lives in the token NAME because Herdr strips ANSI and its gt/lt
    rules can't match a bar string. Exactly one of _ok/_warn/_crit/_none is
    set per gauge; the other three are cleared so the sidebar row drops them.
    """
    tokens, clear = {}, []
    for g in gauges:
        name = f"uq_g{_slug(g.key)}"
        active = band(g, warn, crit)
        for suffix in ("ok", "warn", "crit", "none"):
            key = f"{name}_{suffix}"
            if suffix == active:
                tokens[key] = gauge_text(g, cells, bar_set, stale)
            else:
                clear.append(key)

    for field in ("model", "plan", "burn", "extra"):
        key = f"uq_{field}"
        value = (extras or {}).get(field)
        if value:
            tokens[key] = str(value)[:TOKEN_MAX]
        else:
            clear.append(key)
    return tokens, clear
