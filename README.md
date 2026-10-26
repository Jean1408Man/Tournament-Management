# Tournament Management

Prototipo de una plataforma distribuida para ejecutar torneos computacionales.
La Fase 1 usa Ray para la infraestructura inicial. La Etapa 2 implementa Nim,
bots, árbitro, formatos de torneo y simulación local mediante un backend de
intentos. Las etapas 3.1–3.4 añaden persistencia transaccional, registro
durable de bots por hash, API local y recuperación al reiniciar. Consulta el
[informe consolidado de Fase 3](<docs/Fase 3 Reportes/PHASE3-REPORT.md>) y los
informes individuales de [3.1](<docs/Fase 3 Reportes/PHASE3_1-REPORT.md>),
[3.2](<docs/Fase 3 Reportes/PHASE3_2-REPORT.md>),
[3.3](<docs/Fase 3 Reportes/PHASE3_3-REPORT.md>) y
[3.4](<docs/Fase 3 Reportes/PHASE3_4-REPORT.md>).

## Versiones fijadas

El artefacto usa **Python 3.12** y **Ray 2.40.0**. Las dependencias de la
aplicación están fijadas en `pyproject.toml`; las herramientas de desarrollo
(`pytest` y `ruff`) pertenecen al extra `dev`. El bloqueo único del proyecto es
`uv.lock`, que debe generarse con uv y guardarse junto con el cambio de
dependencias.

## Preparación local

Instala uv y Python 3.12 por tu cuenta. Desde la raíz del repositorio, ejecuta:

```bash
uv python install 3.12
uv sync --locked --extra dev
```

Después, las comprobaciones de la etapa 1.1 son:

```bash
uv run tm --help
uv run tm --version
uv run python -c "import tournament_management; print(tournament_management.__version__)"
uv run pytest
uv run ruff check .
```

`uv.lock` ya contiene las dependencias resueltas. `--locked` comprueba que el
bloqueo coincide con el proyecto. Usar `uv lock` solo cuando se cambien las
dependencias y se quiera actualizar el archivo de bloqueo.

También se puede invocar el paquete sin instalar el ejecutable:

```bash
uv run python -m tournament_management --help
```

## Configuración

La configuración usa el prefijo `TM_` y precedencia argumentos > entorno >
valores por defecto. Los scripts Docker leen las opciones de despliegue de
`.env` como texto; las variables ya exportadas tienen prioridad. Copia
`.env.example` a `.env` y configura `TM_POSTGRES_PASSWORD` con letras y números.
Las demás opciones de la aplicación se exportan para ejecución local; `tm` no
lee `.env` directamente. No se debe versionar `.env`. Cada variable tiene opción equivalente:
`TM_HTTP_PORT` corresponde a `--http-port`, por ejemplo.

## Estado actual

Funcionan el diagnóstico, la API HTTP, Docker y la conexión Ray
Client de la Fase 1. La Etapa 2 añade árbitro, torneos y ejecución **local**
de partidas mediante intentos. La etapa 3.1 implementa creación durable de
torneos, intentos, aceptación atómica, avance del cuadro y eventos en PostgreSQL
a través de `PersistentTournamentService`. La etapa 3.2 permite registrar
archivos Python con `tm register-bot`, consultar su integridad y compartirlos
con los workers mediante un volumen interno de solo lectura.
La etapa 3.3 incorpora registro HTTP, creación idempotente, ejecución local
durable, consultas, cancelación y eventos SSE reanudables. La simulación CLI
sigue en memoria. La etapa 3.4 recupera la cola al arrancar, invalida intentos
anteriores y conserva el presupuesto de reintentos y las cancelaciones.
La ejecución distribuida de partidas con Ray sigue pendiente.

## Arranque y diagnóstico (paso 1.4)

```bash
uv run tm doctor
uv run tm serve --backend local --http-port 8000
curl http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/ready
```

- `doctor` muestra JSON con versiones y configuración efectiva; crea el
  directorio de datos si falta y comprueba escritura temporal sin dejar archivos.
  No inicia Ray ni comprueba un clúster. Falla con código 1 ante error de disco.
