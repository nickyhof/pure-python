"""The legendql-style ``Frame`` query builder: a faithful, immutable sugar facade
over the relation verbs.

``Frame`` adds no new representation -- it accrues ``call("verb", node, ...)``
graphs over the existing builders and lowers via the existing ``to_pure`` path. So
the spec is: every ``Frame`` method produces the SAME m3 graph as the equivalent
hand-written ``call(...)`` / builder graph (asserted under the shared structural
``canon``), each method is immutable (a verb returns a new ``Frame``, never mutates
the receiver), and the emitted ``.to_pure()`` matches the exact expected strings.
Construction from ``from_tds`` / ``from_db`` and ``Frame``-as-``other`` unwrapping
in joins / concatenate are covered too.
"""

from __future__ import annotations

import datetime

import pytest

from pure_python import m3
from pure_python.compile import (
    Column,
    Frame,
    JoinKind,
    Schema,
    SchemaError,
    agg,
    aggs,
    array,
    asc,
    call,
    col,
    cols,
    db_table,
    desc,
    fcol,
    fcols,
    lam,
    over,
    range_,
    rows,
    tds,
    unbounded,
    window,
)

from .test_expressions import canon


# A small shared source CSV reused across the per-verb cases.
SRC = "id,grp,amt\n1,1,10\n2,0,20"


def _frame_eq_builder(frame: Frame, builder_node) -> None:
    """The canonical proof: the ``Frame``'s node equals the builder graph."""
    assert canon(frame.to_m3()) == canon(builder_node)


# --- construction -----------------------------------------------------------

def test_from_tds_wraps_tds_source():
    frame = Frame.from_tds("id,grp\n1,1")
    assert isinstance(frame.to_m3(), m3.InstanceValue)
    _frame_eq_builder(frame, tds("id,grp\n1,1"))
    assert frame.to_pure() == "#TDS{id,grp\n1,1}#"


def test_from_tds_accepts_full_token():
    frame = Frame.from_tds("#TDS{id,grp\n1,1}#")
    assert frame.to_pure() == "#TDS{id,grp\n1,1}#"


def test_from_db_wraps_db_table_source():
    frame = Frame.from_db("my::Store", "myTable")
    _frame_eq_builder(frame, db_table("my::Store", "myTable"))
    assert frame.to_pure() == "#>{my::Store.myTable}#"


def test_from_db_chain_emits_db_source_with_verb():
    frame = Frame.from_db("my::Store", "myTable").limit(5)
    assert frame.to_pure() == "#>{my::Store.myTable}#->limit(5)"


# --- immutability -----------------------------------------------------------

def test_filter_does_not_mutate_receiver():
    base = Frame.from_tds(SRC)
    derived = base.filter(lambda r: r.amt > 5)
    # The receiver is unchanged; a NEW frame is returned.
    assert base.to_pure() == f"#TDS{{{SRC}}}#"
    assert derived is not base
    assert derived.to_pure() == f"#TDS{{{SRC}}}#->filter({{r | ($r.amt > 5)}})"


def test_chaining_returns_fresh_frames_each_step():
    base = Frame.from_tds(SRC)
    a = base.limit(5)
    b = a.distinct()
    assert base.to_pure() == f"#TDS{{{SRC}}}#"
    assert a.to_pure() == f"#TDS{{{SRC}}}#->limit(5)"
    assert b.to_pure() == f"#TDS{{{SRC}}}#->limit(5)->distinct()"


# --- per-method canon equality (Frame == equivalent builder graph) ----------

def test_filter_equals_builder():
    fluent = Frame.from_tds(SRC).filter(lambda r: r.grp > 0)
    builder = call("filter", tds(SRC), lam(["r"], lambda r: r.grp > 0))
    _frame_eq_builder(fluent, builder)


def test_select_single_equals_builder():
    fluent = Frame.from_tds(SRC).select("id")
    builder = call("select", tds(SRC), col("id"))
    _frame_eq_builder(fluent, builder)


def test_select_multi_equals_builder():
    fluent = Frame.from_tds(SRC).select("id", "grp")
    builder = call("select", tds(SRC), cols("id", "grp"))
    _frame_eq_builder(fluent, builder)


def test_select_requires_a_column():
    with pytest.raises(ValueError, match="at least one column"):
        Frame.from_tds(SRC).select()


def test_extend_single_equals_builder():
    fluent = Frame.from_tds(SRC).extend(("doubled", lambda r: r.amt * 2))
    builder = call("extend", tds(SRC), fcol("doubled", lam(["r"], lambda r: r.amt * 2)))
    _frame_eq_builder(fluent, builder)


def test_extend_multi_equals_builder():
    fluent = Frame.from_tds(SRC).extend(
        ("a", lambda r: r.amt + 1),
        ("b", lambda r: r.grp * 2),
    )
    builder = call(
        "extend",
        tds(SRC),
        fcols(
            fcol("a", lam(["r"], lambda r: r.amt + 1)),
            fcol("b", lam(["r"], lambda r: r.grp * 2)),
        ),
    )
    _frame_eq_builder(fluent, builder)


def test_extend_requires_a_column():
    with pytest.raises(ValueError, match="at least one"):
        Frame.from_tds(SRC).extend()


def test_group_by_single_key_and_agg_equals_builder():
    fluent = Frame.from_tds(SRC).group_by(
        "grp", ("total", lambda r: r.amt, lambda c: c.sum())
    )
    builder = call(
        "groupBy",
        tds(SRC),
        cols("grp"),
        agg("total", lam(["r"], lambda r: r.amt), lam(["c"], lambda c: c.sum())),
    )
    _frame_eq_builder(fluent, builder)


def test_group_by_multi_key_and_aggs_equals_builder():
    fluent = Frame.from_tds(SRC).group_by(
        ["grp", "id"],
        ("total", lambda r: r.amt, lambda c: c.sum()),
        ("cnt", lambda r: r.amt, lambda c: c.count()),
    )
    builder = call(
        "groupBy",
        tds(SRC),
        cols("grp", "id"),
        aggs(
            agg("total", lam(["r"], lambda r: r.amt), lam(["c"], lambda c: c.sum())),
            agg("cnt", lam(["r"], lambda r: r.amt), lam(["c"], lambda c: c.count())),
        ),
    )
    _frame_eq_builder(fluent, builder)


