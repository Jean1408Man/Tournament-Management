# Tournament Management

Plataforma distribuida para ejecutar torneos de bots de Nim. Un coordinador
guarda el estado en su PostgreSQL local y reparte las partidas entre varios workers; en
esta Fase 1 los workers se coordinan con Ray. Si un worker falla, la partida se
repite en otro sin perder ni duplicar resultados.

Este README es el camino completo para alguien nuevo: arrancar el clúster,
abrir el panel web y jugar un torneo.

## 1. Requisitos y preparación

- **Linux (primera opción):** Docker Engine en marcha, al menos 8 GB de memoria
  disponibles para Docker, Git, Bash, `curl`, Python 3 y GNU `timeout`.
- **macOS:** Docker Desktop con al menos 8 GB asignados, Git, Bash, `curl`,
  Python 3 y GNU `timeout` (por ejemplo,
  `brew install coreutils`; comprueba que `timeout` esté en el `PATH`).
- **Windows:** Docker Desktop con backend WSL 2 y al menos 8 GB asignados.
  Ejecuta los comandos de esta guía en una distribución Linux de WSL 2, con
  Git, Bash, `curl`, Python 3 y GNU `timeout` instalados. Git Bash
  también sirve si tiene esas herramientas en el `PATH`.
- **Sin Docker (alternativa):** Linux o WSL 2 con Python 3.12,
  [uv](https://docs.astral.sh/uv/), `curl` y binarios de PostgreSQL
  (en Debian/Ubuntu: `sudo apt install postgresql`). Esta ruta no funciona
  directamente en macOS ni en Windows sin WSL.

Abre una terminal en la **raíz del repositorio** (la carpeta que contiene este
README) y ejecuta todos los comandos desde allí. En Linux Debian/Ubuntu, si
faltan las herramientas básicas, instala `git`, `curl` y `python3` con el
gestor de paquetes. Comprueba antes que `docker info` funciona; en Linux,
inicia Docker si está detenido y usa una cuenta con permiso para ejecutarlo.

La configuración por defecto sirve tal cual. La base de datos no necesita
contraseña: vive dentro del coordinador y solo atiende por su socket, sin
puerto de red. Para cambiar alguna opción (por ejemplo `TM_WORKERS`), copia la
plantilla y edítala con cualquier editor, por ejemplo `nano .env`; el script de
arranque lee este archivo:

```bash
cp .env.example .env
```

En la ruta sin Docker, el clúster local crea su propia base de datos.

## 2. Arrancar el sistema

Con Docker (el head de Ray, los workers configurados y el coordinador con su
PostgreSQL local). La construcción crea dos imágenes: la del head y los workers,
sin PostgreSQL, y la del coordinador, con PostgreSQL 17:

```bash
bash deploy/docker/build-image.sh
```

```bash
TM_BACKEND=ray bash deploy/docker/start-cluster.sh
```

Sin Docker, en Linux o desde una copia del repositorio dentro de WSL (`~/…`):

```bash
uv sync --locked --extra dev
```

```bash
bash deploy/local/local-cluster.sh start
```

En los dos casos la API queda en `http://127.0.0.1:8000`. Está lista cuando esto
responde 200:

```bash
curl -i http://127.0.0.1:8000/ready
```

Si no responde, consulta `bash deploy/docker/cluster-status.sh` y
`docker logs tm-coordinator` en la ruta Docker; en la ruta local, consulta
`bash deploy/local/local-cluster.sh status` y los registros en
`~/.local/share/tournament-management/logs/`.

## 3. Abrir la interfaz web

Deja el clúster en marcha. En **otra terminal**, situada en la raíz del
repositorio, arranca el panel. Si usaste Docker (Linux, macOS o Windows):

```bash
TM_UI_MODE=docker python3 ui/server.py
```

En Windows con Git Bash, si `python3` no existe, usa `python ui/server.py`.
Si usaste el clúster local de Linux o WSL:

```bash
TM_UI_MODE=local python3 ui/server.py
```

La terminal imprime una dirección parecida a
`http://127.0.0.1:8765/?token=...`. **Abre la dirección completa que imprimió
tu terminal** en el navegador; el token cambia en cada arranque. En WSL puedes
abrirla en el navegador de Windows. Mantén esta segunda terminal abierta
mientras uses el panel. Allí verás los workers, torneos, eventos y registros.

Para la demostración, entra en **Ejecuciones → Torneo a medida**, elige
**round-robin**, 4 participantes y los bots `optimal` y `random_bot`, y pulsa
**Ejecutar**. El panel crea el torneo y lo sigue hasta terminar. Mira
**Torneos**, selecciona el torneo nuevo y observa sus eventos.

Si la página muestra una franja amarilla, comprueba que elegiste el mismo modo
(`docker` o `local`) que el clúster y que la API responde en `/ready`. Para
detalles de cada botón, consulta [ui/README.md](ui/README.md).

## 4. Cómo funciona la recuperación

1. El worker muere con la partida en marcha; Ray lo avisa y el intento termina
   como **fallo de infraestructura**, no como derrota de ningún bot.
2. El coordinador emite **otro intento** de la misma partida, con una generación
   mayor, en otro worker.
3. Solo se acepta el resultado del intento vigente: si el viejo llegara tarde,
   se descarta. Cada partida termina con **un único resultado**.
4. Todo esto queda en el historial de eventos del torneo, en PostgreSQL.

Por qué funciona y qué se pierde con cada nodo: [docs/architecture.md](docs/architecture.md).
Qué promete cada respuesta: [doc/semantics.md](doc/semantics.md).

## 5. Parar

Si arrancaste con Docker:

```bash
bash deploy/docker/stop-cluster.sh
```

Si arrancaste sin Docker:

```bash
bash deploy/local/local-cluster.sh stop
```

Los datos se conservan hasta el próximo arranque.

## Dónde seguir

- [doc/decisiones.md](doc/decisiones.md): las seis decisiones de diseño que más pesan.
- [doc/campaign/ejecutar.md](doc/campaign/ejecutar.md) y [doc/campaign/resultados.md](doc/campaign/resultados.md): cómo ejecutar la campaña de fallos y carga, y sus resultados.
- [deploy/README.md](deploy/README.md) y [deploy/OPERATIONS.md](deploy/OPERATIONS.md): despliegue y operación.
- [ui/README.md](ui/README.md): panel web para ver workers, torneos, partidas, eventos y logs, y lanzar escenarios.

## Desarrollo

Python 3.12 y Ray 2.40.0, con las dependencias bloqueadas en `uv.lock`. La
configuración usa variables `TM_*` (argumentos > entorno > valores por
defecto; `.env.example` las documenta). Pruebas y estilo:

```bash
uv run pytest
```

```bash
uv run ruff check .
```

Las pruebas de integración necesitan PostgreSQL (`TM_TEST_DATABASE_URL`) y
Linux: [tests/integration/README.md](tests/integration/README.md).

## Uso de IA

```yaml
ai_use:
  used: true
  tools:
    - ChatGPT
    - Claude
  activities:
    - architecture discussion
    - implementation
    - debugging
    - test generation
  verification:
    - unit and integration tests with pytest
    - fault and workload campaign in Docker and WSL
    - independent verification of campaign evidence with tm verify
    - manual review of proposed changes
```