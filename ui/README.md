# Panel del clúster

Una página web local para ver el clúster mientras trabaja y lanzar las órdenes
habituales sin escribir comandos. Solo usa la biblioteca estándar de Python: no
instala nada, no toca `src/` ni el contrato de la API.

## Arrancar

Se arranca en el mismo sitio que el clúster:

- **Docker**: desde Git Bash en Windows (o Linux), en la raíz del repositorio:

  ```bash
  python ui/server.py
  ```

- **Local**: dentro de WSL, en la misma carpeta desde la que arrancaste el
  clúster local:

  ```bash
  TM_UI_MODE=local python3 ui/server.py
  ```

  El entorno de Python lo busca igual que `deploy/local/local-cluster.sh`: en
  `TM_VENV`, si no en `UV_PROJECT_ENVIRONMENT` (donde lo deja `uv sync` si lo
  tienes definido) y si no en `.venv`.

Imprime una URL con un token (`http://127.0.0.1:8765/?token=…`): ábrela en el
navegador. El token cambia en cada arranque. No arranques dos paneles en el mismo
puerto, uno en Windows y otro en WSL: el navegador llega al de Windows y responde
"token incorrecto" con la URL del otro. En Windows el modo es siempre
Docker; en Linux o WSL es Docker si el contenedor `tm-coordinator` está en marcha
y local si no. Variables: `TM_UI_MODE` (`docker` o `local`) fuerza el modo,
`TM_UI_PORT` cambia el puerto (8765) y `TM_API` la dirección de la API
(`http://127.0.0.1:8000`).

Si el panel no puede hablar con el clúster de su modo, lo dice en una franja
amarilla arriba. El caso típico es abrirlo en Windows (modo Docker) con Docker
Desktop cerrado mientras corre el clúster local de WSL: los workers se ven,
porque vienen de la API, pero torneos, partidas, logs y órdenes fallan. La
solución es arrancar el panel dentro de WSL con `TM_UI_MODE=local`.

## Paneles

Arriba, cinco cifras: workers disponibles, ocupación, torneos activos, partidas en
curso y reintentos de los torneos listados.

- **Workers**: una tarjeta por worker con su estado (`available` en verde,
  `suspected` parpadeando en ámbar, `lost` o caído en gris) y una barra de
  ocupación. Elige uno para ver su log.
- **Torneos**: los activos y los últimos 10 terminados, con bots, una barra de
  partidas hechas, las partidas en curso y los reintentos. Elige uno para ver sus
  eventos.
- **Partidas en ejecución**: cada intento en curso con su torneo, bots,
  generación (más de 1 significa reintento), worker y desde hace cuánto.
- **Eventos**: el flujo de eventos del torneo elegido, en vivo, como línea de
  tiempo: `+` creado o emitido, `→` enviado a un worker, `✓` aceptado, `✕` fallo
  o descarte, `⊘` cancelación.
- **Logs**: del coordinador, del head o de un worker, filtrables por nivel.
- **Ejecuciones**: las órdenes, con su salida en vivo, el código de salida y la
  carpeta de resultados del escenario.

### Tipos de eventos

Los iconos son los de la línea de tiempo del panel **Eventos**:

- `tournament.created`: el torneo quedó guardado. Siempre es el primer evento.
- `attempt.issued` (+): el coordinador crea un intento para jugar una partida.
  Cada reintento es un `attempt.issued` nuevo, con una generación mayor.
- `attempt.dispatched` (→): ese intento se envía a un worker. El detalle dice
  cuál, por ejemplo `worker=tm-worker-2`.
- `result.accepted` (✓): el resultado del intento se aceptó y quedó guardado.
  Hay uno por partida terminada.
- `attempt.failed` (✕): el intento acabó sin resultado válido por culpa de la
  infraestructura: el worker murió, venció el plazo o el árbitro detectó una
  congelación. Si quedan reintentos, le sigue otro `attempt.issued`.
- `attempt.lost` (✕): el intento estaba en marcha cuando se reinició el
  coordinador; se da por perdido y se repite. Sale en `coordinator-restart`.
- `result.discarded` (✕): llegó un resultado que no vale (de un intento viejo, o
  con el torneo ya cancelado) y se descartó. Sale en `late-result` y en
  `cancelled-tournament`.
- `tournament.cancelled` (⊘): alguien canceló el torneo. Desde ahí no se lanza
  ni se acepta nada.
- `tournament.blocked` (✕): el torneo se detuvo porque una partida agotó sus
  reintentos. El detalle dice el motivo.
- `attempt.invalidated` (✕): solo lo emite el planificador de simulación; en el
  clúster con Ray no aparece.

El final del torneo no tiene evento propio: el torneo pasa a `completed` en el
panel **Torneos** tras el último `result.accepted`.

Los torneos y las partidas salen de una consulta de solo lectura a PostgreSQL. Lo
demás sale de la API pública y de los logs (`docker logs` o
`~/.local/share/tournament-management/logs/`). Los datos se refrescan cada 2 s.

