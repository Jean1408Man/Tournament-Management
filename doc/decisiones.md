# Decisiones de arquitectura

Las seis decisiones que más pesan en el diseño. De cada una: el problema, lo que
se decidió, por qué y qué implica.

## D1 — PostgreSQL como única fuente de verdad

**Problema.** Los torneos deben sobrevivir a la caída de cualquier proceso,
varios pueden avanzar a la vez y aceptar un resultado tiene que ser atómico con
el avance del torneo.

**Decisión.** PostgreSQL guarda todo lo que importa: bots registrados, torneos,
partidas, intentos con su generación, resultados, historial de eventos y claves
de idempotencia. El coordinador solo mantiene en memoria lo que puede
reconstruir; Ray y los workers mantienen estado transitorio de ejecución, sin autoridad
sobre los resultados durables. Los bytes de los bots se guardan en el almacén
de artefactos del coordinador; deben conservarse junto con PostgreSQL.
PostgreSQL es la persistencia local del nodo coordinador: corre dentro de su
contenedor y solo atiende por socket Unix, sin puerto ni contraseña, así que
ningún otro nodo puede alcanzarla. Head y workers no lo tienen instalado.

**Por qué.** Una base transaccional da de serie lo que habría que construir a
mano con estado en memoria o dentro de Ray: escrituras atómicas, bloqueo por
partida y durabilidad tras un commit. Además, lo guardado no depende de Ray, que
se sustituye en la Fase 2.

**Por qué no un actor de Ray.** Ray puede guardar estado en un actor: una clase
`@ray.remote` cuyo objeto vive en la memoria de un proceso que Ray coloca en
algún nodo y cuyos métodos se ejecutan de uno en uno. En la forma se parece a
esta decisión (un único dueño del estado al que los demás piden por mensajes,
en un solo nodo y sin réplica), pero difiere en seis puntos que la plataforma
necesita:

