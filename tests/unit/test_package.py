from tournament_management import __version__
from tournament_management.cli import build_parser


def test_package_exposes_its_version() -> None:
    assert __version__ == "0.1.0"


def test_cli_identifies_itself_as_tm() -> None:
    assert build_parser().prog == "tm"
