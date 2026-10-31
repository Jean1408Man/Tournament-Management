# Escenarios de fallo

Scripts reproducibles de las etapas 4.4 y 5.3. Demuestran con el clúster real las
exigencias de fallo del encargo P03 y de la guía de la Fase 1, y son la base de
la campaña de la etapa 5.3 y de la Fase 3. **Un script por escenario.**

## Requisitos

- Clúster en marcha con Ray, sin pruebas de WSL en paralelo:

```bash
TM_BACKEND=ray bash scripts/start-cluster.sh
```

- Bash con `curl`, `docker` y Python 3 en el `PATH`. Vale Git Bash en Windows o
  Linux/WSL.
- Ejecutar desde cualquier carpeta; cada script se sitúa en la raíz del
  repositorio.

## Contrato de cada script

- Registra los bots que necesita (`bots/slow.py`, `bots/optimal.py`,
  `bots/faulty/*`) y crea torneos con identificadores nuevos.
- Guarda la evidencia en `experiments/results/<escenario>-<fecha>/` (fuera de
  Git: se regenera ejecutando el script):
  - `resumen.txt`: pasos con hora y cada comprobación como `OK` / `FALLO` / `INFO`;
  - `<torneo>.tournament.json`, `<torneo>.matches.json`, `<torneo>.events.json`:
    estado, todas las partidas (paginadas) con sus intentos, e historial completo
    de eventos (se lee hasta que el servidor cierra el flujo);
  - `workers.log`: muestras de `/api/v1/workers` con hora;
  - `meta.json`: revisión Git, archivos sin commit, imagen, límites de cada
    contenedor, variables `TM_*` (secretos enmascarados), hardware, semillas y
    comando (5.3);
  - `client-<torneo>.jsonl` y `client.jsonl`: lo que el cliente creó y vio en
    vivo por SSE (`load.py create` / `follow`), para I6;
  - `verify.json`: informe de `tm verify` (I1–I6) sobre todos los torneos
    guardados; una violación cuenta como comprobación fallida;
  - extras de cada escenario (`injection.json`, `observacion.log`, `coordinator.log`).
- Termina con **código 0 si todas las comprobaciones pasan** y 1 si alguna falla.
- Deja el clúster como estaba: vuelve a arrancar el worker que haya matado o
  pausado.

## Escenarios

| Script | Exigencia | Hipótesis | Evidencia esperada |
|---|---|---|---|
| `worker-loss.sh` | P03: si un worker desaparece durante una partida, el torneo continúa | Matar el worker de una partida en curso produce otro intento en otro worker | Intento fallido por infraestructura en el worker matado; otro `attempt_id`, generación mayor y mismo `MatchSpec`; un único `result.accepted` |
| `worker-pause.sh` | Guía 4.4: pausar un worker y reanudarlo tras expirar su intento | Pausado más que el plazo del intento, ese plazo vence (Ray no detecta una pausa) y se reintenta en otro. Con una pausa más corta, el árbitro detecta que estuvo congelado (`referee_stalled`), salvo que la pausa caiga con la jugada ya recibida | Ninguna derrota de un bot; un único aceptado: en otro worker tras reintento, o el pausado si ningún plazo venció (E3.10, se registra cuál) |
| `worker-partition.sh` (solo Docker) | Guía 5.3: pérdida temporal de comunicación y reparación | Aislado de `tm-net`, Ray da el nodo por muerto, el intento falla o vence y se reintenta; al reconectar se observa si Ray lo readmite | Comprobación de que el worker no llega al coordinador ni al head; ninguna derrota; un único aceptado; se anota si Ray lo readmite en 90 s y después se reinicia siempre (Ray puede darlo de baja más tarde) |
| `coordinator-restart.sh` | Guía 5.3: reinicio del coordinador | SIGKILL con intentos en curso: lo aceptado sigue, lo que estaba en curso queda `attempt.lost (coordinator_restart)` y se reintenta; el cliente se reconecta | Torneo `completed`, un resultado por partida, I6 en `tm verify` |
| `late-result.sh` | P03: impedir resultados aceptados duplicados | Un resultado válido de un intento ya reemplazado no puede imponerse | Inyección rechazada, `result.discarded (stale_attempt)`, el aceptado sigue siendo el del reintento |
| `cancelled-tournament.sh` | Guía 4.4: un torneo cancelado no se reabre ni avanza | Cancelar, perder el worker e inyectar un resultado no cambia nada | Torneo y partidas `cancelled`, ningún intento nuevo, `result.discarded (tournament_not_running)` |
| `faulty-bot.sh` | P03: distinguir partida que falla de worker que falla | Un bot defectuoso pierde y el worker sigue sano | Por cada defecto, `bot_failure` con victoria del óptimo en un único intento; workers `available` |
| `head-loss.sh` | Guía, salida de 4: documentar la pérdida del head | Perder el head interrumpe la ejecución; con el procedimiento de recuperación el torneo termina | Observación de `/ready`, torneo y contenedores; tras recuperar, `completed` con un resultado por partida |

