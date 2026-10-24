# Scripts operativos

Ejecutar con bash desde cualquier directorio:

- build-image.sh: construcción, único script que permite descargas.
- start-cluster.sh: red, volumen, roles, esperas y diagnóstico si falla.
- cluster-status.sh: imagen común, membresía, DNS y almacenamiento.
- probe-cluster.sh: tarea fijada a cada worker; comprueba identidad y versiones,
  con plazo global de 120 segundos. Requiere reconstruir la imagen del paso 1.6.
- probe-artifact.sh SHA256: comprueba el objeto en ambos workers y exige montaje de solo lectura.
- stop-cluster.sh: retira contenedores etiquetados del proyecto; conserva volumen/red.
- fail-worker.sh tm-worker-1: simula caída de un worker específico.
- common.sh: funciones compartidas; no se ejecuta por separado.

No contienen lógica de torneos. Ver ../deploy/OPERATIONS.md.

El volumen tm-artifacts conserva los bots registrados al recrear el clúster.
Solo el coordinador lo monta con escritura; los workers no reciben credenciales PostgreSQL.

- `uv run python scripts/export-openapi.py`: regenera el contrato OpenAPI
  versionado desde la aplicación, sin iniciar servidor ni conectarse a PostgreSQL.
