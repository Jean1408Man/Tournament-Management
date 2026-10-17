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

Copia `.env.example` a `.env` y ajusta sus valores cuando se implemente el
coordinador. Las variables usarán el prefijo `TM_`; el orden previsto será
argumentos de línea de comandos, variables de entorno y valores por defecto.
No se debe versionar `.env`.

## Estado actual

Esta entrega deja un paquete Python instalable y el ejecutable `tm`. Los
subcomandos del coordinador, la API, Ray, Docker y los formatos de torneo se
implementarán en las etapas posteriores; no se presentan como funcionales.

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

## Uso de IA

Pendiente: el formato de disclosure de IA exigido por el curso no fue incluido
en el material disponible. Antes de la entrega, añade aquí la declaración con
el formato oficial y conserva evidencia de las herramientas y revisiones usadas.
