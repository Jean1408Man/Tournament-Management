# Scripts operativos

Ejecutar con bash desde cualquier directorio:

- build-image.sh: construcción, único script que permite descargas.
- start-cluster.sh: red, volumen, roles, esperas y diagnóstico si falla.
- cluster-status.sh: imagen común, membresía, DNS y almacenamiento.
- probe-cluster.sh: tarea fijada a cada worker; comprueba identidad y versiones,
  con plazo global de 120 segundos. Requiere reconstruir la imagen del paso 1.6.
- stop-cluster.sh: retira contenedores etiquetados del proyecto; conserva volumen/red.
- fail-worker.sh tm-worker-1: simula caída de un worker específico.
- common.sh: funciones compartidas; no se ejecuta por separado.

No contienen lógica de torneos. Ver ../deploy/OPERATIONS.md.
