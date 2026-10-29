# Bots

Scripts independientes con biblioteca estándar, comunicados por stdin/stdout.
En 2.2 se crearán optimal.py, random_bot.py y los defectuosos de faulty/.
No importan tournament_management.

`slow.py` (etapa 4.4) juega legal pero espera 0,5 s por jugada y quita 1 ficha:
entre dos `slow.py` la partida dura ≈ fichas totales × 0,5 s. Se usa en los
escenarios de `experiments/scenarios/` para matar o pausar un worker a mitad
de partida.
