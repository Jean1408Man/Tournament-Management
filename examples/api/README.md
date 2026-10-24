# Recorrido HTTP — etapa 3.3

Ejecutar desde la raíz del repositorio, con API local y PostgreSQL migrado.
Para Docker, reconstruir la imagen y recrear contenedores siguiendo
[operaciones](../../deploy/OPERATIONS.md). Este recorrido usa IDs y claves
estables: repetirlo recupera los mismos recursos, sin iniciar otro torneo.
Para una prueba nueva cambia tanto los IDs de torneo de los JSON como las claves.

## Registrar cuatro participantes

```bash
curl -i http://127.0.0.1:8000/ready
for id in a b c d; do
  bot=bots/optimal.py
  if [ "$id" = b ] || [ "$id" = d ]; then
    bot=bots/random_bot.py
  fi
  curl --fail-with-body --silent --show-error \
    -H 'Content-Type: application/octet-stream' \
    -H "Idempotency-Key: demo-register-$id-v1" \
    --data-binary "@$bot" \
    "http://127.0.0.1:8000/api/v1/competitors?name=Bot-$id&filename=bot.py&competitor_id=$id"
  echo
done
```

Es normal que a/c compartan hash, igual que b/d: son identidades distintas
que registran los mismos bytes. No se duplica el objeto por cambiar de nombre.

## Ejecutar ambos formatos

```bash
curl -i -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-elimination-v1' \
  --data-binary @examples/api/elimination.json \
  http://127.0.0.1:8000/api/v1/tournaments
curl -i -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-round-robin-v1' \
  --data-binary @examples/api/round-robin.json \
  http://127.0.0.1:8000/api/v1/tournaments
```

Ambas respuestas deben ser 202. Repetir exactamente la creación debe devolver
la misma identidad. Cambiar el seed conservando la clave debe producir 409.

```bash
curl --silent http://127.0.0.1:8000/api/v1/tournaments/demo-http-elimination
curl --silent http://127.0.0.1:8000/api/v1/tournaments/demo-http-round-robin
curl --silent 'http://127.0.0.1:8000/api/v1/tournaments/demo-http-elimination/matches?limit=2&offset=0'
curl --silent http://127.0.0.1:8000/api/v1/workers
curl -N http://127.0.0.1:8000/api/v1/tournaments/demo-http-elimination/events
curl -N -H 'Last-Event-ID: demo-http-elimination:1' \
  http://127.0.0.1:8000/api/v1/tournaments/demo-http-elimination/events
```

Repetir las consultas hasta `completed`. Con cuatro participantes deben quedar
3 partidas de eliminación y 6 de round-robin, con intentos aceptados. El cuadro
debe mostrar dos rondas y campeón; la clasificación debe sumar 6 victorias.
Los eventos deben tener secuencias crecientes. La reconexión desde :1 no debe
volver a entregar el evento 1. Los torneos cortos pueden haber acabado cuando
se abre SSE; aun así se entrega su historial y se cierra al agotarlo.

## Cancelación y pruebas automáticas

Para observar cancelación de trabajo activo, crea un torneo nuevo y cancélalo
mientras figure running. Cancelar uno que ya terminó conserva completed:

```bash
curl -i -X POST \
  http://127.0.0.1:8000/api/v1/tournaments/demo-http-elimination/cancel
uv run pytest tests/integration/api tests/unit/test_api_contract.py -q
uv run pytest -q
uv run ruff check .
```

La suite incluye un ejecutor lento controlado para comprobar cancelación
activa, respuestas tardías, timeout y reintentos de forma reproducible.
No debe perderse el resultado aceptado ni existir dos intentos aceptados para
la misma partida. Tras reiniciar se conserva lo consultable, pero todavía no
se reanuda automáticamente el trabajo pendiente: eso corresponde a 3.4.
