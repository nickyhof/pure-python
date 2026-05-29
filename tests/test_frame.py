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

import pytest

from pure_python import m3
from pure_python.compile import (
    Frame,
    JoinKind,
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
