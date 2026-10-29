#!/usr/bin/env python3
"""Bot lento pero legal (4.4): piensa 0,5 s y quita 1 ficha del primer montón.

Sirve para partidas largas y predecibles en los experimentos de fallo: entre dos
bots lentos hay tantas jugadas como fichas, así que la partida dura
≈ fichas totales × 0,5 s. La espera queda bajo el límite de turno de 1 s.
"""

import json
import sys
import time

DELAY_SECONDS = 0.5


def send(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    message = json.loads(raw)
    kind = message.get("type")
    if kind == "hello":
        send({"type": "ready", "protocol": "nim-jsonl", "version": 1})
    elif kind == "turn":
        time.sleep(DELAY_SECONDS)
        pile = next(index for index, size in enumerate(message["piles"]) if size)
        send({"type": "move", "pile": pile, "count": 1})
    elif kind == "game_over":
        break