def test_group_by_requires_an_aggregation():
    with pytest.raises(ValueError, match="at least one"):
        Frame.from_tds(SRC).group_by("grp")


def test_join_equals_builder():
    fluent = Frame.from_tds("id,name\n1,a").join(
        tds("rid,val\n1,10"), lambda l, r: l.id == r.rid
    )
    builder = call(
        "join",
        tds("id,name\n1,a"),
        tds("rid,val\n1,10"),
        JoinKind.INNER,
        lam(["l", "r"], lambda l, r: l.id == r.rid),
    )
    _frame_eq_builder(fluent, builder)


@pytest.mark.parametrize(
    "method,kind",
    [
        ("inner_join", JoinKind.INNER),
        ("left_join", JoinKind.LEFT),
        ("right_join", JoinKind.RIGHT),
        ("full_join", JoinKind.FULL),
    ],
)
def test_join_kind_convenience_methods_equal_builder(method, kind):
    fluent = getattr(Frame.from_tds("id\n1"), method)(
        tds("rid\n1"), lambda l, r: l.id == r.rid
    )
    builder = call(
        "join",
        tds("id\n1"),
        tds("rid\n1"),
        kind,
        lam(["l", "r"], lambda l, r: l.id == r.rid),
    )
    _frame_eq_builder(fluent, builder)


def test_as_of_join_equals_builder():
    fluent = Frame.from_tds("id,t\n1,5").as_of_join(
        tds("rid,rt\n1,4"), lambda l, r: l.t >= r.rt
    )
    builder = call(
        "asOfJoin",
        tds("id,t\n1,5"),
        tds("rid,rt\n1,4"),
        lam(["l", "r"], lambda l, r: l.t >= r.rt),
    )
    _frame_eq_builder(fluent, builder)


def test_sort_scalar_equals_builder():
    fluent = Frame.from_tds(SRC).sort(desc("amt"))
    builder = call("sort", tds(SRC), desc(col("amt")))
    _frame_eq_builder(fluent, builder)


def test_sort_bare_name_defaults_to_ascending():
    fluent = Frame.from_tds(SRC).sort("amt")
    builder = call("sort", tds(SRC), asc(col("amt")))
    _frame_eq_builder(fluent, builder)


def test_sort_multi_equals_builder():
    fluent = Frame.from_tds(SRC).sort(asc("id"), desc("grp"))
    builder = call("sort", tds(SRC), array(asc(col("id")), desc(col("grp"))))
    _frame_eq_builder(fluent, builder)


def test_sort_requires_a_spec():
    with pytest.raises(ValueError, match="at least one"):
        Frame.from_tds(SRC).sort()


def test_limit_equals_builder():
    _frame_eq_builder(Frame.from_tds(SRC).limit(5), call("limit", tds(SRC), 5))


def test_drop_equals_builder():
    _frame_eq_builder(Frame.from_tds(SRC).drop(2), call("drop", tds(SRC), 2))


def test_slice_equals_builder():
    _frame_eq_builder(Frame.from_tds(SRC).slice(0, 10), call("slice", tds(SRC), 0, 10))


def test_distinct_equals_builder():
    _frame_eq_builder(Frame.from_tds(SRC).distinct(), call("distinct", tds(SRC)))


def test_concatenate_equals_builder():
    fluent = Frame.from_tds(SRC).concatenate(tds("id,grp,amt\n3,1,5"))
    builder = call("concatenate", tds(SRC), tds("id,grp,amt\n3,1,5"))
    _frame_eq_builder(fluent, builder)


def test_rename_equals_builder():
    fluent = Frame.from_tds(SRC).rename("id", "identifier")
    builder = call("rename", tds(SRC), col("id"), col("identifier"))
    _frame_eq_builder(fluent, builder)


def test_pivot_equals_builder():
    fluent = Frame.from_tds("id,prod,amt\n1,a,10").pivot(
        "prod", ("amount", lambda r: r.amt, lambda c: c.sum())
    )
    builder = call(
        "pivot",
        tds("id,prod,amt\n1,a,10"),
        cols("prod"),
        agg("amount", lam(["r"], lambda r: r.amt), lam(["c"], lambda c: c.sum())),
    )
    _frame_eq_builder(fluent, builder)


def test_window_extend_func_colspec_equals_builder():
    win = over(col("p"), asc(col("o")), rows(-1, 0))
    fluent = Frame.from_tds("p,o,i\n0,1,10").window_extend(
        over(col("p"), asc(col("o")), rows(-1, 0)),
        ("c", lambda p, w, r: r.i),
    )
    builder = call(
        "extend",
        tds("p,o,i\n0,1,10"),
        win,
        fcol("c", lam(["p", "w", "r"], lambda p, w, r: r.i)),
    )
    _frame_eq_builder(fluent, builder)


def test_window_extend_agg_colspec_equals_builder():
    fluent = Frame.from_tds("p,o,i\n0,1,10").window_extend(
        over(col("p"), asc(col("o")), rows(-1, 0)),
        ("sum_i", lambda p, w, r: r.i, lambda y: y.sum()),
    )
    builder = call(
        "extend",
        tds("p,o,i\n0,1,10"),
        over(col("p"), asc(col("o")), rows(-1, 0)),
        agg(
            "sum_i",
            lam(["p", "w", "r"], lambda p, w, r: r.i),
            lam(["y"], lambda y: y.sum()),
        ),
    )
    _frame_eq_builder(fluent, builder)


# --- Frame-as-other unwrapping ----------------------------------------------

