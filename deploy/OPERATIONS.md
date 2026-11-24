# Operación del clúster de laboratorio

## Estado y decisiones

Guía de diagnóstico y recuperación. Para el primer arranque, sigue
[ARRANQUE.md](ARRANQUE.md); para comprobar ejecución en cada worker,
[REMOTE-TESTS.md](REMOTE-TESTS.md).

Dos imágenes sobre Debian 13 instalan Python 3.12.13, uv 0.12.2, el paquete y
las dependencias de uv.lock, incluido Ray 2.40.0. `worker` (head y workers) no
lleva PostgreSQL; su etiqueta por defecto es tournament-management:step-1.5 y
TM_IMAGE permite otra. `coordinator` añade PostgreSQL 17 de Debian y se etiqueta
como TM_IMAGE con el sufijo `-coordinator`. Las dos registran la misma revisión
Git (con sufijo dirty si procede). Los contenedores se crean mediante el ID
inmutable de su imagen, aunque la etiqueta cambie después.
Las imágenes base se fijan por versión, no por digest: registrar su digest al
hacer la primera construcción si se necesita reproducción binaria exacta.

| Contenedor | Rol | Límite Docker | Recursos Ray |
|---|---|---|---|
| tm-head | Control y servidor Ray Client | 1 CPU, 2 GiB RAM, 512 MiB shm | CPU=0, GPU=0 |
| tm-worker-1 … tm-worker-N | Ejecución, una partida a la vez | 1 CPU, `TM_WORKER_MEMORY` (2 GiB) RAM, 512 MiB shm | CPU=1, GPU=0, tm_worker=1 |
| tm-coordinator | API, driver Ray Client y su PostgreSQL local | 2 CPU, 2 GiB RAM (compartidos con la base) | No registra nodo de ejecución |

N = `TM_WORKERS`, en `.env` o en el entorno. Para
añadir uno con el clúster en marcha: `bash deploy/docker/add-worker.sh tm-worker-N`;
`stop-cluster.sh` retira también los añadidos.

Son límites, no reservas físicas. Dimensiona CPU y memoria según la cantidad de workers; mide con
`docker stats` y ajusta
`TM_WORKER_MEMORY` si no caben. Cada nodo
Ray usa 256 MiB de object store. Los valores están en start-cluster.sh.
El coordinador conserva su estado local en tm-state y su PostgreSQL los datos en
tm-postgres-data, montado en /var/lib/postgresql/data. Los ejecutables se
guardan en tm-artifacts. Los tres volúmenes los monta solo el coordinador. 

PostgreSQL corre dentro del contenedor del coordinador, como usuario postgres,
con `listen_addresses=''`: solo atiende por el socket /run/postgresql, sin
puerto, así que ningún otro nodo puede alcanzarla y no hace falta contraseña.
Al arrancar, el entrypoint crea la base si el volumen está vacío, espera a que
acepte conexiones, aplica el esquema idempotente y lanza `tm serve`. Vigila los
dos procesos: si termina uno, para el otro y el contenedor sale con error. Ante
`docker stop` para primero `tm serve` y después PostgreSQL (parada rápida), dentro
de los 20 s de `--stop-timeout`. Sus registros salen en `docker logs tm-coordinator`.

`.env` es opcional (copia de `.env.example`). Los scripts leen ese archivo sin
ejecutarlo; una variable ya exportada tiene prioridad.


La red bridge tm-net es local a un host Docker. Solo se publica
127.0.0.1:8000 hacia el puerto 8000 del coordinador. Ray 6379/10001 no se
publica al host. TM_API_PORT cambia el puerto público.

## Cómo se conecta el coordinador

El driver usa ray://tm-head:10001. No ejecuta ray start ni registra un worker;
el head no recibe partidas con reserva CPU positiva. Las tareas de partida piden recursos CPU explícitamente; CPU=0 no impide tareas de coste cero.

Con `TM_BACKEND=ray`, app.py abre la conexión mediante
cluster/ray_connection.py al iniciar y la cierra al parar. Si esa conexión
inicial falla, el arranque HTTP falla. Los scripts usan `TM_BACKEND=local`
por defecto desde 3.3; la ejecución local no requiere conexión Ray. Los nodos
Ray se conservan para las comprobaciones de infraestructura.
La consulta de membresía es administrativa, no un endpoint público ni una
demostración de ejecución en workers.

