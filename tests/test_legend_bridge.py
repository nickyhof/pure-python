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
from pure_python.compile.expressions import (
    Expr,
    JoinKind,
    agg,
    array,
    asc,
    c,
    call,
    col,
    cols,
    desc,
    enum_ref,
    fcol,
    lam,
    over,
    range_,
    rows,
    tds,
    unbounded,
    window,
)
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


def test_legend_parses_dsl_tds_group_by_query():
    # An `AggColSpec` (`~name:{map}:{agg}`) + the `groupBy` verb over a `#TDS{...}#`
    # literal PARSES and COMPILES via the real engine (same TDS extensions as the
    # filter / extend cases). The grouping `ColSpecArray` comes first, then the agg
    # colspec. Execution is NOT asserted -- relation execution is blocked upstream on
    # this engine build (see test_legend_java_backend_lacks_relation_size_execution).
    emitted = _expression(
        call(
            "groupBy",
            tds("id,val\n1,10\n1,20"),
            cols("id"),
            agg("total", lam(["r"], lambda r: r.val), lam(["c"], lambda c: c.sum())),
        )
    )
    assert emitted == (
        "#TDS{id,val\n1,10\n1,20}#->groupBy(~[id], ~total:{r | $r.val}:{c | $c->sum()})"
    )
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(e.get("_type") == "function" and e.get("package") == "test" for e in model["elements"])


def test_legend_parses_and_compiles_simple_relation_verb_chain():
    # A chain of the simple relation verbs (`drop` / `distinct` / `limit`, plus
    # `slice` / `rename` / `concatenate`) over a `#TDS{...}#` literal both PARSES
    # and COMPILES via the real engine. None need new lowering -- they are plain
    # arrow calls over already-handled atomics (int literals, relations, `~col`
    # colspecs). Each verb resolves to a `meta::pure::functions::relation::<verb>`
    # function; compilation succeeds and only plan generation fails ("... is not
    # supported yet"), the same execution boundary as the filter / groupBy cases
    # (see test_legend_java_backend_lacks_relation_size_execution). `take` was
    # probed and REJECTED -- it has no relation overload (it matched the
    # collection `take` and failed with "Unhandled value type: ...relation::TDS"),
    # so it is excluded from the verb set.
    chain = (
        Expr(tds("id,grp\n1,1\n2,0\n2,0"))
        .rename(col("id"), col("identifier"))
        .concatenate(tds("identifier,grp\n3,1\n4,0"))
        .drop(1)
        .slice(0, 10)
        .distinct()
        .limit(5)
    )
    emitted = _expression(chain.node)
    assert emitted == (
        "#TDS{id,grp\n1,1\n2,0\n2,0}#"
        "->rename(~id, ~identifier)"
        "->concatenate(#TDS{identifier,grp\n3,1\n4,0}#)"
        "->drop(1)->slice(0, 10)->distinct()->limit(5)"
    )
    # PARSE: the wrapping function appears in the parsed model.
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(
        e.get("_type") == "function" and e.get("package") == "test"
        for e in model["elements"]
    )
    # COMPILE: every verb resolves to a relation function; compilation succeeds
    # and only plan generation hits the upstream execution boundary.
    with pytest.raises(Exception, match="not supported yet"):
        bridge.evaluate("|" + emitted)


def test_legend_parses_and_compiles_dsl_sort_query():
    # `sort` over a `#TDS{...}#` literal PARSES and COMPILES via the real engine.
    # The multi-key list form `[~id->ascending(), ~grp->descending()]` (an
    # `array` of `SortInfo`s) resolves to `sort_Relation_1__SortInfo_MANY__Relation_1_`;
    # compilation succeeds and only plan generation hits the upstream execution
    # boundary ("... is not supported yet"), the same as the filter / groupBy
    # cases. `ascending` / `descending` are the engine's direction function names
    # (the short `asc` / `desc` have no relation overload).
    query = call(
        "sort",
        tds("id,grp\n1,1\n2,0"),
        array(asc(col("id")), desc(col("grp"))),
    )
    emitted = _expression(query)
    assert emitted == (
        "#TDS{id,grp\n1,1\n2,0}#->sort([~id->ascending(), ~grp->descending()])"
    )
    # PARSE: the wrapping function appears in the parsed model.
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(
        e.get("_type") == "function" and e.get("package") == "test"
        for e in model["elements"]
    )
    # COMPILE: `sort` resolves to a relation function; only plan generation fails.
    with pytest.raises(Exception, match="not supported yet"):
        bridge.evaluate("|" + emitted)


