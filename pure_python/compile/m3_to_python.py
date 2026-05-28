"""Emit idiomatic Python dataclass source from Pure M3 instances.

The inverse of :mod:`pure_python.compile.python_to_m3`: walk an ``m3.Class``
(and the enumerations / classes it references) and render a self-contained,
importable Python module. Type parameters become ``typing.Generic[...]`` with
``TypeVar`` declarations, stereotypes / tagged values become
``typing.Annotated`` metadata, and qualified properties become ``@property``
stubs.
"""

from __future__ import annotations

import keyword
import re
from dataclasses import dataclass, field

from pure_python import m3

from .annotations import Stereotype as StereotypeMarker
from .annotations import Tag as TagMarker

_PURE_TO_PY: dict[str, str] = {
    "String": "str",
    "Boolean": "bool",
    "Integer": "int",
    "Float": "float",
    "Number": "float",
    "Decimal": "Decimal",
    "Byte": "bytes",
    "StrictDate": "datetime.date",
    "Date": "datetime.date",
    "LatestDate": "datetime.date",
    "DateTime": "datetime.datetime",
    "StrictTime": "datetime.time",
}


@dataclass
class _Ctx:
    imports: set[str] = field(default_factory=set)
    typevars: set[str] = field(default_factory=set)


def _bounds(mult: m3.Multiplicity | None) -> tuple[int, int | None]:
    if mult is None:
        return 1, 1
    lower = mult.lowerBound.value if mult.lowerBound and mult.lowerBound.value is not None else 0
    upper = mult.upperBound.value if mult.upperBound and mult.upperBound.value is not None else None
    return lower, upper


def _base_type(raw: object, ctx: _Ctx) -> str:
    if isinstance(raw, m3.PrimitiveType):
        py = _PURE_TO_PY.get(raw.name or "", "typing.Any")
    elif isinstance(raw, (m3.Class, m3.Enumeration)):
        py = raw.name or "typing.Any"
    else:
        py = "typing.Any"  # m3.Any, unset rawType
    if py.startswith("datetime."):
        ctx.imports.add("import datetime")
    elif py == "Decimal":
        ctx.imports.add("from decimal import Decimal")
    elif py.startswith("typing."):
        ctx.imports.add("import typing")
    return py


def _generic_annotation(generic: m3.GenericType | None, ctx: _Ctx) -> str:
    if generic is None:
        ctx.imports.add("import typing")
        return "typing.Any"
    if generic.typeParameter is not None:
        name = generic.typeParameter.name
        ctx.typevars.add(name)
        return name
    base = _base_type(generic.rawType, ctx)
    if generic.typeArguments:
        args = ", ".join(_generic_annotation(a, ctx) for a in generic.typeArguments)
        return f"{base}[{args}]"
    return base


def _marker_reprs(prop: m3.Property) -> list[str]:
    out: list[str] = []
    for stereotype in getattr(prop, "stereotypes", []):
        out.append(repr(StereotypeMarker(profile=stereotype.profile.name, value=stereotype.value)))
    for tagged in getattr(prop, "taggedValues", []):
        out.append(
            repr(TagMarker(profile=tagged.tag.profile.name, name=tagged.tag.value, value=tagged.value))
        )
    return out


def _typed_annotation(generic: m3.GenericType | None, mult: m3.Multiplicity | None, ctx: _Ctx) -> tuple[str, bool]:
    """Return (annotation, is_required)."""
    base = _generic_annotation(generic, ctx)
    lower, upper = _bounds(mult)
    if upper is None or upper > 1:
        return f"list[{base}]", False
    if lower >= 1:
        return base, True
    return f"{base} | None", False


@dataclass
class _Field:
    name: str
    annotation: str
    default: str | None
    required: bool

    def render(self) -> str:
        if self.default is None:
            return f"    {self.name}: {self.annotation}"
        return f"    {self.name}: {self.annotation} = {self.default}"


def _field_for(prop: m3.Property, ctx: _Ctx) -> _Field:
    annotation, required = _typed_annotation(prop.genericType, prop.multiplicity, ctx)
    markers = _marker_reprs(prop)
    if markers:
        ctx.imports.add("import typing")
        ctx.imports.add("from pure_python.compile import Stereotype, Tag")
        annotation = f"typing.Annotated[{annotation}, {', '.join(markers)}]"
    name = prop.name + ("_" if keyword.iskeyword(prop.name) else "")
    lower, upper = _bounds(prop.multiplicity)
    if upper is None or upper > 1:
        return _Field(name, annotation, "field(default_factory=list)", required=False)
    if required:
        return _Field(name, annotation, None, required=True)
    return _Field(name, annotation, "None", required=False)