Ray Client depende de mantener la conexión del driver. No se promete que sus
tareas sobrevivan a la pérdida del coordinador o del head. La recuperación durable y los reintentos los implementa el coordinador
a partir del estado de PostgreSQL.

Los scripts pasan GRPC_POLL_STRATEGY=poll y GRPC_ENABLE_FORK_SUPPORT=true a los
contenedores. Mitigan un aborto observado en gRPC/epoll1 al crear el servidor
por cliente mediante fork. No requieren reconstruir la imagen: basta recrear
contenedores con los scripts corregidos. Al usar los comandos Docker manuales
de abajo, añadir también `--env GRPC_POLL_STRATEGY=poll --env GRPC_ENABLE_FORK_SUPPORT=true`
a cada docker run.

Si falla la consulta final, se imprime su código (124 indica timeout) y el log
interno ray_client_server.err. Los plazos de las consultas Ray se ejecutan
dentro del contenedor para no abandonar procesos de diagnóstico.

Referencia: [documentación oficial Ray Client](https://docs.ray.io/en/latest/cluster/running-applications/job-submission/ray-client.html).
Las opciones usadas se contrastaron también con ray start --help de la
instalación local 2.40.0. La documentación latest no sustituye la prueba de
esa versión fijada.

## Comandos que ejecuta el usuario

Desde la raíz del repositorio:

```bash
docker info
bash deploy/docker/build-image.sh
TM_BACKEND=ray bash deploy/docker/start-cluster.sh
bash deploy/docker/cluster-status.sh
curl http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/ready
```

build-image.sh es el paso que puede descargar la imagen base, uv, dependencias,
PostgreSQL y herramientas de construcción. La instalación uv no modifica uv.lock.
start-cluster.sh exige imagen local y usa --pull never.

### Reintentar tras un fallo de conexión

El Dockerfile separa herramientas, dependencias y paquete del proyecto:
uv se copia desde su imagen oficial ghcr.io/astral-sh/uv:0.12.2;
pyproject.toml y uv.lock determinan la capa de dependencias; src/ y README
solo afectan a la instalación del paquete. Bots se copian después.
Los metadatos de revisión Git se añaden al final para no invalidar instalaciones.

Tras esta reorganización pueden repetirse instalaciones una vez porque cambió
la estructura de capas. La caché tm-uv-cache existente mantiene el mismo nombre.
La imagen oficial de uv requiere una descarga inicial de sus capas mediante
Docker, que comprueba su integridad; no usa pip para instalar uv. En construcciones
posteriores, cambios solo en src/ no deben repetir la instalación de uv
ni la capa de dependencias. Esto presupone conservar las cachés del builder.

Repetir `bash deploy/docker/build-image.sh`. El paso uv sync usa una caché BuildKit
persistente (tm-uv-cache) fuera de la capa de imagen: los paquetes completados y
guardados en caché se reutilizan aunque falle la instalación. No garantiza
reanudar un archivo descargado parcialmente. La caché del intento anterior
sin este montaje no se puede recuperar mediante este cambio.

La configuración de descarga de dependencias mediante uv es: lectura HTTP de 300 segundos,
conexión de 60 segundos, 8 reintentos y máximo 2 descargas simultáneas.
Son límites por operación, no un plazo máximo de toda la construcción.
La instalación usa el mismo uv.lock; no cambia versiones de dependencias.
La imagen base de Python sigue siendo la misma.

La caché depende del mismo builder Docker y puede perderse si se limpia o
Docker la elimina por espacio. No ejecutar limpiezas de caché entre intentos.
Referencia: [caché uv en Docker](https://docs.astral.sh/uv/guides/integration/docker/#caching)
y [opciones HTTP de uv](https://docs.astral.sh/uv/configuration/environment/).

Resultado esperado: la base local del coordinador responde por socket y no
escucha en la red, el head sin CPU para partidas y los workers configurados con
CPU, head y workers con la misma imagen y sin PostgreSQL, el coordinador con la
misma revisión, resolución DNS interna y API saludable.
`/ready` devuelve 200 con esquema migrado, almacenamiento disponible y
coordinador sano, con cualquiera de los dos backends. Con Ray, la falta de
workers no cambia `/ready`; se ve en `/api/v1/workers`. `/health` solo
comprueba vida del proceso.

El arranque espera hasta TM_START_TIMEOUT segundos (180 por defecto) por etapa.
Si falla muestra logs y conserva contenedores para diagnóstico; no borra el
estado. Si existe un contenedor con el mismo nombre, el arranque se rechaza: usar stop-cluster.sh y volver a arrancar tras revisar los logs.

## Reinicio y persistencia

```bash
docker exec tm-coordinator python -c 'from pathlib import Path; Path("/var/lib/tm/step15-check.txt").write_text("persistencia-ok")'
bash deploy/docker/stop-cluster.sh
TM_BACKEND=ray bash deploy/docker/start-cluster.sh
docker exec tm-coordinator python -c 'from pathlib import Path; assert Path("/var/lib/tm/step15-check.txt").read_text() == "persistencia-ok"; print("Persistencia verificada")'
```

stop-cluster.sh detiene y elimina los contenedores del proyecto, tras
validar sus etiquetas. Conserva tm-state, tm-postgres-data, tm-artifacts y tm-net. Se pierden los
archivos temporales de los contenedores y sus logs locales Docker al eliminarlos;
guardar evidencia antes de parar. El volumen permite recuperar el estado.

Para simular fallo de un worker:

```bash
bash deploy/docker/fail-worker.sh tm-worker-1
docker start tm-worker-1
bash deploy/docker/cluster-status.sh
```

Puede tardar en volver a registrarse. Con backend local, las partidas se
ejecutan en el coordinador: detener un worker Ray no interrumpe esas partidas.

## Comandos Docker y diagnóstico

Los scripts de `deploy/docker/` contienen los comandos Docker explícitos, con
etiquetas, límites, esperas y comprobaciones. No se usa Compose. Para revisar
un componente: `docker logs --tail 100 NOMBRE_CONTENEDOR`.
La imagen es de laboratorio; no ofrece aislamiento seguro para código hostil.

## Artefactos internos

El volumen tm-artifacts se monta en `/var/lib/tm/artifacts` del coordinador.
Reconstruir la imagen y recrear los contenedores con
`start-cluster.sh` aplica el cambio; reiniciar contenedores antiguos no basta.

`tm register-bot` publica y sincroniza un archivo completo antes del commit SQL.
`tm inspect-bot` consulta el registro y verifica hash/tamaño al leerlo.

### Descarga desde los workers

- El coordinador sirve `GET /internal/artifacts/{hash}` en su puerto 8000. Es
  una ruta interna: no forma parte del contrato `/api/v1` ni del OpenAPI.
- Exige la cabecera `Authorization: Bearer <TM_INTERNAL_TOKEN>`; sin token
  válido responde 401. Si el coordinador no tiene token configurado, la ruta
  responde 404. Un archivo que no supera la verificación en el coordinador
  responde 503 y no se sirve.
- `start-cluster.sh` genera un token aleatorio en cada arranque (salvo que
  `TM_INTERNAL_TOKEN` esté exportado) y lo pasa al coordinador y a los
  workers. Los workers reciben además `TM_ARTIFACT_URL=http://tm-coordinator:8000`.
  El token no se guarda en `.env`, pero es visible con `docker inspect`: es un
  control de laboratorio.
- Cada intento descarga sus dos bots, comprueba tamaño y SHA-256 y los copia a
  una carpeta temporal propia que se borra al terminar. No hay caché. Una
  descarga fallida, cortada o corrupta es un fallo de infraestructura y nunca
  una derrota del bot.
- `tm verify-artifact --artifact-hash HASH` descarga y verifica cuando
  `TM_ARTIFACT_URL` está configurada (en los workers); si no, lee el volumen
  local. `bash deploy/docker/probe-artifact.sh HASH` comprueba en los workers del proyecto que
  **no** montan `tm-artifacts` y que la descarga verifica.
- Una lectura de un objeto alterado o ausente falla: no se repara
  automáticamente ni se acepta otro código bajo el mismo hash.

Conservar y respaldar tm-artifacts junto con tm-postgres-data: conservar solo la
base no conserva los bytes. La base se respalda dentro del coordinador:
`docker exec tm-coordinator pg_dump -h /run/postgresql -U tm tournament_management > respaldo.sql`. Los archivos `.upload-*` que pueda dejar un corte
abrupto no se anuncian ni se ejecutan. Un fallo SQL posterior a la publicación
puede dejar objetos completos sin referencia; no hay recolección automática.
El acceso por hash y el token interno no aíslan código hostil ni restringen a
un administrador del daemon Docker.

## API y ejecución local

Reconstruir y recrear incorpora esta etapa conservando los tres volúmenes:

```bash
bash deploy/docker/stop-cluster.sh
bash deploy/docker/build-image.sh
TM_BACKEND=ray bash deploy/docker/start-cluster.sh
bash deploy/docker/cluster-status.sh
curl -i http://127.0.0.1:8000/ready
```

`TM_BACKEND` se lee de `.env` o del entorno. Con `local` las partidas se juegan
en el coordinador. El coordinador elige el worker, Ray no reintenta y `/api/v1/workers` muestra cada
worker por su hostname (`tm-worker-1`), sin identificadores internos de Ray.
Los workers arrancan con `--node-name` igual a su hostname; hay que recrear
los contenedores con la imagen nueva para que lo tengan.

El [recorrido HTTP](../examples/api/README.md) permite registrar bots, ejecutar
ambos formatos, consultar intentos y reanudar eventos. El cierre del servidor
limita a cinco segundos la espera por conexiones HTTP activas, incluido SSE.


## Reinicio del coordinador

Después de reconstruir y recrear con la imagen que contiene esta etapa, para
reiniciar solo el coordinador conservando PostgreSQL y los artefactos (la base
vive en el mismo contenedor: se reinicia con él y sus datos siguen en
tm-postgres-data):

```bash
docker restart tm-coordinator
curl -i http://127.0.0.1:8000/ready
docker logs --tail 100 tm-coordinator
```

Esperar a que termine el arranque antes de repetir la consulta de disponibilidad.
El log `Recuperación completada` informa cuántos torneos quedaron en la cola.
La API empieza a servir después de confirmar la recuperación. Si esta falla,
el arranque falla y debe revisarse el log; no se anuncia una API lista con
trabajo parcialmente recuperado.

Para una prueba de corte abrupto en el laboratorio, guardar antes los IDs del
torneo y del último evento, y comprobar que el torneo aún figure running:

```bash
docker kill --signal KILL tm-coordinator
docker start tm-coordinator
curl -i http://127.0.0.1:8000/ready
```

El SIGKILL corta también a PostgreSQL sin aviso: al volver, `docker logs`
muestra cómo rehace su registro de cambios antes de aceptar conexiones, y lo
confirmado se conserva. Después, consultar el mismo torneo y su historial de
intentos usando los comandos de examples/api/README.md. Si quedó un intento sin confirmar debe
aparecer lost y, si hay presupuesto, un reemplazo con otro ID y generación.
Si el resultado alcanzó a confirmarse antes del corte se conserva accepted;
no se exige perder un resultado que ya era durable. Los torneos de ejemplo
pueden finalizar muy rápido: la prueba automatizada detiene el proceso
exactamente antes/después del commit para verificar ambas posibilidades.

Repetir cortes puede agotar TM_MAX_RETRIES y dejar blocked; reiniciar no repone
el presupuesto. En ejecución local fuera de Docker, conservar tanto
TM_DATABASE_URL como TM_DATA_DIR y volver a ejecutar `tm serve --backend local`.
No iniciar una segunda instancia mientras la anterior siga activa: esta etapa
no implementa elección de líder ni exclusión entre coordinadores.

Las pruebas automatizadas de recuperación usan PostgreSQL y HTTP reales con
SIGKILL de procesos propios. La campaña complementa esas pruebas con fallos
en el clúster; sus resultados se documentan en `doc/campaign/resultados.md`.

## Membresía, capacidad y plazos

- `/api/v1/workers` muestra cada worker por su nombre con estado propio:
  - `available`: puede recibir intentos;
  - `suspected`: un intento suyo murió por Ray mientras Ray aún lo daba por
    vivo (tras `docker kill`, Ray tarda unos 30 s en confirmarlo). No recibe
    intentos durante `TM_SUSPECT_COOLDOWN_MS` (45 s) o hasta que reaparece
    reiniciado;
  - `lost`: se vio antes y Ray ya no lo da por vivo; capacidad 0.
- Sin workers `available` con hueco, el coordinador espera sin crear intentos
  ni gastar reintentos. Si pasan `TM_CAPACITY_WAIT_MS` (300 s), el torneo queda
  `blocked` con el evento `tournament.blocked` y detalle `no_worker_capacity`.
- Plazo del intento: `TM_ATTEMPT_TIMEOUT_MS` ≥ `TM_MATCH_TIMEOUT_MS` + 2 ×
  `TM_ARTIFACT_DOWNLOAD_TIMEOUT_MS` + 10 s (por defecto 90 ≥ 60 + 20 + 10).
  Si no se cumple, el arranque y `tm doctor` avisan, pero no se impide arrancar.
  Medido en Docker: un intento normal tarda de 0,2 a 2,3 s.
- Añadir un worker al clúster en marcha (misma imagen y token que el coordinador).
  Sustituye `N` por un número libre; el panel propone el siguiente disponible:

```bash
bash deploy/docker/add-worker.sh tm-worker-N
```

  `stop-cluster.sh` retira también los workers añadidos: con un token viejo se
  reconectarían al head del siguiente arranque.

## Pérdida del head de Ray

Observado el 29-10-2026 con `experiments/scenarios/head-loss.sh` (`docker kill
tm-head` con una partida en curso):

| Tiempo tras matar el head | Observado |
|---|---|
| 0–45 s | `/ready` sigue en 200 y el torneo en `running`: Ray Client espera reconectar |
| ~50–70 s | El coordinador se detiene por el error de Ray ("Coordinador detenido por fallo de ejecución o persistencia"); `/ready` pasa a 503 y `/api/v1/workers` responde 503 (`execution_unavailable`) |
| ~85 s | Los workers salen solos: sin GCS, sus procesos de Ray terminan |

No se acepta ni inventa ningún resultado durante la caída: el torneo queda
`running` en PostgreSQL. La pérdida del head **no tiene las garantías de la
pérdida de un worker**: detiene toda la ejecución hasta recuperarlo.

Procedimiento de recuperación (conserva PostgreSQL y artefactos):

```bash
docker start tm-head
docker restart $(docker ps -aq --filter name=tm-worker-)
docker restart tm-coordinator
curl -i http://127.0.0.1:8000/ready
```

Al reiniciar, el coordinador ejecuta la recuperación, el intento
que estaba en curso queda `lost` (`coordinator_restart`) y se reemplaza con una
generación nueva. En la prueba, el torneo terminó `completed` con un único
resultado aceptado, unos 90 s después de empezar la recuperación. Si se
añadieron workers con `add-worker.sh`, reiniciarlos también. Recrear todo con
`stop-cluster.sh` + `start-cluster.sh` es equivalente.

## Modo sin Docker

Arranque paso a paso en [ARRANQUE.md](ARRANQUE.md). Detalles de operación:

- `deploy/local/local-cluster.sh` lanza cada nodo de Ray como un proceso propio
  (`deploy/local/local_cluster.py`, con la misma utilidad de varios nodos que usan
  las pruebas). `ray start` no se usa: en WSL se colgaba al arrancar el head y
  llegó a dejar WSL sin responder (ver `docs/progress.md`, paso 11).
- PostgreSQL es una instancia propia en `TM_LOCAL_HOME/postgres`, accesible solo
  por socket Unix en `TM_LOCAL_HOME/run` (puerto lógico 5433); no abre TCP ni
  usa el servicio del sistema. Los datos persisten entre `stop` y `start`.
- Mismos parámetros que Docker: head sin CPU, workers `tm-worker-N` con el
  recurso `tm_worker`, descarga de bots por `http://127.0.0.1:8000` con un token
  nuevo por arranque, variables gRPC y `TM_BACKEND=ray`.
- `kill`, `pause`, `resume` y `restore` actúan sobre el proceso de un nodo y
  todos sus descendientes, igual que `docker kill`, `pause`, `unpause` y `start`
  sobre un contenedor.
- Antes de arrancar se limpian restos de Ray (`ray stop --force` y la sesión
  vieja en `/tmp/ray`): sin eso, un worker falló al registrarse tras un `stop`.
- Lo que solo da Docker: límites de CPU y memoria por componente y aislamiento
  de archivos entre roles. Sin Docker, un bot comparte máquina con todo lo demás.