def test_legend_parses_and_compiles_dsl_pivot_query():
    # `pivot` over a `#TDS{...}#` literal PARSES and COMPILES via the real engine.
    # The pivot column spec `~[prod]` (a one-element `ColSpecArray`) plus the
    # aggregation `~amount:{r|...}:{c|...}` resolve to
    # `pivot_Relation_1__ColSpecArray_1__AggColSpec_1__Relation_1_`; compilation
    # succeeds and only plan generation hits the upstream execution boundary
    # ("... is not supported yet"). The single-element `~[prod]` must stay a
    # `ColSpecArray` (not collapse to a scalar `ColSpec`) for this overload to
    # resolve -- the bracket-presence fix makes the round trip preserve it.
    query = call(
        "pivot",
        tds("id,prod,amt\n1,a,10\n1,b,20"),
        cols("prod"),
        agg("amount", lam(["r"], lambda r: r.amt), lam(["c"], lambda c: c.sum())),
    )
    emitted = _expression(query)
    assert emitted == (
        "#TDS{id,prod,amt\n1,a,10\n1,b,20}#"
        "->pivot(~[prod], ~amount:{r | $r.amt}:{c | $c->sum()})"
    )
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(
        e.get("_type") == "function" and e.get("package") == "test"
        for e in model["elements"]
    )
    with pytest.raises(Exception, match="not supported yet"):
        bridge.evaluate("|" + emitted)


def test_legend_parses_and_compiles_dsl_join_query():
    # `join` over two `#TDS{...}#` literals PARSES and COMPILES via the real
    # engine. The second relation is a plain value (another `#TDS{}#`), the
    # `JoinKind.INNER` argument is an enum-value reference (a bare `JoinKind`
    # instanceReference + `.INNER` propertyExpression -- the engine resolves the
    # enumeration; bare `JoinKind.INNER` both parses AND compiles), and the
    # condition is the already-supported multi-param lambda. The engine resolves
    # `join_Relation_1__Relation_1__JoinKind_1__Function_1__Relation_1_`;
    # compilation succeeds and only plan generation hits the upstream execution
    # boundary ("... is not supported yet"), the same as the filter / groupBy /
    # sort / pivot cases (see test_legend_java_backend_lacks_relation_size_execution).
    query = call(
        "join",
        tds("id,name\n1,a\n2,b"),
        tds("rid,val\n1,10\n2,20"),
        JoinKind.INNER,
        lam(["l", "r"], lambda l, r: l.id == r.rid),
    )
    emitted = _expression(query)
    assert emitted == (
        "#TDS{id,name\n1,a\n2,b}#"
        "->join(#TDS{rid,val\n1,10\n2,20}#, JoinKind.INNER, {l, r | ($l.id == $r.rid)})"
    )
    # PARSE: the wrapping function appears in the parsed model.
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(
        e.get("_type") == "function" and e.get("package") == "test"
        for e in model["elements"]
    )
    # COMPILE: `join` resolves to a relation function (the enum-value reference
    # `JoinKind.INNER` compiles); only plan generation hits the upstream boundary.
    with pytest.raises(Exception, match="not supported yet"):
        bridge.evaluate("|" + emitted)