def test_join_accepts_a_frame_as_other():
    left = Frame.from_tds("id,name\n1,a")
    right = Frame.from_tds("rid,val\n1,10")
    fluent = left.join(right, lambda l, r: l.id == r.rid)
    builder = call(
        "join",
        tds("id,name\n1,a"),
        tds("rid,val\n1,10"),
        JoinKind.INNER,
        lam(["l", "r"], lambda l, r: l.id == r.rid),
    )
    _frame_eq_builder(fluent, builder)


def test_concatenate_accepts_a_frame_as_other():
    fluent = Frame.from_tds("id\n1").concatenate(Frame.from_tds("id\n2"))
    builder = call("concatenate", tds("id\n1"), tds("id\n2"))
    _frame_eq_builder(fluent, builder)


def test_as_of_join_accepts_a_frame_as_other():
    fluent = Frame.from_tds("id,t\n1,5").as_of_join(
        Frame.from_tds("rid,rt\n1,4"), lambda l, r: l.t >= r.rt
    )
    builder = call(
        "asOfJoin",
        tds("id,t\n1,5"),
        tds("rid,rt\n1,4"),
        lam(["l", "r"], lambda l, r: l.t >= r.rt),
    )
    _frame_eq_builder(fluent, builder)


# --- exact emitted .to_pure() strings, per verb -----------------------------

def test_to_pure_filter():
    q = Frame.from_tds(SRC).filter(lambda r: r.amt > 5)
    assert q.to_pure() == f"#TDS{{{SRC}}}#->filter({{r | ($r.amt > 5)}})"


def test_to_pure_select():
    q = Frame.from_tds(SRC).select("id", "grp")
    assert q.to_pure() == f"#TDS{{{SRC}}}#->select(~[id, grp])"


def test_to_pure_extend():
    q = Frame.from_tds(SRC).extend(("amt_tax", lambda r: r.amt * 1.1))
    assert q.to_pure() == f"#TDS{{{SRC}}}#->extend(~amt_tax:{{r | ($r.amt * 1.1)}})"


def test_to_pure_group_by():
    q = Frame.from_tds(SRC).group_by("grp", ("total", lambda r: r.amt, lambda c: c.sum()))
    assert q.to_pure() == (
        f"#TDS{{{SRC}}}#->groupBy(~[grp], ~total:{{r | $r.amt}}:{{c | $c->sum()}})"
    )


def test_to_pure_sort_scalar():
    q = Frame.from_tds(SRC).sort(desc("amt"))
    assert q.to_pure() == f"#TDS{{{SRC}}}#->sort(~amt->descending())"


def test_to_pure_sort_multi():
    q = Frame.from_tds(SRC).sort(asc("id"), desc("grp"))
    assert q.to_pure() == (
        f"#TDS{{{SRC}}}#->sort([~id->ascending(), ~grp->descending()])"
    )


def test_to_pure_limit_drop_slice_distinct():
    assert Frame.from_tds(SRC).limit(5).to_pure() == f"#TDS{{{SRC}}}#->limit(5)"
    assert Frame.from_tds(SRC).drop(2).to_pure() == f"#TDS{{{SRC}}}#->drop(2)"
    assert Frame.from_tds(SRC).slice(0, 10).to_pure() == f"#TDS{{{SRC}}}#->slice(0, 10)"
    assert Frame.from_tds(SRC).distinct().to_pure() == f"#TDS{{{SRC}}}#->distinct()"


def test_to_pure_rename():
    q = Frame.from_tds(SRC).rename("id", "identifier")
    assert q.to_pure() == f"#TDS{{{SRC}}}#->rename(~id, ~identifier)"


def test_to_pure_concatenate():
    q = Frame.from_tds("id\n1").concatenate(Frame.from_tds("id\n2"))
    assert q.to_pure() == "#TDS{id\n1}#->concatenate(#TDS{id\n2}#)"


def test_to_pure_pivot():
    q = Frame.from_tds("id,prod,amt\n1,a,10").pivot(
        "prod", ("amount", lambda r: r.amt, lambda c: c.sum())
    )
    assert q.to_pure() == (
        "#TDS{id,prod,amt\n1,a,10}#->pivot(~[prod], ~amount:{r | $r.amt}:{c | $c->sum()})"
    )


def test_to_pure_join():
    q = Frame.from_tds("id,name\n1,a").left_join(
        Frame.from_tds("rid,val\n1,10"), lambda l, r: l.id == r.rid
    )
    assert q.to_pure() == (
        "#TDS{id,name\n1,a}#"
        "->join(#TDS{rid,val\n1,10}#, JoinKind.LEFT, {l, r | ($l.id == $r.rid)})"
    )


def test_to_pure_as_of_join():
    q = Frame.from_tds("id,t\n1,5").as_of_join(
        Frame.from_tds("rid,rt\n1,4"), lambda l, r: l.t >= r.rt
    )
    assert q.to_pure() == (
        "#TDS{id,t\n1,5}#->asOfJoin(#TDS{rid,rt\n1,4}#, {l, r | ($l.t >= $r.rt)})"
    )


def test_to_pure_window_extend_func_colspec():
    q = Frame.from_tds("p,o,i\n0,1,10").window_extend(
        over(col("p"), array(asc(col("o")), asc(col("i"))), rows(unbounded(), 0)),
        ("c", lambda p, w, r: r.i),
    )
    assert q.to_pure() == (
        "#TDS{p,o,i\n0,1,10}#"
        "->extend(over(~p, [~o->ascending(), ~i->ascending()], rows(unbounded(), 0)), "
        "~c:{p, w, r | $r.i})"
    )


def test_to_pure_window_extend_agg_colspec():
    q = Frame.from_tds("p,o,i\n0,1,10").window_extend(
        over(col("p"), asc(col("o")), range_(-1, 0)),
        ("sum_i", lambda p, w, r: r.i, lambda y: y.sum()),
    )
    assert q.to_pure() == (
        "#TDS{p,o,i\n0,1,10}#"
        "->extend(over(~p, ~o->ascending(), _range(-1, 0)), "
        "~sum_i:{p, w, r | $r.i}:{y | $y->sum()})"
    )


