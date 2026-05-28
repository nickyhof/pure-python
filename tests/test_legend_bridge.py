"""Integration tests that validate pure-python's Pure against the *real* Legend.

These exercise the ``legend-bridge`` JVM harness (built with
``mvn -f legend-bridge package``). When the jar or a JVM is unavailable the whole
module is skipped, so the default ``pytest`` run is unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pure_python.compile import compile_class, from_pure, to_pure_module
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


@dataclass
class Point:
    x: int
    y: int


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
        "addresses": ("Address", 0, None),
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


def test_legend_executes_over_a_generated_model():
    model = to_pure_module(compile_class(Point, package="demo"))
    value = bridge.evaluate(
        "|^demo::Point(x=3, y=4).x + ^demo::Point(x=3, y=4).y", model=model
    )
    assert value == 7
