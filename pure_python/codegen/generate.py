"""Generate ``pure_python/m3/metamodel.py`` from the vendored ``m3.pure``.

Usage::

    python -m pure_python.codegen.generate [SOURCE] [OUTPUT]
"""

from __future__ import annotations

import sys
from pathlib import Path

from .emit import emit_module
from .schema import load_metamodel

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = _REPO_ROOT / "vendor" / "legend-pure" / "m3.pure"
DEFAULT_OUTPUT = _REPO_ROOT / "pure_python" / "m3" / "metamodel.py"


def generate(source: Path = DEFAULT_SOURCE, output: Path = DEFAULT_OUTPUT) -> MetaModelSummary:
    model = load_metamodel(str(source))
    output.write_text(emit_module(model), encoding="utf-8")
    return MetaModelSummary(
        classes=len(model.classes),
        enums=len(model.enums),
        primitives=len(model.primitives),
        multiplicities=len(model.multiplicities),
        output=output,
    )


class MetaModelSummary:
    def __init__(self, classes: int, enums: int, primitives: int, multiplicities: int, output: Path):
        self.classes = classes
        self.enums = enums
        self.primitives = primitives
        self.multiplicities = multiplicities
        self.output = output

    def __str__(self) -> str:
        return (
            f"Wrote {self.output} "
            f"({self.classes} classes, {self.enums} enums, "
            f"{self.primitives} primitives, {self.multiplicities} multiplicities)"
        )


def main(argv: list[str]) -> int:
    source = Path(argv[0]) if len(argv) > 0 else DEFAULT_SOURCE
    output = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUTPUT
    print(generate(source, output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
