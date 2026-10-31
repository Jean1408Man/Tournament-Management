"""meta.json de los escenarios (5.3): los secretos no llegan a la evidencia."""

import importlib.util
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "experiments/scenarios/meta.py"
spec = importlib.util.spec_from_file_location("scenario_meta", PATH)
meta = importlib.util.module_from_spec(spec)
spec.loader.exec_module(meta)


def test_settings_keep_tm_values_and_mask_secrets():
    lines = [
        "TM_INTERNAL_TOKEN=abc",
        "TM_POSTGRES_PASSWORD=pw",
        "TM_DATABASE_URL=postgresql://tm:pw@tm-postgres/db",
        "TM_MAX_RUNNING_ATTEMPTS=64",
        "PATH=/usr/bin",
    ]
    assert meta.settings(lines) == {
        "TM_DATABASE_URL": "***",
        "TM_INTERNAL_TOKEN": "***",
        "TM_MAX_RUNNING_ATTEMPTS": "64",
        "TM_POSTGRES_PASSWORD": "***",
    }