- `serve` inicia HTTP en localhost. `/health` devuelve 200 si el proceso vive.
  `/ready` devuelve 200 con disco escribible, PostgreSQL migrado y coordinador
  local operativo; devuelve 503 si falta alguna dependencia o se usa backend Ray.
- `serve --backend ray --ray-address ray://tm-head:10001` conecta al head por
  Ray Client antes de servir HTTP y cierra la conexión al parar. Requiere un
  head accesible; no inicia un clúster local automáticamente. Los roles head/worker
  usan el entrypoint Docker, no el comando serve.
- Ctrl+C o SIGTERM detienen el servidor con cierre de su ciclo de vida.
- `match` y `simulate` ejecutan las pruebas locales descritas más abajo;
  `verify` continúa pendiente.
- Logs de aplicación/Uvicorn: JSON por stderr, UTC y reloj monotónico local,
  campos de correlación nulos hasta disponer de una partida.
  Stdout se reserva para salidas de comandos.

Los límites de turnos/partidas/intentos y recursos se validan y se aplican en
el árbitro y el scheduler locales. Se exige turno <= partida < intento y
reintentos >= 0.
Son valores iniciales configurables, no resultados de mediciones.
`tm doctor --help` y `tm serve --help` enumeran las opciones.

La suite incluye arranque HTTP en un puerto local temporal y apagado SIGTERM;
requiere permiso para abrir sockets locales.

## Docker sin Compose (paso 1.5)

### PostgreSQL

PostgreSQL es el almacén transaccional del coordinador. `TM_DATABASE_URL` debe
usar `postgresql://` (o `postgres://`) y este comando aplica el esquema
idempotente incluido con la aplicación:

```bash
uv run tm migrate --database-url postgresql://USUARIO:CONTRASEÑA@HOST:5432/BASE
```

`TM_DATA_DIR` se conserva para artefactos y comprobaciones de disco; ya no debe
usarse como base de datos. Sin una base configurada, el servicio mínimo puede
arrancar para diagnóstico, pero no puede ejecutar torneos.

Desde la raíz, con Docker disponible y permiso para acceder al daemon:

```bash
docker pull postgres:16.6-bookworm
bash scripts/build-image.sh
bash scripts/start-cluster.sh
bash scripts/cluster-status.sh
```

El primer comando descarga la imagen de PostgreSQL y el segundo lo necesario
para la aplicación. Los otros exigen las imágenes locales y no hacen pull. Se
crean PostgreSQL, head, dos workers y coordinador en tm-net. Solo la API se
publica en localhost:8000. El coordinador monta el volumen tm-state y
PostgreSQL conserva sus datos en tm-postgres-data. Los bots se conservan en
tm-artifacts, con escritura para el coordinador y solo lectura para los workers. Define
`TM_POSTGRES_PASSWORD` en `.env` antes de arrancar. Una base existente conserva
su contraseña anterior; modificar `.env` no la cambia en PostgreSQL.
Los tres nodos Ray tienen límite de 2 GiB y el coordinador de 1 GiB;
prever al menos 8 GiB para Docker con margen para el sistema operativo.

`bash scripts/stop-cluster.sh` retira contenedores del proyecto conservando
el volumen y la red. No ejecutar hasta guardar logs que se necesiten.
La guía [deploy/OPERATIONS.md](deploy/OPERATIONS.md) contiene comandos Docker
completos, recursos, diagnóstico y prueba de persistencia tras recrear el clúster.
La ejecución de tareas por worker se verificará en 1.6.

## Simulación local de torneos (etapa 2.5)

```bash
uv run tm simulate --spec examples/elimination.json
uv run tm simulate --spec examples/round-robin.json
```

Eliminación directa avanza por rondas y concede byes sin jugar partidas.
Round-robin programa cada pareja una sola vez. La clasificación usa victorias
descendentes y, en caso de empate, el orden de inscripción de los participantes.
Los ejemplos usan bots del directorio `bots/` y guardan la posición inicial y
el primer jugador en la especificación. `simulate` imprime un informe JSON:
`completed` con campeón o clasificación, o `blocked` ante un resultado sin
ganador (código de salida 1). Esta ejecución es local y secuencial; la
ejecución pasa por `LocalExecution`, `Scheduler` y `ResultService`. Los errores
de infraestructura se reintentan con otro ID y generación; los resultados
tardíos del intento invalidado no pueden alterar el resultado aceptado.
La API descrita más abajo sí persiste los torneos; esta simulación sigue en
memoria. El juego distribuido con Ray sigue pendiente.

