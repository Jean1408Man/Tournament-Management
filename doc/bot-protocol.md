# Protocolo árbitro–bot

Un bot es un programa Python 3.12 que juega al Nim hablando con el árbitro por
su entrada y salida estándar: un objeto JSON por línea. El árbitro empieza
siempre; el bot solo responde.

## Conversación

```text
árbitro: {"type":"hello","protocol":"nim-jsonl","version":1}
bot:     {"type":"ready","protocol":"nim-jsonl","version":1}
árbitro: {"type":"start","game_id":"m1","player":0,"piles":[3,5,7],"your_turn":true}
árbitro: {"type":"turn","turn":0,"piles":[3,5,7],"your_turn":true}
bot:     {"type":"move","pile":1,"count":2}
   … un "turn" por cada jugada propia …
árbitro: {"type":"game_over","result":"win","winner":0,"reason":"last_move"}
```

- `hello` / `ready`: saludo con la versión del protocolo.
- `start`: qué jugador es el bot (`player` 0 o 1) y la posición inicial.
- `turn`: la posición completa cuando le toca; el bot contesta con `move`
  (montón empezando en 0 y cuántas fichas quita, al menos una).
- `game_over`: cada bot recibe su resultado (`win` o `loss`) y el índice del
  ganador. No se responde. Gana quien retira la última ficha.
- El árbitro puede mandar `{"type":"error",…}` antes de cerrar; el bot no debe
  intentar recuperarse.

## Reglas

- Cada mensaje es un objeto JSON UTF-8 terminado en salto de línea, con
  `flush()` tras cada respuesta.
- `stdout` solo lleva mensajes del protocolo; `stderr` es para diagnóstico.
- Mensajes desconocidos, campos que faltan o sobran y tipos incorrectos son
  errores de protocolo.

## Límites (por defecto)

- 1 s por turno (`TM_TURN_TIMEOUT_MS`) y 60 s por partida
  (`TM_MATCH_TIMEOUT_MS`).
- 256 MiB de memoria por bot (`TM_MEMORY_LIMIT_BYTES`).
- 64 KiB por línea y 1 MiB de salida acumulada entre `stdout` y `stderr`
  (`TM_OUTPUT_LIMIT_BYTES`).
- El archivo del bot: hasta 1 MiB (`TM_ARTIFACT_MAX_BYTES`), UTF-8 y sintaxis
  Python válida al registrarlo.

## Derrota o fallo de infraestructura

- **Pierde el bot** que hace una jugada ilegal, agota su turno, escribe algo
  que no es un mensaje válido, se pasa de los límites de salida o memoria, o
  termina antes de tiempo. Es un resultado deportivo: gana el rival y no se
  reintenta. `bots/faulty/` tiene un ejemplo de cada caso.
- **No es culpa de ningún bot** que el worker caiga o quede aislado, que venza
  el plazo del intento, que el árbitro detecte que estuvo congelado
  (`referee_stalled`) o que no se pueda descargar el bot. Se reintenta en otro
  intento (ver [semantics.md](semantics.md#reintentos)).
