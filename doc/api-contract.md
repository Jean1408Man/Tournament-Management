# Contrato HTTP (v1, versión 1.1.0)

La API pública vive bajo `/api/v1`. Su forma exacta está en
[`contracts/openapi-v1.json`](../contracts/openapi-v1.json), que una prueba
(`tests/unit/test_api_contract.py`) compara con la aplicación; su significado
(durabilidad, reintentos, eventos, errores) está en [semantics.md](semantics.md).
La propia aplicación lo publica en `/api/v1/openapi.json` y `/docs`.

El código de cada ruta está en `src/tournament_management/`: `api/routes.py`
monta todas bajo `/api/v1`; `competitors/api.py`, `tournaments/api.py`,
`events/api.py` y `execution/api.py` declaran las suyas; `api/health.py` las de
salud.

## Endpoints

### `POST /api/v1/competitors` — registrar un bot

- **Petición:** los bytes exactos del `.py` en el cuerpo, con
  `Content-Type: application/octet-stream` (no multipart). En la consulta van
  `name`, `filename` y, opcionalmente, `competitor_id`. Exige
  `Idempotency-Key`.
- **Qué hace:**
  1. Lee el archivo controlando el tamaño (1 MiB por defecto,
     `TM_ARTIFACT_MAX_BYTES`), tanto en `Content-Length` como en lo leído.
  2. Comprueba que es UTF-8 y Python válido, sin ejecutarlo.
  3. Guarda los bytes por su SHA-256 y registra el bot con el protocolo
     `nim-jsonl` v1 y el runtime `python3.12`.
  4. Todo queda en PostgreSQL antes de responder.
- **Respuesta 201:** `competitor_id`, `name` y `artifact` (`artifact_hash`,
  `size_bytes`, `protocol`, `protocol_version`, `runtime`), con `Location`.
- **Repetir:** la misma clave con el mismo contenido devuelve el mismo bot; con
  otro contenido, 409.
- **Errores:**
  - 413: el archivo es demasiado grande;
  - 415: el tipo de contenido no es `application/octet-stream`;
  - 422: código inválido o campos mal formados;
  - 409: conflicto de identidad o de clave;
  - 400: subida interrumpida.

### `GET /api/v1/competitors/{competitor_id}` — consultar un bot

- **Qué hace:** devuelve el bot registrado y comprueba que sus bytes siguen
  disponibles en el almacén.
- **Respuesta 200:** los mismos campos que el registro.
- **Errores:** 404 si no existe o sus bytes no están disponibles.

### `POST /api/v1/tournaments` — crear un torneo

- **Petición (JSON):**
  - `format`: `elimination` o `round_robin`;
  - `competitor_ids`: de 2 a 64, distintos y ya registrados;
  - opcionales: `initial_piles` (de 1 a 64 montones de 1 a 10000; por defecto
    `[3,5,7]`), `first_player` (0 o 1; por defecto 0), `seed` (de 0 a 2^63−1;
    por defecto 42) y `tournament_id`.

  Exige `Idempotency-Key`. Los campos de más y las conversiones implícitas de
  tipos se rechazan.
- **Qué hace:**
  1. Resuelve el hash de cada bot desde el registro; el cliente no envía código.
  2. Guarda el torneo, sus participantes y las partidas ya listas para jugar.
  3. Responde después del commit.
  4. El coordinador empieza a jugarlo en cuanto hay huecos libres.
- **Respuesta 202:** `tournament_id`, `status_url` y `events_url`, con
  `Location`. El 202 significa que el torneo ya es durable, no que esté jugado.
- **429 `overloaded`:** si ya hay `TM_MAX_ACTIVE_TOURNAMENTS` torneos en curso.
  Lleva `Retry-After` en segundos, no guarda nada ni consume la clave. Repetir
  la clave de un torneo ya admitido sigue dando 202.
- **Errores:** 404 si un bot no existe; 409 si la clave o el `tournament_id` ya
  tienen otro contenido; 422 si los campos son inválidos.

### `GET /api/v1/tournaments/{tournament_id}` — estado de un torneo

- **Qué hace:** lee el torneo de PostgreSQL y calcula su situación.
- **Respuesta 200:**
  - `state`: `created`, `running`, `completed`, `blocked` o `cancelled`;
  - `format` y `competitor_ids`;
  - `statistics`: total de partidas e intentos, y partidas por estado
    (`waiting`, `ready`, `running`, `completed`, `blocked`, `cancelled`);
  - `bracket` (eliminación): las rondas ya creadas, con pareja, partida,
    participantes, estado y ganador; los byes no son partidas;
  - `champion`: el ganador de la eliminación, cuando termina;
  - `standings` (round-robin): victorias de cada participante, desempatadas por
    orden de inscripción.