def test_to_pure_db_table_chain():
    q = Frame.from_db("my::Store", "myTable").filter(lambda r: r.amt > 5).limit(3)
    assert q.to_pure() == (
        "#>{my::Store.myTable}#->filter({r | ($r.amt > 5)})->limit(3)"
    )


# --- a multi-verb chain ------------------------------------------------------

def test_multi_verb_chain_to_pure():
    src = "id,cust,amt\n1,a,10\n2,a,20\n3,b,5"
    q = (
        Frame.from_tds(src)
        .filter(lambda r: r.amt > 5)
        .extend(("amt_tax", lambda r: r.amt * 1.1))
        .group_by("cust", ("total", lambda r: r.amt, lambda c: c.sum()))
        .sort(desc("total"))
        .limit(10)
    )
    assert q.to_pure() == (
        f"#TDS{{{src}}}#"
        "->filter({r | ($r.amt > 5)})"
        "->extend(~amt_tax:{r | ($r.amt * 1.1)})"
        "->groupBy(~[cust], ~total:{r | $r.amt}:{c | $c->sum()})"
        "->sort(~total->descending())"
        "->limit(10)"
    )


def test_multi_verb_chain_equals_builder_graph():
    # The whole chain's m3 graph equals the equivalent hand-written builder graph.
    src = "id,cust,amt\n1,a,10"
    frame = (
        Frame.from_tds(src)
        .filter(lambda r: r.amt > 5)
        .extend(("amt_tax", lambda r: r.amt * 1.1))
        .group_by("cust", ("total", lambda r: r.amt, lambda c: c.sum()))
        .sort(desc("total"))
        .limit(10)
    )
    builder = call(
        "limit",
        call(
            "sort",
            call(
                "groupBy",
                call(
                    "extend",
                    call(
                        "filter",
                        tds(src),
                        lam(["r"], lambda r: r.amt > 5),
                    ),
                    fcol("amt_tax", lam(["r"], lambda r: r.amt * 1.1)),
                ),
                cols("cust"),
                agg("total", lam(["r"], lambda r: r.amt), lam(["c"], lambda c: c.sum())),
            ),
            desc(col("total")),
        ),
        10,
    )
    _frame_eq_builder(frame, builder)


# --- pylegend-additive alignment (all ADDITIVE; Pure-native forms keep working)
# The `Frame` surface is additively aligned to FINOS pylegend's `legendql_api`:
# subscript columns, string join kinds (with the SQL-ish -> Pure name mapping),
# a `window()` helper, dict/kwargs `rename`, named OLAP proxy methods, and a
# 4-arg `as_of_join`. Each new form builds the SAME m3 graph as the Pure-native
# form (asserted under `canon`), so the alignment is provably sugar.

# --- named OLAP functions on the partial-frame proxy ------------------------

def test_window_extend_row_number_olap_emits_camel_case():
    q = Frame.from_tds("p,o,i\n0,1,10").window_extend(
        over(col("p"), asc(col("o")), rows(unbounded(), 0)),
        ("rn", lambda p, w, r: p.row_number(r)),
    )
    assert q.to_pure() == (
        "#TDS{p,o,i\n0,1,10}#"
        "->extend(over(~p, ~o->ascending(), rows(unbounded(), 0)), "
        "~rn:{p, w, r | $p->rowNumber($r)})"
    )


def test_window_extend_dense_rank_olap_emits_camel_case():
    q = Frame.from_tds("p,o,i\n0,1,10").window_extend(
        over(col("p"), asc(col("o")), rows(unbounded(), 0)),
        ("dr", lambda p, w, r: p.dense_rank(w, r)),
    )
    assert q.to_pure() == (
        "#TDS{p,o,i\n0,1,10}#"
        "->extend(over(~p, ~o->ascending(), rows(unbounded(), 0)), "
        "~dr:{p, w, r | $p->denseRank($w, $r)})"
    )


def test_window_extend_olap_snake_equals_camel_under_canon():
    snake = Frame.from_tds("p,o,i\n0,1,10").window_extend(
        over(col("p"), asc(col("o")), rows(unbounded(), 0)),
        ("rn", lambda p, w, r: p.row_number(r)),
    )
    camel = Frame.from_tds("p,o,i\n0,1,10").window_extend(
        over(col("p"), asc(col("o")), rows(unbounded(), 0)),
        ("rn", lambda p, w, r: p.rowNumber(r)),
    )
    _frame_eq_builder(snake, camel.to_m3())


def test_window_extend_rank_lag_lead_pass_through():
    # Single-word OLAP names already match Pure -- no alias.
    rk = Frame.from_tds("p,o,i\n0,1,10").window_extend(
        over(col("p"), asc(col("o"))), ("rk", lambda p, w, r: p.rank(w, r))
    )
    assert rk.to_pure().endswith("~rk:{p, w, r | $p->rank($w, $r)})")
    lg = Frame.from_tds("p,o,i\n0,1,10").window_extend(
        over(col("p"), asc(col("o"))), ("lg", lambda p, w, r: p.lag(r))
    )
    assert lg.to_pure().endswith("~lg:{p, w, r | $p->lag($r)})")


# --- subscript columns ------------------------------------------------------

def test_subscript_column_equals_attribute_access_under_canon():
    # `r["amt"]` builds the same node as `r.amt`.
    sub = Frame.from_tds(SRC).filter(lambda r: r["amt"] > 5)
    attr = Frame.from_tds(SRC).filter(lambda r: r.amt > 5)
    _frame_eq_builder(sub, attr.to_m3())


def test_subscript_column_with_spaces_emits_the_name_verbatim():
    # A column name with spaces / keywords is only reachable via subscript.
    q = Frame.from_tds("Order Id\n1").filter(lambda r: r["Order Id"] > 0)
    assert q.to_pure() == "#TDS{Order Id\n1}#->filter({r | ($r.Order Id > 0)})"


# --- string join kinds (with the SQL-ish -> Pure name mapping) --------------

