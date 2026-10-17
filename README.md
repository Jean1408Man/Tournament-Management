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
valores por defecto. `.env.example` documenta todas las opciones; copiarlo a
`.env` no las activa: hay que exportarlas en la terminal. No se carga .env
automáticamente ni se debe versionar. Cada variable tiene opción equivalente:
`TM_HTTP_PORT` corresponde a `--http-port`, por ejemplo.

## Estado actual

Funciona el diagnóstico y el arranque HTTP mínimo del paso 1.4. El árbitro,
los torneos, persistencia y ejecución Ray siguen pendientes.

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
- `serve --backend ray` selecciona configuración futura; no conecta a Ray y
  tampoco anuncia disponibilidad. Los roles head/worker no pueden usar serve;
  tendrán entradas propias en 1.5.
- Ctrl+C o SIGTERM detienen el servidor con cierre de su ciclo de vida.
- `probe-cluster`, `match`, `simulate` y `verify` aparecen en ayuda como
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

## Organización del código

El paso 1.2 incorpora la estructura del código:

- `domain/`: reglas y formatos deportivos puros.
- `application/`: casos de uso y puertos de ejecución/persistencia.
- `referee/`: protocolo, procesos de bots y límites.
- `adapters/`: implementaciones local, Ray, SQLite y artefactos.
- `api/`: HTTP, esquemas y eventos.
- `observability/`: logs y métricas.
- `app.py`: composición de componentes; `config.py`: configuración.

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