## Reproducir la campaña de 5.3 desde cero

1. Construir la imagen y arrancar el clúster limpio (para y vuelve a crear los
   contenedores; los datos de PostgreSQL y de los bots se conservan):

```bash
bash scripts/build-image.sh
```

```bash
TM_BACKEND=ray bash scripts/start-cluster.sh
```

2. Ejecutar la matriz completa con repeticiones (unos 75 minutos; arranca y
   para el clúster por su cuenta, incluida la variante con
   `TM_MAX_ACTIVE_TOURNAMENTS=3` para la saturación):

```bash
bash experiments/campaign.sh docker
```

3. Resumir (mediana y rango por escenario):

```bash
python experiments/summarize.py experiments/results/campaign-docker-<fecha>.log
```

Sin Docker, en Linux o WSL con `scripts/local-cluster.sh` disponible (una
pasada de cada escenario; la partición no existe en local):

```bash
bash experiments/campaign.sh local
```

Un escenario suelto se ejecuta igual que antes, por ejemplo
`bash experiments/scenarios/worker-loss.sh`. En Git Bash no se exporta
`MSYS_NO_PATHCONV` para los escenarios: rompe `curl -o /dev/null`.

## Configuración

| Variable | Defecto | Uso |
|---|---|---|
| `TM_API` | `http://127.0.0.1:8000` | Dirección de la API |
| `TM_SCENARIO_PILES` | `[10,10,10]` | Montones de las partidas lentas: dos `slow.py` tardan ≈ fichas × 0,5 s |
| `TM_PAUSE_SECONDS` | plazo del intento + 10 s | Duración de la pausa en `worker-pause.sh`; `40` prueba la variante corta |
| `TM_OBSERVE_SECONDS` | `90` | Observación sin head en `head-loss.sh` |
| `TM_PARTITION_SECONDS` | `60` | Duración del aislamiento en `worker-partition.sh` |
| `TM_DOWN_SECONDS` | `10` | Tiempo con el coordinador caído en `coordinator-restart.sh` |

## Herramientas

- `lib.sh`: llamadas a la API, espera de eventos, guardado de evidencia y
  acciones sobre el clúster.
- `checks.py`: lectura de la evidencia y comprobaciones comunes.
- `tm verify --evidence <carpeta>`: comprobaciones comunes a todos los
  escenarios; `finish()` lo ejecuta con `PYTHONPATH=src` (requiere Python 3.12).
- `inject_late_result.py`: inyección de laboratorio de un resultado tardío.
  Se ejecuta dentro del coordinador y pasa por el mismo código de aceptación;
  no es un endpoint.

## Límites

- Un único host Docker: no se prueba la pérdida de una máquina física.
- `docker pause` congela procesos; no es una partición de red (eso es
  `worker-partition.sh`).
- La partición solo existe en Docker: sin root no se aísla la red de un proceso
  local (E3.3). El resto funciona también con `scripts/local-cluster.sh`.
- `docker network connect` puede dar otra IP al worker; el escenario la anota.
