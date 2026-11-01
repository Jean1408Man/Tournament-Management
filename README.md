# Tournament Management

Plataforma distribuida para ejecutar torneos de bots de Nim. Un coordinador
guarda el estado en PostgreSQL y reparte las partidas entre varios workers; en
esta Fase 1 los workers se coordinan con Ray. Si un worker falla, la partida se
repite en otro sin perder ni duplicar resultados.

Este README es el camino completo para alguien nuevo: arrancar, registrar bots,
jugar torneos, provocar un fallo y entender la recuperación.

## 1. Requisitos

- **Con Docker (recomendado):** Docker Desktop (o Docker en Linux) con al menos
  8 GB de memoria, `bash` (en Windows, Git Bash), `curl` y Python 3 en el `PATH`.
- **Sin Docker:** Linux o WSL con Python 3.12, [uv](https://docs.astral.sh/uv/)
  y los binarios de PostgreSQL (`sudo apt install postgresql`).

Copia la configuración y pon una contraseña (solo letras y números) en
`TM_POSTGRES_PASSWORD`:

```bash
cp .env.example .env
```

## 2. Arrancar el sistema

Con Docker (PostgreSQL, el head de Ray, 4 workers y el coordinador):

```bash
docker pull postgres:16.6-bookworm
```

```bash
bash deploy/docker/build-image.sh
```

```bash
TM_BACKEND=ray bash deploy/docker/start-cluster.sh
```

Sin Docker, desde una copia del repositorio dentro de WSL (`~/…`):

```bash
uv sync --locked --extra dev
```

```bash
bash deploy/local/local-cluster.sh start
```

En los dos casos la API queda en `http://127.0.0.1:8000`. Está lista cuando esto
responde 200:

```bash
curl -i http://127.0.0.1:8000/ready
```

## 3. Registrar bots

Un bot es un archivo Python que juega por `stdin`/`stdout`
([protocolo](docs/bot-protocol.md)). Se registran cuatro participantes con los
bots de ejemplo:

```bash
for id in a b c d; do
  bot=bots/optimal.py; [ "$id" = b ] || [ "$id" = d ] && bot=bots/random_bot.py
  curl --fail-with-body -s -H 'Content-Type: application/octet-stream' \
    -H "Idempotency-Key: demo-register-$id-v1" --data-binary "@$bot" \
    "http://127.0.0.1:8000/api/v1/competitors?name=Bot-$id&filename=bot.py&competitor_id=$id"
  echo
done
```

## 4. Jugar varios torneos

Una eliminación y un round-robin a la vez (las dos respuestas son 202):

```bash
curl -i -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-elimination-v1' \
  --data-binary @examples/api/elimination.json http://127.0.0.1:8000/api/v1/tournaments
```

```bash
curl -i -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-round-robin-v1' \
  --data-binary @examples/api/round-robin.json http://127.0.0.1:8000/api/v1/tournaments
```

Seguirlos: estado, partidas, eventos en vivo y workers.

```bash
curl -s http://127.0.0.1:8000/api/v1/tournaments/demo-http-elimination
```

```bash
curl -N http://127.0.0.1:8000/api/v1/tournaments/demo-http-round-robin/events
```

```bash
curl -s http://127.0.0.1:8000/api/v1/workers
```

Más ejemplos (cancelar, reanudar eventos, idempotencia):
[examples/api/README.md](examples/api/README.md).

## 5. Provocar un fallo

El escenario `worker-loss` lanza una partida lenta, mata el worker que la juega,
lo vuelve a arrancar y comprueba qué pasó:

```bash
bash experiments/scenarios/worker-loss.sh
```

Al terminar muestra cada comprobación (`OK`/`FALLO`) y deja la evidencia en
`experiments/results/worker-loss-<fecha>/`. Hay un escenario por cada fallo
(red cortada, coordinador reiniciado, pérdida del head…):
[experiments/scenarios/README.md](experiments/scenarios/README.md).

## 6. Qué pasó: la recuperación

1. El worker muere con la partida en marcha; Ray lo avisa y el intento termina
   como **fallo de infraestructura**, no como derrota de ningún bot.
2. El coordinador emite **otro intento** de la misma partida, con una generación
   mayor, en otro worker.
3. Solo se acepta el resultado del intento vigente: si el viejo llegara tarde,
   se descarta. Cada partida termina con **un único resultado**.
4. Todo esto queda en el historial de eventos del torneo, en PostgreSQL.

Por qué funciona y qué se pierde con cada nodo: [docs/architecture.md](docs/architecture.md).
Qué promete cada respuesta: [docs/semantics.md](docs/semantics.md).

## 7. Parar

```bash
bash deploy/docker/stop-cluster.sh
```

Sin Docker: `bash deploy/local/local-cluster.sh stop`. Los datos se conservan
hasta el próximo arranque.

## Dónde seguir

- [docs/architecture.md](docs/architecture.md): roles, procesos y quién posee cada estado.
- [docs/api-contract.md](docs/api-contract.md) y [docs/semantics.md](docs/semantics.md): el contrato HTTP congelado.
- [docs/bot-protocol.md](docs/bot-protocol.md): cómo escribir un bot.
- [docs/garantias.md](docs/garantias.md): cada garantía con su evidencia.
- [docs/decisions/](docs/decisions/README.md): las seis decisiones de diseño que más pesan.
- [docs/campaign/](docs/campaign/README.md): la campaña de fallos y carga.
- [docs/middleware-map.md](docs/middleware-map.md): qué aporta Ray y qué sustituye la Fase 2.
- [deploy/README.md](deploy/README.md) y [deploy/OPERATIONS.md](deploy/OPERATIONS.md): despliegue y operación.
- [docs/historia-por-pasos.md](docs/historia-por-pasos.md): cómo se construyó, paso a paso.

## Desarrollo

Python 3.12 y Ray 2.40.0, con las dependencias bloqueadas en `uv.lock`. La
configuración usa variables `TM_*` (argumentos > entorno > valores por
defecto; `.env.example` las documenta). Pruebas y estilo:

```bash
uv run pytest
```

```bash
uv run ruff check .
```

Las pruebas de integración necesitan PostgreSQL (`TM_TEST_DATABASE_URL`) y
Linux: [tests/integration/README.md](tests/integration/README.md).

## Uso de IA

Pendiente: el formato de declaración de uso de IA exigido por el curso no está en
el material disponible. Antes de la entrega, añade aquí la declaración con el
formato oficial.