@pytest.mark.parametrize(
    "kind_string,enum_kind",
    [
        ("INNER", JoinKind.INNER),
        ("inner", JoinKind.INNER),
        ("LEFT_OUTER", JoinKind.LEFT),
        ("left_outer", JoinKind.LEFT),
        ("LEFT", JoinKind.LEFT),
        ("RIGHT_OUTER", JoinKind.RIGHT),
        ("RIGHT", JoinKind.RIGHT),
        ("FULL", JoinKind.FULL),
        ("FULL_OUTER", JoinKind.FULL),
    ],
)
def test_join_string_kind_equals_enum_kind_under_canon(kind_string, enum_kind):
    string_join = Frame.from_tds("id\n1").join(
        tds("rid\n1"), lambda l, r: l.id == r.rid, kind=kind_string
    )
    enum_join = Frame.from_tds("id\n1").join(
        tds("rid\n1"), lambda l, r: l.id == r.rid, kind=enum_kind
    )
    _frame_eq_builder(string_join, enum_join.to_m3())


def test_join_enum_kind_still_accepted():
    # The Pure-native `JoinKind.*` enum-ref keeps working (default + explicit).
    default = Frame.from_tds("id\n1").join(tds("rid\n1"), lambda l, r: l.id == r.rid)
    explicit = Frame.from_tds("id\n1").join(
        tds("rid\n1"), lambda l, r: l.id == r.rid, kind=JoinKind.INNER
    )
    _frame_eq_builder(default, explicit.to_m3())


def test_join_string_kind_left_outer_to_pure():
    q = Frame.from_tds("id,name\n1,a").join(
        tds("rid,val\n1,10"), lambda l, r: l.id == r.rid, kind="LEFT_OUTER"
    )
    assert q.to_pure() == (
        "#TDS{id,name\n1,a}#"
        "->join(#TDS{rid,val\n1,10}#, JoinKind.LEFT, {l, r | ($l.id == $r.rid)})"
    )


def test_join_unknown_string_kind_rejected():
    with pytest.raises(ValueError, match="unknown join kind"):
        Frame.from_tds("id\n1").join(tds("rid\n1"), lambda l, r: l.id == r.rid, kind="CROSS")


# --- window() helper --------------------------------------------------------

def test_window_helper_equals_over_direct_under_canon():
    helper = Frame.window(partition_by="p", order_by="o", frame=rows(unbounded(), 0))
    direct = over(col("p"), asc(col("o")), rows(unbounded(), 0))
    assert canon(helper) == canon(direct)


def test_window_helper_multi_partition_and_order():
    helper = Frame.window(
        partition_by=["a", "b"], order_by=[asc("o"), desc("i")], frame=rows(-1, 0)
    )
    direct = over(cols("a", "b"), array(asc(col("o")), desc(col("i"))), rows(-1, 0))
    assert canon(helper) == canon(direct)


def test_window_helper_partition_only():
    assert canon(Frame.window(partition_by="p")) == canon(over(col("p")))


def test_window_helper_drives_window_extend_two_step():
    # The pylegend two-step form: `f.window_extend(f.window(...), (name, fn))`.
    two_step = Frame.from_tds("p,o,i\n0,1,10").window_extend(
        Frame.window(partition_by="p", order_by="o", frame=rows(unbounded(), 0)),
        ("rn", lambda p, w, r: p.row_number(r)),
    )
    direct = Frame.from_tds("p,o,i\n0,1,10").window_extend(
        over(col("p"), asc(col("o")), rows(unbounded(), 0)),
        ("rn", lambda p, w, r: p.rowNumber(r)),
    )
    _frame_eq_builder(two_step, direct.to_m3())


def test_window_module_helper_matches_frame_window():
    # The re-exported `window(...)` builder equals `Frame.window(...)`.
    assert canon(window(partition_by="p", order_by="o")) == canon(
        Frame.window(partition_by="p", order_by="o")
    )


# --- dict / kwargs rename ---------------------------------------------------

def test_rename_dict_equals_chained_positional_under_canon():
    by_dict = Frame.from_tds(SRC).rename({"id": "identifier", "grp": "group"})
    chained = Frame.from_tds(SRC).rename("id", "identifier").rename("grp", "group")
    _frame_eq_builder(by_dict, chained.to_m3())


def test_rename_kwargs_equals_positional_under_canon():
    by_kwargs = Frame.from_tds(SRC).rename(id="identifier")
    positional = Frame.from_tds(SRC).rename("id", "identifier")
    _frame_eq_builder(by_kwargs, positional.to_m3())


def test_rename_positional_still_works():
    fluent = Frame.from_tds(SRC).rename("id", "identifier")
    builder = call("rename", tds(SRC), col("id"), col("identifier"))
    _frame_eq_builder(fluent, builder)


def test_rename_dict_multi_pair_chains_to_pure():
    q = Frame.from_tds(SRC).rename({"id": "identifier", "grp": "group"})
    assert q.to_pure() == (
        f"#TDS{{{SRC}}}#->rename(~id, ~identifier)->rename(~grp, ~group)"
    )


def test_rename_empty_rejected():
    with pytest.raises(ValueError, match="at least one"):
        Frame.from_tds(SRC).rename()


# --- as_of_join arity -------------------------------------------------------

def test_as_of_join_three_arg_still_works():
    fluent = Frame.from_tds("id,t\n1,5").as_of_join(
        tds("rid,rt\n1,4"), lambda l, r: l.t >= r.rt
    )
    builder = call(
        "asOfJoin",
        tds("id,t\n1,5"),
        tds("rid,rt\n1,4"),
        lam(["l", "r"], lambda l, r: l.t >= r.rt),
    )
    _frame_eq_builder(fluent, builder)


def test_as_of_join_four_arg_with_join_condition():
    fluent = Frame.from_tds("id,t,k\n1,5,9").as_of_join(
        tds("rid,rt,k\n1,4,9"),
        lambda l, r: l.t >= r.rt,
        join_condition=lambda l, r: l.k == r.k,
    )
    builder = call(
        "asOfJoin",
        tds("id,t,k\n1,5,9"),
        tds("rid,rt,k\n1,4,9"),
        lam(["l", "r"], lambda l, r: l.t >= r.rt),
        lam(["l", "r"], lambda l, r: l.k == r.k),
    )
    _frame_eq_builder(fluent, builder)


