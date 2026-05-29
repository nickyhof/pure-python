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
    array,
    asc,
    c,
    call,
    coerce,
    col,
    cols,
    desc,
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
    # A two-column grouping keeps the `ColSpecArray` shape under reverse parse.
    # (A single bracketed `~[id]` now also stays a `ColSpecArray` thanks to the
    # bracket-presence fix -- see the single-element bracket tests below.)
    node = call(
        "groupBy",
        tds("grp,id,val\n1,1,10\n1,1,20"),
        cols("grp", "id"),
        agg("total", lam(["r"], lambda r: r.val), lam(["c"], lambda c: c.sum())),
    )
    _assert_round_trips(node)


# --- simple relation verbs --------------------------------------------------
# `limit` / `drop` / `slice` / `distinct` / `concatenate` / `rename` need no new
# lowering: they are plain `SimpleFunctionExpression` calls over already-handled
# atomics (int literals, `#TDS{}#` relations, `~col` colspecs), so they ride the
# existing `call` / fluent arrow path and reverse-parse. The Legend engine
# confirms each resolves to a `meta::pure::functions::relation::<verb>` function
# (see `tests/test_legend_bridge.py`); `take` was probed and REJECTED -- it has
# no relation overload, matching the collection `take` instead, so it is not a
# relation verb and is excluded here.

def test_fluent_limit_equals_free_builder():
    fluent = Expr(tds("id,grp\n1,1\n2,0")).limit(5)
    builder = call("limit", tds("id,grp\n1,1\n2,0"), 5)
    assert canon(fluent.node) == canon(builder)


def test_fluent_drop_equals_free_builder():
    fluent = Expr(tds("id,grp\n1,1\n2,0")).drop(2)
    builder = call("drop", tds("id,grp\n1,1\n2,0"), 2)
    assert canon(fluent.node) == canon(builder)


def test_fluent_slice_equals_free_builder():
    # A two-arg verb exercises the fluent `_Accessor` *args passthrough.
    fluent = Expr(tds("id,grp\n1,1\n2,0")).slice(0, 10)
    builder = call("slice", tds("id,grp\n1,1\n2,0"), 0, 10)
    assert canon(fluent.node) == canon(builder)


def test_fluent_distinct_equals_free_builder():
    fluent = Expr(tds("id,grp\n1,1\n2,0")).distinct()
    builder = call("distinct", tds("id,grp\n1,1\n2,0"))
    assert canon(fluent.node) == canon(builder)


def test_fluent_concatenate_equals_free_builder():
    # The second relation is another `#TDS{}#` literal.
    fluent = Expr(tds("id,grp\n1,1\n2,0")).concatenate(tds("id,grp\n3,1\n4,0"))
    builder = call("concatenate", tds("id,grp\n1,1\n2,0"), tds("id,grp\n3,1\n4,0"))
    assert canon(fluent.node) == canon(builder)


def test_fluent_rename_equals_free_builder():
    fluent = Expr(tds("id,grp\n1,1\n2,0")).rename(col("old"), col("new"))
    builder = call("rename", tds("id,grp\n1,1\n2,0"), col("old"), col("new"))
    assert canon(fluent.node) == canon(builder)


def test_emit_limit_query():
    node = call("limit", tds("id,grp\n1,1\n2,0"), 5)
    assert _expression(node) == "#TDS{id,grp\n1,1\n2,0}#->limit(5)"


def test_emit_drop_query():
    node = call("drop", tds("id,grp\n1,1\n2,0"), 2)
    assert _expression(node) == "#TDS{id,grp\n1,1\n2,0}#->drop(2)"


def test_emit_slice_query():
    node = call("slice", tds("id,grp\n1,1\n2,0"), 0, 10)
    assert _expression(node) == "#TDS{id,grp\n1,1\n2,0}#->slice(0, 10)"


def test_emit_distinct_query():
    node = call("distinct", tds("id,grp\n1,1\n2,0"))
    assert _expression(node) == "#TDS{id,grp\n1,1\n2,0}#->distinct()"


def test_emit_concatenate_query():
    node = call("concatenate", tds("id,grp\n1,1\n2,0"), tds("id,grp\n3,1\n4,0"))
    assert _expression(node) == (
        "#TDS{id,grp\n1,1\n2,0}#->concatenate(#TDS{id,grp\n3,1\n4,0}#)"
    )


def test_emit_rename_query():
    node = call("rename", tds("id,grp\n1,1\n2,0"), col("old"), col("new"))
    assert _expression(node) == "#TDS{id,grp\n1,1\n2,0}#->rename(~old, ~new)"


