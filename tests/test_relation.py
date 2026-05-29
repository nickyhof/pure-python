"""Relation / TDS foundation: n-ary lambdas, `#TDS{}#` literals, simple column
specs, and the `filter` / `select` verbs.

Fast (jar-free) coverage: builders produce the expected m3; the fluent DSL
matches the free builders under the shared structural ``canon`` projection; the
emitter produces the exact Pure strings; and each emitted form reverse-parses
back to the same graph (a structural Python -> m3 -> Pure -> m3 round trip).
"""

from __future__ import annotations

import pytest

from pure_python import m3
from pure_python.compile import pure_expr
from pure_python.compile.expressions import (
    Expr,
    agg,
    aggs,
    c,
    call,
    coerce,
    col,
    cols,
    fcol,
    fcols,
    lam,
    tds,
)
from pure_python.compile.m3_to_pure import _expression

from .test_expressions import canon


# --- builders ---------------------------------------------------------------

def test_tds_builds_relation_instance_value_from_csv():
    node = tds("id,grp\n1,1\n2,0")
    assert isinstance(node, m3.InstanceValue)
    assert node.values == ["#TDS{id,grp\n1,1\n2,0}#"]
    # discriminated from a plain string literal by a RelationType marker
    assert isinstance(node.genericType.rawType, m3.RelationType)
    assert node.multiplicity is m3.PureOne


def test_tds_accepts_a_full_token_unchanged():
    token = "#TDS{id,grp\n1,1}#"
    assert tds(token).values == [token]


def test_tds_rejects_hash_in_content():
    # The `#TDS{...}#` token is `#`-delimited, so interior `#` cannot round-trip
    # (the non-greedy DSL_TEXT lexer would truncate it). Reject at build time.
    with pytest.raises(ValueError, match="#"):
        tds("id,note\n1,item #1")


def test_col_builds_name_only_colspec():
    node = col("grp")
    assert isinstance(node, m3.ColSpec)
    assert node.name == "grp"


def test_cols_builds_name_only_colspec_array():
    node = cols("id", "grp")
    assert isinstance(node, m3.ColSpecArray)
    assert node.names == ["id", "grp"]


def test_fcol_builds_func_colspec_from_lambda():
    function = lam(["r"], lambda r: r.id * 2)
    node = fcol("doubled", function)
    assert isinstance(node, m3.FuncColSpec)
    assert node.name == "doubled"
    assert node.function is function


def test_fcol_rejects_non_function():
    with pytest.raises(TypeError, match="Function"):
        fcol("x", "not a function")


def test_fcols_builds_func_colspec_array():
    a = fcol("a", lam(["r"], lambda r: r.x + 1))
    b = fcol("b", lam(["r"], lambda r: r.y * 2))
    node = fcols(a, b)
    assert isinstance(node, m3.FuncColSpecArray)
    assert node.funcSpecs == [a, b]


def test_fcols_rejects_non_func_colspec():
    with pytest.raises(TypeError, match="FuncColSpec"):
        fcols(col("a"))


def test_coerce_passes_func_colspecs_through():
    fc = fcol("doubled", lam(["r"], lambda r: r.id * 2))
    assert coerce(fc) is fc
    fca = fcols(fc)
    assert coerce(fca) is fca


def test_agg_builds_agg_colspec_from_map_and_reduce():
    map_fn = lam(["r"], lambda r: r.val)
    reduce_fn = lam(["c"], lambda c: c.sum())
    node = agg("total", map_fn, reduce_fn)
    assert isinstance(node, m3.AggColSpec)
    assert node.name == "total"
    assert node.map is map_fn
    assert node.reduce is reduce_fn


def test_agg_rejects_non_function_map():
    with pytest.raises(TypeError, match="Function"):
        agg("x", "not a function", lam(["c"], lambda c: c.sum()))


def test_agg_rejects_non_function_reduce():
    with pytest.raises(TypeError, match="Function"):
        agg("x", lam(["r"], lambda r: r.val), "not a function")


def test_aggs_builds_agg_colspec_array():
    a = agg("total", lam(["r"], lambda r: r.x), lam(["c"], lambda c: c.sum()))
    b = agg("cnt", lam(["r"], lambda r: r.y), lam(["c"], lambda c: c.count()))
    node = aggs(a, b)
    assert isinstance(node, m3.AggColSpecArray)
    assert node.aggSpecs == [a, b]


def test_aggs_rejects_non_agg_colspec():
    with pytest.raises(TypeError, match="AggColSpec"):
        aggs(fcol("a", lam(["r"], lambda r: r.x)))


def test_coerce_passes_agg_colspecs_through():
    ac = agg("total", lam(["r"], lambda r: r.val), lam(["c"], lambda c: c.sum()))
    assert coerce(ac) is ac
    aca = aggs(ac)
    assert coerce(aca) is aca


