"""A legendql-style fluent, immutable relation query ``Frame``.

:class:`Frame` is a thin, *branded* facade over the relation-verb surface already
built in :mod:`pure_python.compile.expressions` /
:mod:`pure_python.compile.m3_to_pure`. It adds **no new representation**: a
``Frame`` simply wraps a single ``m3`` ``ValueSpecification`` node (a relation
source plus the verbs accrued onto it) and every method returns a *new* ``Frame``
wrapping ``call("verb", self._node, *args)`` -- never mutating the receiver. The
underlying node is the exact same graph the free :func:`call` / builder path
produces, so the facade is provably faithful sugar (see ``tests/test_frame.py``,
which asserts ``Frame(...).to_m3()`` equals the equivalent hand-written builder
graph under the shared ``canon`` projection).

Row-proxy lambdas are wired through the existing :func:`lam`: a method that takes
a per-row expression (``filter`` / ``extend`` / the ``group_by`` agg map) calls
``lam(["r"], f)``, passing ``f`` an :class:`Expr` row proxy so ``r.amt > 5`` /
``r.amt * 1.1`` build expression graphs via ``Expr``'s overloaded operators and
``__getattr__``. Join conditions take two proxies (``lam(["l", "r"], f)``); the
windowed-extend column lambda takes the canonical three (``lam(["p", "w", "r"],
f)``). The arity is fixed per method, so **no lambda-source / AST introspection is
needed** -- the parameter names are explicit, exactly as :func:`lam` already
requires.

Construct a ``Frame`` from a relation source via the entry points
:meth:`Frame.from_tds` (an inline ``#TDS{...}#`` literal -- the primary, fully
working source) and :meth:`Frame.from_db` (a ``#>{db::Store.table}#`` database
table -- parses; compiling needs a real database defined, which this sugar layer
does not fabricate). Expose :meth:`to_m3` (the node) and :meth:`to_pure` (the
emitted Pure relation grammar via the existing :func:`_expression`)::

    orders = Frame.from_tds("id,cust,amt\\n1,a,10\\n2,a,20\\n3,b,5")
    q = (orders
         .filter(lambda r: r.amt > 5)
         .extend(("amt_tax", lambda r: r.amt * 1.1))
         .group_by("cust", ("total", lambda r: r.amt, lambda c: c.sum()))
         .sort(desc("total"))
         .limit(10))
    q.to_pure()   # "#TDS{...}#->filter(...)->extend(...)->groupBy(...)->sort(...)->limit(10)"
    q.to_m3()     # the underlying m3 ValueSpecification node
"""

from __future__ import annotations

from typing import Callable

from pure_python import m3

from .expressions import (
    Expr,
    JoinKind,
    agg,
    aggs,
    array,
    asc,
    call,
    coerce,
    col,
    cols,
    db_table,
    desc,
    fcol,
    fcols,
    join_kind,
    lam,
    tds,
    window,
)
from .m3_to_pure import _expression

# A column lambda receives one row proxy; a join condition two; a windowed-extend
# column the canonical three. Named here so each method reads at a glance.
_ROW = ["r"]
_JOIN = ["l", "r"]
_WINDOW = ["p", "w", "r"]


def _unwrap(other: object) -> object:
    """Unwrap a ``Frame`` to its node; pass an ``Expr`` / raw node / scalar through.

    A relation-valued argument (the second relation of a ``join`` / ``concatenate``
    / ``asOfJoin``) may be given as a ``Frame``, a raw ``m3`` node, a ``tds`` /
    ``db_table`` source, or an :class:`Expr`. ``coerce`` already handles the latter
    three; this just peels a ``Frame`` first.
    """
    if isinstance(other, Frame):
        return other._node
    return other


