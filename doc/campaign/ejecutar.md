# Campaña: cómo ejecutarla y cómo crear escenarios

La campaña pone a prueba el clúster real con fallos provocados y con carga. Cada
escenario es un script de `experiments/scenarios/`: provoca una situación,
comprueba lo que debe pasar, guarda la evidencia y termina con código 0 si todo
se cumple (1 si algo falla). Los resultados obtenidos están en
[resultados.md](resultados.md).

## 1. Preparar el clúster

Los escenarios funcionan igual con Docker que sin él y detectan el modo solos:
Docker si el contenedor `tm-coordinator` está en marcha, local si no.
`TM_SCENARIO_MODE=docker|local` lo fuerza.

**Con Docker**, desde Git Bash en la raíz del repositorio:

```bash
bash deploy/docker/build-image.sh
```

```bash
TM_BACKEND=ray bash deploy/docker/start-cluster.sh
```

**Sin Docker**, dentro de WSL o Linux, tras `uv sync --locked --extra dev`:

```bash
bash deploy/local/local-cluster.sh start
```

En los dos casos la API queda en `http://127.0.0.1:8000`, con el head de Ray,
4 workers y el coordinador. En Windows, abrir Docker Desktop congeló WSL varias
veces: para el modo local, ciérralo antes.

## 2. Ejecutar un escenario

Con el clúster en marcha:

```bash
bash experiments/scenarios/worker-loss.sh
```

Los demás se lanzan igual, cambiando el nombre:

- `worker-loss.sh`: mata el worker que juega una partida lenta y lo arranca de
  nuevo.
- `worker-partition.sh` (solo Docker): corta 60 s la red del worker de una
  partida y la restablece.
- `worker-pause.sh`: congela el worker de una partida 100 s; con
  `TM_PAUSE_SECONDS=40` delante, 40 s.
- `coordinator-restart.sh`: mata el coordinador con partidas en curso y lo
  arranca de nuevo.
- `late-result.sh`: fuerza un reintento y entrega después el resultado del
  intento viejo.
- `cancelled-tournament.sh`: cancela un torneo con partidas en curso, mata un
  worker e inyecta un resultado.
- `faulty-bot.sh`: enfrenta seis bots defectuosos de `bots/faulty/` al óptimo.
- `head-loss.sh`: mata el head de Ray, observa 90 s y aplica la recuperación de
  `deploy/OPERATIONS.md`.

Cada escenario restaura al terminar lo que rompió. El panel de `ui/` lanza los
mismos escenarios con un botón ([ui/README.md](../../ui/README.md)).

## 3. Ejecutar una carga

`load.sh` lanza una mezcla de torneos a la vez y mide. Uso:

```bash
bash experiments/scenarios/load.sh NOMBRE --mix FORMATO:BOTSxTORNEOS,... --bot BOT --piles "[5,5,5]"
```

- `--mix`: los torneos. `round_robin:6x1,elimination:4x2` son un round-robin de 6
  bots y dos eliminaciones de 4.
- `--bot`: el bot de `bots/` para todos. Con varios separados por comas
  (`optimal,random_bot`) se reparten por turnos.
- `--piles`: los montones iniciales de Nim.

Las tres cargas de la campaña:

```bash
bash experiments/scenarios/load.sh carga-lenta --mix round_robin:6x1,elimination:4x2,elimination:2x1 --bot slow --piles "[5,5,5]"
```

```bash
bash experiments/scenarios/load.sh carga-nim --mix round_robin:16x1,elimination:8x2 --bot optimal
```

```bash
bash experiments/scenarios/load.sh saturacion --mix elimination:4x6 --bot slow --piles "[5,5,5]"
```

La saturación necesita el clúster arrancado con un límite de 3 torneos activos:
`TM_MAX_ACTIVE_TOURNAMENTS=3` delante del comando de arranque.

## 4. Ejecutar la campaña completa

```bash
bash experiments/campaign.sh docker
```

`campaign.sh` arranca y para el clúster, ejecuta la matriz entera y guarda un
registro en `experiments/results/campaign-<modo>-<fecha>.log`.

- Repeticiones en Docker: 5 en worker-loss, la partición y las cargas; 3 en el
  resto.
- Con `local` en vez de `docker`: una ejecución de cada escenario, sin la
  partición.

