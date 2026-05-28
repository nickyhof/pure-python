from __future__ import annotations

import dataclasses
import enum
import pathlib

import pure_python.m3 as m3


def test_core_hierarchy():
    assert issubclass(m3.Class, m3.Type)
    assert issubclass(m3.Type, m3.Any)
    assert issubclass(m3.PrimitiveType, m3.DataType)
    assert issubclass(m3.Enumeration, m3.DataType)
    assert issubclass(m3.Property, m3.AbstractProperty)


def test_aggregation_kind_enum():
    assert [m.value for m in m3.AggregationKind] == ["None", "Shared", "Composite"]
    assert m3.AggregationKind.None_.value == "None"


def test_primitive_and_multiplicity_singletons():
    assert isinstance(m3.String, m3.PrimitiveType)
    assert m3.String.name == "String"
    assert (m3.PureOne.lowerBound.value, m3.PureOne.upperBound.value) == (1, 1)
    assert m3.ZeroMany.lowerBound.value == 0
    assert m3.ZeroMany.upperBound is None


def test_can_build_a_domain_class_with_the_metamodel():
    """The metamodel is usable: assemble a Pure Class for a 'Person' by hand."""
    person = m3.Class(
        name="Person",
        properties=[
            m3.Property(
                name="firstName",
                genericType=m3.GenericType(rawType=m3.String),
                multiplicity=m3.PureOne,
                aggregation=m3.AggregationKind.None_,
                owner=None,
            ),
            m3.Property(
                name="age",
                genericType=m3.GenericType(rawType=m3.Integer),
                multiplicity=m3.ZeroOne,
                aggregation=m3.AggregationKind.None_,
                owner=None,
            ),
        ],
    )
    assert person.name == "Person"
    assert [p.name for p in person.properties] == ["firstName", "age"]
    assert person.properties[0].genericType.rawType is m3.String


def test_committed_metamodel_matches_generator():
    """Guards against drift: the checked-in module is exactly what the generator emits."""
    from pure_python.codegen.generate import render

    on_disk = pathlib.Path(m3.metamodel.__file__).read_text(encoding="utf-8")
    assert render() == on_disk


def test_generic_classes():
    import typing

    assert typing.Generic in m3.Class.__mro__
    assert m3.Class[int]  # subscriptable
    # Enumeration.values is now typed by its type parameter rather than Any.
    values_field = next(f for f in dataclasses.fields(m3.Enumeration) if f.name == "values")
    assert "E" in str(values_field.type)


def test_type_arguments_are_preserved_in_annotations():
    source = pathlib.Path(m3.metamodel.__file__).read_text(encoding="utf-8")
    assert "function: Function[Z]" in source  # from relation.pure FuncColSpec
    assert "funcSpecs: list[FuncColSpec[Z, Any]]" in source
    assert "enumeration: Enumeration[Any]" in source  # from the bootstrap EnumStub


def test_grammar_sourced_classes_present():
    # relation.pure
    assert issubclass(m3.FuncColSpec, m3.Any)
    assert m3.ColSpec[int]
    # variant.pure
    assert dataclasses.is_dataclass(m3.Variant)
    # milestoning.pure -- diamond inheritance and keyword-escaped fields
    assert issubclass(m3.BiTemporalMilestoning, m3.DateMilestoning)
    fields = {f.name for f in dataclasses.fields(m3.BusinessDateMilestoning)}
    assert "from_" in fields and "thru" in fields


def test_all_generated_classes_are_dataclasses():
    for name in m3.metamodel.__all__:
        obj = getattr(m3, name)
        if isinstance(obj, type) and not issubclass(obj, enum.Enum):
            assert dataclasses.is_dataclass(obj), name
