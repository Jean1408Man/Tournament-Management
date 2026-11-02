# Contrato HTTP (v1, versión 1.1.0)

La API pública vive bajo `/api/v1`. Su forma exacta está en
[`contracts/openapi-v1.json`](../contracts/openapi-v1.json), que una prueba
(`tests/unit/test_api_contract.py`) compara con la aplicación; su significado
(durabilidad, reintentos, eventos, errores) está en [semantics.md](semantics.md).
La propia aplicación lo publica en `/api/v1/openapi.json` y `/docs`.

## Recursos

- `POST /competitors` → 201. Registra un bot: los bytes del `.py` van en el cuerpo
  (`application/octet-stream`) y `name`, `filename` y opcionalmente
  `competitor_id` en la consulta. Exige `Idempotency-Key`.
- `GET /competitors/{id}` → 200. Datos del bot registrado y su SHA-256.
- `POST /tournaments` → 202. Crea un torneo: `format` (`elimination` o
  `round_robin`), `competitor_ids` (2–64, distintos) y opcionalmente
  `initial_piles`, `first_player`, `seed` y `tournament_id`. Exige
  `Idempotency-Key`. Puede responder 429 si hay demasiados torneos en curso.
- `GET /tournaments/{id}` → 200. Estado (`running`, `completed`, `blocked`,
  `cancelled`), estadísticas, cuadro (eliminación) o clasificación (round-robin).
- `GET /tournaments/{id}/matches` → 200. Partidas con sus intentos y resultados,
  paginadas (`offset`, `limit` de 1 a 100).
- `POST /tournaments/{id}/cancel` → 200. Cancela lo que queda; repetirlo es seguro.
- `GET /tournaments/{id}/events` → 200 `text/event-stream`. Historial confirmado y
  cambios en vivo; se reanuda con `Last-Event-ID` o `?after=N`.
- `GET /workers` → 200. Workers que ve el coordinador, con estado y ocupación.

Fuera de `/api/v1`: `/health` (el proceso vive) y `/ready` (disco, PostgreSQL
migrado y ejecución operativos; si no, 503).

## Reglas comunes

- JSON en peticiones y respuestas, salvo la subida del bot y el flujo de eventos.
- Identificadores públicos: 1–100 caracteres ASCII; el primero alfanumérico y el
  resto alfanuméricos, guion o guion bajo.
- `Idempotency-Key`: 1–128 caracteres ASCII; el primero alfanumérico y el resto
  alfanuméricos o `._:-`.
- Los campos desconocidos y las conversiones implícitas de tipos se rechazan (422).
- Errores con la forma `{"error":{"code":"…","message":"…"}}`; los códigos y su
  significado están en [semantics.md](semantics.md#errores).