- **Errores:** 404 si no existe.

### `GET /api/v1/tournaments/{tournament_id}/matches` — partidas de un torneo

- **Petición:** `offset` (desde 0) y `limit` (de 1 a 100; 50 por defecto).
- **Qué hace:** devuelve las partidas, cada una con todos sus intentos.
- **Respuesta 200:** `items`, `total`, `offset` y `limit`. Cada partida lleva:
  - `match_id`, `state` y `winner_id`;
  - `spec`: participantes, hashes de los bots, montones, primer jugador y
    semilla;
  - `current_attempt_id` y `accepted_attempt_id`;
  - `attempts`: cada uno con su identificador, generación, estado y `outcome`.
    `outcome` lleva `kind` (`completed`, `bot_failure`,
    `infrastructure_failure` o `no_winner`), ganador, worker y motivo.
- **Errores:** 404 si el torneo no existe.

### `POST /api/v1/tournaments/{tournament_id}/cancel` — cancelar un torneo

- **Qué hace:**
  1. Confirma primero la cancelación en PostgreSQL: lo pendiente pasa a
     `cancelled`.
  2. Después pide parar los intentos en curso.
  3. Desde ese momento no se lanza ni se acepta nada; un resultado que llegue
     tarde queda `result.discarded`.
- **Respuesta 200:** `tournament_id` y `state`.
- **Repetir:** cancelar otra vez es seguro. Un torneo ya terminado conserva su
  resultado. El 200 no garantiza que el proceso del bot ya haya parado.
- **Errores:** 404 si no existe.

### `GET /api/v1/tournaments/{tournament_id}/events` — eventos en vivo

- **Qué hace:** abre un flujo `text/event-stream` con el historial ya
  confirmado y, después, cada evento nuevo.
- **Formato:** cada evento tiene un ID `torneo:secuencia`, `event` con su tipo
  (`attempt.issued`, `result.accepted`…) y `data` con su JSON.
- **Reanudar:** con `Last-Event-ID: torneo:secuencia` (o `?after=N` en una
  conexión manual) se entregan solo los posteriores. No se pueden combinar.
- **Ritmo:** envía un comentario de mantenimiento cada 15 s y `retry: 1000`.
  Cierra el flujo cuando el torneo ha terminado y ya entregó todo su historial.
- **Fallos:** si el almacenamiento falla a mitad, envía `event: stream.error` y
  cierra; el cliente reconecta con su último ID. Puede repetirse el último
  evento: el consumidor debe tolerarlo.
- **Errores:** 400 si el cursor es de otro torneo o está mal formado; 409 si es
  posterior al último confirmado; 404 si el torneo no existe.

### `GET /api/v1/workers` — workers del clúster

- **Qué hace:** devuelve los workers que ve el coordinador, según su membresía
  propia. Nunca expone identificadores internos de Ray.
- **Respuesta 200:** por worker:
  - `worker_id`: el nombre del contenedor o proceso;
  - `status`: `available`, `suspected` o `lost`;
  - `capacity`: cuántas partidas puede jugar a la vez;
  - `running_attempts`: cuántas juega ahora.

### `GET /health` y `GET /ready` (fuera de `/api/v1`)

- **`/health`:** 200 mientras el proceso vive, con su versión.
- **`/ready`:** comprueba que el disco es escribible, que PostgreSQL tiene las
  migraciones y que el coordinador está operativo. Da 200 si todo está bien y
  503 si falla algo, con el detalle en `checks`. Con Ray, la falta de workers no
  lo cambia: se ve en `/workers`.

## Reglas comunes

- JSON en peticiones y respuestas, salvo la subida del bot y el flujo de eventos.
- Identificadores públicos: 1–100 caracteres ASCII; el primero alfanumérico y el
  resto alfanuméricos, guion o guion bajo.
- `Idempotency-Key`: 1–128 caracteres ASCII; el primero alfanumérico y el resto
  alfanuméricos o `._:-`. Registro y creación tienen espacios de claves
  separados; las claves no caducan.
- Los campos desconocidos y las conversiones implícitas de tipos se rechazan (422).
- Errores con la forma `{"error":{"code":"…","message":"…"}}`; los códigos y su
  significado están en [semantics.md](semantics.md#errores).
- 503: la base, el almacén de bots o la ejecución no están disponibles.

`/internal/artifacts/{hash}`, que usan los workers para descargar los bots con
un token, no forma parte del contrato.