def test_legend_compiles_each_valid_join_kind_and_rejects_outer():
    # The engine resolves the bare `JoinKind` enumeration; INNER/LEFT/RIGHT/FULL
    # are valid members (each compiles past name resolution to the plan-gen
    # boundary), while OUTER is NOT a member and fails earlier with a distinct
    # "Can't find enum value 'OUTER'" error -- proving the compiler genuinely
    # resolves the enum-value reference rather than ignoring it.
    def join_query(kind_ref):
        return _expression(
            call(
                "join",
                tds("id,name\n1,a"),
                tds("rid,val\n1,10"),
                kind_ref,
                lam(["l", "r"], lambda l, r: l.id == r.rid),
            )
        )

    for kind in (JoinKind.INNER, JoinKind.LEFT, JoinKind.RIGHT, JoinKind.FULL):
        # COMPILE: a valid member reaches the plan-gen "not supported yet" boundary.
        with pytest.raises(Exception, match="not supported yet"):
            bridge.evaluate("|" + join_query(kind))

    # An unknown member is rejected at compile time, not at the plan-gen boundary.
    with pytest.raises(Exception, match="Can't find enum value 'OUTER'"):
        bridge.evaluate("|" + join_query(enum_ref("JoinKind", "OUTER")))


def test_legend_parses_and_compiles_dsl_as_of_join_query():
    # `asOfJoin` over two `#TDS{...}#` literals PARSES and COMPILES via the real
    # engine. Unlike `join`, the 3-arg overload takes NO `JoinKind` -- just the
    # second relation and a single condition lambda. The engine resolves
    # `asOfJoin_Relation_1__Relation_1__Function_1__Relation_1_`; compilation
    # succeeds and only plan generation hits the upstream execution boundary
    # ("... is not supported yet"), the same as the join case above. (A 4-arg
    # `asOfJoin(rel, rel, matchCond, joinCond)` overload also compiles
    # --`asOfJoin_Relation_1__Relation_1__Function_1__Function_1__Relation_1_`-- but
    # the 3-arg form is the representative case here.)
    query = call(
        "asOfJoin",
        tds("id,t\n1,5\n2,9"),
        tds("rid,rt\n1,4\n2,8"),
        lam(["l", "r"], lambda l, r: l.t >= r.rt),
    )
    emitted = _expression(query)
    assert emitted == (
        "#TDS{id,t\n1,5\n2,9}#"
        "->asOfJoin(#TDS{rid,rt\n1,4\n2,8}#, {l, r | ($l.t >= $r.rt)})"
    )
    # PARSE: the wrapping function appears in the parsed model.
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(
        e.get("_type") == "function" and e.get("package") == "test"
        for e in model["elements"]
    )
    # COMPILE: `asOfJoin` resolves to a relation function; only plan generation fails.
    with pytest.raises(Exception, match="not supported yet"):
        bridge.evaluate("|" + emitted)


def test_legend_parses_and_compiles_dsl_windowed_extend_func_colspec():
    # A windowed `extend` -- an OLAP column over a window spec -- PARSES and
    # COMPILES via the real engine. `over(~p, sortList, frame)` builds the
    # `_Window`: the partition is `~p`, the order is the list
    # `[~o->ascending(), ~i->ascending()]` (an `array` of `SortInfo`s), and the
    # frame is `rows(unbounded(), 0)` (ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT
    # ROW -- `unbounded()` resolves to `UnboundedFrameValue`, 0 is the current
    # row). The extend column is a `FuncColSpec` with the canonical 3-param window
    # lambda `{p, w, r | ...}`. The engine resolves
    # `extend_Relation_1___Window_1__FuncColSpec_1__Relation_1_`; compilation
    # succeeds and only plan generation hits the upstream execution boundary
    # ("... is not supported yet"), the same as the filter / groupBy / sort cases.
    # `over` / `rows` / `unbounded` are emitted PREFIX (`over(~p, ...)`, the
    # engine's own canonical OLAP form), not the arrow form.
    query = call(
        "extend",
        tds("p,o,i\n0,1,10\n0,2,20\n100,1,10"),
        over(col("p"), array(asc(col("o")), asc(col("i"))), rows(unbounded(), 0)),
        fcol("c", lam(["p", "w", "r"], lambda p, w, r: r.i)),
    )
    emitted = _expression(query)
    assert emitted == (
        "#TDS{p,o,i\n0,1,10\n0,2,20\n100,1,10}#"
        "->extend(over(~p, [~o->ascending(), ~i->ascending()], rows(unbounded(), 0)), "
        "~c:{p, w, r | $r.i})"
    )
    # PARSE: the wrapping function appears in the parsed model.
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(
        e.get("_type") == "function" and e.get("package") == "test"
        for e in model["elements"]
    )
    # COMPILE: `over` + the frame + the windowed `extend` resolve to relation
    # functions; only plan generation hits the upstream execution boundary.
    with pytest.raises(Exception, match="not supported yet"):
        bridge.evaluate("|" + emitted)


