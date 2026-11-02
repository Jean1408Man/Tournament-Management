# Campaña: resultados

Resultados de la campaña de fallos y carga de la Fase 1. Cómo
ejecutarla: [ejecutar.md](ejecutar.md). Los datos de cada ejecución están en
`experiments/results/<escenario>-<fecha>/`.

**Resumen.**
- Docker: 46 ejecuciones, 46 aprobadas, y `tm verify` sin violaciones
  en ninguna.
- Local (WSL): una ejecución de cada escenario. Todas aprobadas, con `tm verify` sin violaciones.

## Configuración común

**Docker** (campaña de referencia, `campaign-docker-20261030-220035.log`,
30-10-2026 de 22:00 a 23:24):

**Local:** WSL 2.6.3 (Ubuntu, kernel 6.6.87.2, 12 CPU, 6,7 GB).

**Parámetros** (por defecto, iguales en los dos modos):
- plazos: 1 s por turno y 90 s por intento;
- reintentos: 3, con 1 s de espera entre ellos;
- sospecha: 45 s;
- límites: 64 intentos y 16 torneos activos (3 en la saturación).

**Torneos de los escenarios:** bots `slow.py`, `seed` 42 y montones
`[10,10,10]`. En las cargas, la `seed` es la posición del torneo en la mezcla.

Las duraciones son mediana [mínimo–máximo] e incluyen las esperas fijas de
observación de cada escenario: no miden rendimiento.

## Worker muerto durante una partida

- **Comando:** `bash experiments/scenarios/worker-loss.sh`
- **Configuración:** eliminación de dos `slow.py` (una partida de ~15 s). A los
  3 s de enviarse el intento se hace `docker kill --signal KILL` del worker que
  lo juega (en local, `SIGKILL` a su árbol de procesos). Al final se arranca de
  nuevo.
- **Resultados:** Docker 5 de 5 aprobadas, 120 s [107–141]; local 1 de 1.
- **Eventos observados:** en las 5 repeticiones, la misma secuencia:
  1. `attempt.dispatched` al worker víctima;
  2. `attempt.failed` (fallo de infraestructura);
  3. `attempt.issued` de la generación 2;
  4. `attempt.dispatched` a otro worker;
  5. `result.accepted`.

  Ningún `bot_failure`. Ejemplo: `worker-loss-20261030-220427`, worker matado a
  las 22:04:38 y torneo `completed` a las 22:05:42.
- **Observación:** coincide con lo esperado. La partida se repite con el mismo
  `MatchSpec` en otro worker y hay un único resultado aceptado. El reintento
  llega en menos de 1 s, que es lo que tarda Ray en informar de que la tarea
  murió. No se prueba la caída de una máquina física.

## Bot defectuoso con worker sano

- **Comando:** `bash experiments/scenarios/faulty-bot.sh`
- **Configuración:** un torneo por cada bot defectuoso contra `optimal.py`:
  `illegal_move`, `invalid_output`, `premature_exit`, `timeout`,
  `too_much_output` y `stderr_flood`.
- **Resultados:** Docker 3 de 3 aprobadas, 28 s [27–31]; local 1 de 1.
- **Eventos observados:** por cada defecto, un único intento y un
  `result.accepted` con `bot_failure` y ganador el óptimo. Los seis torneos
  terminan en 3–7 s cada uno (`faulty-bot-20261030-224858`) y los workers siguen
  `available`.
- **Observación:** un bot que incumple el protocolo pierde. No se trata como
  fallo de infraestructura: no se reintenta ni castiga al worker. No se cubren
  bots que agoten la memoria (`memory_hog` no está en el escenario) ni que lancen
  procesos que escapen del grupo.

## Resultado duplicado o tardío, y torneo cancelado

- **Comandos:** `bash experiments/scenarios/late-result.sh` y
  `bash experiments/scenarios/cancelled-tournament.sh`
- **Configuración:**
  - `late-result`: eliminación de dos `slow.py`;
  - `cancelled-tournament`: round-robin de tres `slow.py` (tres partidas a la
    vez).

  Los dos matan el worker del primer intento y luego inyectan su resultado con
  `inject_late_result.py`. Esa herramienta corre dentro del coordinador, usa el
  mismo código de aceptación y no es un endpoint.
