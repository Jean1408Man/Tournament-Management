# Escenarios de fallo

Scripts reproducibles de la etapa 4.4. Demuestran con el clúster real las
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
    estado, partidas con sus intentos, e historial de eventos;
  - `workers.log`: muestras de `/api/v1/workers` con hora;
  - extras de cada escenario (`injection.json`, `observacion.log`, `coordinator.log`).
- Termina con **código 0 si todas las comprobaciones pasan** y 1 si alguna falla.
- Deja el clúster como estaba: vuelve a arrancar el worker que haya matado o
  pausado.

## Escenarios

| Script | Exigencia | Hipótesis | Evidencia esperada |
|---|---|---|---|
| `worker-loss.sh` | P03: si un worker desaparece durante una partida, el torneo continúa | Matar el worker de una partida en curso produce otro intento en otro worker | Intento fallido por infraestructura en el worker matado; otro `attempt_id`, generación mayor y mismo `MatchSpec`; un único `result.accepted` |
| `worker-pause.sh` | Guía 4.4: pausar un worker y reanudarlo tras expirar su intento | El intento del worker congelado ~40 s se pierde y se reintenta en otro | El intento pausado no se acepta; un único aceptado en otro worker; si llega algo del pausado, `result.discarded` |
| `late-result.sh` | P03: impedir resultados aceptados duplicados | Un resultado válido de un intento ya reemplazado no puede imponerse | Inyección rechazada, `result.discarded (stale_attempt)`, el aceptado sigue siendo el del reintento |
| `cancelled-tournament.sh` | Guía 4.4: un torneo cancelado no se reabre ni avanza | Cancelar, perder el worker e inyectar un resultado no cambia nada | Torneo y partidas `cancelled`, ningún intento nuevo, `result.discarded (tournament_not_running)` |
| `faulty-bot.sh` | P03: distinguir partida que falla de worker que falla | Un bot defectuoso pierde y el worker sigue sano | Por cada defecto, `bot_failure` con victoria del óptimo en un único intento; workers `available` |
| `head-loss.sh` | Guía, salida de 4: documentar la pérdida del head | Perder el head interrumpe la ejecución; con el procedimiento de recuperación el torneo termina | Observación de `/ready`, torneo y contenedores; tras recuperar, `completed` con un resultado por partida |

## Configuración

| Variable | Defecto | Uso |
|---|---|---|
| `TM_API` | `http://127.0.0.1:8000` | Dirección de la API |
| `TM_SCENARIO_PILES` | `[10,10,10]` | Montones de las partidas lentas: dos `slow.py` tardan ≈ fichas × 0,5 s |
| `TM_PAUSE_SECONDS` | `40` | Duración de la pausa en `worker-pause.sh` |
| `TM_OBSERVE_SECONDS` | `90` | Observación sin head en `head-loss.sh` |

## Herramientas

- `lib.sh`: llamadas a la API, espera de eventos, guardado de evidencia y
  acciones sobre el clúster.
- `checks.py`: lectura de la evidencia y comprobaciones comunes.
- `inject_late_result.py`: inyección de laboratorio de un resultado tardío.
  Se ejecuta dentro del coordinador y pasa por el mismo código de aceptación;
  no es un endpoint.

## Límites

- Un único host Docker: no se prueba la pérdida de una máquina física.
- `docker pause` congela procesos; no es una partición de red (etapa 5.3).
- El modo sin Docker de estos scripts llega en el último paso de 4.4.
