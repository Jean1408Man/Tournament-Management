# Pruebas de ejecución remota

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
bash -n deploy/docker/probe-cluster.sh
.venv/bin/tm probe-cluster --help
```

Resultado esperado: Ruff sin errores, pytest sin fallos, sintaxis correcta y
ayuda con ray-address, expected-workers y timeout-seconds.
Las pruebas unitarias usan dobles; no prueban ejecución distribuida real.
La suite existente incluye un servidor HTTP local temporal.

## 2. Actualizar imagen y contenedores

Si cambió el código, actualiza la imagen. Construir antes de detener
el clúster: si falla la construcción, el clúster anterior permanece disponible.

```bash
bash deploy/docker/build-image.sh
bash deploy/docker/stop-cluster.sh
bash deploy/docker/start-cluster.sh
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
bash deploy/docker/cluster-status.sh
bash deploy/docker/probe-cluster.sh | tee experiments/results/remote-probe.json
echo "Código de salida: $?"
```

El script cuenta los workers del proyecto, incluidos los añadidos, y pasa esa
cantidad a `--expected-workers`. Se espera código 0, `status=ok`,
`scope=remote_execution_per_worker` y un elemento por worker esperado.
Los `node_id` deben ser distintos y las versiones deben coincidir.

El archivo de evidencia queda fuera de Git según la configuración existente.
No confundir el status=ok de membresía con el de ejecución remota: el segundo
requiere haber recogido y validado resultados de tareas.

## 4. Comprobar un fallo controlado

Esta prueba verifica que no se declara éxito con un worker ausente. No
demuestra que un torneo continúe, porque solo ejecuta tareas de diagnóstico.

```bash
bash deploy/docker/fail-worker.sh tm-worker-2
bash deploy/docker/probe-cluster.sh
echo "Código de salida esperado distinto de cero: $?"
```

Puede fallar por topología incompleta o por timeout si Ray todavía considera
vivo el nodo perdido. En ambos casos debe terminar sin status=ok.
El plazo global agotado devuelve 124; el diagnóstico normal de fallo devuelve 1.

Restaurar siempre el worker, incluso si el resultado no coincide con lo esperado:

```bash
docker start tm-worker-2
bash deploy/docker/cluster-status.sh
bash deploy/docker/probe-cluster.sh
```

Si el worker todavía no se ha registrado, esperar unos segundos y repetir las
dos consultas. No repetir start-cluster.sh sobre contenedores existentes.

## Criterio de aceptación

La prueba debe comprobar todos los workers configurados y añadidos, detectar
un worker ausente y volver a pasar tras restaurarlo. No sustituye la campaña
de partidas, fallos y carga ni verifica su recuperación.
