"""Compile plain Python dataclasses (and enums) into Pure M3 instances.

A dataclass becomes a :class:`m3.Class`; each field becomes an
:class:`m3.Property` whose ``genericType`` and ``multiplicity`` are inferred
from the field's type hint:

* ``str/int/float/bool/Decimal/bytes/date/datetime/time`` -> the matching Pure
  primitive type
* a nested dataclass -> a Pure ``Class`` (built once, shared, so self- and
  mutual references resolve to the same instance)
* an ``enum.Enum`` -> a Pure ``Enumeration``
* a ``TypeVar`` -> a ``GenericType`` carrying a ``TypeParameter``; the owning
  class records its ``typeParameters`` from ``typing.Generic[...]``
* ``X`` -> [1..1]; ``X | None`` -> [0..1]; ``list[X]`` -> [0..*]

``typing.Annotated[T, Stereotype(...), Tag(...)]`` attaches Pure stereotypes and
tagged values, and ``@property`` accessors become qualified (derived) properties.
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

from .annotations import ENUM_VALUE_PROFILE, ENUM_VALUE_TAG
from .annotations import Stereotype as StereotypeMarker
from .annotations import Tag as TagMarker

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


def _strip_annotations(hint: object) -> tuple[object, tuple]:
    """Pull ``Annotated`` metadata out, even when nested inside a union.

    ``Annotated[str, M]`` -> ``(str, (M,))``;
    ``Annotated[str, M] | None`` -> ``(str | None, (M,))``.
    """
    if hasattr(hint, "__metadata__"):
        inner, markers = _strip_annotations(hint.__origin__)
        return inner, (*hint.__metadata__, *markers)
    origin = typing.get_origin(hint)
    if origin is typing.Union or origin is types.UnionType:
        cleaned: list = []
        markers: tuple = ()
        for arg in typing.get_args(hint):
            clean_arg, arg_markers = _strip_annotations(arg)
            cleaned.append(clean_arg)
            markers += arg_markers
        return typing.Union[tuple(cleaned)], markers
    return hint, ()


class Compiler:
    """Stateful converter; caches built types so references are shared."""

    def __init__(self, package: str | None = None):
        self.package = package
        self.classes: dict[type, m3.Class] = {}
        self.enums: dict[type, m3.Enumeration] = {}
        self._profiles: dict[str, m3.Profile] = {}

    def to_class(self, py_type: type) -> m3.Class:
        if py_type in self.classes:
            return self.classes[py_type]
        if not dataclasses.is_dataclass(py_type):
            raise TypeError(f"{py_type!r} is not a dataclass")
        cls = m3.Class(name=py_type.__name__, package=self.package)
        self.classes[py_type] = cls  # register before fields so recursion terminates
        for base in py_type.__bases__:
            if dataclasses.is_dataclass(base):
                cls.generalizations.append(
                    m3.Generalization(general=m3.GenericType(rawType=self.to_class(base)), specific=cls)
                )
        type_params = {
            tv.__name__: m3.TypeParameter(name=tv.__name__)
            for tv in getattr(py_type, "__parameters__", ())
        }
        cls.typeParameters = list(type_params.values())
        own_fields = set(py_type.__dict__.get("__annotations__", {}))
        hints = typing.get_type_hints(py_type, include_extras=True)
        properties: list[m3.Property] = []
        for f in dataclasses.fields(py_type):
            if f.name not in own_fields:
                continue  # inherited -- it belongs to the base class
            hint = hints.get(f.name, f.type)
            base, markers = _strip_annotations(hint)
            generic, lower, upper = self._resolve(base, type_params)
            stereotypes, tagged = self._stereotypes_and_tags(markers)
            properties.append(
                m3.Property(
                    name=f.name,
                    genericType=generic,
                    multiplicity=_MULTIPLICITY[(lower, upper)],
                    owner=cls,
                    aggregation=m3.AggregationKind.None_,
                    stereotypes=stereotypes,
                    taggedValues=tagged,
                )
            )
        cls.properties = properties
        cls.qualifiedProperties = self._qualified_properties(py_type, cls, type_params)
        return cls

    def to_enumeration(self, py_enum: type) -> m3.Enumeration:
        if py_enum in self.enums:
            return self.enums[py_enum]
        enumeration = m3.Enumeration(name=py_enum.__name__, package=self.package)
        self.enums[py_enum] = enumeration
        enumeration.values = [self._enum_value(member) for member in py_enum]
        return enumeration

    def _enum_value(self, member) -> m3.Enum:
        value = m3.Enum(name=member.name)
        if member.value != member.name:  # Pure enums are name-only; carry the value as a tag
            value.taggedValues = [
                m3.TaggedValue(
                    tag=m3.Tag(profile=self._profile(ENUM_VALUE_PROFILE), value=ENUM_VALUE_TAG),
                    value=repr(member.value),
                )
            ]
        return value

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

    # -- internals -----------------------------------------------------
    def _profile(self, name: str) -> m3.Profile:
        return self._profiles.setdefault(name, m3.Profile(name=name))

    def _stereotypes_and_tags(
        self, markers: tuple
    ) -> tuple[list[m3.Stereotype], list[m3.TaggedValue]]:
        stereotypes: list[m3.Stereotype] = []
        tagged: list[m3.TaggedValue] = []
        for marker in markers:
            if isinstance(marker, StereotypeMarker):
                stereotypes.append(
                    m3.Stereotype(profile=self._profile(marker.profile), value=marker.value)
                )
            elif isinstance(marker, TagMarker):
                tag = m3.Tag(profile=self._profile(marker.profile), value=marker.name)
                tagged.append(m3.TaggedValue(tag=tag, value=marker.value))
        return stereotypes, tagged

    def _generic_type(
        self, annotation: object, type_params: dict[str, m3.TypeParameter]
    ) -> m3.GenericType:
        if isinstance(annotation, typing.TypeVar):
            param = type_params.get(annotation.__name__) or m3.TypeParameter(
                name=annotation.__name__
            )
            return m3.GenericType(typeParameter=param)
        origin = typing.get_origin(annotation)
        if origin is not None and dataclasses.is_dataclass(origin):
            args = typing.get_args(annotation)
            return m3.GenericType(
                rawType=self.to_class(origin),
                typeArguments=[self._generic_type(a, type_params) for a in args],
            )
        return m3.GenericType(rawType=self.to_type(annotation))

    def _resolve(
        self, annotation: object, type_params: dict[str, m3.TypeParameter]
    ) -> tuple[m3.GenericType, int, int | None]:
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
            return self._generic_type(inner, type_params), 0, None
        return self._generic_type(annotation, type_params), (0 if optional else 1), 1

    def _qualified_properties(
        self, py_type: type, owner: m3.Class, type_params: dict[str, m3.TypeParameter]
    ) -> list[m3.QualifiedProperty]:
        result: list[m3.QualifiedProperty] = []
        for name, attr in vars(py_type).items():
            if not isinstance(attr, property) or attr.fget is None:
                continue
            return_hint = typing.get_type_hints(attr.fget, include_extras=True).get("return")
            if return_hint is None:
                continue
            base, _ = _strip_annotations(return_hint)
            generic, lower, upper = self._resolve(base, type_params)
            result.append(
                m3.QualifiedProperty(
                    name=name,
                    id=f"{name}()",
                    genericType=generic,
                    multiplicity=_MULTIPLICITY[(lower, upper)],
                    owner=owner,
                )
            )
        return result


def compile_class(py_type: type, package: str | None = None) -> m3.Class:
    return Compiler(package).to_class(py_type)


def compile_enumeration(py_enum: type, package: str | None = None) -> m3.Enumeration:
    return Compiler(package).to_enumeration(py_enum)
