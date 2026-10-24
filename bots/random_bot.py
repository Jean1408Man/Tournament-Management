#!/usr/bin/env python3
"""Bot aleatorio reproducible; acepta ``--seed N``."""

import argparse
import json
import random
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--seed", type=int, default=0)
rng = random.Random(parser.parse_args().seed)


def send(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    message = json.loads(raw)
    if message.get("type") == "hello":
        send({"type": "ready", "protocol": "nim-jsonl", "version": 1})
    elif message.get("type") == "turn":
        choices = [(i, n) for i, pile in enumerate(message["piles"]) for n in range(1, pile + 1)]
        pile, count = rng.choice(choices)
        send({"type": "move", "pile": pile, "count": count})
    elif message.get("type") == "game_over":
        break