def test_legend_parses_and_compiles_dsl_windowed_extend_agg_colspec():
    # The aggregating windowed `extend`: the column is an `AggColSpec`
    # (`~name:{map}:{reduce}`) instead of a `FuncColSpec`, so the engine resolves
    # the sibling `extend_Relation_1___Window_1__AggColSpec_1__Relation_1_`
    # overload. `over(~p, ~o->ascending(), rows(-1, 0))` uses a single `SortInfo`
    # (no list) and a `rows(-1, 0)` frame (previous row through current row). Same
    # plan-gen execution boundary; execution is NOT asserted.
    query = call(
        "extend",
        tds("p,o,i\n0,1,10\n0,2,20\n100,1,10"),
        over(col("p"), asc(col("o")), rows(-1, 0)),
        agg("sum_i", lam(["p", "w", "r"], lambda p, w, r: r.i), lam(["y"], lambda y: y.sum())),
    )
    emitted = _expression(query)
    assert emitted == (
        "#TDS{p,o,i\n0,1,10\n0,2,20\n100,1,10}#"
        "->extend(over(~p, ~o->ascending(), rows(-1, 0)), "
        "~sum_i:{p, w, r | $r.i}:{y | $y->sum()})"
    )
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(
        e.get("_type") == "function" and e.get("package") == "test"
        for e in model["elements"]
    )
    with pytest.raises(Exception, match="not supported yet"):
        bridge.evaluate("|" + emitted)


def test_legend_compiles_range_frame_windowed_extend():
    # The value-range frame `_range(...)` (built by `range_`) compiles too: the
    # engine resolves the `over(cols, sortInfo, _range)` overload (the bare `range`
    # is the collection function, so the frame constructor is `_range`). Same
    # plan-gen boundary; execution is NOT asserted.
    query = call(
        "extend",
        tds("p,o,i\n0,1,10\n0,2,20"),
        over(col("p"), asc(col("o")), range_(-1, 0)),
        fcol("c", lam(["p", "w", "r"], lambda p, w, r: r.i)),
    )
    emitted = _expression(query)
    assert emitted == (
        "#TDS{p,o,i\n0,1,10\n0,2,20}#"
        "->extend(over(~p, ~o->ascending(), _range(-1, 0)), ~c:{p, w, r | $r.i})"
    )
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(
        e.get("_type") == "function" and e.get("package") == "test"
        for e in model["elements"]
    )
    with pytest.raises(Exception, match="not supported yet"):
        bridge.evaluate("|" + emitted)


def test_legend_compiles_non_trivial_frame_chain():
    # The `Frame` query builder is pure sugar over the relation verbs, so a
    # non-trivial `Frame` chain emits exactly the Pure the free builders do and the
    # real engine PARSES + COMPILES it. `filter -> groupBy -> sort -> limit` over a
    # `#TDS{...}#` literal: every verb resolves to a
    # `meta::pure::functions::relation::<verb>` function; compilation succeeds and
    # only plan generation hits the upstream execution boundary ("... is not
    # supported yet"), the same boundary as the free-builder cases above. (An
    # `extend(~c:{r | $r.amt * 2})` step BEFORE the `groupBy` was probed and is a
    # genuine engine *compile* constraint -- the engine infers the derived column as
    # `[0..1]` and rejects "Collection element must have a multiplicity [1]" -- so
    # it is left out of this compilable chain; the `Frame.extend` sugar itself is
    # exercised jar-free in `tests/test_frame.py`.)
    from pure_python.compile import Frame, desc

    src = "id,cust,amt\n1,a,10\n2,a,20\n3,b,5"
    query = (
        Frame.from_tds(src)
        .filter(lambda r: r.amt > 5)
        .group_by("cust", ("total", lambda r: r.amt, lambda c: c.sum()))
        .sort(desc("total"))
        .limit(10)
    )
    emitted = query.to_pure()
    assert emitted == (
        f"#TDS{{{src}}}#"
        "->filter({r | ($r.amt > 5)})"
        "->groupBy(~[cust], ~total:{r | $r.amt}:{c | $c->sum()})"
        "->sort(~total->descending())"
        "->limit(10)"
    )
    # PARSE: the wrapping function appears in the parsed model.
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(
        e.get("_type") == "function" and e.get("package") == "test"
        for e in model["elements"]
    )
    # COMPILE: every verb resolves; only plan generation hits the upstream boundary.
    with pytest.raises(Exception, match="not supported yet"):
        bridge.evaluate("|" + emitted)


