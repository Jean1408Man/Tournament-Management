# Especificaciones de ejemplo

`match.json` ejecuta una partida. `elimination.json` y `round-robin.json`
simulan torneos locales con `tm simulate --spec examples/<archivo>.json`.
El formato acepta IDs únicos, bots Python del directorio `bots/`, montones
iniciales, primer jugador y semilla. Se usa una partida por pareja.
En round-robin, los empates en victorias se deshacen por el orden de inscripción.
Un resultado sin ganador bloquea el torneo y el comando termina con código 1.