def test_emit_simple_verb_chain():
    node = Expr(tds("id,grp\n1,1\n2,0")).drop(1).distinct().limit(5).node
    assert _expression(node) == "#TDS{id,grp\n1,1\n2,0}#->drop(1)->distinct()->limit(5)"


def test_round_trip_limit_query():
    _assert_round_trips(call("limit", tds("id,grp\n1,1\n2,0"), 5))


def test_round_trip_drop_query():
    _assert_round_trips(call("drop", tds("id,grp\n1,1\n2,0"), 2))


def test_round_trip_slice_query():
    _assert_round_trips(call("slice", tds("id,grp\n1,1\n2,0"), 0, 10))


def test_round_trip_distinct_query():
    _assert_round_trips(call("distinct", tds("id,grp\n1,1\n2,0")))


def test_round_trip_concatenate_query():
    node = call("concatenate", tds("id,grp\n1,1\n2,0"), tds("id,grp\n3,1\n4,0"))
    _assert_round_trips(node)


def test_round_trip_rename_query():
    _assert_round_trips(call("rename", tds("id,grp\n1,1\n2,0"), col("old"), col("new")))


def test_round_trip_simple_verb_chain():
    node = Expr(tds("id,grp\n1,1\n2,0")).drop(1).distinct().limit(5).node
    _assert_round_trips(node)


# --- collection literals, sort and pivot ------------------------------------
# `sort` takes one `SortInfo` (`~col->ascending()`) or a collection of them; the
# engine resolves `sort_Relation_1__SortInfo_MANY__Relation_1_` for both the
# scalar and the bracketed multi forms (confirmed via the Legend bridge). `pivot`
# takes a pivot column spec (`~[col]`) plus an aggregation; the engine resolves
# `pivot_Relation_1__ColSpecArray_1__AggColSpec_1__Relation_1_`. Both need the
# `array(...)` collection literal (`[a, b]`, an `expressionsArray`) and the
# `ascending` / `descending` direction helpers built here.

# --- array builder ----------------------------------------------------------

def test_array_builds_multi_value_instance_value():
    node = array(1, 2, 3)
    assert isinstance(node, m3.InstanceValue)
    # each element is coerced to a node (here `lit`s), held with ZeroMany mult
    assert all(isinstance(v, m3.InstanceValue) for v in node.values)
    assert [v.values[0] for v in node.values] == [1, 2, 3]
    assert node.multiplicity is m3.ZeroMany


def test_array_coerces_elements():
    # Exprs are unwrapped, m3 nodes pass through, scalars become `lit`s.
    node = array(c(1), col("a"))
    assert isinstance(node.values[0], m3.InstanceValue)  # c(1) -> lit
    assert node.values[0].values == [1]
    assert isinstance(node.values[1], m3.ColSpec)  # col passes through


def test_array_of_sort_infos():
    node = array(asc(col("id")), desc(col("grp")))
    assert canon(node) == (
        "collection",
        (
            ("call", "ascending", (("colspec", "id"),)),
            ("call", "descending", (("colspec", "grp"),)),
        ),
    )


def test_coerce_passes_array_through():
    a = array(1, 2)
    assert coerce(a) is a


# --- direction helpers ------------------------------------------------------

def test_asc_builds_ascending_call():
    node = asc(col("id"))
    assert isinstance(node, m3.SimpleFunctionExpression)
    assert node.functionName == "ascending"
    assert canon(node) == ("call", "ascending", (("colspec", "id"),))


def test_desc_builds_descending_call():
    node = desc(col("grp"))
    assert node.functionName == "descending"
    assert canon(node) == ("call", "descending", (("colspec", "grp"),))


# --- DSL equals the builders ------------------------------------------------

def test_fluent_sort_scalar_equals_free_builder():
    fluent = Expr(tds("id,grp\n1,1\n2,0")).sort(asc(col("grp")))
    builder = call("sort", tds("id,grp\n1,1\n2,0"), asc(col("grp")))
    assert canon(fluent.node) == canon(builder)


def test_fluent_sort_multi_key_equals_free_builder():
    keys = lambda: array(asc(col("id")), desc(col("grp")))
    fluent = Expr(tds("id,grp\n1,1\n2,0")).sort(keys())
    builder = call("sort", tds("id,grp\n1,1\n2,0"), keys())
    assert canon(fluent.node) == canon(builder)


def test_fluent_pivot_equals_free_builder():
    spec = lambda: agg("amount", lam(["r"], lambda r: r.amt), lam(["c"], lambda c: c.sum()))
    fluent = Expr(tds("id,prod,amt\n1,a,10\n1,b,20")).pivot(cols("prod"), spec())
    builder = call("pivot", tds("id,prod,amt\n1,a,10\n1,b,20"), cols("prod"), spec())
    assert canon(fluent.node) == canon(builder)