def test_legend_compiles_frame_join_chain():
    # A `Frame` join chain (`filter -> inner_join`) PARSES + COMPILES. The right
    # relation is another `Frame` (unwrapped to its node), the `JoinKind.INNER` is
    # the enum-value reference, and the condition is the two-row proxy lambda. The
    # engine resolves `join_Relation_1__Relation_1__JoinKind_1__Function_1__Relation_1_`;
    # compilation succeeds and only plan generation hits the upstream boundary.
    from pure_python.compile import Frame

    left = Frame.from_tds("id,name\n1,a\n2,b")
    right = Frame.from_tds("rid,val\n1,10\n2,20")
    query = left.filter(lambda r: r.id > 0).inner_join(right, lambda l, r: l.id == r.rid)
    emitted = query.to_pure()
    assert emitted == (
        "#TDS{id,name\n1,a\n2,b}#->filter({r | ($r.id > 0)})"
        "->join(#TDS{rid,val\n1,10\n2,20}#, JoinKind.INNER, {l, r | ($l.id == $r.rid)})"
    )
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(
        e.get("_type") == "function" and e.get("package") == "test"
        for e in model["elements"]
    )
    with pytest.raises(Exception, match="not supported yet"):
        bridge.evaluate("|" + emitted)


def test_legend_compiles_frame_windowed_extend():
    # A `Frame.window_extend` (an OLAP column over an `over(...)` window) PARSES +
    # COMPILES, resolving `extend_Relation_1___Window_1__FuncColSpec_1__Relation_1_`;
    # compilation succeeds and only plan generation hits the upstream boundary.
    from pure_python.compile import Frame

    query = Frame.from_tds("p,o,i\n0,1,10\n0,2,20\n100,1,10").window_extend(
        over(col("p"), array(asc(col("o")), asc(col("i"))), rows(unbounded(), 0)),
        ("c", lambda p, w, r: r.i),
    )
    emitted = query.to_pure()
    assert emitted == (
        "#TDS{p,o,i\n0,1,10\n0,2,20\n100,1,10}#"
        "->extend(over(~p, [~o->ascending(), ~i->ascending()], rows(unbounded(), 0)), "
        "~c:{p, w, r | $r.i})"
    )
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(
        e.get("_type") == "function" and e.get("package") == "test"
        for e in model["elements"]
    )
    with pytest.raises(Exception, match="not supported yet"):
        bridge.evaluate("|" + emitted)


def test_legend_parses_frame_from_db_source():
    # `Frame.from_db` emits a `#>{db::Store.table}#` database-table source, which
    # PARSES via the real engine: the source is a `classInstance` of `type ">"`
    # whose value is the `[database, table]` path, and the relation verb resolves
    # over it. The wrapping function appears in the parsed model. (COMPILING needs
    # the named store defined -- see the next test.)
    from pure_python.compile import Frame

    query = Frame.from_db("my::Store", "myTable").limit(5)
    emitted = query.to_pure()
    assert emitted == "#>{my::Store.myTable}#->limit(5)"
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(
        e.get("_type") == "function" and e.get("package") == "test"
        for e in model["elements"]
    )
    # The parsed source is a `classInstance` of `type ">"` carrying the dotted path.
    fn = next(e for e in model["elements"] if e.get("_type") == "function")
    source = fn["body"][0]["parameters"][0]
    assert source["_type"] == "classInstance" and source["type"] == ">"
    assert source["value"]["path"] == ["my::Store", "myTable"]


