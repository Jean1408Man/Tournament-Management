"""Guardas estáticas de dependencias; también recorren imports dentro de funciones."""

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "tournament_management"
# Módulos internos que cada módulo de dominio puede importar. La raíz del
# paquete (app, runtime, cli, commands, config) es la composición y no se limita.
ALLOWED = {
    "shared": {"shared"},
    "game": {"game", "shared"},
    "competitors": {"competitors", "shared", "api"},
    "events": {"events", "shared", "api"},
    "tournaments": {"tournaments", "shared", "events", "api"},
    "execution": {"execution", "shared", "game", "competitors", "tournaments", "events", "api"},
    "cluster": {"cluster", "shared", "execution"},
    "simulation": {"simulation", "shared", "game", "tournaments", "execution"},
    "database": {"database", "shared", "competitors", "tournaments", "events"},
    "api": {"api", "shared", "competitors", "tournaments", "execution", "events"},
    "verification": {"verification", "shared", "tournaments"},
}
HTTP = {"fastapi", "starlette", "pydantic", "uvicorn", "requests", "httpx"}
DATABASE = {"psycopg", "sqlite3", "sqlalchemy"}


def ray_allowed(module: str) -> bool:
    # Fase 1: diagnóstico en cluster/ray_*; la etapa 4.1 añadirá execution/backends/ray.
    return module.startswith("tournament_management.cluster.ray_") or (
        module == "tournament_management.execution.backends.ray"
    )


def http_allowed(module: str, owner: str) -> bool:
    return owner in {"", "api"} or module.rsplit(".", 1)[-1] in {"api", "schemas"}


def violations(source: str, module: str, *, package: bool = False) -> list[str]:
    errors = []
    parts = module.split(".")
    # Un archivo directamente bajo el paquete (app.py, cli.py…) pertenece a la raíz.
    owner = parts[1] if len(parts) > 2 or (len(parts) == 2 and package) else ""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                context = module if package else module.rpartition(".")[0]
                base = resolve_name("." * node.level + base, context)
            imports = [base, *(f"{base}.{alias.name}" for alias in node.names)]
        else:
            continue
        for imported in imports:
            top = imported.split(".")[0]
            if top == "ray" and not ray_allowed(module):
                errors.append(f"{module}: Ray fuera de cluster/ray_*")
            if top in HTTP and not http_allowed(module, owner):
                errors.append(f"{module}: HTTP fuera de api.py/schemas.py {imported}")
            if top in DATABASE and owner not in {"", "database"}:
                errors.append(f"{module}: controlador de base fuera de database/ {imported}")
            if imported.startswith("tournament_management.") and owner in ALLOWED:
                target = imported.split(".")[1]
                if (PACKAGE / target).is_dir() and target not in ALLOWED[owner]:
                    errors.append(f"{module}: dependencia prohibida {imported}")
    return errors


def test_project_respects_dependency_boundaries() -> None:
    errors = []
    for path in PACKAGE.rglob("*.py"):
        parts = list(path.relative_to(PACKAGE.parent).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts.pop()
        errors.extend(
            violations(path.read_text(encoding="utf-8"), ".".join(parts), package=is_package)
        )
    for path in (ROOT / "bots").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.split(".")[0] == "tournament_management" for name in names):
                errors.append(f"{path}: bot acoplado al paquete")
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize(
    ("source", "module"),
    [
        ("import ray", "tournament_management.game.nim"),
        ("from ray import remote", "tournament_management.api.routes"),
        ("def f():\n    import ray", "tournament_management.cli"),
        ("import ray", "tournament_management.execution.coordinator"),
        ("from ..execution import coordinator", "tournament_management.tournaments.service"),
        ("from tournament_management.api import errors", "tournament_management.game.referee"),
        ("import fastapi", "tournament_management.tournaments.service"),
        ("import psycopg", "tournament_management.execution.recovery"),
        ("import sqlite3", "tournament_management.game.referee"),
        ("from tournament_management.database import connection", "tournament_management.shared"),
    ],
)
def test_guard_rejects_forbidden_imports(source: str, module: str) -> None:
    assert violations(source, module, package=module.endswith(".shared"))


@pytest.mark.parametrize(
    ("source", "module"),
    [
        ("import ray", "tournament_management.cluster.ray_probe"),
        ("import ray", "tournament_management.execution.backends.ray"),
        ("from ..tournaments import service", "tournament_management.execution.coordinator"),
        ("from fastapi import APIRouter", "tournament_management.tournaments.api"),
        ("from . import common", "tournament_management.api.routes"),
        ("import psycopg", "tournament_management.database.connection"),
        ("from tournament_management.execution import coordinator", "tournament_management.app"),
    ],
)
def test_guard_accepts_allowed_imports(source: str, module: str) -> None:
    assert not violations(source, module)
