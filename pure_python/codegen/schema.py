"""Turn parsed ``m3.pure`` instances into a clean metamodel schema.

This walks the low-level instance graph and pulls out, for every metaclass,
the information needed to emit Python: its generalizations (base classes), its
declared properties (name, raw type, multiplicity), plus the enumerations,
primitive types and packageable multiplicities defined alongside it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .parser import Instance, Ref, Value, parse

# Named multiplicities defined in m3.pure -> (lower, upper); upper None == unbounded (*).
_NAMED_MULTIPLICITY: dict[str, tuple[int, int | None]] = {
    "PureZero": (0, 0),
    "PureOne": (1, 1),
    "ZeroOne": (0, 1),
    "ZeroMany": (0, None),
    "OneMany": (1, None),
}


@dataclass
class MetaProperty:
    name: str
    type_name: str | None  # raw type simple name; None for type-parameter / unresolved
    lower: int
    upper: int | None  # None == unbounded


@dataclass
class MetaClass:
    name: str
    package: str
    bases: list[str]
    properties: list[MetaProperty]


@dataclass
class MetaEnum:
    name: str
    package: str
    values: list[str]


@dataclass
class MetaPrimitive:
    name: str
    package: str
    base: str | None


@dataclass
class MetaMultiplicity:
    name: str
    package: str
    lower: int
    upper: int | None


@dataclass
class MetaModel:
    classes: dict[str, MetaClass] = field(default_factory=dict)
    enums: dict[str, MetaEnum] = field(default_factory=dict)
    primitives: dict[str, MetaPrimitive] = field(default_factory=dict)
    multiplicities: dict[str, MetaMultiplicity] = field(default_factory=dict)

    @property
    def type_names(self) -> set[str]:
        return set(self.classes) | set(self.enums) | set(self.primitives)


def _package_of(inst: Instance) -> str:
    if inst.package is not None:
        return inst.package.qualified
    pkg = inst.get("package")
    if isinstance(pkg, Ref):
        return pkg.path.qualified
    return ""


def _generalization_bases(inst: Instance) -> list[str]:
    value = inst.get("generalizations")
    if value is None:
        return []
    gens = value if isinstance(value, list) else [value]
    bases: list[str] = []
    for gen in gens:
        if not isinstance(gen, Instance):
            continue
        general = gen.get("general")
        if isinstance(general, Instance):
            raw = general.get("rawType")
            if isinstance(raw, Ref) and raw.target not in bases:
                bases.append(raw.target)
    return bases


def _resolve_multiplicity(value: Value | None) -> tuple[int, int | None]:
    if isinstance(value, Ref):
        return _NAMED_MULTIPLICITY.get(value.target, (0, None))
    if isinstance(value, Instance):  # inline Multiplicity, e.g. Association's [2]
        lower = _bound_value(value.get("lowerBound"), default=0)
        upper = _bound_value(value.get("upperBound"), default=None)
        return lower, upper
    return (0, None)


def _bound_value(value: Value | None, default: int | None) -> int | None:
    if isinstance(value, Instance):
        v = value.get("value")
        return v if isinstance(v, int) else default
    return default


def _raw_type(prop: Instance) -> str | None:
    generic = prop.get("genericType")
    if isinstance(generic, Instance):
        raw = generic.get("rawType")
        if isinstance(raw, Ref):
            return raw.target
    return None  # type parameter or unresolved


def _properties(inst: Instance) -> list[MetaProperty]:
    value = inst.get("properties")
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    result: list[MetaProperty] = []
    for prop in items:
        if not isinstance(prop, Instance):
            continue
        name = prop.get("name") or prop.name
        if not isinstance(name, str):
            continue
        lower, upper = _resolve_multiplicity(prop.get("multiplicity"))
        result.append(MetaProperty(name, _raw_type(prop), lower, upper))
    return result


def _enum_values(inst: Instance) -> list[str]:
    value = inst.get("values")
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    for v in items:
        if isinstance(v, Instance):
            nm = v.get("name") or v.name
            if isinstance(nm, str):
                out.append(nm)
    return out


def build_metamodel(instances: list[Instance]) -> MetaModel:
    model = MetaModel()
    for inst in instances:
        if inst.name is None:
            continue
        kind = inst.kind
        if kind == "Class":
            model.classes[inst.name] = MetaClass(
                name=inst.name,
                package=_package_of(inst),
                bases=_generalization_bases(inst),
                properties=_properties(inst),
            )
        elif kind == "Enumeration":
            model.enums[inst.name] = MetaEnum(inst.name, _package_of(inst), _enum_values(inst))
        elif kind == "PrimitiveType":
            base = None
            bases = _generalization_bases(inst)
            if bases:
                base = bases[0]
            model.primitives[inst.name] = MetaPrimitive(inst.name, _package_of(inst), base)
        elif kind == "PackageableMultiplicity":
            lower = _bound_value(inst.get("lowerBound"), default=0) or 0
            upper = _bound_value(inst.get("upperBound"), default=None)
            model.multiplicities[inst.name] = MetaMultiplicity(
                inst.name, _package_of(inst), lower, upper
            )
    return model


def load_metamodel(path: str) -> MetaModel:
    return build_metamodel(parse(open(path, encoding="utf-8").read()))
