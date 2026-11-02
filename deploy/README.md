# Despliegue

Todo lo necesario para levantar el sistema, con Docker o sin él. Guías:
[ARRANQUE.md](ARRANQUE.md) (puesta en marcha) y [OPERATIONS.md](OPERATIONS.md)
(operación y recuperación). 

## `docker/` — clúster de contenedores

Se ejecutan con bash desde cualquier carpeta.

- `Dockerfile`: imagen única con el paquete y sus dependencias bloqueadas.
- `entrypoint.sh`: elige el rol del contenedor (`coordinator`, `ray-head` o
  `ray-worker`).
- `build-image.sh`: construye la imagen; es el único script que descarga.
- `start-cluster.sh`: red, volúmenes, PostgreSQL, head, workers y coordinador,
  con esperas y diagnóstico si algo falla.
- `cluster-status.sh`: imagen común, membresía Ray, DNS y almacenamiento.
- `probe-cluster.sh`: una tarea fijada a cada worker (identidad y versiones).
- `probe-artifact.sh SHA256`: cada worker descarga y verifica un bot.
- `add-worker.sh tm-worker-N`: añade un worker con el clúster en marcha.
- `fail-worker.sh tm-worker-N`: simula la caída de un worker.
- `stop-cluster.sh`: retira los contenedores; conserva volúmenes y red.
- `common.sh`: funciones compartidas (no se ejecuta solo). Genera
  `tm-worker-1..N` a partir de `TM_WORKERS` (4) y aplica `TM_WORKER_MEMORY`.

El volumen `tm-artifacts` conserva los bots al recrear el clúster; solo el
coordinador lo monta con escritura y los workers no reciben credenciales de
PostgreSQL.

## `local/` — sin Docker (Linux o WSL)

- `local-cluster.sh`: PostgreSQL propio, head y workers de Ray y el coordinador
  como procesos (`start`, `stop`, `status`, `kill`/`pause`/`resume`/`restore`
  NOMBRE, `recover`, `reset`).
- `local_cluster.py`: arranca cada nodo de Ray en su propio proceso; lo usa
  `local-cluster.sh`.

El contrato OpenAPI se regenera con `contracts/export-openapi.py`.
