"""Interfaz de línea de comandos del artefacto desplegable."""

from __future__ import annotations

import argparse

from tournament_management import __version__


def build_parser() -> argparse.ArgumentParser:
    """Crea el parser raíz sin registrar comandos aún no implementados."""
    parser = argparse.ArgumentParser(
        prog="tm",
        description="Plataforma distribuida para ejecutar torneos computacionales.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main() -> None:
    """Punto de entrada del comando ``tm``."""
    build_parser().parse_args()
