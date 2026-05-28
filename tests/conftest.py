from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
M3_SOURCE = REPO_ROOT / "vendor" / "legend-pure" / "m3.pure"


@pytest.fixture(scope="session")
def m3_source() -> str:
    return M3_SOURCE.read_text(encoding="utf-8")
