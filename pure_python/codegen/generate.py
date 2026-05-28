"""Generate ``pure_python/m3/metamodel.py`` from the vendored ``m3.pure``.

Usage::

    python -m pure_python.codegen.generate [SOURCE] [OUTPUT]
"""

from __future__ import annotations

import sys
from pathlib import Path

from .emit import emit_module
from .grammar import parse_grammar
from .schema import MetaModel, load_metamodel

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VENDOR = _REPO_ROOT / "vendor" / "legend-pure"
DEFAULT_SOURCE = _VENDOR / "m3.pure"
DEFAULT_GRAMMAR_SOURCES = (
    _VENDOR / "relation.pure",
    _VENDOR / "variant.pure",
    _VENDOR / "milestoning.pure",
)
DEFAULT_OUTPUT = _REPO_ROOT / "pure_python" / "m3" / "metamodel.py"


def _merge_grammar(model: MetaModel, source: Path) -> None:
    result = parse_grammar(source.read_text(encoding="utf-8"))
    for cls in result.classes:
        if cls.name in model.classes:
            raise ValueError(f"duplicate class {cls.name} from {source.name}")
        model.classes[cls.name] = cls
    for enumeration in result.enums:
        model.enums.setdefault(enumeration.name, enumeration)
    for profile in result.profiles:
        model.profiles[profile.name] = profile
    for association in result.associations:
        model.associations[association.name] = association


def build_model(
    source: Path = DEFAULT_SOURCE,
    grammar_sources: tuple[Path, ...] = DEFAULT_GRAMMAR_SOURCES,
) -> MetaModel:
    model = load_metamodel(str(source))
    for grammar_source in grammar_sources:
        _merge_grammar(model, grammar_source)
    return model


def render(
    source: Path = DEFAULT_SOURCE,
    grammar_sources: tuple[Path, ...] = DEFAULT_GRAMMAR_SOURCES,
) -> str:
    return emit_module(build_model(source, grammar_sources))


def generate(
    source: Path = DEFAULT_SOURCE,
    output: Path = DEFAULT_OUTPUT,
    grammar_sources: tuple[Path, ...] = DEFAULT_GRAMMAR_SOURCES,
) -> MetaModelSummary:
    model = build_model(source, grammar_sources)
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
