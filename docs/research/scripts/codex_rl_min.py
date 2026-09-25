#!/usr/bin/env python3
"""Minimal codex app-server rate-limit read. Prints JSON, exits."""
import json, subprocess, sys, time

t0 = time.time()
p = subprocess.Popen(["codex", "app-server", "--stdio"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True, bufsize=1)
p.stdin.write(json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize",
    "params":{"clientInfo":{"name":"herdr-usage","version":"0.1.0"}}}) + "\n")
p.stdin.write(json.dumps({"jsonrpc":"2.0","id":2,"method":"account/rateLimits/read",
    "params":{"excludeResetCreditDetails":True}}) + "\n")
p.stdin.flush()

result = None
deadline = time.time() + 20
while time.time() < deadline:
    line = p.stdout.readline()
    if not line:
        break
    try:
        o = json.loads(line)
    except Exception:
        continue
    if o.get("id") == 2:
        result = o
        break
p.terminate()
print("elapsed_s=%.2f" % (time.time() - t0))
print(json.dumps(result, indent=1) if result else "NO RESULT")
