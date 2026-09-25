#!/usr/bin/env python3
"""Probe codex app-server over stdio for account/rateLimits/read + account/read."""
import json, subprocess, sys, threading, time

CMD = ["codex", "app-server", "--stdio"]

p = subprocess.Popen(CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True, bufsize=1)

out = []
def reader():
    for line in p.stdout:
        out.append(line.rstrip("\n"))
threading.Thread(target=reader, daemon=True).start()

def send(obj):
    p.stdin.write(json.dumps(obj) + "\n")
    p.stdin.flush()

send({"jsonrpc":"2.0","id":1,"method":"initialize",
      "params":{"clientInfo":{"name":"probe","version":"0.0.1"}}})
time.sleep(2)
send({"jsonrpc":"2.0","method":"initialized","params":{}})
time.sleep(0.5)
send({"jsonrpc":"2.0","id":2,"method":"account/read","params":{}})
send({"jsonrpc":"2.0","id":3,"method":"account/rateLimits/read","params":None})
send({"jsonrpc":"2.0","id":4,"method":"account/usage/read","params":None})
send({"jsonrpc":"2.0","id":5,"method":"model/list","params":{}})
time.sleep(12)
p.terminate()
try: p.wait(timeout=5)
except Exception: p.kill()

for line in out:
    try:
        o = json.loads(line)
    except Exception:
        print("RAW:", line[:400]); continue
    if o.get("id") in (1,2,3,4,5) or "error" in o:
        s = json.dumps(o)
        print(("ID%s " % o.get("id")) + (s if len(s) < 6000 else s[:6000] + "...TRUNC"))
    else:
        print("NOTIF:", o.get("method"), json.dumps(o.get("params"))[:300])
err = p.stderr.read()
if err: print("STDERR:", err[:3000], file=sys.stderr)
