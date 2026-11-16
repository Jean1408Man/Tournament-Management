# Revisión de documentación y correspondencia con P03

La referencia oficial disponible es
[orientación P03](../docs/P03_Plataforma_distribuida_de_ejecucion_de_torneos.md).
La [planificación de Fase 1](../docs/tournament-management-guia-fase1.md) es del
proyecto: sus escenarios concretos no son requisitos adicionales del profesor.
La orientación disponible no incluye la rúbrica común completa.

| Aspecto de P03 | Implementación y comprobación | Límite |
| --- | --- | --- |
| Competidores ejecutables y formatos | Registro durable; eliminación y round-robin; contrato HTTP y pruebas | Python y Nim; código de laboratorio |
| Scheduling y concurrencia | Coordinador asigna intentos a workers; cargas y saturación | Capacidad configurable; disponibilidad condicionada a recursos |
| Pérdida de worker | Reintento con generación nueva; escenario de pérdida | Presupuesto finito y necesidad de otro worker disponible |
| Fallo deportivo frente a infraestructura | Escenario de bots defectuosos y escenarios de infraestructura | Seis defectos automatizados; no prueba exhaustiva de aislamiento |
| Duplicados y estado durable | Aceptación transaccional, resultado tardío y reinicio | Un coordinador activo; PostgreSQL y artefactos deben conservarse |
| Comunicación y reparación | Corte de red en Docker, comprobación de enlaces, reconexión | Un único host; sin ensayo de caída física ni partición coordinador–BD |
| Contrato externo y papel de Ray | OpenAPI, semántica, decisiones y mapa de middleware | El rediseño nativo sigue siendo una propuesta |
| Despliegue sin Compose | Dockerfile y scripts de comandos Docker | Recursos y permisos del equipo condicionan la reproducción |

La campaña puede falsar las garantías en las ejecuciones observadas; no prueba
su validez para todos los fallos posibles. La pérdida del head necesita
recuperación manual. La continuidad automática exigida ante pérdida de worker
no debe confundirse con alta disponibilidad de todos los componentes.

## Cambios de documentación

Se conserva `deploy/OPERATIONS.md` porque explica conservación de datos,
diagnóstico y recuperación. `deploy/REMOTE-TESTS.md` sustituye el nombre histórico
`STEP16-TESTS.md`: sigue siendo útil para comprobar que cada worker ejecuta
realmente una tarea. Las instrucciones usan la cantidad configurada de workers;
las cifras históricas se conservan únicamente para interpretar mediciones.

Se corrigieron las expectativas de partición: un intento original todavía
vigente puede terminar tras recuperar la red. Solo un resultado de un intento
sustituido debe descartarse. La campaña de bots defectuosos incluye seis casos,
no todos los archivos de `bots/faulty/`.

## Comprobaciones de esta revisión

- 32 pruebas de UI, contrato HTTP y verificador: aprobadas.
- Arranque HTTP de la UI en modos explícitos Docker y local, con puertos
  personalizados: respuesta 200 en ambos. El modo local se comprobó como
  servidor del panel, no como ejecución de un clúster local completo.
- Suite unitaria completa tras corregir el doble de prueba de artefactos:
  220 pruebas aprobadas. La corrección conserva las comprobaciones de copia
  privada, integridad y limpieza; añade al doble los campos usados por los logs.
- El primer intento de campaña usó una imagen anterior (`5d158ae-dirty`):
  se interrumpió tras detectar la diferencia y no valida la revisión actual.
  Se reconstruyó la imagen y se inició una pasada de toda la matriz con una
  repetición por escenario; los resultados se incorporarán al terminar.
