"""Regenera el contrato versionado sin abrir servidores ni conexiones a la base."""

import json
from pathlib import Path

from tournament_management.app import create_app
from tournament_management.config import Settings

output = Path(__file__).resolve().parents[1] / "contracts" / "openapi-v1.json"
output.parent.mkdir(exist_ok=True)
output.write_text(
    json.dumps(create_app(Settings()).openapi(), indent=2, sort_keys=True, ensure_ascii=False)
    + "\n",
    encoding="utf-8",
)
print(output)