1. **Durabilidad.** El estado del actor está en RAM y se pierde si muere su
   proceso o su nodo. Ray puede reiniciarlo (`max_restarts`), pero vuelve a
   ejecutar el constructor y el actor arranca vacío, salvo que la aplicación
   guarde sus propias copias. En la
   [pérdida del head](campaign/resultados.md#pérdida-del-head) la recuperación
   reinicia los workers y el coordinador: con el estado en un actor se habría
   perdido; con PostgreSQL el torneo terminó con un resultado por partida.
2. **Atomicidad.** Si un método del actor cambia varios atributos y falla a
   mitad, los primeros cambios quedan hechos: no hay vuelta atrás. Aceptar un
   resultado actualiza el intento, `results`, el avance del torneo y el
   historial en una sola transacción: queda todo o nada.
3. **Ubicación y descubrimiento.** Ray elige el nodo del actor, lo anota en el
   head y la referencia oculta dónde está. La base está donde la pone el
   despliegue (`TM_DATABASE_URL`): todo es explícito, se puede inspeccionar y
   no depende del head.
4. **Concurrencia.** Un actor atiende una llamada a la vez (o, con varios hilos,
   deja la protección a la aplicación): las consultas de la API esperarían
   detrás de las escrituras del coordinador. En PostgreSQL las peticiones HTTP
   y el hilo coordinador trabajan a la vez, y arbitran los bloqueos de fila y
   las restricciones (una sola fila por partida en `results`, D2).
5. **Consulta del estado.** El estado de un actor solo se ve a través de sus
   métodos. La base se consulta con SQL: la API la lee para producir la
   evidencia que revisa `tm verify` (D6), y los escenarios y la UI la consultan
   en modo de solo lectura.
6. **Quién decide ante un fallo.** Ray detecta la muerte de un actor, avisa a
   quien lo llama (`RayActorError`) y puede reiniciarlo y repetir llamadas por
   su cuenta (`max_restarts`, `max_task_retries`): habría ejecuciones que la
   plataforma no ve ni cuenta, la misma razón de D3. Con el estado en
   PostgreSQL cada pérdida queda como evento (`attempt.lost`) y la plataforma
   decide repetir con una generación nueva (D2).

**Consecuencias.**

- Una respuesta (202, 201, 200 de cancelar) llega siempre después del commit:
  lo confirmado sobrevive a reinicios que conserven la base y los artefactos.
- Al arrancar, el coordinador reconstruye su trabajo desde la base.
- `tm verify` puede comprobar cualquier ejecución a partir de lo guardado.
- Presupone un único coordinador activo por base (no hay elección de líder).
- PostgreSQL pasa a ser imprescindible. Cae y se recupera con el coordinador:
  tras un SIGKILL del contenedor rehace su registro de cambios y lo confirmado
  se conserva; si muere solo PostgreSQL, el contenedor del coordinador termina
  con error en vez de seguir vivo sin base.

## D2 — Generación por intento y aceptación transaccional

**Problema.** Una partida puede ejecutarse varias veces: un worker cae, un plazo
vence o el coordinador se reinicia. El resultado de una ejecución vieja puede
llegar tarde, o duplicado.

**Decisión.** Cada intento de una partida tiene un identificador y una
generación creciente. Aceptar es una transacción que bloquea la partida y solo
admite el resultado del intento vigente de una partida aún sin resultado. Todo
lo demás se registra como `result.discarded`, con su motivo (`stale_attempt`,
`tournament_not_running`…).

**Por qué.** Ningún middleware garantiza entregar cada resultado exactamente una
vez, y aceptar el primero que llegue premiaría a un intento viejo. Numerar los
intentos permite decidir, con un dato guardado, cuál es el válido, sea cual sea
el orden de llegada.

**Consecuencias.**

- Se garantiza un único resultado deportivo aceptado por partida, no una única
  ejecución física.
- El presupuesto de reintentos cuenta las generaciones ya guardadas: reiniciar
  no lo repone.
- Los descartes quedan en el historial como prueba de que la protección actuó.

## D3 — Ray solo como transporte, sin reintentos propios

**Problema.** Ray puede reintentar tareas, decidir dónde se ejecutan y guardar
estado. La Fase 2 debe sustituirlo por una implementación propia sin rehacer la
lógica de corrección.

**Decisión.** La tarea de Ray se crea con `max_retries=0`: Ray nunca repite nada a
escondidas. El coordinador elige el worker y Ray lo respeta con afinidad
estricta; un recurso propio (`tm_worker`) limita a una partida por worker.
Intentos y resultados viajan como JSON y los identificadores internos de Ray no
salen del adaptador (`execution/backends/ray.py`).

**Por qué.** Si Ray reintentara por su cuenta, habría ejecuciones que la
plataforma no ve ni cuenta, y la corrección dependería de Ray. Usarlo solo para
llevar un intento a un worker concreto y avisar si muere deja toda la lógica en
la plataforma, que es lo que se conserva en la Fase 2.

**Consecuencias.**

- Los reintentos, plazos y descartes son de la plataforma (D2), visibles en el
  historial.
- El head de Ray es un punto único de fallo con recuperación manual.

## D4 — Membresía propia de los workers

**Problema.** Lo que Ray dice de los nodos llega tarde o confunde: tras matar un
worker lo sigue dando por vivo 15–30 s; tras reiniciarlo lista a la vez el nodo
viejo y el nuevo; no detecta un worker pausado.

**Decisión.** Cada worker tiene un estado propio: `available`, `suspected` o
`lost`. Pasa a `suspected` durante 45 s si un intento suyo falla por
infraestructura mientras Ray aún lo lista vivo, y a `lost` si deja de aparecer.
Un worker reiniciado (otra identidad interna con el mismo nombre) vuelve como
`available`, y la sospecha solo se aplica a la identidad que falló. Solo se
envía trabajo a workers `available` con hueco libre.

**Por qué.** Con la lista de Ray tal cual, el coordinador seguiría enviando
partidas a un worker recién caído y gastaría reintentos. Combinar lo que Ray ve
con lo que observan los propios intentos detecta antes los fallos, y la Fase 2
puede alimentar la misma membresía con latidos propios.

**Consecuencias.**

- No se manda trabajo a un worker que acaba de fallar aunque Ray aún lo liste.
- La API (`/workers`) muestra este estado, nunca identificadores de Ray.
- El primer intento enviado a un nodo viejo tras un reinicio aún gasta un
  reintento (límite aceptado; lo resuelven los latidos propios de la Fase 2).

## D5 — Coordinador de un hilo, con turnos y admisión con 429

**Problema.** El sistema ejecutaba un intento a la vez y
agotaba un torneo antes de empezar el siguiente. Hacía falta jugar varias
partidas y varios torneos a la vez sin carreras entre comprobar un hueco y
usarlo, y sin aceptar trabajo sin límite.

**Decisión.** Un solo hilo revisa cada 20 ms los resultados, los plazos y los
huecos libres. Calcula cuántos intentos puede emitir (el mínimo entre los
huecos libres de los workers y `TM_MAX_RUNNING_ATTEMPTS`) antes de emitirlos, y
reparte los turnos rotando entre los torneos con partidas listas. Con
`TM_MAX_ACTIVE_TOURNAMENTS` torneos en curso, crear otro responde 429 con
`Retry-After`, sin guardar nada.

**Por qué.** Un hilo por intento o por torneo, o la programación asíncrona,
obligan a coordinar el acceso a los huecos y abren carreras entre comprobar uno
y ocuparlo. Con un único hilo dueño de la tabla de intentos en vuelo esas
carreras no pueden ocurrir. El 429 pone un límite explícito en lugar de aceptar
trabajo que no se puede atender.

**Consecuencias.**

- Nadie ocupa un hueco entre comprobarlo y usarlo: una carrera ya no gasta
  reintentos.
- Un torneo grande no deja esperando a los pequeños.
- El contrato pasó a la versión 1.1.0 (respuesta 429).
- El ritmo queda limitado por la CPU de ese hilo: con partidas muy cortas (Nim),
  aceptar resultados lo saturaba (ver
  [campaña, carga](campaign/resultados.md#carga-concurrente-y-saturación)).

## D6 — Evidencia del cliente y `tm verify` como verificador independiente

**Problema.** Las pruebas automáticas cubren el código, pero la campaña tiene que
demostrar que en ejecuciones reales con fallos se cumplen las garantías, sin
fiarse de lo que el propio servidor diga de sí mismo.

**Decisión.** Cada ejecución guarda el estado final, todas las partidas, el
historial completo de eventos y el registro del cliente que escuchó en vivo.
`tm verify --evidence <carpeta>` comprueba sobre eso seis invariantes: un único
resultado por partida (I1), avance coherente con el formato (I2), solo se
acepta el intento vigente (I3), nada tras cancelar (I4), ninguna derrota por
fallo de infraestructura (I5) y lo que vio el cliente sigue en el historial
(I6).

**Por qué.** Si cada escenario comprobara solo lo suyo, una garantía podría
romperse en un escenario que no la mira. Un verificador común revisa todas las
garantías en todas las ejecuciones, de fallo o de carga, y al incluir lo que vio
el cliente no depende solo de lo que diga el servidor.

**Consecuencias.**

- Todas las ejecuciones de la campaña se verifican igual, sean de fallo o de
  carga.
- Detecta violaciones en lo observado; no demuestra que no puedan existir.
- I2 reutiliza los formatos del servidor; para no compartir un posible error,
  añade propiedades independientes (parejas jugadas, derrotas por participante).