## Manual de las órdenes

Las órdenes están en el panel **Ejecuciones**, en pestañas (Clúster, Workers,
Escenarios, Carga, Torneo a medida y Verificar). Cada orden es una tarjeta con una
frase de qué hace y su botón **Ejecutar**. Solo puede correr una a la vez: mientras tanto los botones se desactivan y la salida aparece
debajo, línea a línea. Al terminar, el título muestra el código de salida (0 en
verde: todo bien; otro número en rojo: algo falló) y, si es un escenario, la
carpeta donde dejó su evidencia. Si recargas la página, vuelve a mostrar la
ejecución en curso. "Parar" y "Provocar fallo" piden confirmación.

### Clúster

Antes que nada, el clúster tiene que estar arrancado.

- **Arrancar**: levanta PostgreSQL, el head de Ray, 4 workers (`tm-worker-1` a
  `tm-worker-4`) y el coordinador. Tarda entre 1 y 2 minutos. En Docker equivale
  a `TM_BACKEND=ray bash deploy/docker/start-cluster.sh`; en local, a
  `bash deploy/local/local-cluster.sh start`.
- **Parar**: detiene todo. Los torneos guardados se conservan para el próximo
  arranque.
- **Estado**: comprueba que cada pieza responde e imprime la configuración con la
  que corre el coordinador (los secretos salen como `[oculta]`).

### Workers

Cada worker es una máquina (un contenedor o un proceso) que juega partidas, una
a la vez. El clúster arranca con 4 (`tm-worker-1` a `tm-worker-4`). Los
selectores solo ofrecen lo que tiene sentido con los workers de ese momento, y se
actualizan solos cada 5 s.

- **Añadir worker**: arranca el siguiente número libre (al principio
  `tm-worker-5`; el panel acepta hasta `tm-worker-16`) y lo une al clúster.
  - Qué verás: en unos segundos aparece su tarjeta en `available`.
  - Resultado: el coordinador ya puede darle partidas, así que caben más a la
    vez. Se nota en una carga: hasta 5 partidas en curso en vez de 4.
- **Provocar fallo**: mata de golpe un worker que esté en marcha, como si se
  apagara la máquina. Lo que pasa depende de si estaba jugando:
  - **Sin partidas en curso**: nadie lo nota al momento. Cuando Ray detecta que
    el nodo murió, unos 15–20 s después, la tarjeta pasa a `lost` y el
    coordinador deja de contar con él.
  - **Con una partida en curso**: el intento termina como fallo de
    infraestructura, el worker pasa a `suspected` y la partida se repite en otro
    worker con generación 2 (en **Partidas en ejecución** sale "reintento 2").
    Ningún bot pierde por ello.
  - En los dos casos el worker no vuelve solo: queda caído hasta que lo recuperes.
- **Recuperar worker**: vuelve a arrancar un worker caído, sea por "Provocar
  fallo" o por un escenario interrumpido. El selector solo lista los caídos.
  - En Docker hace `docker start` del contenedor; en local, lo relanza como al
    arrancar el clúster.
  - Qué verás: la tarjeta vuelve a `available` en unos segundos. Si estaba
    `suspected`, el coordinador espera a que pase la sospecha (45 s) antes de
    darle partidas.

### Torneo a medida

Crea un torneo con lo que elijas y lo sigue hasta el final, como una carga de un
solo torneo:

- **Formato**: round-robin (todos contra todos, una partida por pareja) o
  eliminación (el que pierde queda fuera; con un número de bots que no es
  potencia de 2, algunos pasan de ronda sin jugar).
- **Número de bots**: de 2 a 16 participantes.
- **Bots**: de 1 a 4 de la lista, que se reparten por turnos. Por ejemplo, con 4
  participantes y `optimal` + `random_bot` salen `optimal-01`, `random_bot-02`,
  `optimal-03` y `random_bot-04`.
  - `optimal` juega perfecto, `random_bot` al azar y `slow` despacio (para ver
    partidas largas).
  - Los de `faulty/` fallan a propósito: con ellos verás derrotas por
    `bot_failure`, nunca reintentos.
- **Montones**: los montones iniciales de Nim, por ejemplo `5,5,5`. De 1 a 5
  montones, cada uno de 1 a 50. Más grandes significa partidas más largas.

Qué verás: el torneo `load-torneo-…` en **Torneos** y sus partidas en **Partidas
en ejecución**. Al terminar, el resumen (tiempo, partidas, partidas por segundo)
y `tm verify`. Código 0 si todo cuadra. La evidencia queda en
`experiments/results/load-torneo-<fecha>/`.

### Escenarios

Cada escenario prepara su propio torneo con bots lentos, provoca un fallo
concreto en el momento justo, comprueba lo que debe pasar y deja la evidencia en
`experiments/results/<escenario>-<fecha>/`. Termina con código 0 si todas las
comprobaciones salen `OK`. Duran de 1 a 3 minutos. Al terminar, el escenario
restaura lo que rompió. Mientras corre, los paneles muestran el fallo en vivo:
elige su torneo (empieza por `scn-`) para ver los eventos.

