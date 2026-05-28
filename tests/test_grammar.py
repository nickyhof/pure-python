from __future__ import annotations

import pathlib

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