def test_lam_builds_lambda_with_param_names_and_body():
    from pure_python.compile.expressions import prop, var

    node = lam(["r"], lambda r: r.grp > 0)
    assert isinstance(node, m3.LambdaFunction)
    assert node.openVariables == ["r"]  # names carried via openVariables
    assert len(node.expressionSequence) == 1
    assert canon(node.expressionSequence[0]) == canon(
        call("greaterThan", prop(var("r"), "grp"), 0)
    )


def test_lam_arity_one_two_three():
    assert lam(["r"], lambda r: r.grp).openVariables == ["r"]
    assert lam(["p", "w"], lambda p, w: p + w).openVariables == ["p", "w"]
    assert lam(["p", "w", "r"], lambda p, w, r: r.grp).openVariables == ["p", "w", "r"]


def test_coerce_passes_lambda_and_colspecs_through():
    lf = lam(["r"], lambda r: r.grp)
    assert coerce(lf) is lf
    cs = col("id")
    assert coerce(cs) is cs
    csa = cols("a", "b")
    assert coerce(csa) is csa


# --- DSL equals the builders ------------------------------------------------

def test_fluent_filter_equals_free_builder():
    fluent = Expr(tds("id,grp\n1,1\n2,0")).filter(lam(["r"], lambda r: r.grp > 0))
    builder = call("filter", tds("id,grp\n1,1\n2,0"), lam(["r"], lambda r: r.grp > 0))
    assert canon(fluent.node) == canon(builder)


def test_fluent_select_equals_free_builder():
    fluent = Expr(tds("id,grp\n1,1")).select(cols("id", "grp"))
    builder = call("select", tds("id,grp\n1,1"), cols("id", "grp"))
    assert canon(fluent.node) == canon(builder)


def test_fluent_extend_equals_free_builder():
    fluent = Expr(tds("id\n1\n2")).extend(fcol("doubled", lam(["r"], lambda r: r.id * 2)))
    builder = call("extend", tds("id\n1\n2"), fcol("doubled", lam(["r"], lambda r: r.id * 2)))
    assert canon(fluent.node) == canon(builder)


def test_fluent_extend_with_func_colspec_array_equals_free_builder():
    specs = lambda: fcols(
        fcol("a", lam(["r"], lambda r: r.x + 1)),
        fcol("b", lam(["r"], lambda r: r.y * 2)),
    )
    fluent = Expr(tds("x,y\n1,2")).extend(specs())
    builder = call("extend", tds("x,y\n1,2"), specs())
    assert canon(fluent.node) == canon(builder)


def test_fluent_group_by_equals_free_builder():
    spec = lambda: agg("total", lam(["r"], lambda r: r.val), lam(["c"], lambda c: c.sum()))
    fluent = Expr(tds("id,val\n1,10\n1,20")).groupBy(cols("id"), spec())
    builder = call("groupBy", tds("id,val\n1,10\n1,20"), cols("id"), spec())
    assert canon(fluent.node) == canon(builder)


def test_fluent_group_by_with_agg_array_equals_free_builder():
    specs = lambda: aggs(
        agg("total", lam(["r"], lambda r: r.val), lam(["c"], lambda c: c.sum())),
        agg("cnt", lam(["r"], lambda r: r.val), lam(["c"], lambda c: c.count())),
    )
    fluent = Expr(tds("id,val\n1,10\n1,20")).groupBy(cols("id"), specs())
    builder = call("groupBy", tds("id,val\n1,10\n1,20"), cols("id"), specs())
    assert canon(fluent.node) == canon(builder)


def test_lambda_body_uses_row_property_access():
    node = lam(["r"], lambda r: r.grp > 0)
    assert canon(node) == (
        "lambda",
        ("r",),
        (("call", "greaterThan", (("prop", "grp", ("var", "r")), ("lit", "Integer", (0,)))),),
    )


# --- emit -------------------------------------------------------------------

def test_emit_single_colspec():
    assert _expression(col("id")) == "~id"


def test_emit_colspec_array():
    assert _expression(cols("id", "grp")) == "~[id, grp]"


def test_emit_tds_literal_verbatim():
    assert _expression(tds("id,grp\n1,1\n2,0")) == "#TDS{id,grp\n1,1\n2,0}#"


def test_emit_lambda_one_two_three_params():
    assert _expression(lam(["r"], lambda r: r.grp > 0)) == "{r | ($r.grp > 0)}"
    assert _expression(lam(["p", "w"], lambda p, w: p + w)) == "{p, w | ($p + $w)}"
    assert _expression(lam(["p", "w", "r"], lambda p, w, r: r.grp)) == "{p, w, r | $r.grp}"


def test_emit_zero_param_lambda():
    assert _expression(lam([], lambda: c(1))) == "{| 1}"


def test_emit_filter_query():
    node = call("filter", tds("id,grp\n1,1\n2,0"), lam(["r"], lambda r: r.grp > 0))
    assert _expression(node) == "#TDS{id,grp\n1,1\n2,0}#->filter({r | ($r.grp > 0)})"


def test_emit_select_query():
    node = call("select", tds("id,grp\n1,1\n2,0"), cols("id", "grp"))
    assert _expression(node) == "#TDS{id,grp\n1,1\n2,0}#->select(~[id, grp])"


