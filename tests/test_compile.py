from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
import importlib.util

import pytest

from pure_python import m3
from pure_python.compile import Compiler, compile_class, to_module


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
