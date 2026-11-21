# Crear y editar un bot

Un bot es **un solo archivo Python 3.12** que juega al Nim por `stdin`/`stdout`
([protocolo](../doc/bot-protocol.md)). Reglas:

- Solo biblioteca estándar, máximo 1 MiB, sin argumentos de línea de comandos.
- `stdout` solo para mensajes del protocolo, con `flush()`; depura por `stderr`.
- Nombre del archivo: letras, números, `-` y `_`, empezando por letra o número.

## 1. Crear

`bots/mi_bot.py` (quita 1 ficha del montón más grande):

```python
import json
import sys


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def jugar(piles):
    pile = piles.index(max(piles))
    return pile, 1


for line in sys.stdin:
    message = json.loads(line)
    if message["type"] == "hello":
        send({"type": "ready", "protocol": "nim-jsonl", "version": 1})
    elif message["type"] == "turn":
        pile, count = jugar(message["piles"])
        send({"type": "move", "pile": pile, "count": count})
    elif message["type"] == "game_over":
        break
```

## 2. Probar sin clúster

```bash
printf '%s\n' '{"type":"hello","protocol":"nim-jsonl","version":1}' \
  '{"type":"turn","turn":0,"piles":[3,5,7],"your_turn":true}' | python3 bots/mi_bot.py
```

Debe imprimir un `ready` y un `move`.

## 3. Registrar y jugar

Con el clúster arrancado (Docker o local). No hace falta reconstruir la imagen:
el bot viaja por HTTP al coordinador y los workers lo descargan.

**Desde la UI:** deja el archivo en `bots/` (en la máquina donde corre
`ui/server.py`). En unos 5 s aparece en **Ejecuciones → Torneo a medida**;
selecciónalo y pulsa **Crear torneo**. Se registra solo como `mi_bot-01`,
`mi_bot-03`…

**Por la API:**

```bash
curl --fail-with-body -s -H 'Content-Type: application/octet-stream' \
  -H 'Idempotency-Key: reg-mi-bot-v1' --data-binary @bots/mi_bot.py \
  'http://127.0.0.1:8000/api/v1/competitors?name=mi-bot&filename=mi_bot.py&competitor_id=mi-bot-v1'
curl --fail-with-body -s -H 'Content-Type: application/octet-stream' \
  -H 'Idempotency-Key: reg-rival-optimal' --data-binary @bots/optimal.py \
  'http://127.0.0.1:8000/api/v1/competitors?name=rival&filename=optimal.py&competitor_id=rival-optimal'
curl --fail-with-body -s -H 'Content-Type: application/json' -H 'Idempotency-Key: t-mi-bot-1' \
  -d '{"format":"round_robin","competitor_ids":["mi-bot-v1","rival-optimal"],"tournament_id":"t-mi-bot-1"}' \
  http://127.0.0.1:8000/api/v1/tournaments
curl -s http://127.0.0.1:8000/api/v1/tournaments/t-mi-bot-1
```

## 4. Editar

Ejemplo de cambio (`mi_bot_v2.py`: si queda un solo montón, se lo lleva entero
y gana):

```python
def jugar(piles):
    pile = piles.index(max(piles))
    if sum(1 for n in piles if n) == 1:
        return pile, piles[pile]
    return pile, 1
```