def _qualified_property_source(qp: m3.QualifiedProperty, ctx: _Ctx) -> str:
    annotation, _ = _typed_annotation(qp.genericType, qp.multiplicity, ctx)
    name = qp.name + ("_" if keyword.iskeyword(qp.name) else "")
    return f"    @property\n    def {name}(self) -> {annotation}:\n        ..."


def to_source(cls: m3.Class, ctx: _Ctx | None = None) -> str:
    """Render a single dataclass definition (no module header)."""
    context = ctx if ctx is not None else _Ctx()
    # kw_only=True so inherited defaulted fields never force an ordering on a
    # subclass's required fields; field order then just follows the metamodel.
    ordered = [_field_for(p, context) for p in cls.properties]

    base_names = [
        g.general.rawType.name
        for g in cls.generalizations
        if isinstance(g.general.rawType, m3.Class)
    ]
    if cls.typeParameters:
        params = ", ".join(tp.name for tp in cls.typeParameters)
        for tp in cls.typeParameters:
            context.typevars.add(tp.name)
        context.imports.add("import typing")
        base_names.append(f"typing.Generic[{params}]")
    bases = f"({', '.join(base_names)})" if base_names else ""

    lines = ["@dataclass(kw_only=True)", f"class {cls.name}{bases}:"]
    body = [f.render() for f in ordered]
    qualified = [_qualified_property_source(qp, context) for qp in cls.qualifiedProperties]
    if body and qualified:
        body.append("")  # blank line between fields and @property stubs
    body += qualified
    if body:
        lines.extend(body)
    else:
        lines.append("    pass")
    return "\n".join(lines)


def _enum_member(name: str) -> str:
    ident = re.sub(r"\W", "_", name)
    if not ident or ident[0].isdigit():
        ident = "_" + ident
    if keyword.iskeyword(ident) or ident in ("None", "True", "False"):
        ident += "_"
    return ident


def _enum_source(enumeration: m3.Enumeration) -> str:
    lines = [f"class {enumeration.name}(enum.Enum):"]
    if enumeration.values:
        for value in enumeration.values:
            lines.append(f'    {_enum_member(value.name or "")} = "{value.name}"')
    else:
        lines.append("    pass")
    return "\n".join(lines)


def _referenced_types(generic: m3.GenericType | None) -> list[m3.Type]:
    if generic is None:
        return []
    found: list[m3.Type] = []
    if isinstance(generic.rawType, (m3.Class, m3.Enumeration)):
        found.append(generic.rawType)
    for arg in generic.typeArguments:
        found.extend(_referenced_types(arg))
    return found


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
                stack.extend(_referenced_types(prop.genericType))
            for qp in node.qualifiedProperties:
                stack.extend(_referenced_types(qp.genericType))
            for generalization in node.generalizations:
                raw = generalization.general.rawType if generalization.general else None
                if isinstance(raw, m3.Class):
                    stack.append(raw)
    return classes, enums


def _topo_sorted(classes: list[m3.Class]) -> list[m3.Class]:
    """Order classes so a base class is emitted before its subclasses."""
    by_name = {c.name: c for c in classes}
    ordered: list[m3.Class] = []
    visited: set[int] = set()

    def visit(cls: m3.Class) -> None:
        if id(cls) in visited:
            return
        visited.add(id(cls))
        for generalization in cls.generalizations:
            raw = generalization.general.rawType if generalization.general else None
            if isinstance(raw, m3.Class) and raw.name in by_name:
                visit(by_name[raw.name])
        ordered.append(cls)

    for cls in classes:
        visit(cls)
    return ordered


def to_module(*roots: m3.Type) -> str:
    """Render a self-contained module for the given classes/enumerations and their deps."""
    classes, enums = _collect(roots)
    classes = _topo_sorted(classes)
    ctx = _Ctx()
    enum_blocks = [_enum_source(e) for e in enums]
    class_blocks = [to_source(c, ctx) for c in classes]

    if enums:
        ctx.imports.add("import enum")
    ctx.imports.add("from dataclasses import dataclass, field")
    if ctx.typevars:
        ctx.imports.add("import typing")
    plain = sorted(i for i in ctx.imports if i.startswith("import "))
    froms = sorted(i for i in ctx.imports if i.startswith("from "))

    header_lines = ["from __future__ import annotations", "", *plain, *froms]
    if ctx.typevars:
        header_lines.append("")
        header_lines.extend(f'{name} = typing.TypeVar("{name}")' for name in sorted(ctx.typevars))
    header = "\n".join(header_lines)

    blocks = [header] + enum_blocks + class_blocks
    return "\n\n\n".join(b for b in blocks if b) + "\n"
