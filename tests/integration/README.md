# Integración

Pruebas con procesos, PostgreSQL y adaptadores reales; incorporarlas al implementar
cada componente. Las pruebas Ray deberán declarar sus requisitos de clúster.

## PostgreSQL (etapa 3.1)

`uv run pytest tests/integration/test_postgres_persistence.py -q` verifica
transacciones, carreras, avance deportivo y migraciones contra una base real.

La fixture crea un servidor temporal mediante los binarios PostgreSQL locales,
solo con socket Unix privado. Requiere usuario sin privilegios y permiso para
abrir sockets; si faltan los binarios o se ejecuta como root, explica la omisión.
También admite `TM_TEST_DATABASE_URL` para una BD de pruebas con permiso CREATE
SCHEMA. Cada prueba crea y elimina su propio esquema. No usa `TM_DATABASE_URL`
ni las credenciales de desarrollo. CI proporciona PostgreSQL 16 explícitamente.

El [informe de 3.1](<../../docs/Fase 3 Reportes/PHASE3_1-REPORT.md>) explica las garantías y los límites.

## Registro de bots (etapa 3.2)

`uv run pytest tests/integration/test_bot_registry.py -q` comprueba el registro
con PostgreSQL y archivos reales: recuperación desde otra instancia y desde otro
proceso CLI, deduplicación por hash, idempotencia, carreras de identidad y fallo
de publicación/commit. Se aplican los mismos requisitos PostgreSQL anteriores.
Las pruebas de despliegue usan dobles de Docker para comprobar los montajes;
la comprobación manual del clúster real es `bash scripts/probe-artifact.sh HASH`.

## API durable (etapa 3.3)

`uv run pytest tests/integration/api -q` levanta Uvicorn en un puerto temporal
con PostgreSQL real y almacenamiento temporal. El cliente usa HTTP estándar;
no se sustituye el transporte por llamadas directas a las rutas. Requiere los
mismos permisos para sockets y PostgreSQL indicados arriba.

Los archivos separan pruebas de torneos, subidas, eventos y ejecución. Cubren
ambos formatos, idempotencia y creación concurrente, límites de entrada,
subidas interrumpidas, integridad, consultas paginadas, reanudación SSE y más
de una página de eventos, cancelación, reintentos, timeout y fallo al confirmar
el resultado. Los fallos controlados usan ejecutores instrumentados para
observar el orden entre commit, acuse y cancelación.

`uv run pytest tests/unit/test_api_contract.py -q` comprueba que el OpenAPI
publicado coincide con `contracts/openapi-v1.json`, sin requerir PostgreSQL.

## Recuperación (etapa 3.4)

`uv run pytest tests/integration/recovery -q` comprueba pérdida de intentos,
presupuesto que no se reinicia, conservación de resultados y próximos cruces,
estados terminales, cancelación entre enumeración/bloqueo y rollback durante
la recuperación, incluso con un torneo anterior ya confirmado.

`uv run pytest tests/integration/api/test_restart_process.py -q` arranca un
servidor en un proceso independiente con bots y HTTP reales. Una pausa de
prueba señala antes/después de aceptar un resultado; el test envía SIGKILL y
arranca otra API con la misma base y archivos. Cubre ambos formatos, resultados
ya observados, eventos reanudados y cancelaciones que sobreviven al corte.
`test_recovery.py` comprueba cierre limpio, cola aún no iniciada, presupuesto
agotado y fallo de detención física después de cancelar durablemente.

El cliente HTTP compartido se separa en api/client.py y el servidor
instrumentado está en api/recovery_server.py; ese código solo pertenece a
pruebas. No se exponen endpoints de fallo en producción ni se detienen procesos
externos a la suite. Se aplican los requisitos PostgreSQL/socket anteriores.