def test_as_of_join_four_arg_to_pure():
    q = Frame.from_tds("id,t\n1,5").as_of_join(
        tds("rid,rt\n1,4"),
        lambda l, r: l.t >= r.rt,
        join_condition=lambda l, r: l.t == r.rt,
    )
    assert q.to_pure() == (
        "#TDS{id,t\n1,5}#"
        "->asOfJoin(#TDS{rid,rt\n1,4}#, {l, r | ($l.t >= $r.rt)}, {l, r | ($l.t == $r.rt)})"
    )


# --- a full pylegend-style chain --------------------------------------------

def test_pylegend_style_chain_to_pure():
    # Subscript columns + a string join kind + `window()` + an OLAP column,
    # the pylegend-aligned surface end-to-end.
    orders = Frame.from_tds("Order Id,cust,amt\n1,a,10\n2,a,20\n3,b,5")
    custs = Frame.from_tds("cid,region\na,US\nb,EU")
    q = (
        orders
        .filter(lambda r: r["amt"] > 5)
        .join(custs, lambda l, r: l.cust == r.cid, kind="LEFT_OUTER")
        .window_extend(
            Frame.window(partition_by="cust", order_by="amt", frame=rows(unbounded(), 0)),
            ("rn", lambda p, w, r: p.row_number(r)),
        )
        .rename({"rn": "row_num"})
    )
    assert q.to_pure() == (
        "#TDS{Order Id,cust,amt\n1,a,10\n2,a,20\n3,b,5}#"
        "->filter({r | ($r.amt > 5)})"
        "->join(#TDS{cid,region\na,US\nb,EU}#, JoinKind.LEFT, {l, r | ($l.cust == $r.cid)})"
        "->extend(over(~cust, ~amt->ascending(), rows(unbounded(), 0)), "
        "~rn:{p, w, r | $p->rowNumber($r)})"
        "->rename(~rn, ~row_num)"
    )

# --- typed-schema layer: from_db / from_tds / from_rows + getters -----------

def test_from_tds_carries_an_optional_schema():
    s = Schema.of(id=int, grp=int)
    f = Frame.from_tds("id,grp\n1,1", schema=s)
    assert f.schema is s
    assert f.columns == s.columns
    # Emit is unchanged -- the TDS text is the source of truth.
    assert f.to_pure() == "#TDS{id,grp\n1,1}#"


def test_from_db_carries_an_optional_schema():
    s = Schema.of(id=int, name=str)
    f = Frame.from_db("my::Store", "myTable", schema=s)
    assert f.schema is s
    assert f.to_pure() == "#>{my::Store.myTable}#"


def test_from_rows_builds_a_tds_literal_from_typed_rows_positional():
    s = Schema.of(id=int, name=str, amt=float)
    f = Frame.from_rows(s, [(1, "a", 1.5), (2, "b", 2.5)])
    assert f.schema is s
    assert f.to_pure() == "#TDS{id,name,amt\n1,a,1.5\n2,b,2.5}#"


def test_from_rows_accepts_dict_rows_by_name():
    s = Schema.of(id=int, name=str)
    f = Frame.from_rows(s, [{"id": 1, "name": "a"}, {"name": "b", "id": 2}])
    # Dict rows are reordered to schema order before serialization.
    assert f.to_pure() == "#TDS{id,name\n1,a\n2,b}#"


def test_from_rows_empty_rows_produces_header_only_tds():
    s = Schema.of(id=int, name=str)
    f = Frame.from_rows(s, [])
    assert f.to_pure() == "#TDS{id,name}#"


def test_from_rows_dict_row_missing_column_raises_schema_error():
    s = Schema.of(id=int, name=str)
    with pytest.raises(SchemaError, match="missing columns"):
        Frame.from_rows(s, [{"id": 1}])


def test_from_rows_tuple_row_arity_mismatch_raises_schema_error():
    s = Schema.of(id=int, name=str)
    with pytest.raises(SchemaError, match="2 columns"):
        Frame.from_rows(s, [(1,)])


def test_from_rows_string_cell_containing_comma_rejected():
    s = Schema.of(name=str)
    with pytest.raises(SchemaError, match="delimiter"):
        Frame.from_rows(s, [("a,b",)])


def test_from_rows_serializes_each_primitive_in_its_pure_canonical_form():
    import datetime
    s = Schema.from_columns(
        Column.integer("i"),
        Column.float_("f"),
        Column.boolean("b"),
        Column.string("s"),
        Column.strict_date("d"),
        Column.date_time("dt"),
        Column.strict_time("t"),
    )
    f = Frame.from_rows(
        s,
        [(
            -3,
            1.5,
            True,
            "hello",
            datetime.date(2020, 1, 2),
            datetime.datetime(2020, 1, 2, 10, 30, 0),
            datetime.time(10, 30, 0),
        )],
    )
    assert f.to_pure() == (
        "#TDS{i,f,b,s,d,dt,t\n"
        "-3,1.5,true,hello,2020-01-02,2020-01-02T10:30:00,10:30:00}#"
    )


def test_schema_and_columns_are_none_without_a_schema():
    f = Frame.from_tds("id\n1")
    assert f.schema is None
    assert f.columns is None


# --- pass-through: filter / sort / limit / drop(n) / slice / distinct -------

def test_filter_passes_through_input_schema():
    s = Schema.of(id=int, amt=int)
    f = Frame.from_tds("id,amt\n1,10", schema=s).filter(lambda r: r.amt > 5)
    assert f.schema is s


def test_sort_pass_through_validates_a_bare_column_name():
    s = Schema.of(id=int, amt=int)
    Frame.from_tds("id,amt\n1,10", schema=s).sort("amt")  # OK
    with pytest.raises(SchemaError, match="verb='sort'"):
        Frame.from_tds("id,amt\n1,10", schema=s).sort("ammt")


