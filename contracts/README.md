# Contrato HTTP v1 — etapas 3.3–3.4

Contrato de máquina: [openapi-v1.json](openapi-v1.json), versión 1.1.0 (1.1.0 añade la respuesta 429 de 5.1; ampliación compatible).
La aplicación publica el mismo documento en `/api/v1/openapi.json` y su interfaz
en `/docs`. Regenerar con `uv run python scripts/export-openapi.py` y verificar
con `uv run pytest tests/unit/test_api_contract.py -q`. No requiere servidor ni BD.

## Recursos

Todas las rutas de esta tabla usan el prefijo `/api/v1`.

| Método y ruta | Respuesta y propósito |
| --- | --- |
| POST /competitors | 201; registra un archivo y devuelve identidad y metadatos de su hash |
| GET /competitors/{id} | 200; consulta registro y verifica bytes disponibles |
| POST /tournaments | 202; confirma creación durable y devuelve URLs de estado/eventos |
| GET /tournaments/{id} | 200; estado, formato, participantes, estadísticas, cuadro y clasificación |
| GET /tournaments/{id}/matches | 200; partidas, especificaciones, intentos y resultados |
| POST /tournaments/{id}/cancel | 200; cancela el trabajo aún aceptable y devuelve estado |
| GET /tournaments/{id}/events | 200 text/event-stream; historial confirmado y cambios posteriores |
| GET /workers | 200; identidades y capacidad observadas por el backend configurado |

`/health` indica vida del proceso. `/ready` comprueba disco escribible,
PostgreSQL con las tres migraciones y coordinador local operativo. Devuelve
503 cuando alguna condición no se cumple; con backend Ray la ejecución aún
figura como no implementada.

## Registro e idempotencia

Subir los bytes exactos del `.py` con `Content-Type: application/octet-stream`.
Enviar `name`, `filename` y opcionalmente `competitor_id` en la consulta;
no se usa multipart. Límite predeterminado: 1 MiB, configurable mediante
`TM_ARTIFACT_MAX_BYTES`. Se controla tanto Content-Length como el tamaño leído,
incluso con transferencia por fragmentos. No se confirma una subida parcial.
Se valida UTF-8 y sintaxis sin importar el código, con protocolo nim-jsonl v1
y runtime python3.12 fijos. Los bytes se conservan por SHA-256.

Registro y creación exigen `Idempotency-Key`: 1–128 caracteres ASCII, primero
alfanumérico, resto alfanumérico o `._:-`. La clave se conserva en PostgreSQL
junto a la identidad de la petición. Repetir la misma operación y clave
recupera el recurso; cambiar el contenido con la misma clave produce 409.
Registro y creación tienen sus propios espacios de claves. No hay caducidad
ni purga automática. `Location` identifica el recurso en respuestas 201/202.

La creación admite `format` elimination/round_robin, `competitor_ids` distintos
(2–64), `initial_piles` (1–64 montones de 1–10000), `first_player` (0/1), `seed`
(0–2^63−1) y opcionalmente `tournament_id`. Los campos extra y las conversiones
implícitas de tipos se rechazan. Valores por defecto: [3,5,7], 0 y 42.
Los IDs públicos admiten 1–100 caracteres ASCII: primero alfanumérico, resto
alfanumérico, guion o guion bajo. Se resuelven los hashes desde el registro;
el cliente no envía comandos ni sustituye artefactos al crear el torneo.

202 significa que el commit ya ocurrió. La ejecución avanza después en una
cola local FIFO, con un intento a la vez. La consulta de estado puede mostrar
`running`, `completed`, `blocked` o `cancelled`. En eliminación, `bracket`
contiene las rondas materializadas con sus parejas; los byes no son partidas.
En round-robin, `standings` contiene las victorias y el desempate por orden
de inscripción. `statistics` resume partidas e intentos. La consulta de
partidas usa `offset` >= 0 y `limit` entre 1 y 100 (50 por defecto).

Cancelar confirma primero el estado en PostgreSQL y luego solicita la parada
física. Un resultado posterior no puede cambiar lo ya cancelado. Repetir la
cancelación es seguro; cancelar un torneo finalizado conserva su resultado.
Los fallos deportivos con ganador se aceptan; los de infraestructura pueden
reintentarse con nueva generación hasta el límite. Un resultado sin ganador
o el agotamiento de reintentos deja el torneo bloqueado.

## Eventos y reconexión