## Organización del código

El paso 1.6 incorpora `tm probe-cluster` y `bash scripts/probe-cluster.sh`:
envían una tarea a cada worker y verifican identidades, versiones y resultados.
Implementación entregada, pendiente de ejecutar las pruebas por el usuario.
La guía [deploy/STEP16-TESTS.md](deploy/STEP16-TESTS.md) contiene los comandos
locales, reconstrucción de imagen, prueba positiva y fallo controlado.
`.github/workflows/ci.yml` configura lint y pytest al publicar en GitHub.

El código está organizado por módulos de dominio (monolito modular). Cada
carpeta reúne la lógica, el SQL y las rutas HTTP de una parte del problema:

- `shared/`: mensajes versionados, estados, errores, contrato de la unidad de
  trabajo, logging y métricas.
- `game/`: reglas de Nim, protocolo nim-jsonl, árbitro, procesos y límites.
- `competitors/`: registro de bots, almacén inmutable por SHA-256, SQL y rutas.
- `tournaments/`: formatos, definición y cuadro, servicio durable (crear,
  emitir, aceptar, fallar, cancelar), consultas, SQL (`persistence/`) y rutas.
- `execution/`: puerto `ExecutionBackend`, coordinador, plazos, recuperación,
  cancelación física, backends (`backends/`) y ruta `/workers`.
- `events/`: eventos durables, su repositorio y SSE reanudable.
- `cluster/`: conexión, topología y diagnóstico del clúster (`ray_*.py` para Ray).
- `simulation/`: simulación en memoria de la etapa 2 (`tm simulate`).
- `database/`: conexión, migraciones SQL, idempotencia y unidad de trabajo.
- `api/`: infraestructura HTTP común (errores, dependencias, salud, rutas).
- Raíz: `app.py` (ciclo de vida HTTP), `runtime.py` (composición), `config.py`,
  `cli.py` y `commands.py`.

Todos los repositorios de una unidad de trabajo comparten la misma conexión y
transacción. Solo `cluster/ray_*.py` puede importar Ray; `game/`, los formatos y
`shared/` no dependen de infraestructura, y `tournaments/` nunca importa
`execution/`. `tests/unit/test_architecture.py` comprueba estas reglas
automáticamente, incluidas dependencias relativas.

`bots/`, `deploy/`, `scripts/`, `examples/` y `experiments/` contienen README
con sus siguientes pasos. Las pruebas de integración y punta a punta tienen
directorios reservados. La documentación detallada está en
`docs/architecture.md` y `docs/progress.md`; la configuración existente excluye
`docs/` de Git, por lo que esos documentos son locales por ahora.

## Contratos internos (paso 1.3)

`shared/messages.py` define mensajes inmutables con JSON versionado para partidas,
intentos, resultados, workers y eventos. `shared/states.py` valida transiciones
de torneo, partida e intento. `execution/backend.py` define el puerto de
ejecución y `shared/unit_of_work.py` la unidad de trabajo. El backend local ya está
implementado; la aceptación transaccional en PostgreSQL se incorpora en 3.1
y la API con ejecución local durable en 3.3.

Las pruebas cubren serialización, mensajes inválidos, fallos deportivos frente
a infraestructura y transiciones. Detalle local en `docs/internal-contracts.md`.

## Uso de IA

Pendiente: el formato de disclosure de IA exigido por el curso no fue incluido
en el material disponible. Antes de la entrega, añade aquí la declaración con
el formato oficial y conserva evidencia de las herramientas y revisiones usadas.

## Registro de bots (etapa 3.2)

Después de reconstruir la imagen y recrear los contenedores con los scripts:

```bash
docker cp /tmp/mi-bot.py tm-coordinator:/tmp/mi-bot.py
docker exec tm-coordinator tm register-bot --file /tmp/mi-bot.py --name "Mi bot" --competitor-id mi-bot --idempotency-key registro-mi-bot-v1
docker exec tm-coordinator tm inspect-bot --competitor-id mi-bot
```

