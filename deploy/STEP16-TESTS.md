# Paso 1.6 — prueba de ejecución remota en cada worker

## Estado de entrega

Implementación preparada. No se ejecutaron comandos de prueba, construcción
ni operaciones sobre el clúster durante esta entrega, a petición del usuario.
Completar la validación con las salidas de los comandos de esta guía.

## Qué comprueba

`tm probe-cluster` conecta con Ray Client, consulta membresía y exige el número
esperado de workers, además de un head sin CPU para partidas.
Después envía una tarea a cada worker mediante afinidad estricta de nodo
(`NodeAffinitySchedulingStrategy(..., soft=False)`), con una CPU, recurso
tm_worker y reintentos automáticos desactivados.

La tarea consulta su identidad en el propio worker y devuelve node_id, hostname,
PID, versiones Python/Ray/aplicación y un nonce específico de la prueba.
El coordinador exige exactamente los nodos seleccionados y las mismas versiones.
Dos resultados del mismo nodo, un resultado del head o una respuesta de otra
prueba provocan error. Los PID pueden coincidir entre contenedores: lo que debe
ser distinto es node_id; cada contenedor tiene su propio espacio de procesos.

El plazo de tareas predeterminado es 30 segundos. El script añade un plazo global
de 120 segundos dentro del contenedor, que incluye conexión y diagnóstico.
Ante fallo se cancelan tareas pendientes y se cierra la conexión.
La prueba no verifica partidas ni recuperación automática de torneos.

Referencia de la estrategia:
https://docs.ray.io/en/latest/ray-core/api/doc/ray.util.scheduling_strategies.NodeAffinitySchedulingStrategy.html

## 1. Comprobaciones locales, sin clúster

Desde la raíz del proyecto y con el entorno ya instalado:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest
bash -n scripts/probe-cluster.sh
.venv/bin/tm probe-cluster --help
```

Resultado esperado: Ruff sin errores, pytest sin fallos, sintaxis correcta y
ayuda con ray-address, expected-workers y timeout-seconds.
Las pruebas unitarias usan dobles; no prueban ejecución distribuida real.
La suite existente incluye un servidor HTTP local temporal.

## 2. Actualizar imagen y contenedores

La imagen actual no contiene el nuevo comando. Construir antes de detener
el clúster: si falla la construcción, el clúster anterior permanece disponible.

```bash
bash scripts/build-image.sh
bash scripts/stop-cluster.sh
bash scripts/start-cluster.sh
```

No se cambiaron dependencias ni uv.lock. Docker reutiliza caché, pero la
construcción puede consultar registros y descargar lo que falte.
La etiqueta por defecto conserva el nombre histórico step-1.5; el ID de imagen
cambia al incorporar código nuevo. Todos los contenedores deben usar ese nuevo ID.
Si build o start fallan, detener esta secuencia y compartir la salida.

## 3. Ejecutar y guardar evidencia positiva

```bash
mkdir -p experiments/results
set -o pipefail
bash scripts/cluster-status.sh
bash scripts/probe-cluster.sh | tee experiments/results/step16-probe.json
echo "Código de salida: $?"
```

El script ejecuta dentro del coordinador:

```bash
docker exec tm-coordinator timeout --kill-after=5 120 tm probe-cluster --ray-address ray://tm-head:10001 --expected-workers 2 --timeout-seconds 30
```

Resultado esperado: código 0 y JSON con status=ok,
scope=remote_execution_per_worker, expected_workers=2 y dos elementos workers.
Los node_id deben ser diferentes y los hostname deben identificar tm-worker-1
y tm-worker-2. Sus versiones deben coincidir. La duración se mide en el
coordinador; no se restan relojes monotónicos de máquinas diferentes.

El archivo de evidencia queda fuera de Git según la configuración existente.
No confundir el status=ok de membresía con el de ejecución remota: el segundo
requiere haber recogido y validado resultados de tareas.

## 4. Comprobar un fallo controlado

Esta prueba verifica que no se declara éxito con un worker ausente. No
demuestra que un torneo continúe, pues aún no hay motor de torneos.

```bash
bash scripts/fail-worker.sh tm-worker-2
bash scripts/probe-cluster.sh
echo "Código de salida esperado distinto de cero: $?"
```

Puede fallar por topología incompleta o por timeout si Ray todavía considera
vivo el nodo perdido. En ambos casos debe terminar sin status=ok.
El plazo global agotado devuelve 124; el diagnóstico normal de fallo devuelve 1.

Restaurar siempre el worker, incluso si el resultado no coincide con lo esperado:

```bash
docker start tm-worker-2
bash scripts/cluster-status.sh
bash scripts/probe-cluster.sh
```

Si el worker todavía no se ha registrado, esperar unos segundos y repetir las
dos consultas. No repetir start-cluster.sh sobre contenedores existentes.

## 5. CI y criterio de cierre

.github/workflows/ci.yml define lint y pytest para push/pull_request en GitHub.
Descarga el entorno en el runner de CI cuando se active; no se ejecutó ni publicó
desde esta sesión. No sustituye las pruebas Docker de esta guía.

Cerrar 1.6 únicamente tras recibir:
- Pruebas locales y revisión de código correctas.
- Arranque del clúster con la imagen actualizada.
- Evidencia de ejecución en dos workers distintos y versiones coincidentes.
- Fallo explícito con un worker ausente y éxito después de restaurarlo.

Al cerrar 1.6 queda validada la base de la etapa 1. La implementación del juego,
árbitro y torneos locales empieza en etapa 2.
