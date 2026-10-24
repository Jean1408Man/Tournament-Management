#!/usr/bin/env python3
import json
import sys

for raw in sys.stdin:
    message = json.loads(raw)
    if message.get("type") == "hello":
        print(json.dumps({"type": "ready", "protocol": "nim-jsonl", "version": 1}), flush=True)
    elif message.get("type") == "turn":
        print(json.dumps({"type": "move", "pile": 0, "count": 999999}), flush=True)