- **Resultados:**
  - `late-result`: Docker 3 de 3, 92 s [87–99]; local 1 de 1.
  - `cancelled-tournament`: Docker 3 de 3, 94 s [90–95]; local 1 de 1.
- **Eventos observados:**
  - `late-result-20261030-223937`: worker matado a las 22:39:46; reintento y
    torneo `completed` a las 22:40:03. La inyección del resultado de la
    generación 1 devolvió `accepted: false` y quedó `result.discarded` con motivo
    `stale_attempt`.
  - `cancelled-tournament-20261030-224416`: cancelado a las 22:44:24 con 3
    intentos en curso; 20 s después seguía habiendo 3, ninguno nuevo. La
    inyección quedó `result.discarded` con `tournament_not_running`.
- **Observación:** un resultado de un intento ya sustituido, o de un torneo
  cancelado, nunca se acepta, y el rechazo queda en el historial. El resultado
  tardío se inyecta; que el worker original lo devuelva tarde de verdad se ve en
  la partición y en la pausa.

## Reinicio del coordinador

- **Comando:** `bash experiments/scenarios/coordinator-restart.sh`
- **Configuración:** round-robin de cuatro `slow.py` (6 partidas, varias a la
  vez). El coordinador se mata con `SIGKILL` y se arranca de nuevo a los 10 s.
- **Resultados:** Docker 3 de 3 aprobadas, 76 s [71–155], con el coordinador
  listo de nuevo en 16 s [12–22]; local 1 de 1.
- **Eventos observados:** `coordinator-restart-20261030-230431`:
  - matado a las 23:04:46 con 4 intentos en curso y ninguno aceptado;
  - `/ready` sin respuesta hasta que vuelve, 22 s después;
  - los 4 intentos quedan `attempt.lost` con motivo `coordinator_restart`;
  - el torneo termina `completed` con 14 intentos para 6 partidas, uno aceptado
    por partida.
- **Observación:** lo aceptado antes de morir sigue aceptado. Lo que estaba en
  curso se repite con otra generación. El cliente se reconecta desde su última
  `sequence` sin perder eventos (I6). Los intentos que Ray seguía ejecutando para
  el coordinador muerto no se aceptan.

## Pérdida temporal de comunicación (solo Docker)

- **Comando:** `bash experiments/scenarios/worker-partition.sh`
- **Configuración:** eliminación de dos `slow.py`. El worker de la partida se
  saca 60 s de la red con `docker network disconnect` y se vuelve a conectar.
  Antes y durante el corte se comprueba desde el worker una conexión TCP al
  coordinador y al head. Al final el worker se reinicia siempre.
- **Resultados:** 5 de 5 aprobadas, 149 s [135–201]. En las 5 la comunicación
  falló de verdad durante el corte y volvió al reconectar.
- **Eventos observados:**
  - En las 5, el intento aislado terminó por el plazo propio de 90 s
    (`attempt.failed` con `attempt_timeout`), no por Ray. El reintento se aceptó
    en otro worker.
  - Tras reconectar, Ray volvió a tener un nodo por worker en 20–58 s en 4
    repeticiones. En la otra (`worker-partition-20261030-222221`) Ray dio de baja
    el nodo y el contenedor terminó.
- **Observación:** Ray no detectó el nodo aislado en 65 s; quien actuó fue el
  plazo del intento. Al volver, Ray readmite el nodo o lo da de baja, pero la
  corrección no depende de cuál: la generación descarta lo tardío. Se pierde
  disponibilidad (hasta 90 s por partida), no corrección. Es una desconexión
  limpia, no pérdida de paquetes ni latencia; el corte entre coordinador y head
  no se probó.

## Pausa de un worker

- **Comandos:** `bash experiments/scenarios/worker-pause.sh` y
  `TM_PAUSE_SECONDS=40 bash experiments/scenarios/worker-pause.sh`
- **Configuración:** eliminación de dos `slow.py`. El worker de la partida se
  congela con `docker pause` (en local, `SIGSTOP`) 100 s o 40 s, sin cortar su
  red.
- **Resultados:**
  - Pausa de 100 s: Docker 3 de 3, 172 s [171–173]; local 1 de 1.
  - Pausa de 40 s: Docker 3 de 3, 107 s [107–108]; local 1 de 1.
