from __future__ import annotations

from pathlib import Path

import pytest

from pure_python.codegen.parser import parse
from pure_python.codegen.schema import build_metamodel

REPO_ROOT = Path(__file__).resolve().parents[1]
M3_SOURCE = REPO_ROOT / "vendor" / "legend-pure" / "m3.pure"


@pytest.fixture(scope="session")
def m3_source() -> str:
    return M3_SOURCE.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def m3_instances(m3_source: str):
    """Parsed bootstrap ``m3.pure`` instance graph -- parsed once per session.

    The bootstrap parse is ~4s, so re-running it per test dominated the suite;
    tests that only read the result share this fixture.
    """
    return parse(m3_source)


@pytest.fixture(scope="session")
def m3_model(m3_source: str):
    """Lowered bootstrap metamodel -- parsed + built once per session."""
    return build_metamodel(parse(m3_source))