Se acepta un archivo `.py` UTF-8 de hasta 1 MiB por defecto, con sintaxis Python
válida, protocolo `nim-jsonl` versión 1 y runtime `python3.12`. Se conserva el
contenido exacto por SHA-256, sin importar ni ejecutar el código al registrarlo.
`TM_ARTIFACT_MAX_BYTES` configura el límite; los scripts lo leen de `.env` o del
entorno y lo pasan a los contenedores. Cambiar el código requiere otro competidor;
la identidad registrada no se sobrescribe. Repetir clave y petición devuelve
el mismo registro; reutilizar la clave con otro contenido produce error.

Con el hash devuelto, `bash scripts/probe-artifact.sh HASH` comprueba los bytes
en ambos workers y exige que sus montajes sean de solo lectura. El volumen no
se publica por HTTP. Las rutas originales y los comandos del cliente no forman
parte del contrato de ejecución. `run_registered_match` verifica ambos hashes
y prepara copias privadas antes de llamar al árbitro. La API de 3.3 usa este
camino mediante el coordinador local; su ejecución en workers Ray sigue pendiente.

```bash
uv run pytest tests/unit/test_artifact_store.py tests/integration/test_bot_registry.py -q
```

La prueba usa archivos y PostgreSQL reales. El informe de implementación contiene
los comandos para comprobar también la conservación tras recrear el clúster.

## API operativa (etapa 3.3)

Para ejecución sin Docker, exporta `TM_DATABASE_URL` apuntando a PostgreSQL y:

```bash
uv run tm migrate
uv run tm serve --backend local
```

En Docker, los scripts usan `TM_BACKEND=local` por defecto. Reconstruye la imagen
antes de recrear los contenedores para incorporar el código nuevo.
Consulta `/docs`, `/api/v1/openapi.json` y el [contrato versionado](contracts/README.md).
Los [ejemplos HTTP](examples/api/README.md) contienen un recorrido reproducible.

`POST /api/v1/competitors` recibe el archivo como cuerpo binario y los metadatos
como parámetros de consulta. Registro y creación de torneos requieren
`Idempotency-Key`. Crear un torneo responde 202 después del commit; consulta
su URL de estado para conocer el resultado. SSE publica eventos confirmados
con ID `torneo:secuencia`, reanudables mediante `Last-Event-ID`.

```bash
uv run pytest tests/integration/api tests/unit/test_api_contract.py -q
uv run python scripts/export-openapi.py
```

La cola local ejecuta un intento a la vez. Usa un solo proceso coordinador.
Desde 3.4 el arranque reconstruye el trabajo pendiente desde PostgreSQL.
El backend Ray aún no admite crear torneos.

## Recuperación y cancelación (etapa 3.4)

Con backend local, el arranque recupera los torneos running con definición
persistida antes de atender HTTP. Los intentos que quedaron activos pasan a
lost y sus reemplazos reciben otro ID y una generación mayor. El historial
consume el presupuesto de `TM_MAX_RETRIES`; reiniciar no lo pone a cero.
Si no queda presupuesto, el torneo queda blocked sin adjudicar ganador.

Los resultados confirmados se conservan y los torneos completed, blocked o
cancelled no se reactivan. Cancelar confirma primero PostgreSQL; la detención
física es de mejor esfuerzo y un fallo al solicitarla no revoca la cancelación.
Los resultados tardíos siguen sujetos a la validación de vigencia.

```bash
uv run pytest tests/integration/recovery tests/integration/api/test_recovery.py tests/integration/api/test_restart_process.py tests/unit/test_coordinator_lifecycle.py -q
```

Las pruebas incluyen cortes SIGKILL de procesos HTTP reales antes y después del
commit y reutilizan la misma base y directorio. No requieren Docker ni Ray.
La guía [de operaciones](deploy/OPERATIONS.md) explica el reinicio manual.
Se mantiene el requisito de un solo coordinador activo por base; no hay elección
de líder ni exclusión entre instancias. La distribución por red de artefactos
permanece en 4.2, según el plan original.