def test_legend_from_db_compile_needs_a_defined_database():
    # `from_db` only PARSES without a database; COMPILING it fails because the named
    # store is not defined -- a DISTINCT, earlier error than the relation plan-gen
    # boundary ("The store '...' can't be found.", not "not supported yet"). This
    # sugar layer deliberately does not fabricate a database/store definition; a
    # real `from_db` execution needs a modelled relational store + connection +
    # runtime. Pinned so a future store-aware path surfaces here.
    from pure_python.compile import Frame

    emitted = Frame.from_db("my::Store", "myTable").limit(5).to_pure()
    with pytest.raises(Exception, match="store 'my::Store' can't be found"):
        bridge.evaluate("|" + emitted)


def test_legend_compiles_named_olap_functions_in_windowed_extend():
    # The named OLAP functions PARSE + COMPILE inside a windowed `extend` over a
    # `#TDS{...}#`, each resolving the `extend_..._Window_..._FuncColSpec_...`
    # plan-gen boundary. The engine-resolved compilable forms (verified by probing
    # the engine): `rowNumber($p, $r)` (p, r -- NOT p, w, r), `rank($p, $w, $r)`,
    # `denseRank($p, $w, $r)`, `lag($p, $r)` / `lead($p, $r)` (p, r + optional
    # Integer offset). The snake_case `row_number` / `dense_rank` are REJECTED by
    # the engine ("Function does not exist"); the `Expr` call-path alias map emits
    # the camelCase the engine resolves (so `p.row_number(r)` -> `$p->rowNumber($r)`
    # here). Execution is NOT asserted -- relation execution is blocked upstream.
    olap_columns = {
        "rowNumber": ("rn", lambda p, w, r: p.row_number(r)),  # snake -> rowNumber
        "rank": ("rk", lambda p, w, r: p.rank(w, r)),
        "denseRank": ("dr", lambda p, w, r: p.dense_rank(w, r)),  # snake -> denseRank
        "lag": ("lg", lambda p, w, r: p.lag(r)),
        "lead": ("ld", lambda p, w, r: p.lead(r)),
    }
    for name, (colname, fn) in olap_columns.items():
        query = call(
            "extend",
            tds("p,o,i\n0,1,10\n0,2,20\n100,1,10"),
            over(col("p"), asc(col("o")), rows(unbounded(), 0)),
            fcol(colname, lam(["p", "w", "r"], fn)),
        )
        emitted = _expression(query)
        # PARSE: the wrapping function appears in the parsed model.
        model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
        assert any(
            e.get("_type") == "function" and e.get("package") == "test"
            for e in model["elements"]
        ), name
        # COMPILE: the OLAP function resolves; only plan generation hits the boundary.
        with pytest.raises(Exception, match="not supported yet"):
            bridge.evaluate("|" + emitted)


def test_legend_rejects_snake_case_olap_function_names():
    # Proof the alias map is load-bearing: the raw snake_case spelling that
    # pylegend uses (`row_number`) does NOT exist in Pure -- the engine rejects it
    # at compile time with "Function does not exist", a DISTINCT, earlier error
    # than the plan-gen boundary -- so emitting it verbatim would never compile.
    from pure_python.compile.expressions import var

    emitted = _expression(
        call(
            "extend",
            tds("p,o,i\n0,1,10\n0,2,20"),
            over(col("p"), asc(col("o")), rows(unbounded(), 0)),
            fcol("c", lam(["p", "w", "r"], lambda p, w, r: call("row_number", var("p"), var("r")))),
        )
    )
    with pytest.raises(Exception, match=r"Function does not exist 'row_number"):
        bridge.evaluate("|" + emitted)


