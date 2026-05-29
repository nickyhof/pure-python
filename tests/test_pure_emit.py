from __future__ import annotations

import dataclasses
import enum
import typing

from pure_python import m3
from pure_python.codegen.grammar import parse_grammar
from pure_python.compile import (
    Compiler,
    Stereotype,
    Tag,
    compile_class,
    from_pure,
    to_pure,
    to_pure_module,
)
from pure_python.compile.annotations import Body
from pure_python.compile.m3_to_pure import _expression, _multiplicity

RT = typing.TypeVar("RT")


class Color(enum.Enum):
    RED = "RED"
    GREEN = "GREEN"


@dataclasses.dataclass
class Address:
    street: str
    zipCode: str | None = None


@dataclasses.dataclass
class Box(typing.Generic[RT]):
    value: RT
    items: list[RT] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Person:
    firstName: typing.Annotated[str, Stereotype("pii", "sensitive")]
    age: int | None = None
    nicknames: list[str] = dataclasses.field(default_factory=list)
    address: Address | None = None
    favoriteColor: Color | None = None
    box: Box | None = None
    note: typing.Annotated[str | None, Tag("doc", "about", "a note")] = None


@dataclasses.dataclass
class Simple:
    a: str
    b: int | None = None


@dataclasses.dataclass
class Vehicle:
    wheels: int


@dataclasses.dataclass
class Car(Vehicle):
    brand: str


def test_multiplicity_rendering():
    assert _multiplicity(m3.PureOne) == "[1]"
    assert _multiplicity(m3.ZeroOne) == "[0..1]"
    assert _multiplicity(m3.ZeroMany) == "[*]"
    assert _multiplicity(m3.OneMany) == "[1..*]"


def test_single_class_golden():
    assert to_pure(compile_class(Simple, package="demo")) == (
        "Class demo::Simple\n"
        "{\n"
        "    a : String[1];\n"
        "    b : Integer[0..1];\n"
        "}"
    )


def test_emitted_source_contains_generics_stereotypes_and_tags():
    compiler = Compiler("demo")
    source = to_pure_module(compiler.to_class(Person), compiler.to_class(Box))
    assert "Class demo::Box<RT>" in source
    assert "value : RT[1];" in source
    assert "items : RT[*];" in source
    assert "<<pii.sensitive>> firstName : String[1];" in source
    assert "{doc.about = 'a note'} note : String[0..1];" in source
    assert "Profile pii" in source and "stereotypes: [sensitive];" in source
    assert "Enum demo::Color" in source


# --- reverse round trip: m3 -> Pure -> grammar parser -> m3 (at grammar fidelity) ---

def _simple_type(generic: m3.GenericType | None) -> str | None:
    if generic is None:
        return "Any"
    if generic.typeParameter is not None:
        return generic.typeParameter.name
    return getattr(generic.rawType, "name", None) or "Any"


def _bounds(prop) -> tuple[int, int | None]:
    mult = prop.multiplicity
    upper = mult.upperBound.value if mult.upperBound else None
    return mult.lowerBound.value, upper


def _m3_sig(cls: m3.Class):
    bases = tuple(
        sorted(
            getattr(getattr(g, "general", None), "rawType", None).name
            for g in cls.generalizations
        )
    )
    return (
        cls.name,
        cls.package or "",
        tuple(tp.name for tp in cls.typeParameters),
        tuple(b for b in bases if b != "Any"),
        {p.name: (_simple_type(p.genericType), *_bounds(p)) for p in cls.properties},
    )


def _meta_sig(meta):
    return (
        meta.name,
        meta.package,
        tuple(meta.type_parameters),
        tuple(b for b in meta.bases if b != "Any"),
        {p.name: (p.type_name, p.lower, p.upper) for p in meta.properties},
    )


def test_reverse_round_trip_class_signatures():
    compiler = Compiler("demo")
    source = to_pure_module(compiler.to_class(Person), compiler.to_class(Box))
    reparsed = {c.name: c for c in parse_grammar(source).classes}

    for cls in compiler.classes.values():
        assert cls.name in reparsed, cls.name
        assert _m3_sig(cls) == _meta_sig(reparsed[cls.name])