Para resumir una campaña (mediana y rango de cada escenario):

```bash
python experiments/tools/summarize.py experiments/results/campaign-docker-<fecha>.log
```

## 5. Qué deja cada ejecución

En `experiments/results/<escenario>-<fecha>/`, fuera de Git:

- `resumen.txt`: los pasos con su hora y cada comprobación (`OK`, `FALLO` o
  `INFO`).
- `<torneo>.tournament.json`, `.matches.json` y `.events.json`: la evidencia
  final del torneo.
- `client.jsonl`: lo que el cliente creó y vio en vivo.
- `verify.json`: el informe de `tm verify`. Una violación cuenta como fallo.
- `meta.json`: revisión, configuración (con los secretos enmascarados), hardware
  y comando.

Para volver a verificar una ejecución:

```bash
PYTHONPATH=src python -m tournament_management verify --evidence experiments/results/<carpeta>
```

## 6. Configuración

- `TM_API` (`http://127.0.0.1:8000`): dirección de la API.
- `TM_SCENARIO_PILES` (`[10,10,10]`): montones de las partidas lentas.
- `TM_PAUSE_SECONDS` (plazo del intento + 10 s), `TM_PARTITION_SECONDS` (60),
  `TM_DOWN_SECONDS` (10) y `TM_OBSERVE_SECONDS` (90): duración de cada fallo.
- En Git Bash no exportes `MSYS_NO_PATHCONV` para los escenarios: rompe
  `curl -o /dev/null`.

## 7. Crear un escenario nuevo

Un escenario es un script de Bash que carga `experiments/tools/lib.sh` y sigue
siempre el mismo esquema: preparar, provocar, esperar, comprobar, restaurar y
cerrar. `worker-loss.sh` es el modelo más corto:

```bash
#!/usr/bin/env bash
source "$(dirname -- "${BASH_SOURCE[0]}")/../tools/lib.sh"
scenario_start mi-escenario                  # carpeta de resultados y meta.json
register slow-1 bots/slow.py                 # bots que hagan falta
register slow-2 bots/slow.py
tournament="scn-mi-escenario-$STAMP"
create "$tournament" elimination slow-1 slow-2   # crea y deja un cliente escuchando (I6)
event=$(wait_event "$tournament" attempt.dispatched 60)
victim=$(worker_of_event "$event")
worker_kill "$victim"                         # el fallo que se quiere provocar
log "Estado final: $(wait_terminal "$tournament" 180)"
dump "$tournament"                            # guarda torneo, partidas y eventos
run_checks "$tournament" <<'EOF'
import sys
from checks import Evidence
results, tournament = sys.argv[1:]
e = Evidence(results, tournament)
e.check("el torneo termina completed", e.tournament["state"] == "completed")
e.one_result_per_completed_match()
e.done()
EOF
worker_restore "$victim"                      # deja el clúster como estaba
finish                                        # tm verify y código de salida
```

Lo que ofrece `lib.sh`:

- **Preparar:** `scenario_start`, `register` y `create`.
- **Esperar y observar:**
  - `wait_event`: espera un tipo de evento y lo devuelve;
  - `wait_terminal`: espera a que el torneo acabe;
  - `state`, `field` y `worker_of_event`;
  - `sample_workers`: añade una muestra de `/workers` a `workers.log`;
  - `db_scalar`: una consulta SQL de solo lectura.
- **Provocar fallos:**
  - workers: `worker_kill`, `worker_pause` y `worker_unpause`;
  - coordinador y head: `coordinator_kill` y `head_kill`;
  - `inject_late`: entrega el resultado de un intento viejo.
- **Restaurar:** `worker_restore`, `coordinator_restore` y `cluster_recover`.
  `worker_restore` espera a que Ray tenga un solo nodo por worker.
- **Comprobar y cerrar:**
  - `run_checks`: ejecuta comprobaciones en Python con `checks.Evidence`, que da
    `check`, `attempts`, `of_type`, `dispatched_worker`,
    `one_result_per_completed_match` y `done`;
  - `log`: escribe en `resumen.txt`;
  - `finish`: espera a los clientes, pasa `tm verify` y fija el código de salida.

Para que entre en la campaña, añade su línea a la matriz de
`experiments/campaign.sh`. El panel de `ui/` lo muestra solo, porque lista los
scripts de `experiments/scenarios/`.
