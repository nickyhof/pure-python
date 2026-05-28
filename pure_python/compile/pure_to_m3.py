"""Build Pure M3 instances from Pure grammar source.

The inverse of :mod:`pure_python.compile.m3_to_pure`: parse Pure source with
:mod:`pure_python.codegen.grammar` and lift the resulting schema into live
``m3`` instances (``Class`` / ``Enumeration`` with shared references), closing
the ``m3 -> Pure -> m3`` loop.

Fidelity is bounded by the grammar parser: classes, packages, type parameters,
generalizations, properties (name, type + arguments, multiplicity) and
enumerations are preserved; stereotypes, tagged values and qualified
properties are not (the parser skips them).
"""

from __future__ import annotations

from pure_python import m3
from pure_python.codegen.grammar import parse_grammar
from pure_python.codegen.schema import TypeRef

_MULTIPLICITY: dict[tuple[int, int | None], m3.PackageableMultiplicity] = {
    (1, 1): m3.PureOne,
    (0, 1): m3.ZeroOne,
    (0, None): m3.ZeroMany,
    (1, None): m3.OneMany,
    (0, 0): m3.PureZero,
}


def _multiplicity(lower: int, upper: int | None) -> m3.Multiplicity:
    if (lower, upper) in _MULTIPLICITY:
        return _MULTIPLICITY[(lower, upper)]
    upper_bound = m3.MultiplicityValue(value=upper) if upper is not None else None
    return m3.Multiplicity(lowerBound=m3.MultiplicityValue(value=lower), upperBound=upper_bound)


def _resolve(name: str | None, registry: dict[str, m3.Type]) -> m3.Type | None:
    if name in registry:
        return registry[name]
    candidate = getattr(m3, name, None) if name else None
    if isinstance(candidate, m3.PrimitiveType):
        return candidate
    if name == "Any":
        return m3.Any()
    return None  # unresolved -- leave rawType empty


def _generic(
    name: str | None,
    is_type_parameter: bool,
    arguments: list[TypeRef],
    registry: dict[str, m3.Type],
) -> m3.GenericType:
    if is_type_parameter:
        return m3.GenericType(typeParameter=m3.TypeParameter(name=name))
    return m3.GenericType(
        rawType=_resolve(name, registry),
        typeArguments=[_generic(a.name, a.is_type_parameter, a.arguments, registry) for a in arguments],
    )


def from_pure(source: str) -> dict[str, m3.Type]:
    """Parse Pure source and return a ``name -> m3 instance`` registry."""
    result = parse_grammar(source)
    registry: dict[str, m3.Type] = {}

    for meta in result.classes:
        registry[meta.name] = m3.Class(name=meta.name, package=meta.package or None)
    for meta in result.enums:
        enumeration = m3.Enumeration(name=meta.name, package=meta.package or None)
        enumeration.values = [m3.Enum(name=value) for value in meta.values]
        registry[meta.name] = enumeration

    for meta in result.classes:
        cls = registry[meta.name]
        cls.typeParameters = [m3.TypeParameter(name=name) for name in meta.type_parameters]
        for base in meta.bases:
            if base == "Any":
                continue  # the implicit root -- not modelled as an explicit generalization
            cls.generalizations.append(
                m3.Generalization(general=m3.GenericType(rawType=_resolve(base, registry)), specific=cls)
            )
        cls.properties = [
            m3.Property(
                name=p.name,
                genericType=_generic(p.type_name, p.is_type_parameter, p.type_arguments, registry),
                multiplicity=_multiplicity(p.lower, p.upper),
                owner=cls,
                aggregation=m3.AggregationKind.None_,
            )
            for p in meta.properties
        ]
    return registry
