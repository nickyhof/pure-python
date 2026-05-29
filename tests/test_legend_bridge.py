"""Integration tests that validate pure-python's Pure against the *real* Legend.

These exercise the ``legend-bridge`` JVM harness (built with
``mvn -f legend-bridge package``). When the jar or a JVM is unavailable the whole
module is skipped, so the default ``pytest`` run is unaffected.
"""

from __future__ import annotations

import typing
from dataclasses import dataclass

import pytest

from pure_python import m3
from pure_python.compile import compile_class, from_pure, to_pure_module
from pure_python.compile.annotations import Body
from pure_python.compile.expressions import c, call, fcol, lam, tds
from pure_python.compile.m3_to_pure import _expression
from pure_python.legend import LegendBridge

bridge = LegendBridge()
# `integration` -> excluded from the default run (each call boots a fresh JVM +
# Legend engine, ~4s); enable explicitly with `pytest -m integration`.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not bridge.available(),
        reason="legend-bridge jar/JVM not available; build with `mvn -f legend-bridge package`",
    ),
]


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


def test_legend_executes_dsl_emitted_infix_operators():
    # The DSL emits fully parenthesized infix for the core binary operators, the
    # form Legend's stdlib actually executes (arrow `1->plus(1)` has no two-arg
    # match because core arithmetic binds variadically). Build with the DSL,
    # emit, and let Legend run it.
    def run(expr_node):
        return bridge.evaluate("|" + _expression(expr_node))

    assert _expression((c(1) + c(1)).node) == "(1 + 1)"
    assert run((c(1) + c(1)).node) == 2
    assert run((c(2) * c(3)).node) == 6
    assert run((c(3) > c(2)).node) is True
    assert run((c(6) == c(6)).node) is True
    assert run((c(6) != c(7)).node) is True
    assert run((c(4) / c(2)).node) == 2.0
    assert run(((c(1) + c(2)) * c(3)).node) == 9


def test_legend_executes_body_derived_property_model():
    # A class with a Body-derived property, emitted to Pure, both parses and the
    # derived property executes end-to-end through Legend.
    @dataclass
    class Item:
        base: int

        @property
        def doubled(self) -> typing.Annotated[int, Body(lambda this: this.base * 2)]: ...

    model = to_pure_module(compile_class(Item, package="demo"))
    assert "doubled() { ($this.base * 2); }" in model
    parsed = bridge.parse(model)
    assert "Item" in {e.get("name") for e in parsed["elements"]}
    assert bridge.evaluate("|^demo::Item(base=21).doubled", model=model) == 42


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


def test_legend_executes_dsl_filter_lambda_and_size():
    # `filter`/`size` over a literal collection reduce to an Integer constant
    # (`evaluate` only returns constants). The engine's Java backend executes
    # collection `size`, but NOT the relation reducer `relation::size` (see
    # test_legend_java_backend_lacks_relation_size_execution) -- so this exercises
    # the reusable DSL machinery (an n-ary lambda + `filter` + `size`, which the
    # relation query shares) over a collection it can actually run.
    source = m3.InstanceValue(
        values=[1, 2, 0],
        genericType=m3.GenericType(rawType=m3.Integer),
        multiplicity=m3.ZeroMany,
    )
    query = call("size", call("filter", source, lam(["r"], lambda r: r > 0)))
    emitted = _expression(query)
    assert emitted == "[1, 2, 0]->filter({r | ($r > 0)})->size()"
    assert bridge.evaluate("|" + emitted) == 2  # two rows pass `$r > 0`


def test_legend_parses_dsl_tds_query():
    # The `legend-bridge` jar now bundles the `legend-engine-xt-tds-{grammar,
    # compiler}` extensions, so the real engine PARSES our emitted
    # `#TDS{...}#->filter(...)` query -- previously rejected with "Can't find an
    # embedded Pure parser for the type 'TDS'". The wrapping function appears in
    # the parsed model.
    emitted = _expression(call("filter", tds("id,grp\n1,1\n2,0"), lam(["r"], lambda r: r.grp > 0)))
    assert emitted == "#TDS{id,grp\n1,1\n2,0}#->filter({r | ($r.grp > 0)})"
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(e.get("_type") == "function" and e.get("package") == "test" for e in model["elements"])


def test_legend_parses_dsl_tds_extend_query():
    # A `FuncColSpec` (`~c:{r|...}`) + the `extend` verb over a `#TDS{...}#`
    # literal PARSES and COMPILES via the real engine (same TDS extensions as
    # the filter case). Execution is NOT asserted -- relation execution is blocked
    # upstream on this engine build (see test_legend_java_backend_lacks_relation_size_execution).
    emitted = _expression(
        call("extend", tds("id\n1\n2"), fcol("doubled", lam(["r"], lambda r: r.id * 2)))
    )
    assert emitted == "#TDS{id\n1\n2}#->extend(~doubled:{r | ($r.id * 2)})"
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(e.get("_type") == "function" and e.get("package") == "test" for e in model["elements"])


def test_legend_java_backend_lacks_relation_size_execution():
    # TDS now parses AND compiles, but this engine build's Java execution codegen
    # does not yet implement the relation reducers, so a TDS query cannot be
    # executed down to a constant: `relation::size ... is not supported yet`. The
    # error is raised in plan generation -- i.e. AFTER a successful compile, which
    # is itself evidence the `#TDS{}#` literal compiles. Pin the boundary so a
    # future engine that implements relation execution surfaces here.
    emitted = _expression(
        call("size", call("filter", tds("id,grp\n1,1\n2,0"), lam(["r"], lambda r: r.grp > 0)))
    )
    with pytest.raises(Exception, match="not supported yet"):
        bridge.evaluate("|" + emitted)