# --- emit -------------------------------------------------------------------

def test_emit_array_of_scalars():
    assert _expression(array(1, 2, 3)) == "[1, 2, 3]"


def test_emit_array_of_sort_infos():
    node = array(asc(col("id")), desc(col("grp")))
    assert _expression(node) == "[~id->ascending(), ~grp->descending()]"


def test_emit_sort_scalar_query():
    node = call("sort", tds("id,grp\n1,1\n2,0"), asc(col("grp")))
    assert _expression(node) == "#TDS{id,grp\n1,1\n2,0}#->sort(~grp->ascending())"


def test_emit_sort_multi_key_query():
    node = call(
        "sort",
        tds("id,grp\n1,1\n2,0"),
        array(asc(col("id")), desc(col("grp"))),
    )
    assert _expression(node) == (
        "#TDS{id,grp\n1,1\n2,0}#->sort([~id->ascending(), ~grp->descending()])"
    )


def test_emit_pivot_query():
    node = call(
        "pivot",
        tds("id,prod,amt\n1,a,10\n1,b,20"),
        cols("prod"),
        agg("amount", lam(["r"], lambda r: r.amt), lam(["c"], lambda c: c.sum())),
    )
    assert _expression(node) == (
        "#TDS{id,prod,amt\n1,a,10\n1,b,20}#"
        "->pivot(~[prod], ~amount:{r | $r.amt}:{c | $c->sum()})"
    )


# --- single-element bracket preservation ------------------------------------
# The real engine keeps `~[a]` a one-element `ColSpecArray` (it resolves the
# `pivot(Relation, ColSpecArray, AggColSpec)` overload), and bracket presence is
# recoverable from the parse tree (`columnBuilders.BRACKET_OPEN`), so a single
# bracketed `~[a]` reverse-parses to the *Array* family while a bracketless `~a`
# stays the scalar. The shared `_lower_column_builders` change is exercised by
# select/extend/groupBy too (their existing round trips still pass).

def test_emit_single_element_colspec_array():
    assert _expression(cols("id")) == "~[id]"


def test_round_trip_single_element_colspec_array_stays_array():
    node = cols("id")
    parsed = pure_expr.parse_expression(_expression(node))
    assert isinstance(parsed, m3.ColSpecArray)  # NOT collapsed to a scalar ColSpec
    assert canon(parsed) == canon(node)


def test_round_trip_single_element_func_colspec_array_stays_array():
    node = fcols(fcol("a", lam(["r"], lambda r: r.x + 1)))
    parsed = pure_expr.parse_expression(_expression(node))
    assert isinstance(parsed, m3.FuncColSpecArray)
    assert canon(parsed) == canon(node)


def test_round_trip_single_element_agg_colspec_array_stays_array():
    node = aggs(agg("t", lam(["r"], lambda r: r.v), lam(["c"], lambda c: c.sum())))
    parsed = pure_expr.parse_expression(_expression(node))
    assert isinstance(parsed, m3.AggColSpecArray)
    assert canon(parsed) == canon(node)


def test_round_trip_scalar_colspec_stays_scalar():
    # A bracketless `~a` still lowers to the scalar `ColSpec` (the bracket fix is
    # keyed on `BRACKET_OPEN`, so the non-bracketed form is unaffected).
    node = col("id")
    parsed = pure_expr.parse_expression(_expression(node))
    assert isinstance(parsed, m3.ColSpec)
    assert canon(parsed) == canon(node)


# --- reverse parse (round trip) ---------------------------------------------

def test_round_trip_array_of_scalars():
    _assert_round_trips(array(1, 2, 3))


def test_round_trip_array_of_sort_infos():
    _assert_round_trips(array(asc(col("id")), desc(col("grp"))))


def test_round_trip_sort_scalar_query():
    _assert_round_trips(call("sort", tds("id,grp\n1,1\n2,0"), asc(col("grp"))))


def test_round_trip_sort_multi_key_query():
    node = call(
        "sort",
        tds("id,grp\n1,1\n2,0"),
        array(asc(col("id")), desc(col("grp"))),
    )
    _assert_round_trips(node)


def test_round_trip_pivot_query():
    node = call(
        "pivot",
        tds("id,prod,amt\n1,a,10\n1,b,20"),
        cols("prod"),
        agg("amount", lam(["r"], lambda r: r.amt), lam(["c"], lambda c: c.sum())),
    )
    _assert_round_trips(node)