class Frame:
    """An immutable, fluent relation-query builder over the existing verbs.

    Wraps a single relation ``ValueSpecification`` node. Build one with
    :meth:`from_tds` / :meth:`from_db` (or, internally, from any relation node) and
    chain verbs; each verb returns a new ``Frame``. Read the result with
    :meth:`to_m3` (the node) / :meth:`to_pure` (emitted Pure).
    """

    __slots__ = ("_node",)
    # `Frame` brands a value graph; comparing two frames structurally is done via
    # `to_m3()` + the test `canon`, not `==`, so leave equality/hashing as identity.

    def __init__(self, node: m3.ValueSpecification | m3.InstanceValue):
        self._node = node

    # -- entry points --------------------------------------------------
    @classmethod
    def from_tds(cls, text: str) -> "Frame":
        """A ``Frame`` over an inline ``#TDS{...}#`` literal (the primary source).

        ``text`` is the CSV body (``"id,amt\\n1,10"``) or a full ``#TDS{...}#``
        token; delegates to :func:`tds`.
        """
        return cls(tds(text))

    @classmethod
    def from_db(cls, database: str, table: str) -> "Frame":
        """A ``Frame`` over a ``#>{database.table}#`` database-table source.

        Delegates to :func:`db_table`. This source PARSES via the real engine, but
        only COMPILES once the named ``database`` store is defined in the model
        (with no store it fails compile with ``The store '<database>' can't be
        found.``); this sugar layer does not fabricate a database, so a ``from_db``
        chain is validated to parse (see ``tests/test_legend_bridge.py``).
        """
        return cls(db_table(database, table))

    # -- row-filtering / projection ------------------------------------
    def filter(self, predicate: Callable[[Expr], object]) -> "Frame":
        """``->filter({r | <predicate>})`` -- keep rows where the predicate holds.

        ``predicate`` is a one-row lambda (``lambda r: r.amt > 5``); wired via
        :func:`lam`.
        """
        return self._verb("filter", lam(_ROW, predicate))

    def select(self, *names: str) -> "Frame":
        """``->select(~a)`` / ``->select(~[a, b])`` -- project the named columns.

        One name builds a scalar :func:`col`, several a :func:`cols` array.
        """
        if not names:
            raise ValueError("select requires at least one column name")
        spec = col(names[0]) if len(names) == 1 else cols(*names)
        return self._verb("select", spec)

    def extend(self, *columns: tuple[str, Callable[[Expr], object]]) -> "Frame":
        """``->extend(~name:{r | <expr>})`` -- add one or many derived columns.

        Each ``column`` is a ``("name", lambda r: <expr>)`` pair; one pair builds a
        :func:`fcol`, several a :func:`fcols` array. Each column lambda is wired via
        :func:`lam` with a single row proxy.
        """
        if not columns:
            raise ValueError("extend requires at least one (name, lambda) column")
        specs = [fcol(name, lam(_ROW, fn)) for name, fn in columns]
        spec = specs[0] if len(specs) == 1 else fcols(*specs)
        return self._verb("extend", spec)

    # -- grouping ------------------------------------------------------
    def group_by(
        self,
        keys: str | list[str],
        *aggregations: tuple[str, Callable[[Expr], object], Callable[[Expr], object]],
    ) -> "Frame":
        """``->groupBy(~[keys], ~name:{r | <map>}:{c | <reduce>})`` -- grouped aggregation.

        ``keys`` is one column name or a list of names (always emitted as a
        :func:`cols` ``ColSpecArray`` -- the engine's ``groupBy`` overload takes a
        ``ColSpecArray``). Each aggregation is a ``("name", map_lambda,
        reduce_lambda)`` triple: ``map_lambda`` is a one-row lambda producing the
        value (``lambda r: r.amt``), ``reduce_lambda`` a one-collection lambda
        producing the aggregate (``lambda c: c.sum()``); both wired via
        :func:`lam`. One triple builds an :func:`agg`, several an :func:`aggs` array.
        """
        if not aggregations:
            raise ValueError("group_by requires at least one (name, map, reduce) aggregation")
        key_names = [keys] if isinstance(keys, str) else list(keys)
        key_spec = cols(*key_names)
        specs = [
            agg(name, lam(_ROW, map_fn), lam(["c"], reduce_fn))
            for name, map_fn, reduce_fn in aggregations
        ]
        agg_spec = specs[0] if len(specs) == 1 else aggs(*specs)
        return self._verb("groupBy", key_spec, agg_spec)

    # -- joins ---------------------------------------------------------
    def join(
        self,
        other: object,
        on: Callable[[Expr, Expr], object],
        kind: object = JoinKind.INNER,
    ) -> "Frame":
        """``->join(other, kind, {l, r | <on>})`` -- relational join.

        ``other`` is the right relation (a ``Frame`` / raw node / :func:`tds` /
        :func:`db_table`), ``on`` a two-row condition lambda (``lambda l, r: l.id
        == r.fid``) wired via :func:`lam`, and ``kind`` either a :class:`JoinKind`
        constant (default ``INNER``) or a pylegend string -- ``'INNER'`` /
        ``'LEFT_OUTER'`` / ``'RIGHT_OUTER'`` / ``'FULL'`` (case-insensitive;
        ``LEFT_OUTER`` -> ``JoinKind.LEFT``, ``RIGHT_OUTER`` -> ``JoinKind.RIGHT``),
        normalized via :func:`join_kind`.
        """
        return self._verb("join", _unwrap(other), join_kind(kind), lam(_JOIN, on))

    def inner_join(self, other: object, on: Callable[[Expr, Expr], object]) -> "Frame":
        """:meth:`join` with ``JoinKind.INNER``."""
        return self.join(other, on, JoinKind.INNER)

    def left_join(self, other: object, on: Callable[[Expr, Expr], object]) -> "Frame":
        """:meth:`join` with ``JoinKind.LEFT``."""
        return self.join(other, on, JoinKind.LEFT)

    def right_join(self, other: object, on: Callable[[Expr, Expr], object]) -> "Frame":
        """:meth:`join` with ``JoinKind.RIGHT``."""
        return self.join(other, on, JoinKind.RIGHT)

    def full_join(self, other: object, on: Callable[[Expr, Expr], object]) -> "Frame":
        """:meth:`join` with ``JoinKind.FULL``."""
        return self.join(other, on, JoinKind.FULL)

    def as_of_join(
        self,
        other: object,
        on: Callable[[Expr, Expr], object],
        join_condition: Callable[[Expr, Expr], object] | None = None,
    ) -> "Frame":
        """``->asOfJoin(other, {l, r | <match>}[, {l, r | <join>}])`` -- as-of join.

        ``other`` is the right relation; ``on`` the two-row *match* condition
        (``lambda l, r: l.t >= r.rt``) wired via :func:`lam`. No ``JoinKind`` (the
        as-of overloads take none).

        With ``join_condition`` omitted this is the 3-arg overload
        (``asOfJoin_Relation_1__Relation_1__Function_1__Relation_1_``). pylegend's
        ``as_of_join(other, match_function, join_condition=...)`` second condition
        wires the 4-arg overload
        (``asOfJoin_Relation_1__Relation_1__Function_1__Function_1__Relation_1_``),
        emitting ``->asOfJoin(other, {match}, {join})`` -- both compile (verified
        via the Legend bridge).
        """
        match = lam(_JOIN, on)
        if join_condition is None:
            return self._verb("asOfJoin", _unwrap(other), match)
        return self._verb("asOfJoin", _unwrap(other), match, lam(_JOIN, join_condition))

    # -- ordering ------------------------------------------------------
    def sort(self, *specs: object) -> "Frame":
        """``->sort(~c->ascending())`` / ``->sort([...])`` -- order by sort specs.

        Each spec is an :func:`asc` / :func:`desc` ``SortInfo`` or a bare column
        name (defaulting to ascending). One spec emits the scalar form, several the
        bracketed :func:`array` list form (the engine's ``SortInfo[*]`` overload).
        """
        if not specs:
            raise ValueError("sort requires at least one sort spec")
        directions = [self._sort_spec(s) for s in specs]
        spec = directions[0] if len(directions) == 1 else array(*directions)
        return self._verb("sort", spec)

    @staticmethod
    def _sort_spec(spec: object) -> object:
        """Normalize a sort spec: a bare name -> ascending ``~name->ascending()``."""
        if isinstance(spec, str):
            return asc(col(spec))
        return spec

    # -- row slicing ---------------------------------------------------
    def limit(self, n: int) -> "Frame":
        """``->limit(n)`` -- keep the first ``n`` rows."""
        return self._verb("limit", n)

    def drop(self, n: int) -> "Frame":
        """``->drop(n)`` -- skip the first ``n`` rows."""
        return self._verb("drop", n)

    def slice(self, start: int, stop: int) -> "Frame":
        """``->slice(start, stop)`` -- the ``[start, stop)`` row window."""
        return self._verb("slice", start, stop)

    def distinct(self) -> "Frame":
        """``->distinct()`` -- drop duplicate rows."""
        return self._verb("distinct")

    def concatenate(self, other: object) -> "Frame":
        """``->concatenate(other)`` -- union/append another relation's rows.

        ``other`` may be a ``Frame`` / raw node / :func:`tds` / :func:`db_table`.
        """
        return self._verb("concatenate", _unwrap(other))

    # -- renaming ------------------------------------------------------
    def rename(
        self,
        old: str | dict[str, str] | None = None,
        new: str | None = None,
        **column_renames: str,
    ) -> "Frame":
        """``->rename(~old, ~new)`` -- rename one or more columns.

        Three additive call shapes, all building the same graph:

        * positional ``rename("old", "new")`` -- the Pure-native one-pair form.
        * a mapping ``rename({"old": "new", ...})`` -- pylegend's
          ``rename(column_renames)``.
        * keyword ``rename(old="new", ...)`` -- pylegend kwargs.

        Pure's ``rename`` takes ONE ``(~old, ~new)`` pair per call, so several
        pairs are emitted as a *chain* of ``->rename(~old, ~new)`` verbs (one per
        entry, in order; the chained form compiles -- verified via the Legend
        bridge). Names are built into scalar :func:`col` specs.
        """
        if isinstance(old, dict):
            if new is not None:
                raise ValueError("rename(mapping) takes no positional `new`")
            pairs = list(old.items())
        elif old is not None:
            if new is None:
                raise ValueError("rename(old, new) requires both names")
            pairs = [(old, new)]
        else:
            pairs = []
        pairs += list(column_renames.items())
        if not pairs:
            raise ValueError("rename requires at least one (old, new) pair")
        frame = self
        for old_name, new_name in pairs:
            frame = frame._verb("rename", col(old_name), col(new_name))
        return frame

    # -- pivot ---------------------------------------------------------
    def pivot(
        self,
        on: str | list[str],
        aggregation: tuple[str, Callable[[Expr], object], Callable[[Expr], object]],
    ) -> "Frame":
        """``->pivot(~[on], ~name:{r | <map>}:{c | <reduce>})`` -- pivot to columns.

        ``on`` is one pivot column name or a list (always a :func:`cols`
        ``ColSpecArray`` -- the engine's ``pivot`` overload needs it), and
        ``aggregation`` a ``("name", map_lambda, reduce_lambda)`` triple (the same
        shape as :meth:`group_by`'s aggregations) built into an :func:`agg`.
        """
        on_names = [on] if isinstance(on, str) else list(on)
        name, map_fn, reduce_fn = aggregation
        agg_spec = agg(name, lam(_ROW, map_fn), lam(["c"], reduce_fn))
        return self._verb("pivot", cols(*on_names), agg_spec)

    # -- window / OLAP -------------------------------------------------
    @staticmethod
    def window(
        partition_by: object = None,
        order_by: object = None,
        frame: object = None,
    ) -> m3.SimpleFunctionExpression:
        """A pylegend-style ``over(...)`` window spec for :meth:`window_extend`.

        Thin wrapper over the :func:`window` builder so the two-step pylegend OLAP
        form reads on the ``Frame``::

            f.window_extend(
                f.window(partition_by="cust", order_by="id", frame=rows(unbounded(), 0)),
                ("rn", lambda p, w, r: p.row_number(r)),
            )

        ``partition_by`` is a column name / list of names / ready-made
        :func:`col` / :func:`cols` spec; ``order_by`` one or a list of
        :func:`asc` / :func:`desc` ``SortInfo``s or bare names (a bare name =
        ascending); ``frame`` an optional :func:`rows` / :func:`range_` frame.
        Returns the SAME node :func:`over` does, so a direct ``over(...)`` is
        still usable in :meth:`window_extend`.
        """
        return window(partition_by, order_by, frame)

    def window_extend(
        self,
        window: m3.SimpleFunctionExpression,
        column: tuple[str, Callable[[Expr, Expr, Expr], object]]
        | tuple[str, Callable[[Expr, Expr, Expr], object], Callable[[Expr], object]],
    ) -> "Frame":
        """``->extend(over(...), ~name:{p, w, r | <expr>})`` -- a windowed OLAP column.

        ``window`` is an :func:`over` window spec (built with :func:`over` /
        :func:`rows` / :func:`range_` / :func:`unbounded`). ``column`` is either a
        ``("name", lambda p, w, r: <expr>)`` pair -- a :func:`fcol` whose lambda is
        the canonical 3-param window lambda (partition / window / row proxies, wired
        via :func:`lam`) -- or a ``("name", map_lambda, reduce_lambda)`` triple for
        the aggregating window column (an :func:`agg` with a 3-param map lambda and a
        one-collection reduce lambda).
        """
        if len(column) == 2:
            name, fn = column
            spec = fcol(name, lam(_WINDOW, fn))
        else:
            name, map_fn, reduce_fn = column
            spec = agg(name, lam(_WINDOW, map_fn), lam(["y"], reduce_fn))
        return self._verb("extend", window, spec)

    # -- output --------------------------------------------------------
    def to_m3(self) -> m3.ValueSpecification | m3.InstanceValue:
        """The underlying ``m3`` relation node (the accrued verb graph)."""
        return self._node

    def to_pure(self) -> str:
        """The emitted Pure relation grammar (via :func:`_expression`)."""
        return _expression(self._node)

    # -- internals -----------------------------------------------------
    def _verb(self, name: str, *args: object) -> "Frame":
        """Apply one relation verb: a NEW ``Frame`` wrapping ``call(name, node, *args)``.

        Never mutates ``self`` -- the receiver's node is passed as the verb's
        receiver and a fresh ``Frame`` is returned (immutability).
        """
        return Frame(call(name, self._node, *(coerce(a) for a in args)))

    def __repr__(self) -> str:
        return f"Frame({self.to_pure()!r})"


__all__ = ["Frame"]
