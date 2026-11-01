# Escenarios

Cada script provoca una situación concreta en el clúster real, comprueba lo que
debe pasar y termina con código 0 si todo se cumple (1 si algo falla). Sirven
igual con Docker (`deploy/docker/`) que sin él (`deploy/local/`): detectan el
modo solos (`TM_SCENARIO_MODE=docker|local` lo fuerza). Informes de la campaña:
`docs/campaign/`.

## Los escenarios

- **`worker-loss.sh` — worker muerto durante una partida.** Mata el worker que
  juega una partida lenta y lo vuelve a arrancar. Se espera: el intento acaba
  como fallo de infraestructura, otro intento (mayor generación, misma partida)
  se acepta en otro worker, ninguna derrota.
- **`worker-partition.sh` — corte de red (solo Docker).** Saca de la red al worker
  de una partida durante 60 s, comprueba que de verdad no llega al coordinador
  ni al head, y lo reconecta. Se espera: ninguna derrota, un único resultado
  aceptado (en otro worker) y lo que llegue tarde descartado. Anota si Ray
  readmitió el worker y después lo reinicia.
- **`worker-pause.sh` — worker congelado.** Pausa el worker de una partida 100 s
  (o 40 s con `TM_PAUSE_SECONDS=40`). Se espera: con 100 s vence el plazo del
  intento y se reintenta en otro worker; con 40 s el árbitro detecta que estuvo
  congelado (`referee_stalled`) y se reintenta, o la partida termina con
  normalidad si ningún plazo llegó a vencer. Nunca una derrota.
- **`coordinator-restart.sh` — reinicio del coordinador.** Mata el coordinador con
  varias partidas en curso y lo arranca de nuevo. Se espera: lo aceptado sigue
  aceptado, lo que estaba en curso queda `attempt.lost` y se repite, y el
  cliente no pierde ningún evento (I6).
- **`late-result.sh` — resultado tardío.** Provoca un reintento e inyecta después
  el resultado del intento viejo. Se espera: se rechaza (`result.discarded`,
  `stale_attempt`) y el aceptado sigue siendo el del reintento.
- **`cancelled-tournament.sh` — torneo cancelado.** Cancela un torneo con partidas
  en curso, mata un worker e inyecta un resultado. Se espera: todo queda
  `cancelled`, no se emiten intentos nuevos y el resultado se descarta.
- **`faulty-bot.sh` — bots defectuosos.** Un torneo por cada bot de
  `bots/faulty/` contra el óptimo. Se espera: el defectuoso pierde en un único
  intento (`bot_failure`) y los workers siguen sanos.
- **`head-loss.sh` — pérdida del head de Ray.** Mata el head, observa 90 s y
  aplica la recuperación de `deploy/OPERATIONS.md`. Se espera: no se acepta nada
  mientras falta y, tras recuperar, el torneo termina con un resultado por
  partida.
- **`load.sh` — carga concurrente y saturación.** Lanza una mezcla de torneos a la
  vez y mide. Uso: `bash experiments/scenarios/load.sh NOMBRE --mix
  round_robin:6x1,elimination:4x2 --bot slow`. Se espera: varios torneos
  avanzan a la vez sin pasar de los workers; con más torneos que el límite de
  admisión, el exceso recibe 429 y entra al reintentar.

## Ejecutar

Con el clúster en marcha (`bash deploy/docker/start-cluster.sh` o
`bash deploy/local/local-cluster.sh start`):

```bash
bash experiments/scenarios/worker-loss.sh
```

La campaña completa (repeticiones, arranque y parada del clúster):

```bash
bash experiments/campaign.sh docker
```

```bash
python experiments/tools/summarize.py experiments/results/campaign-docker-<fecha>.log
```

En Git Bash no se exporta `MSYS_NO_PATHCONV` para los escenarios: rompe
`curl -o /dev/null`.

## Qué deja cada ejecución

`experiments/results/<escenario>-<fecha>/` (fuera de Git):

- `resumen.txt`: los pasos con su hora y cada comprobación (`OK`/`FALLO`/`INFO`).
- `<torneo>.tournament.json`, `.matches.json`, `.events.json`: la evidencia final.
- `client.jsonl`: lo que el cliente creó y vio en vivo (para I6).
- `verify.json`: el informe de `tm verify` (I1–I6); una violación cuenta como fallo.
- `meta.json`: revisión, configuración (secretos enmascarados), hardware y comando.

## Configuración

- `TM_API` (`http://127.0.0.1:8000`): dirección de la API.
- `TM_SCENARIO_PILES` (`[10,10,10]`): montones de las partidas lentas.
- `TM_PAUSE_SECONDS` (plazo del intento + 10 s), `TM_PARTITION_SECONDS` (60),
  `TM_DOWN_SECONDS` (10), `TM_OBSERVE_SECONDS` (90): duraciones de cada fallo.

## Utilidades (`experiments/tools/`)

`lib.sh` (API, esperas y acciones sobre el clúster), `checks.py` (comprobaciones
comunes), `load.py` (cliente: crea, escucha y guarda evidencia), `meta.py`,
`inject_late_result.py` (inyección de laboratorio, dentro del coordinador; no es
un endpoint) y `summarize.py`.

## Límites

Un solo equipo: no hay pérdida de máquina física ni red real entre nodos. La
partición solo existe en Docker (sin root no se aísla la red de un proceso).