def test_legend_compiles_windowed_aggregate_column():
    # A cumulative windowed aggregate is the AGG-COLSPEC form
    # `~c:{p, w, r | $r.i}:{y | $y->sum()}` (resolving
    # `extend_..._Window_..._AggColSpec_...`), NOT a `$p->sum(...)` proxy call --
    # the engine REJECTS `$p->sum(...)` over the window (only the collection
    # `sum(Number[*])` matches). PARSES + COMPILES to the plan-gen boundary.
    query = call(
        "extend",
        tds("p,o,i\n0,1,10\n0,2,20\n100,1,10"),
        over(col("p"), asc(col("o")), rows(unbounded(), 0)),
        agg("cum", lam(["p", "w", "r"], lambda p, w, r: r.i), lam(["y"], lambda y: y.sum())),
    )
    emitted = _expression(query)
    assert emitted.endswith("~cum:{p, w, r | $r.i}:{y | $y->sum()})")
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(
        e.get("_type") == "function" and e.get("package") == "test"
        for e in model["elements"]
    )
    with pytest.raises(Exception, match="not supported yet"):
        bridge.evaluate("|" + emitted)


def test_legend_compiles_pylegend_style_frame_chain():
    # The pylegend-aligned `Frame` surface end-to-end: subscript columns, a string
    # join kind (`'LEFT_OUTER'` -> `JoinKind.LEFT`), the `window()` helper, and a
    # named OLAP column (`p.row_number(r)` -> `$p->rowNumber($r)`). It emits exactly
    # the Pure the free builders do and the real engine PARSES + COMPILES it, every
    # verb resolving to a `meta::pure::functions::relation::<verb>` function; only
    # plan generation hits the upstream execution boundary.
    from pure_python.compile import Frame

    # The join's two relations must not share a column name (Pure `join` unions the
    # columns and rejects "The relation contains duplicates"), so the right side
    # keys on `cid` matched against `cust`.
    orders = Frame.from_tds("id,cust,amt\n1,a,10\n2,a,20\n3,b,5")
    custs = Frame.from_tds("cid,region\na,US\nb,EU")
    query = (
        orders
        .filter(lambda r: r["amt"] > 5)
        .join(custs, lambda l, r: l.cust == r.cid, kind="LEFT_OUTER")
        .window_extend(
            Frame.window(partition_by="cust", order_by="amt", frame=rows(unbounded(), 0)),
            ("rn", lambda p, w, r: p.row_number(r)),
        )
    )
    emitted = query.to_pure()
    assert emitted == (
        "#TDS{id,cust,amt\n1,a,10\n2,a,20\n3,b,5}#"
        "->filter({r | ($r.amt > 5)})"
        "->join(#TDS{cid,region\na,US\nb,EU}#, JoinKind.LEFT, {l, r | ($l.cust == $r.cid)})"
        "->extend(over(~cust, ~amt->ascending(), rows(unbounded(), 0)), "
        "~rn:{p, w, r | $p->rowNumber($r)})"
    )
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(
        e.get("_type") == "function" and e.get("package") == "test"
        for e in model["elements"]
    )
    with pytest.raises(Exception, match="not supported yet"):
        bridge.evaluate("|" + emitted)


def test_legend_compiles_frame_as_of_join_four_arg():
    # The 4-arg `as_of_join(other, match_function, join_condition=...)` wires the
    # `asOfJoin_..._Function_1__Function_1__...` overload; it PARSES + COMPILES to
    # the plan-gen boundary (the 3-arg form is covered separately above).
    from pure_python.compile import Frame

    # Distinct column names across the two relations (Pure `asOfJoin` unions the
    # columns and rejects duplicates): the right side keys on `k2`.
    query = Frame.from_tds("id,t,k\n1,5,9\n2,9,9").as_of_join(
        Frame.from_tds("rid,rt,k2\n1,4,9\n2,8,9"),
        lambda l, r: l.t >= r.rt,
        join_condition=lambda l, r: l.k == r.k2,
    )
    emitted = query.to_pure()
    assert emitted == (
        "#TDS{id,t,k\n1,5,9\n2,9,9}#"
        "->asOfJoin(#TDS{rid,rt,k2\n1,4,9\n2,8,9}#, "
        "{l, r | ($l.t >= $r.rt)}, {l, r | ($l.k == $r.k2)})"
    )
    model = bridge.parse(f"function test::f(): Any[*] {{ {emitted} }}")
    assert any(
        e.get("_type") == "function" and e.get("package") == "test"
        for e in model["elements"]
    )
    with pytest.raises(Exception, match="not supported yet"):
        bridge.evaluate("|" + emitted)


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
