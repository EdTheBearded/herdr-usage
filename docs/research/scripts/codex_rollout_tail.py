#!/usr/bin/env python3
"""Newest rate_limits snapshot from Codex rollout files, reading as few bytes as possible.

Strategy: newest rollout file first (by mtime), tail-read in growing chunks from EOF,
scan backwards for the last `token_count` event_msg carrying `rate_limits`.
Falls back to progressively older files when a file has no snapshot (fresh session).
"""
import glob, json, os, time

SESSIONS = os.path.expanduser("~/.codex/sessions")
TAIL_CHUNKS = (64 * 1024, 512 * 1024, 4 * 1024 * 1024)


def _scan_tail(path, chunk):
    """Return (payload, timestamp) of the last token_count w/ rate_limits in the last `chunk` bytes."""
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
        if b'"rate_limits"' not in line:
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
                break                # whole file already scanned; try older file
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
