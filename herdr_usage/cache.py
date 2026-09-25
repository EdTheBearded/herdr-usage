"""Usage cache: atomic writes, tolerant reads, one retained previous sample."""

import json
import os
import pathlib
import tempfile
import time

# ponytail: no lock file. os.replace already makes a concurrent write safe
# (last writer wins, a reader never sees a torn file), so a lock would add a
# failure mode -- a stale one blocking writes -- without preventing anything.
# Any .lock left by an older version is simply ignored.


def load(path) -> dict:
    """Anything unreadable, truncated or not a JSON object reads as empty.

    A corrupt cache must degrade to 'no data', never crash a refresh.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(path, data: dict) -> None:
    """Write via a temp file in the same directory, then os.replace.

    The temp file must share the destination's filesystem or the rename is
    not atomic; the destination is never opened for writing in place.
    """
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _with_previous(load(path), data)

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".usage-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _with_previous(old: dict, new: dict, min_span: float = 60) -> dict:
    """Carry each gauge's outgoing reading into prev_* so burn_eta has a slope.

    Only shifts when this entry actually carries a NEW reading: a cache-hit
    re-save would otherwise copy the current pct into prev_pct, flattening the
    slope to zero and silently killing the burn-rate ETA forever.

    A baseline younger than `min_span` is also kept rather than replaced.
    Forced refreshes (settings save, startup, setup-sidebar --force) can land
    seconds apart, and advancing the baseline each time pins the sample window
    below burn_eta's floor, so the ETA could never appear.
    """
    out = json.loads(json.dumps(new))
    for provider, entry in out.items():
        if not isinstance(entry, dict):
            continue
        prev_entry = old.get(provider) if isinstance(old, dict) else None
        prev_entry = prev_entry if isinstance(prev_entry, dict) else {}

        keep_prev = prev_entry and prev_entry.get("fetched_at") == entry.get("fetched_at")
        if not keep_prev and prev_entry:
            # A new reading normally becomes the next baseline. But if the
            # span it would create is shorter than burn_eta's floor, keeping
            # the older baseline preserves a usable slope instead -- only when
            # one already exists, otherwise nothing would ever establish it.
            span = _span(entry.get("fetched_at"), prev_entry.get("fetched_at"))
            keep_prev = (
                span is not None
                and span < min_span
                and _has_baseline(prev_entry)
            )

        if keep_prev:
            keep = {
                g.get("key"): g
                for g in prev_entry.get("gauges") or []
                if isinstance(g, dict)
            }
            for gauge in entry.get("gauges") or []:
                if isinstance(gauge, dict):
                    before = keep.get(gauge.get("key")) or {}
                    gauge["prev_pct"] = before.get("prev_pct")
                    gauge["prev_fetched_at"] = before.get("prev_fetched_at")
            continue

        prev_at = prev_entry.get("fetched_at")
        prev_by_key = {
            g.get("key"): g
            for g in prev_entry.get("gauges") or []
            if isinstance(g, dict)
        }
        for gauge in entry.get("gauges") or []:
            if not isinstance(gauge, dict):
                continue
            before = prev_by_key.get(gauge.get("key")) or {}
            gauge["prev_pct"] = before.get("pct")
            gauge["prev_fetched_at"] = prev_at if before else None
    return out


def _has_baseline(entry: dict) -> bool:
    """True when at least one gauge already carries a usable prev_fetched_at."""
    for gauge in entry.get("gauges") or []:
        if not isinstance(gauge, dict):
            continue
        at = gauge.get("prev_fetched_at")
        if isinstance(at, (int, float)) and not isinstance(at, bool):
            return True
    return False


def _span(now, before):
    """Gap between two readings, or None when either is not a real number."""
    for value in (now, before):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
    return now - before


def is_fresh(data: dict, ttl: float, now: float | None = None) -> bool:
    if not isinstance(data, dict):
        return False
    stamp = data.get("fetched_at")
    if not isinstance(stamp, (int, float)) or isinstance(stamp, bool):
        return False
    return (time.time() if now is None else now) - stamp <= ttl
