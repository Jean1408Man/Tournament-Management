# Mapa de dependencias del middleware

Para cada requisito distribuido: qué lo proporciona en la Fase 1 y qué responde
nuestro sistema a la pregunta del rediseño nativo (Fase 2). Las cifras se
midieron en la campaña ([resultados](campaign/resultados.md)); no se suponen.

| Requisito | Mecanismo en Fase 1 | Pregunta para el rediseño nativo | Respuesta del proyecto |
| --- | --- | --- | --- |
| ejecución remota | Ray task: `play_attempt` con `ray.remote(max_retries=0)`, lanzada desde el coordinador por Ray Client (`ray://tm-head:10001`) | ¿Qué protocolo explícito de worker hace falta? | Un protocolo HTTP propio: el worker pide un intento al coordinador, recibe el `Attempt` en JSON (el mismo de hoy), juega y devuelve el `AttemptOutcome` en JSON. Los bots ya los descarga por HTTP con token y verifica su SHA-256. |
| worker con estado | No se usa Ray actor: los workers no guardan estado | ¿Qué proceso posee el estado y cómo se recupera? | Lo posee PostgreSQL. El coordinador solo tiene en memoria lo que reconstruye al arrancar: marca los intentos en curso como `attempt.lost` y los repite con otra generación. El worker sigue sin estado. |
| scheduling | El coordinador elige el worker; Ray solo lo respeta con afinidad estricta (`NodeAffinitySchedulingStrategy(soft=False)`) | ¿Hace falta scheduling dinámico? | No hace falta el de Ray. Ya decide el coordinador: un hilo que rota turnos entre torneos, usa el worker libre menos usado y no pasa de los huecos ni de `TM_MAX_RUNNING_ATTEMPTS`. En la Fase 2 basta con que cada worker pida trabajo cuando esté libre. |
| resultado remoto | ObjectRef: el coordinador lo guarda en memoria y lo recoge con `ray.wait` y `ray.get` sin bloquear | ¿Basta job ID + result store? | Sí. El job ID es el `attempt_id`, con su generación, y el almacén de resultados es PostgreSQL, que ya es la fuente de verdad. La aceptación transaccional decide qué resultado vale, llegue cuando llegue. |
| membresía | runtime Ray: los workers se registran con `ray start --address=tm-head:6379` y el coordinador lee `ray.nodes()` | ¿Bootstrap estático, registro, gossip, DHT, coordinador? | Coordinador con registro: cada worker conoce una dirección fija (arranque estático), se registra y envía latidos con un plazo corto. La membresía propia (`available`, `suspected`, `lost`) ya existe; cambia de dónde recibe los datos. |
| reintento | Política propia: Ray tiene `max_retries=0`; la plataforma reintenta con nueva generación (3 reintentos, 1 s de espera) | ¿Cuál es la semántica real de reintento? | Al menos una ejecución física y como mucho un resultado aceptado por partida. Solo se reintentan los fallos de infraestructura; un fallo del bot es derrota. El presupuesto cuenta las generaciones guardadas, así que reiniciar no lo repone. Lo tardío se descarta (`result.discarded`). |
| recursos | Recursos lógicos de Ray: `tm_worker: 1` por worker y `num_cpus=1` por tarea | ¿Qué restricciones importan realmente? | Una partida a la vez por worker (capacidad 1), un límite de partidas simultáneas y de torneos activos (429), y los límites de cada bot: memoria, salida y plazo por turno, que impone el árbitro, no Ray. |
| observabilidad | runtime Ray, con su panel desactivado (`--include-dashboard=false`); se usan logs JSON propios, eventos de dominio y `/workers` | ¿Qué evidencia debe exponer la versión nativa? | La misma que hoy, sin Ray: historial de eventos con secuencia por torneo, estado de cada worker en `/workers`, logs JSON con torneo, partida, intento y worker, y la evidencia que comprueba `tm verify` (I1–I6). |

## Lo que la Fase 1 añadió sobre Ray

Ray no cubrió estos casos, así que los resuelve la plataforma:

- **Worker congelado:** Ray no lo detecta. Lo cubren el plazo del intento
  (90 s) y el árbitro (`referee_stalled`).
- **Worker aislado:** Ray no lo detectó en 60 s. Actúa el plazo del intento y la
  generación descarta lo tardío.
- **Worker reiniciado:** Ray lista el nodo viejo y el nuevo durante 15–30 s. La
  sospecha solo castiga a la identidad que falló.
- **Pérdida del head:** se para toda la ejecución y la recuperación es manual. En
  la Fase 2 no hay head: el coordinador es el único punto de control.