def test_sort_pass_through_preserves_schema():
    s = Schema.of(id=int, amt=int)
    f = Frame.from_tds("id,amt\n1,10", schema=s).sort(desc("amt"))
    assert f.schema is s


def test_limit_drop_n_slice_distinct_pass_through_preserve_schema():
    s = Schema.of(id=int, amt=int)
    base = Frame.from_tds("id,amt\n1,10", schema=s)
    assert base.limit(5).schema is s
    assert base.drop(2).schema is s
    assert base.slice(0, 10).schema is s
    assert base.distinct().schema is s


# --- computed: select / drop(*names) / rename / concatenate -----------------

def test_select_validates_and_computes_output_schema():
    s = Schema.of(id=int, amt=int, name=str)
    f = Frame.from_tds("id,amt,name\n1,10,a", schema=s).select("name", "id")
    # Schema mirrors the requested order.
    assert f.schema.names() == ("name", "id")
    assert f.schema.of_name("name").type is m3.String
    assert f.schema.of_name("id").type is m3.Integer


def test_select_unknown_column_raises_schema_error_with_verb_in_message():
    s = Schema.of(id=int, amt=int)
    with pytest.raises(SchemaError) as exc:
        Frame.from_tds("id,amt\n1,10", schema=s).select("ammt")
    msg = str(exc.value)
    assert "verb='select'" in msg
    assert "'ammt'" in msg
    assert "id" in msg and "amt" in msg


def test_drop_names_validates_and_removes_columns_from_schema():
    s = Schema.of(id=int, amt=int, name=str)
    f = Frame.from_tds("id,amt,name\n1,10,a", schema=s).drop("amt")
    assert f.schema.names() == ("id", "name")
    # Without a schema, drop(*names) cannot compute the remaining columns.
    with pytest.raises(SchemaError, match="requires a schema"):
        Frame.from_tds("id,amt,name\n1,10,a").drop("amt")


def test_drop_names_unknown_column_raises_schema_error():
    s = Schema.of(id=int, amt=int)
    with pytest.raises(SchemaError, match="verb='drop'"):
        Frame.from_tds("id,amt\n1,10", schema=s).drop("ammt")


def test_drop_n_row_form_preserves_schema_and_existing_behavior():
    # The original int-arg row-drop verb is unchanged (schema pass-through).
    s = Schema.of(id=int, amt=int)
    f = Frame.from_tds("id,amt\n1,10", schema=s).drop(2)
    assert f.schema is s
    assert f.to_pure() == "#TDS{id,amt\n1,10}#->drop(2)"


def test_rename_validates_old_name_and_renames_in_schema():
    s = Schema.of(id=int, amt=int)
    f = Frame.from_tds("id,amt\n1,10", schema=s).rename("amt", "amount")
    assert f.schema.names() == ("id", "amount")
    assert f.schema.of_name("amount").type is m3.Integer
    with pytest.raises(SchemaError, match="verb='rename'"):
        Frame.from_tds("id,amt\n1,10", schema=s).rename("ammt", "amount")


def test_rename_dict_multi_pair_chains_validation_and_propagates_schema():
    s = Schema.of(id=int, amt=int)
    f = Frame.from_tds("id,amt\n1,10", schema=s).rename({"id": "identifier", "amt": "amount"})
    assert f.schema.names() == ("identifier", "amount")


def test_concatenate_with_matching_schemas_preserves_left_schema():
    s = Schema.of(id=int, amt=int)
    a = Frame.from_tds("id,amt\n1,10", schema=s)
    b = Frame.from_tds("id,amt\n2,20", schema=Schema.of(id=int, amt=int))
    out = a.concatenate(b)
    assert out.schema.names() == ("id", "amt")


def test_concatenate_mismatched_schemas_raise_schema_error():
    a = Frame.from_tds("id,amt\n1,10", schema=Schema.of(id=int, amt=int))
    b = Frame.from_tds("id,name\n1,x", schema=Schema.of(id=int, name=str))
    with pytest.raises(SchemaError, match="concatenate schemas must match"):
        a.concatenate(b)


def test_concatenate_with_other_unschematized_drops_downstream_schema():
    a = Frame.from_tds("id,amt\n1,10", schema=Schema.of(id=int, amt=int))
    b = Frame.from_tds("id,amt\n2,20")
    assert a.concatenate(b).schema is None


# --- unknown by default: extend / window_extend / group_by / pivot / joins --

def test_extend_without_out_schema_drops_downstream_schema():
    s = Schema.of(id=int, amt=int)
    f = Frame.from_tds("id,amt\n1,10", schema=s).extend(
        ("doubled", lambda r: r.amt * 2)
    )
    assert f.schema is None
    # Validation stops past the boundary.
    f.select("nope")  # no raise -- downstream has no schema.


def test_extend_with_out_schema_appends_columns_and_keeps_validation_alive():
    s = Schema.of(id=int, amt=int)
    f = Frame.from_tds("id,amt\n1,10", schema=s).extend(
        ("doubled", lambda r: r.amt * 2),
        out_schema=Schema.of(doubled=int),
    )
    assert f.schema.names() == ("id", "amt", "doubled")
    # Downstream validation still works.
    with pytest.raises(SchemaError, match="verb='select'"):
        f.select("nope")
    # And valid downstream is happy.
    assert f.select("doubled").schema.names() == ("doubled",)


def test_extend_out_schema_clash_raises():
    s = Schema.of(id=int, amt=int)
    with pytest.raises(SchemaError, match="already in schema"):
        Frame.from_tds("id,amt\n1,10", schema=s).extend(
            ("amt", lambda r: r.amt),
            out_schema=Schema.of(amt=int),
        )


def test_window_extend_propagation_mirrors_extend():
    s = Schema.of(p=int, o=int, i=int)
    base = Frame.from_tds("p,o,i\n0,1,10", schema=s)
    no_out = base.window_extend(
        Frame.window(partition_by="p", order_by="o"),
        ("rn", lambda p, w, r: p.row_number(r)),
    )
    assert no_out.schema is None
    with_out = base.window_extend(
        Frame.window(partition_by="p", order_by="o"),
        ("rn", lambda p, w, r: p.row_number(r)),
        out_schema=Schema.of(rn=int),
    )
    assert with_out.schema.names() == ("p", "o", "i", "rn")


