from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
import importlib.util

import pytest

from pure_python import m3
from pure_python.compile import Compiler, Stereotype, Tag, compile_class, to_module

import typing

RT = typing.TypeVar("RT")


@dataclasses.dataclass
class Box(typing.Generic[RT]):
    value: RT
    items: list[RT] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Holder:
    box: Box[int]


@dataclasses.dataclass
class Tagged:
    name: typing.Annotated[str, Stereotype("pii", "sensitive")]
    note: typing.Annotated[str | None, Tag("doc", "about", "a note")] = None

    @property
    def summary(self) -> str: ...


class Color(enum.Enum):
    RED = "RED"
    GREEN = "GREEN"
    BLUE = "BLUE"


@dataclasses.dataclass
class Address:
    street: str
    zipCode: str | None = None


@dataclasses.dataclass
class Person:
    firstName: str
    lastName: str
    age: int | None = None
    nicknames: list[str] = dataclasses.field(default_factory=list)
    address: Address | None = None
    favoriteColor: Color | None = None


@dataclasses.dataclass
class Node:  # self-referential
    label: str
    children: list["Node"] = dataclasses.field(default_factory=list)


def _bounds(prop: m3.Property) -> tuple[int, int | None]:
    mult = prop.multiplicity
    upper = mult.upperBound.value if mult.upperBound else None
    return mult.lowerBound.value, upper


def _props(cls: m3.Class) -> dict[str, tuple[str, int, int | None]]:
    out = {}
    for p in cls.properties:
        raw = p.genericType.rawType
        name = getattr(raw, "name", None) or type(raw).__name__
        lower, upper = _bounds(p)
        out[p.name] = (name, lower, upper)
    return out


def test_primitive_type_mapping():
    @dataclasses.dataclass
    class Sample:
        s: str
        b: bool
        i: int
        f: float
        d: decimal.Decimal
        when_date: datetime.date
        when_dt: datetime.datetime
        when_time: datetime.time

    props = _props(compile_class(Sample))
    assert props["s"][0] == "String"
    assert props["b"][0] == "Boolean"
    assert props["i"][0] == "Integer"
    assert props["f"][0] == "Float"
    assert props["d"][0] == "Decimal"
    assert props["when_date"][0] == "StrictDate"
    assert props["when_dt"][0] == "DateTime"
    assert props["when_time"][0] == "StrictTime"


def test_multiplicity_inference():
    props = _props(compile_class(Person))
    assert props["firstName"] == ("String", 1, 1)
    assert props["age"] == ("Integer", 0, 1)
    assert props["nicknames"] == ("String", 0, None)
    assert props["address"] == ("Address", 0, 1)


def test_owner_backreference_and_sharing():
    cls = compile_class(Person)
    assert all(p.owner is cls for p in cls.properties)
    # The nested Address is a single shared m3.Class instance.
    address_prop = next(p for p in cls.properties if p.name == "address")
    assert isinstance(address_prop.genericType.rawType, m3.Class)
    assert address_prop.genericType.rawType.name == "Address"


def test_enum_conversion():
    cls = compile_class(Person)
    color_prop = next(p for p in cls.properties if p.name == "favoriteColor")
    enumeration = color_prop.genericType.rawType
    assert isinstance(enumeration, m3.Enumeration)
    assert [v.name for v in enumeration.values] == ["RED", "GREEN", "BLUE"]


def test_self_reference_terminates():
    cls = compile_class(Node)
    children = next(p for p in cls.properties if p.name == "children")
    assert children.genericType.rawType is cls  # points back to the same Class


def test_rejects_non_dataclass():
    with pytest.raises(TypeError):
        compile_class(int)


def _load_module(source: str, name: str):
    spec = importlib.util.spec_from_loader(name, loader=None)
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[name] = module
    exec(compile(source, f"<{name}>", "exec"), module.__dict__)
    return module


def _graph(compiler: Compiler) -> dict:
    classes = {c.name: _props(c) for c in compiler.classes.values()}
    enums = {e.name: [v.name for v in e.values] for e in compiler.enums.values()}
    return {"classes": classes, "enums": enums}