- **cancelled-tournament**: cancela un torneo con partidas en marcha, mata un
  worker e intenta colar un resultado. Debe quedar todo `cancelled`, sin
  intentos nuevos, y el resultado se descarta.
- **coordinator-restart**: mata el coordinador con partidas en curso y lo vuelve
  a arrancar. Lo ya aceptado se mantiene, lo que estaba en curso se repite y el
  cliente no pierde ningún evento.
- **faulty-bot**: enfrenta cada bot defectuoso de `bots/faulty/` contra el
  óptimo. Hay siete: hace una jugada ilegal, escribe basura, consume demasiada
  memoria, se cierra antes de tiempo, inunda stderr, no responde a tiempo o
  escribe demasiado. El defectuoso pierde en un
  único intento y los workers siguen sanos.
- **head-loss**: mata el head de Ray, observa 90 s y aplica la recuperación de
  `deploy/OPERATIONS.md`. No se acepta nada mientras falta el head; después, el
  torneo termina.
- **late-result**: fuerza un reintento y luego entrega el resultado del intento
  viejo. Se rechaza y vale el del reintento.
- **worker-loss**: mata el worker que juega una partida y lo arranca de nuevo. La
  partida se repite en otro worker y ningún bot pierde. Es el más útil para
  enseñar la recuperación.
- **worker-partition** (solo Docker): corta la red del worker de una partida 60 s
  y la restablece. Hay un único resultado aceptado, en otro worker, y lo que
  llegue tarde se descarta.
- **worker-pause** y **worker-pause (40 s)**: congelan el worker de una partida
  100 s o 40 s. Con 100 s vence el plazo del intento; con 40 s el árbitro detecta
  la congelación. En los dos casos la partida se repite, sin derrotas.

Qué hace cada uno por dentro: `experiments/scenarios/README.md`.

### Carga

Una carga lanza muchos torneos a la vez para ver si el sistema los reparte entre
los workers sin mezclarlos ni perder resultados. Al terminar imprime cuánto tardó,
cuántas partidas se aceptaron, cuántas partidas por segundo, cuántos intentos
corrieron a la vez y cuántos le tocaron a cada worker, y pasa `tm verify`.

- **carga-lenta**: un round-robin de 6 bots, dos eliminaciones de 4 y una de 2,
  todos con `slow.py` (piensa despacio) y montones `[5,5,5]`. Son 22 partidas
  largas: sirve para ver los 4 workers ocupados a la vez durante unos 50 s.
- **carga-nim**: un round-robin de 16 bots y dos eliminaciones de 8, con
  `optimal.py` (juega perfecto y rápido). Son 134 partidas cortas: mide cuánto
  aguanta el coordinador aceptando resultados. Tarda entre 20 s (local) y 90 s
  (Docker).

Mientras corre, el panel **Torneos** se llena de torneos `load-…` en `running` y
el de **Partidas en ejecución** muestra hasta 4 a la vez.

### Verificar

**tm verify** es el verificador independiente: lee la evidencia que guardó una
ejecución y comprueba, sin fiarse del coordinador, seis reglas:

- I1: cada partida tiene como mucho un resultado aceptado.
- I2: nada se lanza ni se acepta antes de estar listo, y el cuadro final cuadra
  al repetir el historial.
- I3: lo aceptado es del intento vigente (la última generación).
- I4: tras cancelar un torneo no se lanza ni se acepta nada.
- I5: un fallo de la infraestructura nunca decide una partida.
- I6: lo que el cliente recibió en vivo sigue igual en el historial final, y todo
  torneo que la API aceptó (202) existe.

El selector muestra las 15 ejecuciones más recientes de
`experiments/results/`, con el escenario y la fecha legibles (por ejemplo
`worker-loss · 01-11 01:15` es el `worker-loss` del 1 de noviembre a las 01:15).
La primera es la última que corriste. La salida es un informe JSON con cada
comprobación. Código 0: ninguna regla violada.

Los escenarios ya pasan `tm verify` al terminar (lo guardan en `verify.json` de
su carpeta), así que esta orden sirve para revisar una ejecución antigua o
enseñarlo en la defensa.


## Seguridad

El panel ejecuta órdenes, así que:

- Escucha solo en `127.0.0.1`.
- Todas las rutas exigen el token de este arranque, porque los logs son internos.
- Rechaza cualquier `Host` que no sea `127.0.0.1:<puerto>` o `localhost:<puerto>`.
  Así otra web no puede alcanzarlo cambiando su DNS.
- Las órdenes solo se aceptan por POST, con el token y con el `Origin` de la
  propia página. Así otra web abierta en el navegador no puede dispararlas.
- Admite una ejecución a la vez y nunca pasa texto libre a la shell.

Las pruebas están en `tests/unit/test_ui_server.py`.