def test_emit_single_func_colspec():
    node = fcol("doubled", lam(["r"], lambda r: r.id * 2))
    assert _expression(node) == "~doubled:{r | ($r.id * 2)}"


def test_emit_func_colspec_array():
    node = fcols(
        fcol("a", lam(["r"], lambda r: r.x + 1)),
        fcol("b", lam(["r"], lambda r: r.y * 2)),
    )
    assert _expression(node) == "~[a:{r | ($r.x + 1)}, b:{r | ($r.y * 2)}]"


def test_emit_extend_query():
    node = call("extend", tds("id\n1\n2"), fcol("doubled", lam(["r"], lambda r: r.id * 2)))
    assert _expression(node) == "#TDS{id\n1\n2}#->extend(~doubled:{r | ($r.id * 2)})"


def test_emit_single_agg_colspec():
    node = agg("total", lam(["r"], lambda r: r.val), lam(["c"], lambda c: c.sum()))
    assert _expression(node) == "~total:{r | $r.val}:{c | $c->sum()}"


def test_emit_agg_colspec_array():
    node = aggs(
        agg("total", lam(["r"], lambda r: r.val), lam(["c"], lambda c: c.sum())),
        agg("cnt", lam(["r"], lambda r: r.val), lam(["c"], lambda c: c.count())),
    )
    assert _expression(node) == (
        "~[total:{r | $r.val}:{c | $c->sum()}, cnt:{r | $r.val}:{c | $c->count()}]"
    )


def test_emit_group_by_query():
    node = call(
        "groupBy",
        tds("id,val\n1,10\n1,20"),
        cols("id"),
        agg("total", lam(["r"], lambda r: r.val), lam(["c"], lambda c: c.sum())),
    )
    assert _expression(node) == (
        "#TDS{id,val\n1,10\n1,20}#->groupBy(~[id], ~total:{r | $r.val}:{c | $c->sum()})"
    )


# --- reverse parse (round trip) ---------------------------------------------

def _assert_round_trips(node) -> None:
    emitted = _expression(node)
    parsed = pure_expr.parse_expression(emitted)
    assert canon(parsed) == canon(node)
    assert _expression(parsed) == emitted


def test_round_trip_tds_literal():
    _assert_round_trips(tds("id,grp\n1,1\n2,0"))


def test_round_trip_single_colspec():
    _assert_round_trips(col("id"))


def test_round_trip_colspec_array():
    _assert_round_trips(cols("id", "grp"))


def test_round_trip_lambda_one_two_three_params():
    _assert_round_trips(lam(["r"], lambda r: r.grp > 0))
    _assert_round_trips(lam(["p", "w"], lambda p, w: p + w))
    _assert_round_trips(lam(["p", "w", "r"], lambda p, w, r: r.grp))


def test_round_trip_zero_param_lambda():
    _assert_round_trips(lam([], lambda: c(1)))


def test_round_trip_filter_query():
    node = call("filter", tds("id,grp\n1,1\n2,0"), lam(["r"], lambda r: r.grp > 0))
    _assert_round_trips(node)


def test_round_trip_select_query():
    node = call("select", tds("id,grp\n1,1\n2,0"), cols("id", "grp"))
    _assert_round_trips(node)


def test_round_trip_select_single_column():
    node = call("select", tds("id,grp\n1,1"), col("id"))
    _assert_round_trips(node)


def test_round_trip_single_func_colspec():
    _assert_round_trips(fcol("doubled", lam(["r"], lambda r: r.id * 2)))


def test_round_trip_func_colspec_array():
    node = fcols(
        fcol("a", lam(["r"], lambda r: r.x + 1)),
        fcol("b", lam(["r"], lambda r: r.y * 2)),
    )
    _assert_round_trips(node)


def test_round_trip_extend_query():
    node = call("extend", tds("id\n1\n2"), fcol("doubled", lam(["r"], lambda r: r.id * 2)))
    _assert_round_trips(node)


def test_round_trip_single_agg_colspec():
    _assert_round_trips(
        agg("total", lam(["r"], lambda r: r.val), lam(["c"], lambda c: c.sum()))
    )


def test_round_trip_agg_colspec_array():
    node = aggs(
        agg("total", lam(["r"], lambda r: r.val), lam(["c"], lambda c: c.sum())),
        agg("cnt", lam(["r"], lambda r: r.val), lam(["c"], lambda c: c.count())),
    )
    _assert_round_trips(node)


def test_round_trip_group_by_query():
    # A two-column grouping keeps the `ColSpecArray` shape under reverse parse (a
    # single `~[id]` lowers to a scalar `ColSpec`, the same asymmetry the single-
    # column `select` round trip side-steps with `col` rather than `cols`).
    node = call(
        "groupBy",
        tds("grp,id,val\n1,1,10\n1,1,20"),
        cols("grp", "id"),
        agg("total", lam(["r"], lambda r: r.val), lam(["c"], lambda c: c.sum())),
    )
    _assert_round_trips(node)
