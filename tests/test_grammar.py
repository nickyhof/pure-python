from __future__ import annotations

import pathlib

import pytest

from pure_python.codegen.grammar import parse_grammar

VENDOR = pathlib.Path(__file__).resolve().parents[1] / "vendor" / "legend-pure"


def _classes(name: str):
    result = parse_grammar((VENDOR / name).read_text(encoding="utf-8"))
    return {c.name: c for c in result.classes}, result


def test_relation_generics_and_type_args():
    classes, _ = _classes("relation.pure")
    assert set(classes) == {
        "ColSpec",
        "ColSpecArray",
        "FuncColSpec",
        "FuncColSpecArray",
        "AggColSpec",
        "AggColSpecArray",
    }
    assert classes["FuncColSpec"].type_parameters == ["Z", "T"]
    function = next(p for p in classes["FuncColSpec"].properties if p.name == "function")
    assert function.type_name == "Function"  # Function<Z>
    (arg,) = function.type_arguments
    assert arg.name == "Z" and arg.is_type_parameter  # Z is a type parameter of FuncColSpec
    assert (function.lower, function.upper) == (1, 1)
    # AggColSpec<A, B, Any> -- nested args, mix of type parameters and a concrete type.
    agg = next(p for p in classes["AggColSpecArray"].properties if p.name == "aggSpecs")
    assert [a.name for a in agg.type_arguments] == ["A", "B", "Any"]
    names = next(p for p in classes["ColSpecArray"].properties if p.name == "names")
    assert (names.lower, names.upper) == (0, None)  # String[*]


def test_extends_and_diamond_and_keyword_fields():
    classes, _ = _classes("milestoning.pure")
    assert classes["SingleDateTemporalStrategy"].bases == ["TemporalStrategy"]
    assert classes["BiTemporalMilestoning"].bases == [
        "ProcessingDateMilestoning",
        "BusinessDateMilestoning",
    ]
    # Implicit Any root for declarations without `extends`.
    assert classes["TemporalStrategy"].bases == ["Any"]
    props = {p.name: p for p in classes["BusinessDateMilestoning"].properties}
    assert set(props) == {"from", "thru"}  # 'from' is a Python keyword; escaped only on emit


def test_profiles_parsed():
    _, result = _classes("milestoning.pure")
    profiles = {p.name: p for p in result.profiles}
    assert profiles["temporal"].stereotypes == [
        "bitemporal",
        "businesstemporal",
        "processingtemporal",
    ]


def test_variant_minimal():
    classes, _ = _classes("variant.pure")
    assert set(classes) == {"Variant"}
    assert classes["Variant"].properties == []


def test_qualified_property_parsing():
    source = """
    Class my::Person
    {
        firstName : String[1];
        fullName() {$this.firstName} : String[1];
        scores(n: Integer[1]) {[]} : Integer[1..*];
    }
    """
    cls = parse_grammar(source).classes[0]
    assert [p.name for p in cls.properties] == ["firstName"]  # simple properties only
    qualified = {q.name: (q.type_name, q.lower, q.upper) for q in cls.qualified_properties}
    assert qualified == {"fullName": ("String", 1, 1), "scores": ("Integer", 1, None)}


def test_association_parsing():
    source = """
    Association my::Employment
    {
        employer : my::Firm[1];
        employees : my::Person[*];
    }
    """
    assoc = parse_grammar(source).associations[0]
    assert assoc.name == "Employment" and assoc.package == "my"
    ends = {p.name: (p.type_name, p.lower, p.upper) for p in assoc.properties}
    assert ends == {"employer": ("Firm", 1, 1), "employees": ("Person", 0, None)}


def test_real_grammar_rejects_empty_qualified_body():
    # legend-pure's grammar requires a body expression; the lenient hand-written
    # parser used to tolerate `foo() {}`, so m3_to_pure now emits `foo() { [] }`.
    with pytest.raises(SyntaxError):
        parse_grammar("Class my::A { foo() {} : String[1]; }")
    parse_grammar("Class my::A { foo() { [] } : String[1]; }")  # accepted