Cada evento confirmado tiene ID `torneo:secuencia`, `event` con el tipo de
dominio y `data` con su JSON versionado. El cliente guarda el ID después de
procesarlo y reabre con `Last-Event-ID: torneo:secuencia`; se entregan solo los
posteriores. Como alternativa para una conexión manual se admite `?after=N`.
No combinar un after distinto de cero con Last-Event-ID. Un cursor de otro
torneo o mal formado produce 400; uno posterior al último confirmado, 409.

El servidor relee PostgreSQL en páginas de 100, sin mantener transacciones
abiertas durante la espera. Envía comentarios de mantenimiento a los 15 s y
`retry: 1000`. Cierra el stream al agotar el historial de un estado terminal.
Una caída del almacenamiento después de iniciar el stream produce
`event: stream.error` sin ID y cierre; el cliente puede reconectar con su
último ID procesado. No existe promesa de entrega exactamente una vez:
el consumidor debe tolerar repetir su último evento al recuperarse.

## Errores y límites operativos

Los errores de validación, negocio y disponibilidad siguen la estructura
`{"error":{"code":"...","message":"..."}}`, sin SQL, credenciales ni cuerpos
subidos. Los detalles precisos de campos y tipos están en OpenAPI.

| HTTP | Significado |
| --- | --- |
| 400 | Cursor ambiguo/mal formado o subida interrumpida |
| 404 | Recurso inexistente o registro no disponible |
| 409 | Conflicto de identidad, idempotencia o cursor futuro |
| 413 | Archivo demasiado grande |
| 415 | Tipo de contenido distinto de application/octet-stream |
| 422 | Campos, tipos o código fuente inválidos |
| 429 | `POST /tournaments` con `TM_MAX_ACTIVE_TOURNAMENTS` torneos en curso (code `overloaded`). Lleva `Retry-After` (s); no guarda nada ni consume la `Idempotency-Key`. Repetir la clave de un torneo ya admitido sigue dando 202 |
| 503 | Base, artefacto o ejecución no disponibles |

Usar un solo proceso coordinador activo por base. Desde 3.4 el arranque
reconstruye la cola antes de atender HTTP. La confirmación de resultados
precede al acuse al backend; si falla la persistencia, el coordinador deja de
admitir nuevas creaciones y /ready responde 503. El backend local expone su
worker propio; el backend Ray aún devuelve una lista vacía y no admite crear
torneos. No se confunde membresía Ray con capacidad de ejecutar partidas.

Recorrido completo: [ejemplos HTTP](../examples/api/README.md).

## Recuperación al arrancar (3.4)

El arranque local enumera los torneos running con definición durable, en orden
de creación e ID, y toma brevemente el bloqueo de cada torneo. Marca como lost
los intentos activos de la ejecución anterior y libera su referencia vigente.
Registra `attempt.lost` con detalle `coordinator_restart` en esa misma transacción.
Los resultados, cuadros, clasificaciones e IDs ya confirmados no se recrean.
No se modifica el contrato de creación ni se exige un endpoint de recuperación.

Los reemplazos usan otro ID y MAX(generation)+1. El máximo total por partida es
1 + TM_MAX_RETRIES del nuevo arranque, contando generaciones ya persistidas:
reinicios consecutivos no reponen ese presupuesto. Un intento emitido cuenta
aunque el corte ocurriera antes de enviarlo. Agotar el presupuesto deja blocked
y añade `tournament.blocked` con detalle `retry_budget_exhausted_on_recovery`.
Una bajada de TM_MAX_RETRIES puede bloquear trabajo antes reintentable.

completed, cancelled y blocked se conservan. Las filas antiguas sin definición
no se reconstruyen inventando un torneo. Recuperar un torneo es transaccional;
si falla, el arranque no atiende HTTP. Otro arranque puede repetir lo ya
confirmado sin duplicar eventos de pérdida. SSE conserva los IDs confirmados
y permite reanudar desde el cursor que el cliente guardó antes del corte.

Un resultado que se confirmó justo antes de detenerse permanece aceptado, aunque
faltara el acuse al backend. Uno no confirmado puede ejecutarse de nuevo. Se
promete un único resultado deportivo aceptado por partida, no una única ejecución
física. No hay elección de líder ni exclusión de otros coordinadores activos:
esta recuperación presupone que la instancia anterior ya se detuvo.

La cancelación se confirma antes de solicitar parar el intento. Si esa solicitud
falla se registra el error, pero la operación HTTP conserva el estado cancelado.
La petición de parada no garantiza que el proceso ya terminó al responder 200.
