# Despliegue

Todo lo necesario para levantar el sistema, con Docker o sin él. Guías:
[ARRANQUE.md](ARRANQUE.md) (puesta en marcha) y [OPERATIONS.md](OPERATIONS.md)
(operación y recuperación). 

## `docker/` — clúster de contenedores

Se ejecutan con bash desde cualquier carpeta.

- `Dockerfile`: dos imágenes con el mismo paquete y sus dependencias
  bloqueadas: `worker` (head y workers, sin PostgreSQL) y `coordinator` (con
  PostgreSQL 17, su base local).
- `entrypoint.sh`: elige el rol del contenedor (`coordinator`, `ray-head` o
  `ray-worker`). En la imagen del coordinador arranca antes PostgreSQL, solo
  por socket, y vigila los dos procesos: si uno termina, para el otro y el
  contenedor sale con error.
- `build-image.sh`: construye las dos imágenes; es el único script que descarga.
- `start-cluster.sh`: red, volúmenes, head, workers y coordinador (con su
  base), con esperas y diagnóstico si algo falla.
- `cluster-status.sh`: imágenes y revisión, membresía Ray, DNS, que la base
  responde en el coordinador sin escuchar en la red, que ningún otro nodo tiene
  PostgreSQL, y almacenamiento.
- `probe-cluster.sh`: una tarea fijada a cada worker (identidad y versiones).
- `probe-artifact.sh SHA256`: cada worker descarga y verifica un bot.
- `add-worker.sh tm-worker-N`: añade un worker con el clúster en marcha.
- `fail-worker.sh tm-worker-N`: simula la caída de un worker.
- `stop-cluster.sh`: retira los contenedores; conserva volúmenes y red.
- `common.sh`: funciones compartidas (no se ejecuta solo). Genera
  `tm-worker-1..N` a partir de `TM_WORKERS` (4) y aplica `TM_WORKER_MEMORY`.
  También pasa `TM_WORKER_DELAY_MS` (0) a workers y coordinador: espera de
  laboratorio antes de cada partida para poder matar o pausar un worker a mano;
  el coordinador alarga el plazo del intento lo mismo. `add-worker.sh` copia la
  del coordinador en marcha.

El volumen `tm-artifacts` conserva los bots y `tm-postgres-data` la base al
recrear el clúster; solo el coordinador los monta. Los workers no tienen
PostgreSQL ni forma de llegar a la base del coordinador.

## `local/` — sin Docker (Linux o WSL)

- `local-cluster.sh`: PostgreSQL propio, head y workers de Ray y el coordinador
  como procesos (`start`, `stop`, `status`, `kill`/`pause`/`resume`/`restore`
  NOMBRE, `recover`, `reset`).
- `local_cluster.py`: arranca cada nodo de Ray en su propio proceso; lo usa
  `local-cluster.sh`.

El contrato OpenAPI se regenera con `contracts/export-openapi.py`.