def test_group_by_validates_keys_and_uses_out_schema():
    s = Schema.of(grp=int, amt=int)
    base = Frame.from_tds("grp,amt\n1,10", schema=s)
    no_out = base.group_by("grp", ("total", lambda r: r.amt, lambda c: c.sum()))
    assert no_out.schema is None
    with_out = base.group_by(
        "grp",
        ("total", lambda r: r.amt, lambda c: c.sum()),
        out_schema=Schema.of(total=int),
    )
    assert with_out.schema.names() == ("grp", "total")
    with pytest.raises(SchemaError, match="verb='group_by'"):
        base.group_by("grpp", ("total", lambda r: r.amt, lambda c: c.sum()))


def test_pivot_validates_pivot_columns_and_uses_out_schema():
    s = Schema.of(id=int, prod=str, amt=int)
    base = Frame.from_tds("id,prod,amt\n1,a,10", schema=s)
    # Pivot output columns are engine-fanned; without out_schema downstream is None.
    no_out = base.pivot("prod", ("amount", lambda r: r.amt, lambda c: c.sum()))
    assert no_out.schema is None
    with_out = base.pivot(
        "prod",
        ("amount", lambda r: r.amt, lambda c: c.sum()),
        out_schema=Schema.of(a_amount=int, b_amount=int),
    )
    assert with_out.schema.names() == ("a_amount", "b_amount")
    with pytest.raises(SchemaError, match="verb='pivot'"):
        base.pivot("prodd", ("amount", lambda r: r.amt, lambda c: c.sum()))


def test_join_infers_union_when_both_schemas_known():
    left = Frame.from_tds("id,name\n1,a", schema=Schema.of(id=int, name=str))
    right = Frame.from_tds("rid,val\n1,10", schema=Schema.of(rid=int, val=int))
    f = left.join(right, lambda l, r: l.id == r.rid)
    assert f.schema.names() == ("id", "name", "rid", "val")


def test_join_column_collision_raises_schema_error_naming_both_sides():
    left = Frame.from_tds("id,name\n1,a", schema=Schema.of(id=int, name=str))
    right = Frame.from_tds("id,val\n1,10", schema=Schema.of(id=int, val=int))
    with pytest.raises(SchemaError) as exc:
        left.join(right, lambda l, r: l.id == r.id)
    msg = str(exc.value)
    assert "'id'" in msg
    assert "left=" in msg and "right=" in msg


def test_join_out_schema_overrides_inferred_union():
    left = Frame.from_tds("id\n1", schema=Schema.of(id=int))
    right = Frame.from_tds("id\n1", schema=Schema.of(id=int))
    explicit = Schema.of(left_id=int, right_id=int)
    f = left.join(right, lambda l, r: l.id == r.id, out_schema=explicit)
    assert f.schema is explicit


def test_join_with_one_side_unschematized_drops_downstream_schema_without_out():
    left = Frame.from_tds("id\n1", schema=Schema.of(id=int))
    right = Frame.from_tds("id\n1")  # no schema
    assert left.join(right, lambda l, r: l.id == r.id).schema is None


def test_as_of_join_infers_union_and_accepts_out_schema():
    left = Frame.from_tds("id,t\n1,5", schema=Schema.of(id=int, t=int))
    right = Frame.from_tds("rid,rt\n1,4", schema=Schema.of(rid=int, rt=int))
    f = left.as_of_join(right, lambda l, r: l.t >= r.rt)
    assert f.schema.names() == ("id", "t", "rid", "rt")
    explicit = Schema.of(only=int)
    f2 = left.as_of_join(right, lambda l, r: l.t >= r.rt, out_schema=explicit)
    assert f2.schema is explicit


# --- emit invariance: with-schema produces the SAME m3 graph / Pure text -----

def test_with_schema_emits_byte_identical_tds_and_node_as_without():
    src = "id,grp\n1,1\n2,0"
    bare = Frame.from_tds(src).filter(lambda r: r.grp > 0).limit(5)
    typed = (
        Frame.from_tds(src, schema=Schema.of(id=int, grp=int))
        .filter(lambda r: r.grp > 0)
        .limit(5)
    )
    assert bare.to_pure() == typed.to_pure()
    assert canon(bare.to_m3()) == canon(typed.to_m3())


def test_inner_left_right_full_join_thread_out_schema_through():
    left = Frame.from_tds("id\n1", schema=Schema.of(id=int))
    right = Frame.from_tds("rid\n1", schema=Schema.of(rid=int))
    explicit = Schema.of(merged=int)
    for method in ("inner_join", "left_join", "right_join", "full_join"):
        f = getattr(left, method)(right, lambda l, r: l.id == r.rid, out_schema=explicit)
        assert f.schema is explicit


# --- a typed-chain example weaving Schema.of(...) through several verbs -----

def test_typed_chain_with_out_schema_threads_validation_to_the_tail():
    schema = Schema.of(cust=str, amt=int, ship_date=datetime.date)
    orders = Frame.from_db("my::Store", "orders", schema=schema)
    q = (
        orders
        .filter(lambda r: r.amt > 5)
        .extend(
            ("amt_tax", lambda r: r.amt * 1.1),
            out_schema=Schema.of(amt_tax=float),
        )
        .select("cust", "amt", "amt_tax")
        .sort(desc("amt_tax"))
    )
    # The tail schema is computed mechanically.
    assert q.schema.names() == ("cust", "amt", "amt_tax")
    # A typo would have raised:
    with pytest.raises(SchemaError, match="verb='select'"):
        (
            orders
            .filter(lambda r: r.amt > 5)
            .extend(
                ("amt_tax", lambda r: r.amt * 1.1),
                out_schema=Schema.of(amt_tax=float),
            )
            .select("cust", "ammt")  # typo
        )
