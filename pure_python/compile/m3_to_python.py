"""Emit idiomatic Python dataclass source from Pure M3 instances.

The inverse of :mod:`pure_python.compile.python_to_m3`: walk an ``m3.Class``
(and the enumerations / classes it references) and render a self-contained,
importable Python module of plain dataclasses.
"""

from __future__ import annotations

import keyword
import re

from pure_python import m3

_PURE_TO_PY: dict[str, str] = {
    "String": "str",
    "Boolean": "bool",
    "Integer": "int",
    "Float": "float",
    "Number": "float",
    "Decimal": "Decimal",
    "Byte": "int",
    "StrictDate": "datetime.date",
    "Date": "datetime.date",
    "LatestDate": "datetime.date",
    "DateTime": "datetime.datetime",
    "StrictTime": "datetime.time",
}


def _bounds(mult: m3.Multiplicity | None) -> tuple[int, int | None]:
    if mult is None:
        return 1, 1
    lower = mult.lowerBound.value if mult.lowerBound and mult.lowerBound.value is not None else 0
    upper = mult.upperBound.value if mult.upperBound and mult.upperBound.value is not None else None
    return lower, upper


def _base_type(raw: object) -> str:
    if isinstance(raw, m3.PrimitiveType):
        return _PURE_TO_PY.get(raw.name or "", "typing.Any")
    if isinstance(raw, (m3.Class, m3.Enumeration)):
        return raw.name or "typing.Any"
    return "typing.Any"  # m3.Any, type parameters, unset rawType


def _enum_member(name: str) -> str:
    ident = re.sub(r"\W", "_", name)
    if not ident or ident[0].isdigit():
        ident = "_" + ident
    if keyword.iskeyword(ident) or ident in ("None", "True", "False"):
        ident += "_"
    return ident


def _imports_for(base: str, imports: set[str]) -> None:
    if base.startswith("datetime."):
        imports.add("import datetime")
    elif base == "Decimal":
        imports.add("from decimal import Decimal")
    elif base.startswith("typing."):
        imports.add("import typing")


class _Field:
    def __init__(self, name: str, annotation: str, default: str | None, required: bool):
        self.name = name + ("_" if keyword.iskeyword(name) else "")
        self.annotation = annotation
        self.default = default
        self.required = required

    def render(self) -> str:
        if self.default is None:
            return f"    {self.name}: {self.annotation}"
        return f"    {self.name}: {self.annotation} = {self.default}"


def _field_for(prop: m3.Property, imports: set[str]) -> _Field:
    raw = prop.genericType.rawType if prop.genericType else None
    base = _base_type(raw)
    _imports_for(base, imports)
    lower, upper = _bounds(prop.multiplicity)
    if upper is None or upper > 1:
        return _Field(prop.name, f"list[{base}]", "field(default_factory=list)", required=False)
    if lower >= 1:
        return _Field(prop.name, base, None, required=True)
    return _Field(prop.name, f"{base} | None", "None", required=False)


def to_source(cls: m3.Class, imports: set[str] | None = None) -> str:
    """Render a single dataclass definition (no module header)."""
    collected = imports if imports is not None else set()
    fields = [_field_for(p, collected) for p in cls.properties]
    # Required (no default) must precede defaulted fields for a valid dataclass.
    ordered = [f for f in fields if f.required] + [f for f in fields if not f.required]
    lines = ["@dataclass", f"class {cls.name}:"]
    if ordered:
        lines.extend(f.render() for f in ordered)
    else:
        lines.append("    pass")
    return "\n".join(lines)


def _enum_source(enumeration: m3.Enumeration) -> str:
    lines = [f"class {enumeration.name}(enum.Enum):"]
    if enumeration.values:
        for value in enumeration.values:
            member = _enum_member(value.name or "")
            lines.append(f'    {member} = "{value.name}"')
    else:
        lines.append("    pass")
    return "\n".join(lines)


def _collect(roots: tuple[m3.Type, ...]) -> tuple[list[m3.Class], list[m3.Enumeration]]:
    classes: list[m3.Class] = []
    enums: list[m3.Enumeration] = []
    seen: set[int] = set()
    stack: list[m3.Type] = list(roots)
    while stack:
        node = stack.pop(0)
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, m3.Enumeration):
            enums.append(node)
        elif isinstance(node, m3.Class):
            classes.append(node)
            for prop in node.properties:
                raw = prop.genericType.rawType if prop.genericType else None
                if isinstance(raw, (m3.Class, m3.Enumeration)):
                    stack.append(raw)
    return classes, enums


def to_module(*roots: m3.Type) -> str:
    """Render a self-contained module for the given classes/enumerations and their deps."""
    classes, enums = _collect(roots)
    imports: set[str] = set()
    enum_blocks = [_enum_source(e) for e in enums]
    class_blocks = [to_source(c, imports) for c in classes]

    if enums:
        imports.add("import enum")
    imports.add("from dataclasses import dataclass, field")
    plain = sorted(i for i in imports if i.startswith("import "))
    froms = sorted(i for i in imports if i.startswith("from "))
    header = "\n".join(["from __future__ import annotations", "", *plain, *froms])

    blocks = [header] + enum_blocks + class_blocks
    return "\n\n\n".join(b for b in blocks if b) + "\n"