def test_round_trip_python_to_m3_to_python():
    forward = Compiler(package="demo")
    person = forward.to_class(Person)
    source = to_module(person)

    module = _load_module(source, "pure_python_roundtrip_demo")
    back = Compiler(package="demo")
    back.to_class(module.Person)

    assert _graph(forward) == _graph(back)


def test_emitted_module_is_importable_and_correct():
    source = to_module(compile_class(Person))
    module = _load_module(source, "pure_python_roundtrip_person")
    assert dataclasses.is_dataclass(module.Person)
    assert issubclass(module.Color, enum.Enum)
    p = module.Person(firstName="Ada", lastName="Lovelace")
    assert p.age is None and p.nicknames == []


def test_generic_type_parameters_and_typevar_fields():
    cls = compile_class(Box)
    assert [tp.name for tp in cls.typeParameters] == ["RT"]
    value = next(p for p in cls.properties if p.name == "value")
    assert value.genericType.typeParameter is not None
    assert value.genericType.typeParameter.name == "RT"
    items = next(p for p in cls.properties if p.name == "items")
    assert items.genericType.typeParameter.name == "RT"
    assert (items.multiplicity.lowerBound.value, items.multiplicity.upperBound) == (0, None)


def test_subscripted_generic_captures_type_arguments():
    cls = compile_class(Holder)
    box = next(p for p in cls.properties if p.name == "box")
    assert isinstance(box.genericType.rawType, m3.Class)
    assert box.genericType.rawType.name == "Box"
    (arg,) = box.genericType.typeArguments
    assert arg.rawType is m3.Integer


def test_stereotypes_and_tagged_values():
    cls = compile_class(Tagged)
    name = next(p for p in cls.properties if p.name == "name")
    assert [(s.profile.name, s.value) for s in name.stereotypes] == [("pii", "sensitive")]
    note = next(p for p in cls.properties if p.name == "note")
    assert [(t.tag.profile.name, t.tag.value, t.value) for t in note.taggedValues] == [
        ("doc", "about", "a note")
    ]
    # Tagging an optional field still infers [0..1].
    assert (note.multiplicity.lowerBound.value, note.multiplicity.upperBound.value) == (0, 1)


def test_qualified_property_from_python_property():
    cls = compile_class(Tagged)
    assert [q.name for q in cls.qualifiedProperties] == ["summary"]
    summary = cls.qualifiedProperties[0]
    assert summary.id == "summary()"
    assert isinstance(summary.genericType.rawType, m3.PrimitiveType)
    assert summary.genericType.rawType.name == "String"


def _type_sig(generic) -> str:
    if generic is None:
        return "Any"
    if generic.typeParameter is not None:
        return f"param:{generic.typeParameter.name}"
    raw = generic.rawType
    name = getattr(raw, "name", None) or type(raw).__name__
    if generic.typeArguments:
        return f"{name}[{','.join(_type_sig(a) for a in generic.typeArguments)}]"
    return name


def _rich_graph(compiler: Compiler) -> dict:
    graph = {}
    for cls in compiler.classes.values():
        props = {}
        for p in cls.properties:
            lower, upper = _bounds(p)
            stereo = tuple(sorted((s.profile.name, s.value) for s in p.stereotypes))
            tags = tuple(sorted((t.tag.profile.name, t.tag.value, t.value) for t in p.taggedValues))
            props[p.name] = (_type_sig(p.genericType), lower, upper, stereo, tags)
        qps = {q.name: (_type_sig(q.genericType), q.id) for q in cls.qualifiedProperties}
        graph[cls.name] = ([tp.name for tp in cls.typeParameters], props, qps)
    return graph


def test_rich_round_trip_preserves_generics_annotations_and_qualified_properties():
    forward = Compiler(package="demo")
    forward.to_class(Box)
    forward.to_class(Holder)
    forward.to_class(Tagged)
    source = to_module(forward.to_class(Box), forward.to_class(Holder), forward.to_class(Tagged))

    module = _load_module(source, "pure_python_rich_round_trip")
    back = Compiler(package="demo")
    back.to_class(module.Box)
    back.to_class(module.Holder)
    back.to_class(module.Tagged)

    assert _rich_graph(forward) == _rich_graph(back)
