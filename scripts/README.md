# Scripts operativos

Ejecutar con bash desde cualquier directorio:

- build-image.sh: construcción, único script que permite descargas.
- start-cluster.sh: red, volumen, roles, esperas y diagnóstico si falla.
- cluster-status.sh: imagen común, membresía, DNS y almacenamiento.
- probe-cluster.sh: tarea fijada a cada worker; comprueba identidad y versiones,
  con plazo global de 120 segundos. Requiere reconstruir la imagen del paso 1.6.
- probe-artifact.sh SHA256: comprueba la descarga del objeto en cada worker.
- add-worker.sh tm-worker-N: añade un worker con el clúster en marcha.
- stop-cluster.sh: retira contenedores etiquetados del proyecto; conserva volumen/red.
- fail-worker.sh tm-worker-N: simula caída de un worker específico.
- common.sh: funciones compartidas; no se ejecuta por separado. Genera los
  nombres tm-worker-1..N a partir de `TM_WORKERS` (4) y aplica `TM_WORKER_MEMORY`.

No contienen lógica de torneos. Ver ../deploy/OPERATIONS.md.

El volumen tm-artifacts conserva los bots registrados al recrear el clúster.
Solo el coordinador lo monta con escritura; los workers no reciben credenciales PostgreSQL.

- `uv run python scripts/export-openapi.py`: regenera el contrato OpenAPI
  versionado desde la aplicación, sin iniciar servidor ni conectarse a PostgreSQL.
