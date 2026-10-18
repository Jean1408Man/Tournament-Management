"""Guardas estáticas de dependencias; también recorren imports dentro de funciones."""

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "tournament_management"
ALLOWED = {
    "domain": {"domain"},
    "application": {"application", "domain", "observability"},
    "referee": {"referee", "domain", "observability"},
    "api": {"api", "application", "domain", "observability"},
    "observability": {"observability"},
}
INFRASTRUCTURE = {"ray", "fastapi", "uvicorn", "sqlite3", "sqlalchemy", "requests", "httpx"}


def violations(source: str, module: str, *, package: bool = False) -> list[str]:
    errors = []
    parts = module.split(".")
    layer = parts[1] if len(parts) > 1 else ""
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
            if top == "ray" and module not in {
                "tournament_management.adapters.ray_execution",
                "tournament_management.adapters.ray_membership",
            }:
                errors.append(f"{module}: Ray fuera de adaptadores autorizados")
            if layer in {"domain", "application", "referee"} and top in INFRASTRUCTURE:
                errors.append(f"{module}: infraestructura prohibida {imported}")
            if imported.startswith("tournament_management.") and layer in ALLOWED:
                target = imported.split(".")[1]
                if target not in ALLOWED[layer]:
                    errors.append(f"{module}: dependencia prohibida {imported}")
    return errors


def test_project_respects_dependency_boundaries() -> None:
    errors = []
    for path in PACKAGE.rglob("*.py"):
        parts = list(path.relative_to(PACKAGE.parent).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts.pop()
        errors.extend(violations(path.read_text(), ".".join(parts), package=is_package))
    for path in (ROOT / "bots").rglob("*.py"):
        tree = ast.parse(path.read_text())
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
        ("import ray", "tournament_management.domain.nim"),
        ("from ray import remote", "tournament_management.api.routes"),
        ("from ..adapters import sqlite_store", "tournament_management.application.scheduler"),
        ("import sqlite3", "tournament_management.referee.runner"),
        ("def f():\n    import ray", "tournament_management.cli"),
    ],
)
def test_guard_rejects_forbidden_imports(source: str, module: str) -> None:
    assert violations(source, module)


@pytest.mark.parametrize(
    ("source", "module"),
    [
        ("import ray", "tournament_management.adapters.ray_execution"),
        ("from ..domain import matches", "tournament_management.application.scheduler"),
        ("from . import schemas", "tournament_management.api.routes"),
    ],
)
def test_guard_accepts_allowed_imports(source: str, module: str) -> None:
    assert not violations(source, module)