def test_pure_emits_inheritance_with_extends():
    source = to_pure_module(compile_class(Car, package="demo"))
    assert "Class demo::Car extends demo::Vehicle" in source
    assert "Class demo::Vehicle" in source
    assert "brand : String[1];" in source
    car_block = source.split("Class demo::Car")[1].split("}")[0]  # Car body only
    assert "wheels" not in car_block  # inherited field is not redeclared on the subclass


def _end(prop):
    upper = prop.multiplicity.upperBound.value if prop.multiplicity.upperBound else None
    return prop.genericType.rawType.name, prop.multiplicity.lowerBound.value, upper


def test_association_round_trips_through_pure():
    person = m3.Class(name="Person", package="hr")
    firm = m3.Class(name="Firm", package="hr")
    assoc = m3.Association(name="Employment", package="hr")
    assoc.properties = [
        m3.Property(
            name="employer",
            genericType=m3.GenericType(rawType=firm),
            multiplicity=m3.PureOne,
            owner=assoc,
            aggregation=m3.AggregationKind.None_,
        ),
        m3.Property(
            name="employees",
            genericType=m3.GenericType(rawType=person),
            multiplicity=m3.ZeroMany,
            owner=assoc,
            aggregation=m3.AggregationKind.None_,
        ),
    ]
    source = to_pure_module(assoc)
    assert "Association hr::Employment" in source
    assert "employer : hr::Firm[1];" in source and "employees : hr::Person[*];" in source

    registry = from_pure(source)
    back = registry["Employment"]
    assert isinstance(back, m3.Association) and back.package == "hr"
    ends = {p.name: _end(p) for p in back.properties}
    assert ends == {"employer": ("Firm", 1, 1), "employees": ("Person", 0, None)}
    # the ends resolve to the round-tripped Class instances, not fresh ones
    assert back.properties[0].genericType.rawType is registry["Firm"]


def test_qualified_property_round_trips_through_pure():
    @dataclasses.dataclass
    class Customer:
        firstName: str

        @property
        def greeting(self) -> str: ...

    cls = compile_class(Customer, package="demo")
    source = to_pure_module(cls)
    # Signature-only (no Body) keeps the `[]` placeholder body.
    assert "greeting() { [] } : String[1];" in source
    back = from_pure(source)["Customer"]
    assert [q.name for q in back.qualifiedProperties] == ["greeting"]
    qp = back.qualifiedProperties[0]
    assert qp.genericType.rawType.name == "String"
    assert (qp.multiplicity.lowerBound.value, qp.multiplicity.upperBound.value) == (1, 1)
    assert qp.expressionSequence == []  # no body -> empty sequence


def test_qualified_property_with_body_emits_and_round_trips_through_pure():
    @dataclasses.dataclass
    class Customer:
        firstName: str
        lastName: str

        @property
        def fullName(
            self,
        ) -> typing.Annotated[str, Body(lambda this: this.firstName + " " + this.lastName)]:
            ...

    cls = compile_class(Customer, package="demo")
    source = to_pure_module(cls)
    assert (
        "fullName() { $this.firstName->plus(' ')->plus($this.lastName) } : String[1];"
        in source
    )

    # The body graph survives m3 -> Pure -> m3 (re-emits to the same arrow form).
    back = from_pure(source)["Customer"]
    qp = back.qualifiedProperties[0]
    assert len(qp.expressionSequence) == 1
    assert _expression(qp.expressionSequence[0]) == (
        "$this.firstName->plus(' ')->plus($this.lastName)"
    )


def test_reverse_round_trip_enum_and_profiles():
    compiler = Compiler("demo")
    result = parse_grammar(to_pure_module(compiler.to_class(Person)))
    enums = {e.name: e for e in result.enums}
    assert enums["Color"].values == ["RED", "GREEN"]
    profiles = {p.name: p for p in result.profiles}
    assert profiles["pii"].stereotypes == ["sensitive"]
    assert profiles["doc"].tags == ["about"]
