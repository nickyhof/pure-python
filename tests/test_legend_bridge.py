"""Integration tests that validate pure-python's Pure against the *real* Legend.

These exercise the ``legend-bridge`` JVM harness (built with
``mvn -f legend-bridge package``). When the jar or a JVM is unavailable the whole
module is skipped, so the default ``pytest`` run is unaffected.
"""

from __future__ import annotations

import typing
from dataclasses import dataclass

import pytest

from pure_python.compile import compile_class, from_pure, to_pure_module
from pure_python.compile.annotations import Body
from pure_python.compile.expressions import c
from pure_python.compile.m3_to_pure import _expression
from pure_python.legend import LegendBridge

bridge = LegendBridge()
pytestmark = pytest.mark.skipif(
    not bridge.available(),
    reason="legend-bridge jar/JVM not available; build with `mvn -f legend-bridge package`",
)


@dataclass
class Address:
    street: str
    city: str | None


@dataclass
class Person:
    firstName: str
    age: int | None
    addresses: list[Address]


def _classes(model: dict) -> dict[str, dict]:
    return {e["name"]: e for e in model["elements"] if e.get("_type") == "class"}


def _props(cls: dict) -> dict[str, tuple]:
    out = {}
    for p in cls["properties"]:
        m = p["multiplicity"]
        out[p["name"]] = (
            p["genericType"]["rawType"]["fullPath"],
            m["lowerBound"],
            m.get("upperBound"),  # absent => unbounded (*)
        )
    return out


def test_legend_accepts_pure_python_output():
    pure = to_pure_module(compile_class(Person, package="demo"))
    model = bridge.parse(pure)
    classes = _classes(model)
    assert {"Person", "Address"} <= set(classes)
    assert _props(classes["Person"]) == {
        "firstName": ("String", 1, 1),
        "age": ("Integer", 0, 1),
        "addresses": ("demo::Address", 0, None),  # qualified so Legend can resolve it
    }


def test_parse_compose_is_stable_under_legend():
    pure = to_pure_module(compile_class(Person, package="demo"))
    once = bridge.parse(pure)
    twice = bridge.parse(bridge.compose(once))
    # Legend is the oracle: structure must survive a parse -> compose -> parse loop.
    assert {n: _props(c) for n, c in _classes(once).items()} == {
        n: _props(c) for n, c in _classes(twice).items()
    }


def test_legend_compose_round_trips_back_into_pure_python():
    pure = to_pure_module(compile_class(Person, package="demo"))
    recomposed = bridge.compose(bridge.parse(pure))
    recovered = from_pure(recomposed)
    assert {"Person", "Address"} <= set(recovered)
    person = recovered["Person"]
    assert {p.name for p in person.properties} == {"firstName", "age", "addresses"}


def test_legend_executes_pure_expressions():
    # Delegate execution to Legend: it compiles and runs the expression.
    assert bridge.evaluate("|1 + 1") == 2
    assert bridge.evaluate("|[1, 2, 3]->sum()") == 6
    assert bridge.evaluate("|'a' + 'b'") == "ab"


def test_legend_accepts_dsl_emitted_arrow_expression():
    # The DSL emits a uniform arrow form. Legend's real grammar parser accepts
    # it inside a function body. (Direct *execution* of `a->plus(b)` is skipped:
    # Legend's core arithmetic functions bind variadically -- `plus(Integer[*])`
    # -- so `1->plus(1)` is a two-arg call that the stdlib has no match for; the
    # infix `1 + 1` desugars to `plus([1, 1])` instead. The arrow form is a
    # deliberate, round-trippable representation, not an executable spelling of
    # variadic core functions.)
    plus = _expression((c(1) + c(1)).node)
    assert plus == "1->plus(1)"
    pure = "function demo::run(): Integer[1] { 1->plus(1) }"
    model = bridge.parse(pure)
    names = {e.get("name") for e in model["elements"]}
    assert any(n.startswith("run") for n in names)


def test_legend_accepts_body_derived_property_model():
    # A class with a Body-derived property, emitted to Pure, is accepted by the
    # real Legend grammar parser (the body is no longer a `[]` placeholder).
    @dataclass
    class Item:
        base: int

        @property
        def doubled(self) -> typing.Annotated[int, Body(lambda this: this.base * 2)]: ...

    model = to_pure_module(compile_class(Item, package="demo"))
    assert "doubled() { $this.base->times(2) }" in model
    parsed = bridge.parse(model)
    assert "Item" in {e.get("name") for e in parsed["elements"]}


def test_legend_executes_over_a_generated_model():
    # Person references Address, so the emitted model must use qualified type
    # references (`demo::Address`) to compile -- Legend's compiler rejects the
    # bare `Address`. Constructing and navigating across both classes proves it.
    model = to_pure_module(compile_class(Person, package="demo"))
    value = bridge.evaluate(
        "|^demo::Person(firstName='Ada', addresses=^demo::Address(street='Main')).addresses->size()",
        model=model,
    )
    assert value == 1
