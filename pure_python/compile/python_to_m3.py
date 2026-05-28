"""Compile plain Python dataclasses (and enums) into Pure M3 instances.

A dataclass becomes a :class:`m3.Class`; each field becomes an
:class:`m3.Property` whose ``genericType`` and ``multiplicity`` are inferred
from the field's type hint:

* ``str/int/float/bool/Decimal/bytes/date/datetime/time`` -> the matching Pure
  primitive type
* a nested dataclass -> a Pure ``Class`` (built once, shared, so self- and
  mutual references resolve to the same instance)
* an ``enum.Enum`` -> a Pure ``Enumeration``
* ``X``  -> multiplicity [1..1]; ``X | None`` -> [0..1]; ``list[X]`` -> [0..*]
"""

from __future__ import annotations

import collections.abc
import dataclasses
import datetime
import decimal
import enum
import types
import typing

from pure_python import m3

# Exact-type -> Pure primitive singleton. ``bool`` precedes ``int`` and
# ``datetime`` precedes ``date`` only matters for issubclass checks; lookups
# here are by exact type so order is irrelevant, but both are listed.
_PRIMITIVE: dict[type, m3.PrimitiveType] = {
    str: m3.String,
    bool: m3.Boolean,
    int: m3.Integer,
    float: m3.Float,
    decimal.Decimal: m3.Decimal,
    bytes: m3.Byte,
    datetime.datetime: m3.DateTime,
    datetime.date: m3.StrictDate,
    datetime.time: m3.StrictTime,
}

_MULTIPLICITY: dict[tuple[int, int | None], m3.PackageableMultiplicity] = {
    (1, 1): m3.PureOne,
    (0, 1): m3.ZeroOne,
    (0, None): m3.ZeroMany,
    (1, None): m3.OneMany,
    (0, 0): m3.PureZero,
}

_COLLECTION_ORIGINS = (
    list,
    set,
    frozenset,
    tuple,
    collections.abc.Sequence,
    collections.abc.Set,
    collections.abc.MutableSequence,
)


class Compiler:
    """Stateful converter; caches built types so references are shared."""

    def __init__(self, package: str | None = None):
        self.package = package
        self.classes: dict[type, m3.Class] = {}
        self.enums: dict[type, m3.Enumeration] = {}

    def to_class(self, py_type: type) -> m3.Class:
        if py_type in self.classes:
            return self.classes[py_type]
        if not dataclasses.is_dataclass(py_type):
            raise TypeError(f"{py_type!r} is not a dataclass")
        cls = m3.Class(name=py_type.__name__, package=self.package)
        self.classes[py_type] = cls  # register before fields so recursion terminates
        hints = typing.get_type_hints(py_type)
        properties: list[m3.Property] = []
        for f in dataclasses.fields(py_type):
            raw, lower, upper = self._resolve(hints.get(f.name, f.type))
            properties.append(
                m3.Property(
                    name=f.name,
                    genericType=m3.GenericType(rawType=raw),
                    multiplicity=_MULTIPLICITY[(lower, upper)],
                    owner=cls,
                    aggregation=m3.AggregationKind.None_,
                )
            )
        cls.properties = properties
        return cls

    def to_enumeration(self, py_enum: type) -> m3.Enumeration:
        if py_enum in self.enums:
            return self.enums[py_enum]
        enumeration = m3.Enumeration(name=py_enum.__name__, package=self.package)
        self.enums[py_enum] = enumeration
        enumeration.values = [m3.Enum(name=member.name) for member in py_enum]
        return enumeration

    def to_type(self, annotation: object) -> m3.Type:
        if isinstance(annotation, type):
            if annotation in _PRIMITIVE:
                return _PRIMITIVE[annotation]
            if issubclass(annotation, enum.Enum):
                return self.to_enumeration(annotation)
            if dataclasses.is_dataclass(annotation):
                return self.to_class(annotation)
        if annotation is typing.Any:
            return m3.Any()
        raise TypeError(f"cannot map Python type {annotation!r} to a Pure type")

    def _resolve(self, annotation: object) -> tuple[m3.Type, int, int | None]:
        optional = False
        origin = typing.get_origin(annotation)
        if origin is typing.Union or origin is types.UnionType:
            args = typing.get_args(annotation)
            non_none = [a for a in args if a is not type(None)]
            optional = len(non_none) != len(args)
            if len(non_none) != 1:
                raise TypeError(f"unsupported union type {annotation!r}")
            annotation = non_none[0]
            origin = typing.get_origin(annotation)
        if origin in _COLLECTION_ORIGINS:
            args = typing.get_args(annotation)
            inner = args[0] if args else typing.Any
            return self.to_type(inner), 0, None
        return self.to_type(annotation), (0 if optional else 1), 1


def compile_class(py_type: type, package: str | None = None) -> m3.Class:
    return Compiler(package).to_class(py_type)


def compile_enumeration(py_enum: type, package: str | None = None) -> m3.Enumeration:
    return Compiler(package).to_enumeration(py_enum)
