# Despliegue de laboratorio — paso 1.5

## Estado y decisiones

Validado el 17 de octubre de 2026 con dos recreaciones reales tras la mitigación
gRPC descrita abajo: arranque con código 0, membresía, DNS y persistencia correctos.
Las pruebas automatizadas con dobles de Ray/Docker complementan esa comprobación.
La ejecución de tareas de diagnóstico en cada worker corresponde a 1.6.

Una imagen instala Python 3.12.13, uv 0.12.2, el paquete y las dependencias de
uv.lock, incluido Ray 2.40.0. La etiqueta por defecto es
tournament-management:step-1.5; TM_IMAGE permite otra. La imagen registra la
revisión Git (con sufijo dirty si procede). Los cuatro contenedores de la
aplicación se crean mediante el mismo ID inmutable de imagen, aunque la etiqueta
cambie después; PostgreSQL usa su imagen oficial independiente.
Las imágenes base se fijan por versión, no por digest: registrar su digest al
hacer la primera construcción si se necesita reproducción binaria exacta.

| Contenedor | Rol | Límite Docker | Recursos Ray |
|---|---|---|---|
| tm-postgres | Persistencia PostgreSQL | Sin límite adicional | No aplica |
| tm-head | Control y servidor Ray Client | 1 CPU, 2 GiB RAM, 512 MiB shm | CPU=0, GPU=0 |
| tm-worker-1 | Ejecución | 1 CPU, 2 GiB RAM, 512 MiB shm | CPU=1, GPU=0, tm_worker=1 |
| tm-worker-2 | Ejecución | 1 CPU, 2 GiB RAM, 512 MiB shm | CPU=1, GPU=0, tm_worker=1 |
| tm-coordinator | API y driver Ray Client | 1 CPU, 1 GiB RAM | No registra nodo de ejecución |

Son límites, no reservas físicas. Presupuesto orientativo: 4 CPU y al menos
8 GiB para Docker, con margen adicional para el sistema operativo. Cada nodo
Ray usa 256 MiB de object store. Los valores están en start-cluster.sh.
El coordinador conserva su estado local en tm-state y PostgreSQL sus datos en
tm-postgres-data. Desde 3.2, los ejecutables se guardan en tm-artifacts: el
coordinador tiene escritura y los workers solo lectura, sin credenciales SQL.
Los tres volúmenes se preservan al detener el clúster. El
arranque requiere que la imagen `postgres:16.6-bookworm` esté disponible y crea
la base `tournament_management`; el coordinador aplica el esquema idempotente.
Copiar `.env.example` a `.env` y configurar `TM_POSTGRES_PASSWORD` con letras y
números antes de arrancar. Los scripts leen ese archivo sin ejecutarlo; una
variable ya exportada tiene prioridad. Una base existente conserva la contraseña
que tenía, incluso si se modifica `.env`.

La red bridge tm-net es local a un host Docker. Solo se publica
127.0.0.1:8000 hacia el puerto 8000 del coordinador. Ray 6379/10001 no se
publica al host. TM_API_PORT cambia el puerto público.

## Cómo se conecta el coordinador

El driver usa ray://tm-head:10001. No ejecuta ray start ni registra un worker;
el head no recibe partidas con reserva CPU positiva. Al implementar tasks
deben pedir recursos CPU explícitamente; CPU=0 no impide tareas de coste cero.

Con `TM_BACKEND=ray`, app.py abre la conexión mediante
cluster/ray_connection.py al iniciar y la cierra al parar. Si esa conexión
inicial falla, el arranque HTTP falla. Los scripts usan `TM_BACKEND=local`
por defecto desde 3.3; la ejecución local no requiere conexión Ray. Los nodos
Ray se conservan para las comprobaciones de infraestructura.
La consulta de membresía es administrativa, no un endpoint público ni una
demostración de ejecución en workers.

