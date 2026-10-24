#!/usr/bin/env python3
import sys

for raw in sys.stdin:
    if '"type":"hello"' in raw or '"type": "hello"' in raw:
        print("not-json", flush=True)
