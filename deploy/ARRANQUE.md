# Arranque: con Docker o sin Docker

Los dos modos ejecutan lo mismo: API, coordinador, Ray (un head y `TM_WORKERS` workers, 4 por defecto) y
PostgreSQL, con las partidas en los workers (`TM_BACKEND=ray`). Cambia dónde
vive cada pieza:

| | Con Docker | Sin Docker |
|---|---|---|
| Dónde corre | Contenedores (`tm-postgres`, `tm-head`, `tm-worker-N`, `tm-coordinator`) | Procesos de Linux/WSL |
| Datos | Volúmenes `tm-postgres-data`, `tm-artifacts`, `tm-state` | `~/.local/share/tournament-management/` (`TM_LOCAL_HOME`) |
| Persisten al parar | Sí | Sí |
| Límites de CPU y memoria por componente, aislamiento de archivos | Sí | No: todos los procesos comparten la máquina |

Usa **un modo a la vez**: los dos publican la API en `http://127.0.0.1:8000`.

## Con Docker

Requisitos: Docker Desktop (o Docker en Linux) en marcha, bash y `.env` copiado
de `.env.example` con `TM_POSTGRES_PASSWORD` (solo letras y números).

```bash
docker pull postgres:16.6-bookworm
```

```bash
bash scripts/build-image.sh
```

```bash
TM_BACKEND=ray bash scripts/start-cluster.sh
```

```bash
curl -i http://127.0.0.1:8000/ready
```

Parar (los datos se conservan):

```bash
bash scripts/stop-cluster.sh
```

Añadir un worker: `bash scripts/add-worker.sh tm-worker-3`. En Git Bash de
Windows, si un script falla con rutas `C:/...` en argumentos de Docker, antepón
`MSYS_NO_PATHCONV=1` a `start-cluster.sh`.

## Sin Docker (Linux o WSL)

Requisitos: Python 3.12, uv y los binarios de PostgreSQL (no hace falta que el
servicio esté en marcha). En WSL, trabaja sobre una copia del repositorio dentro
del sistema de archivos de Linux (`~/...`, no `/mnt/c/...`): es mucho más rápido.

```bash
sudo apt install postgresql
```

```bash
uv sync --locked --extra dev
```

```bash
bash scripts/local-cluster.sh start
```

```bash
bash scripts/local-cluster.sh status
```

Parar (los datos se conservan) y volver a arrancar:

```bash
bash scripts/local-cluster.sh stop
```

```bash
bash scripts/local-cluster.sh start
```

Otros subcomandos: `add-worker N`, `kill|pause|resume|restore NOMBRE` (`head` o
`tm-worker-N`), `recover` (tras perder el head) y `reset` (borra todos los datos
locales; pide confirmación). Logs en `~/.local/share/tournament-management/logs/`.

## Primer torneo (igual en los dos modos)

```bash
curl -s -H 'Content-Type: application/octet-stream' -H 'Idempotency-Key: demo-a' --data-binary @bots/optimal.py 'http://127.0.0.1:8000/api/v1/competitors?name=a&filename=bot.py&competitor_id=a'
```

```bash
curl -s -H 'Content-Type: application/octet-stream' -H 'Idempotency-Key: demo-b' --data-binary @bots/random_bot.py 'http://127.0.0.1:8000/api/v1/competitors?name=b&filename=bot.py&competitor_id=b'
```

```bash
curl -s -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-t' --data-binary '{"tournament_id":"demo","format":"round_robin","competitor_ids":["a","b"]}' http://127.0.0.1:8000/api/v1/tournaments
```

```bash
curl -s http://127.0.0.1:8000/api/v1/tournaments/demo
```

Más ejemplos: [examples/api/README.md](../examples/api/README.md).

## Escenarios de fallo (igual en los dos modos)

Detectan el modo solos (Docker si `tm-coordinator` está en marcha; si no,
local). `TM_SCENARIO_MODE=docker|local` lo fuerza.

```bash
bash experiments/scenarios/worker-loss.sh
```

Los seis escenarios y su evidencia: [experiments/scenarios/README.md](../experiments/scenarios/README.md).

## Cambiar de modo

1. Para el modo actual: `bash scripts/stop-cluster.sh` o `bash scripts/local-cluster.sh stop`.
2. Arranca el otro con su `start`.

Los datos de cada modo son independientes: un bot o un torneo creado en Docker
no aparece en el modo local, ni al revés.

Más detalle de operación (pérdida del head, plazos, membresía, artefactos):
[OPERATIONS.md](OPERATIONS.md).