- **Eventos observados:**
  - Con 100 s, en todas: `attempt.failed` por `attempt_timeout`, reintento
    aceptado en otro worker y el resultado tardío descartado. Ejemplo:
    `worker-pause-20261030-225027`, tm-worker-4 pausado a las 22:50:38.
  - Con 40 s, en todas: `attempt.failed` por `referee_stalled` (el árbitro
    detecta que estuvo congelado) y reintento en otro worker.
- **Observación:** Ray no detecta una pausa; la cubren el plazo del intento y el
  árbitro. Nunca hay derrota de un bot. Si la pausa cae con la jugada ya
  recibida, la partida puede terminar con normalidad; no pasó en esta campaña.

## Pérdida del head

- **Comando:** `bash experiments/scenarios/head-loss.sh`
- **Configuración:** eliminación de dos `slow.py`. El head se mata con `SIGKILL`
  y se observa 90 s sin él. Después se aplica la recuperación de
  `deploy/OPERATIONS.md`: arrancar el head, reiniciar los workers y reiniciar el
  coordinador.
- **Resultados:** Docker 3 de 3 aprobadas, 160 s [159–164], casi todo
  observación; local 1 de 1.
- **Eventos observados:** `head-loss-20261030-230933`:
  - head matado a las 23:09:41 con un intento en curso;
  - recuperación a las 23:11:21;
  - `/ready` 200 y torneo `completed` a las 23:12:15;
  - el intento en curso quedó `attempt.lost` al reiniciar el coordinador y se
    aceptó la generación 2.
- **Observación:** sin head no se acepta nada; con la recuperación, el torneo
  termina con un resultado por partida. Es la mayor dependencia de Ray en la
  Fase 1: la recuperación es manual y no hay head de reserva. En la Fase 2 no
  hay head.

## Carga concurrente y saturación

- **Comandos:** los tres de la sección 3 de [ejecutar.md](ejecutar.md).
- **Configuración:**
  - Carga lenta: round-robin de 6, dos eliminaciones de 4 y una de 2 con
    `slow.py` y montones `[5,5,5]` (22 partidas).
  - Carga Nim: round-robin de 16 y dos eliminaciones de 8 con `optimal.py`
    (134 partidas).
  - Saturación: seis eliminaciones de 4 con `slow.py` y el clúster arrancado
    con `TM_MAX_ACTIVE_TOURNAMENTS=3`.
- **Resultados en Docker** (5 de 5 en cada carga):
  - Carga lenta: 52,5 s [52,3–53,0]; 0,42 partidas/s; 3,46 intentos a la vez de
    media, pico 4.
  - Carga Nim: 84,7 s [45,2–88,0]; 1,58 partidas/s [1,52–2,97]; 0,91 intentos a
    la vez, pico 3–4.
  - Saturación: 50,2 s [45,2–53,0]; 14 respuestas 429 [14–16]; los 6 torneos
    terminan; pico 4.
- **Resultados en local** (1 de 1 en cada una):
  - Carga lenta: 47,9 s, 0,46 partidas/s, 3,62 intentos a la vez.
  - Carga Nim: 21,5 s, 6,22 partidas/s.
  - Saturación: 49,4 s, 18 partidas, 14 respuestas 429.
- **Eventos observados:**
  - Partidas de distintos torneos se alternan en los cuatro workers; en la carga
    lenta, los torneos pequeños terminan sin esperar al round-robin.
  - Ningún reintento en las cargas Nim.
  - Las conexiones a PostgreSQL muestreadas no pasan de 2–4.
- **Observación:**
  - Con partidas largas la concurrencia escala casi con los workers: 3,7 veces
    más rápido que con un intento a la vez (194 s en 5.1).
  - Con Nim manda el coste del coordinador por partida (ver la sección
    siguiente): los workers pasan la mayor parte del tiempo ociosos.
  - En Docker queda una variación sin explicar en la carga Nim: cuatro
    repeticiones en 85–88 s y una en 45 s. En local la misma carga tarda 21,5 s.
  - El límite de admisión funciona: el exceso recibe 429 y entra al reintentar.

## Limitaciones comunes

- Un solo equipo: no hay pérdida de una máquina física ni red real entre nodos.
- El reloj de la VM de Docker salta ~24 s a veces; las métricas usan la hora de
  recepción del cliente y el orden siempre la `sequence`.
- Las duraciones de los escenarios incluyen esperas fijas; no miden rendimiento.
