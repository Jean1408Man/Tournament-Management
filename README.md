# Tournament Management

Prototipo de una plataforma distribuida para ejecutar torneos computacionales.
La Fase 1 usa Ray; las reglas de torneo y los servicios aún se incorporarán en
las etapas siguientes de la planificación.

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

Funciona el diagnóstico y el arranque HTTP mínimo del paso 1.4. El paso 1.5
incorpora Docker y conexión Ray Client, validados con dos recreaciones reales y
persistencia de archivos en volumen. El árbitro, los torneos, persistencia de
resultados y ejecución de partidas siguen pendientes.

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
  `/ready` devuelve 503 porque aún no se ejecutan torneos, incluso con disco
  disponible. Su JSON explica las dependencias pendientes.
- `serve --backend ray --ray-address ray://tm-head:10001` conecta al head por
  Ray Client antes de servir HTTP y cierra la conexión al parar. Requiere un
  head accesible; no inicia un clúster local automáticamente. Los roles head/worker
  usan el entrypoint Docker, no el comando serve.
- Ctrl+C o SIGTERM detienen el servidor con cierre de su ciclo de vida.
- `match`, `simulate` y `verify` aparecen en ayuda como
  pendientes y terminan con código 2 si se intentan usar.
- Logs de aplicación/Uvicorn: JSON por stderr, UTC y reloj monotónico local,
  campos de correlación nulos hasta disponer de una partida.
  Stdout se reserva para salidas de comandos.

Los límites de turnos/partidas/intentos y recursos se validan, pero se aplicarán
con árbitro y scheduler. Se exige turno <= partida < intento y reintentos >= 0.
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
PostgreSQL conserva sus datos en tm-postgres-data. Define
`TM_POSTGRES_PASSWORD` en `.env` antes de arrancar. Una base existente conserva
su contraseña anterior; modificar `.env` no la cambia en PostgreSQL.
Los tres nodos Ray tienen límite de 2 GiB y el coordinador de 1 GiB;
prever al menos 8 GiB para Docker con margen para el sistema operativo.

`bash scripts/stop-cluster.sh` retira contenedores del proyecto conservando
el volumen y la red. No ejecutar hasta guardar logs que se necesiten.
La guía [deploy/OPERATIONS.md](deploy/OPERATIONS.md) contiene comandos Docker
completos, recursos, diagnóstico y prueba de persistencia tras recrear el clúster.
La ejecución de tareas por worker se verificará en 1.6.

## Simulación local de torneos (etapa 2.4)

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
integración con el backend de intentos corresponde a la etapa 2.5.

## Organización del código

El paso 1.6 incorpora `tm probe-cluster` y `bash scripts/probe-cluster.sh`:
envían una tarea a cada worker y verifican identidades, versiones y resultados.
Implementación entregada, pendiente de ejecutar las pruebas por el usuario.
La guía [deploy/STEP16-TESTS.md](deploy/STEP16-TESTS.md) contiene los comandos
locales, reconstrucción de imagen, prueba positiva y fallo controlado.
`.github/workflows/ci.yml` configura lint y pytest al publicar en GitHub.

El paso 1.2 incorpora la estructura del código:

- `domain/`: reglas y formatos deportivos puros.
- `application/`: casos de uso y puertos de ejecución/persistencia.
- `referee/`: protocolo, procesos de bots y límites.
- `adapters/`: implementaciones local, Ray, PostgreSQL y artefactos.
- `api/`: HTTP, esquemas y eventos.
- `observability/`: logs y métricas.
- `app.py`: composición de componentes; `config.py`: configuración;
  `cli.py`: argumentos y despacho; `commands.py`: ejecución de comandos.

La persistencia PostgreSQL separa la conexión y migración (`postgres_connection.py`),
la unidad de trabajo (`postgres_store.py`) y los repositorios de partidas,
torneos y eventos (`postgres_matches.py`, `postgres_tournaments.py` y
`postgres_events.py`). Todos los repositorios de una unidad de trabajo comparten
la misma conexión y transacción.

Los módulos nuevos documentan responsabilidades y aún no implementan negocio.
Solo los dos adaptadores Ray pueden importar Ray; aplicación y dominio no
dependen de adaptadores. `tests/unit/test_architecture.py` comprueba estas
reglas automáticamente, incluidas dependencias relativas.

`bots/`, `deploy/`, `scripts/`, `examples/` y `experiments/` contienen README
con sus siguientes pasos. Las pruebas de integración y punta a punta tienen
directorios reservados. La documentación detallada está en
`docs/architecture.md` y `docs/progress.md`; la configuración existente excluye
`docs/` de Git, por lo que esos documentos son locales por ahora.

## Contratos internos (paso 1.3)

`domain/messages.py` define mensajes inmutables con JSON versionado para partidas,
intentos, resultados, workers y eventos. `domain/states.py` valida transiciones
de torneo, partida e intento. `application/ports.py` define los puertos de
ejecución, repositorios y unidad de trabajo para aceptar resultados y avanzar
torneos en una transacción. Los adaptadores aún no están implementados.

Las pruebas cubren serialización, mensajes inválidos, fallos deportivos frente
a infraestructura y transiciones. Detalle local en `docs/internal-contracts.md`.

## Uso de IA

Pendiente: el formato de disclosure de IA exigido por el curso no fue incluido
en el material disponible. Antes de la entrega, añade aquí la declaración con
el formato oficial y conserva evidencia de las herramientas y revisiones usadas.
