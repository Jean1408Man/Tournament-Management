# Semántica del contrato

Qué promete cada respuesta de la API, más allá de su forma.

## Cuándo algo es durable

- **201 al registrar un bot:** los bytes ya están guardados por su SHA-256 y el
  registro confirmado en PostgreSQL.
- **202 al crear un torneo:** el torneo y sus primeras partidas ya están
  confirmados. La ejecución empieza después; el 202 no espera a ninguna
  partida.
- **Un resultado aceptado** (`result.accepted`) está confirmado antes de que el
  coordinador acuse recibo al worker. Sobrevive a reinicios.
- **Una cancelación** (200) está confirmada antes de pedir que se paren los
  intentos; la parada física puede terminar después.

## Idempotencia

Registrar y crear exigen `Idempotency-Key`. Repetir la misma petición con la
misma clave devuelve el mismo recurso; la misma clave con otro contenido da 409.
Cada operación tiene su propio espacio de claves y no caducan. Un 429 no
consume la clave.

## Reintentos

- **Fallo deportivo** (jugada ilegal, plazo de turno vencido, salida inválida,
  bot que termina antes de tiempo…): es un resultado; el rival gana y no se
  reintenta.
- **Fallo de infraestructura** (worker caído o aislado, plazo del intento
  vencido, árbitro congelado, descarga del bot fallida): no es culpa de ningún
  bot; se emite otro intento con mayor generación, hasta 1 + `TM_MAX_RETRIES`
  (3) por partida. Los reinicios del coordinador no reponen ese presupuesto.
- Un resultado de un intento ya sustituido llega tarde y se descarta
  (`result.discarded`). Agotar los reintentos, o una partida sin ganador, deja
  el torneo `blocked`.

## Cancelación

Es segura de repetir. Cancelar un torneo terminado conserva su resultado. Tras
cancelar no se emiten intentos nuevos y cualquier resultado posterior se
descarta (`tournament_not_running`).

## Eventos

- Cada torneo tiene un historial durable de eventos numerados por `sequence`,
  que sigue el orden de confirmación sin huecos. El orden lo da siempre la
  `sequence`; `occurred_at` es informativo (el reloj puede saltar).
- `GET …/events` entrega el historial y los cambios posteriores (SSE). Se
  reanuda con `Last-Event-ID: torneo:secuencia` o `?after=N`.
- Entrega **al menos una vez**: tras reconectar puede repetirse el último
  evento; el cliente deduplica por `event_id`. Lo que el cliente ya recibió no
  desaparece del historial.
- El flujo se cierra cuando el torneo es terminal y no quedan eventos. Si falla
  la base a mitad, llega `stream.error` y el cliente reconecta desde su cursor.

## Sobrecarga

Con `TM_MAX_ACTIVE_TOURNAMENTS` (16) torneos en curso, crear otro da **429**
`overloaded` con `Retry-After` en segundos. No se guarda nada; repetir la
clave de un torneo ya admitido sigue dando 202.

## Errores

Siempre `{"error":{"code":"…","message":"…"}}`, sin SQL, credenciales ni
cuerpos subidos.

- 400: cursor mal formado o subida interrumpida.
- 404: recurso inexistente.
- 409: conflicto de idempotencia o de identidad, o cursor futuro.
- 413: bot demasiado grande (1 MiB por defecto).
- 415: tipo de contenido distinto de `application/octet-stream` al registrar.
- 422: campos, tipos o código del bot inválidos.
- 429: sobrecarga (ver arriba).
- 503: base, artefacto o ejecución no disponibles.
