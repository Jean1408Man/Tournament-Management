# Despliegue

Dockerfile instala el paquete y dependencias bloqueadas en una imagen.
entrypoint.sh selecciona coordinator, ray-head o ray-worker. Compose está prohibido.

Ver [OPERATIONS.md](OPERATIONS.md) para construcción, arranque y validación.
Validado con contenedores reales: dos ciclos de recreación tras aplicar la
mitigación gRPC documentada en OPERATIONS.md, diagnóstico final correcto y
persistencia del archivo de prueba. Las pruebas automatizadas de scripts usan
dobles; la ejecución de tareas en cada worker corresponde al paso 1.6.
