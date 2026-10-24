#!/usr/bin/env python3
"""Bot óptimo de Nim normal; solo biblioteca estándar y stdout de protocolo."""

import json
import sys


def send(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def move(piles):
    xor = 0
    for pile in piles:
        xor ^= pile
    if xor:
        for index, pile in enumerate(piles):
            target = pile ^ xor
            if target < pile:
                return index, pile - target
    for index, pile in enumerate(piles):
        if pile:
            return index, 1
    raise ValueError("No hay jugadas disponibles")


for raw in sys.stdin:
    message = json.loads(raw)
    kind = message.get("type")
    if kind == "hello":
        send({"type": "ready", "protocol": "nim-jsonl", "version": 1})
    elif kind == "turn":
        pile, count = move(message["piles"])
        send({"type": "move", "pile": pile, "count": count})
    elif kind == "game_over":
        break