Ray Client depende de mantener la conexión del driver. No se promete que sus
tareas sobrevivan a la pérdida del coordinador o del head. La recuperación
durable y reintentos se implementarán en etapas posteriores.

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
docker pull postgres:16.6-bookworm
bash scripts/build-image.sh
bash scripts/start-cluster.sh
bash scripts/cluster-status.sh
curl http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/ready
```

build-image.sh es el paso que puede descargar la imagen base, uv, dependencias
y herramientas de construcción. La instalación uv no modifica uv.lock.
No ejecutarlo automáticamente si se ha reservado la descarga al usuario.
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

Repetir `bash scripts/build-image.sh`. El paso uv sync usa una caché BuildKit
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

Resultado esperado: PostgreSQL disponible, tres nodos Ray vivos (head sin CPU y
dos workers con CPU), cuatro contenedores de aplicación con la misma imagen,
resolución DNS interna y API saludable.
`/ready` devuelve 200 con esquema migrado, almacenamiento disponible y
coordinador sano, con cualquiera de los dos backends. Con Ray, la falta de
workers no cambia `/ready`; se ve en `/api/v1/workers`. `/health` solo
comprueba vida del proceso.

El arranque espera hasta TM_START_TIMEOUT segundos (180 por defecto) por etapa.
Si falla muestra logs y conserva contenedores para diagnóstico; no borra el
estado. Si existe un contenedor con el mismo nombre, se detiene antes de crear
nuevos: usar stop-cluster.sh y volver a arrancar tras revisar los logs.

## Reinicio y persistencia

```bash
docker exec tm-coordinator python -c 'from pathlib import Path; Path("/var/lib/tm/step15-check.txt").write_text("persistencia-ok")'
bash scripts/stop-cluster.sh
bash scripts/start-cluster.sh
docker exec tm-coordinator python -c 'from pathlib import Path; assert Path("/var/lib/tm/step15-check.txt").read_text() == "persistencia-ok"; print("Persistencia verificada")'
```

stop-cluster.sh detiene y elimina los cinco contenedores del proyecto, tras
validar sus etiquetas. Conserva tm-state, tm-postgres-data, tm-artifacts y tm-net. Se pierden los
archivos temporales de los contenedores y sus logs locales Docker al eliminarlos;
guardar evidencia antes de parar. El volumen permite recuperar el estado.

Para simular fallo de un worker:

```bash
bash scripts/fail-worker.sh tm-worker-1
docker start tm-worker-1
bash scripts/cluster-status.sh
```

Puede tardar en volver a registrarse. Con backend local, las partidas se
ejecutan en el coordinador: detener un worker Ray no interrumpe esas partidas.

## Equivalentes Docker explícitos

Los scripts incluyen además etiquetas de propiedad, límites, esperas,
rotación de logs y verificaciones. Esta secuencia muestra los comandos
principales con las etiquetas necesarias para que stop-cluster.sh los reconozca.
Usar en un entorno sin esos nombres ya creados.

```bash
docker build -f deploy/Dockerfile -t tournament-management:step-1.5 .
docker network create --driver bridge --label org.tournament-management.project=tournament-management tm-net
docker volume create --label org.tournament-management.project=tournament-management tm-state
docker run -d --pull never --init --name tm-head --hostname tm-head --network tm-net --label org.tournament-management.project=tournament-management --cpus 1 --memory 2g --shm-size 512m tournament-management:step-1.5 ray-head
docker exec tm-head ray health-check --address=127.0.0.1:6379
docker volume create --label org.tournament-management.project=tournament-management tm-artifacts
docker run -d --pull never --init --name tm-worker-1 --hostname tm-worker-1 --network tm-net --label org.tournament-management.project=tournament-management --cpus 1 --memory 2g --shm-size 512m --mount source=tm-artifacts,target=/var/lib/tm/artifacts,readonly tournament-management:step-1.5 ray-worker
docker run -d --pull never --init --name tm-worker-2 --hostname tm-worker-2 --network tm-net --label org.tournament-management.project=tournament-management --cpus 1 --memory 2g --shm-size 512m --mount source=tm-artifacts,target=/var/lib/tm/artifacts,readonly tournament-management:step-1.5 ray-worker
docker exec tm-head python -m tournament_management.cluster.ray_topology --address ray://tm-head:10001 --expected-workers 2
docker run -d --pull never --init --name tm-coordinator --hostname tm-coordinator --network tm-net --label org.tournament-management.project=tournament-management --cpus 1 --memory 1g -p 127.0.0.1:8000:8000 --mount source=tm-state,target=/var/lib/tm --mount source=tm-artifacts,target=/var/lib/tm/artifacts tournament-management:step-1.5 coordinator
```

Esperar a que las comprobaciones intermedias tengan éxito antes de continuar.
ray health-check es una CLI interna de la versión fijada; revisarla al actualizar Ray.
Para diagnóstico: docker logs --tail 100 NOMBRE_CONTENEDOR.
La imagen es de laboratorio, ejecuta como root y no aísla código hostil;
no usar como servicio público para bots no confiables.

## Artefactos internos (etapa 3.2)

Reconstruir la imagen y recrear contenedores incorpora el nuevo montaje. No basta
con reiniciar contenedores creados antes de 3.2. El volumen tm-artifacts se monta
en `/var/lib/tm/artifacts`; está fuera de los puertos publicados y los workers
lo reciben con `readonly`. El head no lo monta. El almacenamiento compartido
presupone el despliegue actual en un único host Docker.

`tm register-bot` publica y sincroniza un archivo completo antes del commit SQL.
`tm inspect-bot` consulta el registro y verifica hash/tamaño al leerlo. Los workers
pueden usar `tm verify-artifact --artifact-hash HASH` sin URL PostgreSQL.
`bash scripts/probe-artifact.sh HASH` aplica esa verificación en ambos workers y
comprueba sus montajes. Una lectura de objeto alterado o ausente falla: no se
repara automáticamente ni se acepta otro código bajo el mismo hash.

Conservar y respaldar tm-artifacts junto con tm-postgres-data: conservar solo la
base no conserva los bytes. Los archivos `.upload-*` que pueda dejar un corte
abrupto no se anuncian ni se ejecutan. Un fallo SQL posterior a la publicación
puede dejar objetos completos sin referencia; no hay recolección automática.
El acceso por hash y los montajes de solo lectura no aíslan código hostil ni
restringen a un administrador del daemon Docker.

## API y ejecución local (etapa 3.3)

Reconstruir y recrear incorpora esta etapa conservando los tres volúmenes:

```bash
bash scripts/stop-cluster.sh
bash scripts/build-image.sh
bash scripts/start-cluster.sh
bash scripts/cluster-status.sh
curl -i http://127.0.0.1:8000/ready
```

`TM_BACKEND` se lee de `.env` o del entorno. Con `local` las partidas se juegan
en el coordinador. Desde 4.1, con `ray` se juegan en los workers: el
coordinador elige el worker, Ray no reintenta y `/api/v1/workers` muestra cada
worker por su hostname (`tm-worker-1`), sin identificadores internos de Ray.
Los workers arrancan con `--node-name` igual a su hostname; hay que recrear
los contenedores con la imagen nueva para que lo tengan.

El [recorrido HTTP](../examples/api/README.md) permite registrar bots, ejecutar
ambos formatos, consultar intentos y reanudar eventos. El cierre del servidor
limita a cinco segundos la espera por conexiones HTTP activas, incluido SSE.
Desde 3.4 el arranque reconstruye automáticamente los torneos pendientes con
backend local, preservando resultados, presupuesto y cancelaciones. Usa un solo
coordinador activo por base.

Esta etapa se verificó con PostgreSQL y HTTP reales en las pruebas; no se
reconstruyeron ni recrearon los contenedores como parte de esa verificación.

## Reinicio del coordinador (etapa 3.4)

Después de reconstruir y recrear con la imagen que contiene esta etapa, para
reiniciar solo el coordinador conservando PostgreSQL y los artefactos:

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

Después, consultar el mismo torneo y su historial de intentos usando los
comandos de examples/api/README.md. Si quedó un intento sin confirmar debe
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

Las pruebas automatizadas de 3.4 usan PostgreSQL y HTTP reales con SIGKILL de
procesos propios. No se ejecutaron estas operaciones Docker durante la entrega.
El mecanismo de distribución de artefactos entre máquinas permanece en 4.2.
