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
from .schema import Column, Schema, SchemaError

# A column lambda receives one row proxy; a join condition two; a windowed-extend
# column the canonical three. Named here so each method reads at a glance.
_ROW = ["r"]
_JOIN = ["l", "r"]
_WINDOW = ["p", "w", "r"]


# --- TDS value serialization (for Frame.from_rows) -----------------------------
#
# The `#TDS{header\nrow1\nrow2}#` token's body is RAW TEXT -- a comma-separated
# header line and one row per `\n`. The grammar parser ingests the whole token
# verbatim; the compiler resolves it as a `relation::TDS` value; downstream value
# typing happens at the execution layer (currently unreachable on this engine
# build, see TODO.md). Compile-stage validation is therefore permissive (any
# bytes parse + compile), so we PICK serializations canonical to Pure -- the same
# spellings :func:`pure_python.compile.expressions.lit` would emit for the
# corresponding scalar literal (or its inner text) -- so a typed `from_rows`
# round-trips intent and so downstream tooling reading the TDS body can rely on
# a stable form. Each choice below was probed via the Legend bridge and
# accepted; see the report for the full table.
import datetime as _datetime
import decimal as _decimal

_TDS_DELIM_CHARS = (",", "\n", "#")
"""Characters that would corrupt a `#TDS{...}#` literal -- `,` separates fields,
`\n` separates rows, and `#` would terminate the `DSL_TEXT` token early."""


def _serialize_tds_value(value: object, primitive: m3.PrimitiveType) -> str:
    """Render ``value`` as the inner text for a ``#TDS{...}#`` row cell.

    ``primitive`` is the column's declared Pure primitive (from the
    :class:`Schema`); the value is rendered in that primitive's canonical Pure
    inner-text form:

    * ``String``  -> raw text (rejected if it contains ``,`` / ``\n`` / ``#`` --
      no quoting layer exists in the TDS DSL_TEXT token).
    * ``Integer`` -> ``str(int(value))``.
    * ``Float`` / ``Decimal`` -> ``str(float(value))`` / ``str(value)``.
    * ``Boolean`` -> ``true`` / ``false`` (Pure-lowercase; the Pure-canonical
      spelling, *not* Python's ``True`` / ``False``).
    * ``StrictDate`` -> ``YYYY-MM-DD`` (``date.isoformat()``).
    * ``DateTime``   -> ``YYYY-MM-DDTHH:MM:SS`` (``datetime.isoformat()``).
    * ``StrictTime`` -> ``HH:MM:SS`` (``time.isoformat()``).
    * ``Byte`` -> ``str(int(value))`` (a single byte 0..255).

    ``None`` raises -- the engine's inline TDS literal has no null sentinel and
    the user should drop the column or omit the row instead of guessing.
    """
    if value is None:
        raise SchemaError(
            "TDS row cells cannot be None (no null sentinel in the inline "
            "#TDS{...} literal)"
        )
    if primitive is m3.String:
        text = str(value)
        for bad in _TDS_DELIM_CHARS:
            if bad in text:
                raise SchemaError(
                    f"TDS String cell {text!r} contains the delimiter {bad!r}; "
                    f"the #TDS{{...}}# literal cannot escape it"
                )
        return text
    if primitive is m3.Integer or primitive is m3.Byte:
        return str(int(value))
    if primitive is m3.Float:
        return str(float(value))
    if primitive is m3.Decimal:
        return str(_decimal.Decimal(value) if not isinstance(value, _decimal.Decimal) else value)
    if primitive is m3.Boolean:
        return "true" if bool(value) else "false"
    if primitive is m3.StrictDate:
        if isinstance(value, _datetime.datetime):
            value = value.date()
        if not isinstance(value, _datetime.date):
            raise SchemaError(f"StrictDate cell expects a datetime.date; got {value!r}")
        return value.isoformat()
    if primitive is m3.DateTime:
        if not isinstance(value, _datetime.datetime):
            raise SchemaError(f"DateTime cell expects a datetime.datetime; got {value!r}")
        # Pure's DateTime literal uses `YYYY-MM-DDTHH:MM:SS` (ISO 8601 with `T`).
        return value.replace(microsecond=0).isoformat(sep="T")
    if primitive is m3.StrictTime:
        if not isinstance(value, _datetime.time):
            raise SchemaError(f"StrictTime cell expects a datetime.time; got {value!r}")
        return value.replace(microsecond=0).isoformat()
    # Fallback for any other m3 primitive (Number / Date / LatestDate -- not
    # produced by Column factories): plain string conversion.
    return str(value)


def _coerce_other_schema(other: object) -> Schema | None:
    """The schema of a join/concatenate right-hand side, when known.

    A :class:`Frame` carries its own; a raw node has none.
    """
    if isinstance(other, Frame):
        return other._schema
    return None


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

    __slots__ = ("_node", "_schema")
    # `Frame` brands a value graph; comparing two frames structurally is done via
    # `to_m3()` + the test `canon`, not `==`, so leave equality/hashing as identity.

    def __init__(
        self,
        node: m3.ValueSpecification | m3.InstanceValue,
        schema: Schema | None = None,
    ):
        self._node = node
        self._schema = schema

    # -- entry points --------------------------------------------------
    @classmethod
    def from_tds(cls, text: str, schema: Schema | None = None) -> "Frame":
        """A ``Frame`` over an inline ``#TDS{...}#`` literal (the primary source).

        ``text`` is the CSV body (``"id,amt\\n1,10"``) or a full ``#TDS{...}#``
        token; delegates to :func:`tds`. The optional ``schema`` is for offline
        validation only -- the verbatim ``text`` remains the source of truth for
        emit (so an existing ``from_tds(...)`` call without a schema produces the
        BYTE-IDENTICAL ``#TDS{...}#`` token it did before).
        """
        return cls(tds(text), schema=schema)

    @classmethod
    def from_db(
        cls, database: str, table: str, schema: Schema | None = None
    ) -> "Frame":
        """A ``Frame`` over a ``#>{database.table}#`` database-table source.

        Delegates to :func:`db_table`. This source PARSES via the real engine, but
        only COMPILES once the named ``database`` store is defined in the model
        (with no store it fails compile with ``The store '<database>' can't be
        found.``); this sugar layer does not fabricate a database, so a ``from_db``
        chain is validated to parse (see ``tests/test_legend_bridge.py``). The
        optional ``schema`` is for offline column-name validation -- the emitted
        ``#>{...}#`` token is unchanged.
        """
        return cls(db_table(database, table), schema=schema)

    @classmethod
    def from_rows(
        cls,
        schema: Schema,
        rows: list,
    ) -> "Frame":
        """A ``Frame`` over a ``#TDS{header\\nrow\\n...}#`` literal built from typed rows.

        ``schema`` is a :class:`Schema`; ``rows`` is a list of either tuples
        (positional, in schema order) or dicts (keyed by column name -- a missing
        key is rejected). Each cell is serialized via
        :func:`_serialize_tds_value` in its column's declared Pure primitive's
        canonical inner-text form; the header is ``,``-joined column names. The
        resulting TDS literal token is the source of truth for emit, and the
        schema rides along for downstream verb validation.
        """
        if not isinstance(schema, Schema):
            raise TypeError(f"from_rows requires a Schema; got {schema!r}")
        names = schema.names()
        if any(bad in n for n in names for bad in _TDS_DELIM_CHARS):
            raise SchemaError(
                f"TDS header names cannot contain the delimiters {_TDS_DELIM_CHARS}; "
                f"got {list(names)}"
            )
        header = ",".join(names)
        body_rows: list[str] = []
        for i, row in enumerate(rows):
            if isinstance(row, dict):
                missing = [n for n in names if n not in row]
                if missing:
                    raise SchemaError(
                        f"row {i} is missing columns {missing} "
                        f"(available keys={sorted(row)})"
                    )
                ordered = [row[n] for n in names]
            else:
                ordered = list(row)
                if len(ordered) != len(names):
                    raise SchemaError(
                        f"row {i} has {len(ordered)} values but schema has "
                        f"{len(names)} columns ({list(names)})"
                    )
            cells = [
                _serialize_tds_value(v, c.type)
                for v, c in zip(ordered, schema.columns)
            ]
            body_rows.append(",".join(cells))
        text = header + ("\n" + "\n".join(body_rows) if body_rows else "")
        return cls(tds(text), schema=schema)

    # -- schema accessors ----------------------------------------------
    @property
    def schema(self) -> Schema | None:
        """The :class:`Schema` if known (``None`` when the upstream is unknown)."""
        return self._schema

    @property
    def columns(self) -> tuple[Column, ...] | None:
        """``self.schema.columns`` when a schema is attached, else ``None``."""
        return self._schema.columns if self._schema is not None else None

    # -- row-filtering / projection ------------------------------------
    def filter(self, predicate: Callable[[Expr], object]) -> "Frame":
        """``->filter({r | <predicate>})`` -- keep rows where the predicate holds.

        ``predicate`` is a one-row lambda (``lambda r: r.amt > 5``); wired via
        :func:`lam`.
        """
        return self._verb("filter", lam(_ROW, predicate))

    def select(self, *names: str) -> "Frame":
        """``->select(~a)`` / ``->select(~[a, b])`` -- project the named columns.

        One name builds a scalar :func:`col`, several a :func:`cols` array. If a
        schema is attached, each name is validated and the output schema is the
        selected columns in that order.
        """
        if not names:
            raise ValueError("select requires at least one column name")
        self._validate_columns("select", list(names))
        spec = col(names[0]) if len(names) == 1 else cols(*names)
        out = (
            Schema.from_columns(*(self._schema.of_name(n) for n in names))
            if self._schema is not None
            else None
        )
        return self._verb_with_schema("select", out, spec)

    def extend(
        self,
        *columns: tuple[str, Callable[[Expr], object]],
        out_schema: Schema | None = None,
    ) -> "Frame":
        """``->extend(~name:{r | <expr>})`` -- add one or many derived columns.

        Each ``column`` is a ``("name", lambda r: <expr>)`` pair; one pair builds a
        :func:`fcol`, several a :func:`fcols` array. Each column lambda is wired via
        :func:`lam` with a single row proxy.

        The derived-column types cannot be inferred from the stringly-typed lambda
        bodies (Tier 1 -- the row proxy stays untyped), so if a schema is attached
        the downstream schema is the receiver's input schema plus ``out_schema``'s
        columns (if given), else ``None`` (schema propagation stops past this
        verb until a later ``out_schema=``).
        """
        if not columns:
            raise ValueError("extend requires at least one (name, lambda) column")
        specs = [fcol(name, lam(_ROW, fn)) for name, fn in columns]
        spec = specs[0] if len(specs) == 1 else fcols(*specs)
        out = self._extended_schema(out_schema)
        return self._verb_with_schema("extend", out, spec)

    # -- grouping ------------------------------------------------------
    def group_by(
        self,
        keys: str | list[str],
        *aggregations: tuple[str, Callable[[Expr], object], Callable[[Expr], object]],
        out_schema: Schema | None = None,
    ) -> "Frame":
        """``->groupBy(~[keys], ~name:{r | <map>}:{c | <reduce>})`` -- grouped aggregation.

        ``keys`` is one column name or a list of names (always emitted as a
        :func:`cols` ``ColSpecArray`` -- the engine's ``groupBy`` overload takes a
        ``ColSpecArray``). Each aggregation is a ``("name", map_lambda,
        reduce_lambda)`` triple: ``map_lambda`` is a one-row lambda producing the
        value (``lambda r: r.amt``), ``reduce_lambda`` a one-collection lambda
        producing the aggregate (``lambda c: c.sum()``); both wired via
        :func:`lam`. One triple builds an :func:`agg`, several an :func:`aggs` array.

        With a schema attached: key names are validated; aggregation result types
        cannot be inferred from the stringly-typed reduce lambdas, so the
        downstream schema is the input schema's key columns + ``out_schema``'s
        columns (if given), else ``None``.
        """
        if not aggregations:
            raise ValueError("group_by requires at least one (name, map, reduce) aggregation")
        key_names = [keys] if isinstance(keys, str) else list(keys)
        self._validate_columns("group_by", key_names)
        key_spec = cols(*key_names)
        specs = [
            agg(name, lam(_ROW, map_fn), lam(["c"], reduce_fn))
            for name, map_fn, reduce_fn in aggregations
        ]
        agg_spec = specs[0] if len(specs) == 1 else aggs(*specs)
        out: Schema | None = None
        if self._schema is not None and out_schema is not None:
            out = Schema.from_columns(
                *(self._schema.of_name(k) for k in key_names),
                *out_schema.columns,
            )
        return self._verb_with_schema("groupBy", out, key_spec, agg_spec)

    # -- joins ---------------------------------------------------------
    def join(
        self,
        other: object,
        on: Callable[[Expr, Expr], object],
        kind: object = JoinKind.INNER,
        out_schema: Schema | None = None,
    ) -> "Frame":
        """``->join(other, kind, {l, r | <on>})`` -- relational join.

        ``other`` is the right relation (a ``Frame`` / raw node / :func:`tds` /
        :func:`db_table`), ``on`` a two-row condition lambda (``lambda l, r: l.id
        == r.fid``) wired via :func:`lam`, and ``kind`` either a :class:`JoinKind`
        constant (default ``INNER``) or a pylegend string -- ``'INNER'`` /
        ``'LEFT_OUTER'`` / ``'RIGHT_OUTER'`` / ``'FULL'`` (case-insensitive;
        ``LEFT_OUTER`` -> ``JoinKind.LEFT``, ``RIGHT_OUTER`` -> ``JoinKind.RIGHT``),
        normalized via :func:`join_kind`.

        With schemas on both sides: the output schema is the left columns then
        the right columns. A name collision between the two sides raises
        :class:`SchemaError` (the engine itself rejects duplicate columns in a
        joined relation). An explicit ``out_schema`` overrides the inferred
        union; if either side has no schema and ``out_schema`` is omitted, the
        downstream schema is ``None``.
        """
        out = self._joined_schema("join", other, out_schema)
        return self._verb_with_schema(
            "join", out, _unwrap(other), join_kind(kind), lam(_JOIN, on)
        )

    def inner_join(
        self,
        other: object,
        on: Callable[[Expr, Expr], object],
        out_schema: Schema | None = None,
    ) -> "Frame":
        """:meth:`join` with ``JoinKind.INNER``."""
        return self.join(other, on, JoinKind.INNER, out_schema=out_schema)

    def left_join(
        self,
        other: object,
        on: Callable[[Expr, Expr], object],
        out_schema: Schema | None = None,
    ) -> "Frame":
        """:meth:`join` with ``JoinKind.LEFT``."""
        return self.join(other, on, JoinKind.LEFT, out_schema=out_schema)

    def right_join(
        self,
        other: object,
        on: Callable[[Expr, Expr], object],
        out_schema: Schema | None = None,
    ) -> "Frame":
        """:meth:`join` with ``JoinKind.RIGHT``."""
        return self.join(other, on, JoinKind.RIGHT, out_schema=out_schema)

    def full_join(
        self,
        other: object,
        on: Callable[[Expr, Expr], object],
        out_schema: Schema | None = None,
    ) -> "Frame":
        """:meth:`join` with ``JoinKind.FULL``."""
        return self.join(other, on, JoinKind.FULL, out_schema=out_schema)

    def as_of_join(
        self,
        other: object,
        on: Callable[[Expr, Expr], object],
        join_condition: Callable[[Expr, Expr], object] | None = None,
        out_schema: Schema | None = None,
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

        Schema propagation mirrors :meth:`join` (left columns + right columns
        when both are known, ``out_schema`` to override, ``None`` otherwise).
        """
        match = lam(_JOIN, on)
        out = self._joined_schema("as_of_join", other, out_schema)
        if join_condition is None:
            return self._verb_with_schema("asOfJoin", out, _unwrap(other), match)
        return self._verb_with_schema(
            "asOfJoin", out, _unwrap(other), match, lam(_JOIN, join_condition)
        )

    # -- ordering ------------------------------------------------------
    def sort(self, *specs: object) -> "Frame":
        """``->sort(~c->ascending())`` / ``->sort([...])`` -- order by sort specs.

        Each spec is an :func:`asc` / :func:`desc` ``SortInfo`` or a bare column
        name (defaulting to ascending). One spec emits the scalar form, several the
        bracketed :func:`array` list form (the engine's ``SortInfo[*]`` overload).
        Pass-through schema: when a schema is attached, any *bare* string sort
        spec is validated (the ``asc("x")`` / ``desc("x")`` helpers also accept a
        bare name and reach this verb the same way -- those are validated below
        when their column-name string is recoverable). The downstream schema is
        the receiver's schema unchanged.
        """
        if not specs:
            raise ValueError("sort requires at least one sort spec")
        bare_names = [s for s in specs if isinstance(s, str)]
        if bare_names:
            self._validate_columns("sort", bare_names)
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

    def drop(self, *args: int | str) -> "Frame":
        """``->drop(n)`` -- skip the first ``n`` rows.

        Two additive shapes, distinguished by argument type:

        * ``drop(n: int)`` -- the Pure-native row-drop verb (the original form);
          a pass-through schema (the receiver's schema is preserved).
        * ``drop(*names: str)`` -- a column-drop computed mechanically against
          the receiver's schema: each name is validated and the downstream
          schema is the input schema minus those columns. Emitted as a
          ``select(~[remaining])`` (Pure has no relation-level "drop columns"
          verb; the engine's idiomatic equivalent is select-of-the-rest). Calling
          this shape on a Frame without a schema raises :class:`SchemaError`
          (we have no list of columns to remove from).
        """
        if not args:
            raise ValueError("drop requires at least one argument (int row count or column names)")
        if all(isinstance(a, str) for a in args):
            names = list(args)
            if self._schema is None:
                raise SchemaError(
                    f"drop(*names) requires a schema on the Frame to compute the "
                    f"remaining columns (verb='drop', requested_drops={names})"
                )
            self._validate_columns("drop", names)
            remaining = [c for c in self._schema.columns if c.name not in set(names)]
            if not remaining:
                raise SchemaError(
                    f"drop would remove every column (verb='drop', schema="
                    f"{list(self._schema.names())}, drops={names})"
                )
            remaining_names = [c.name for c in remaining]
            spec = col(remaining_names[0]) if len(remaining_names) == 1 else cols(*remaining_names)
            out = Schema.from_columns(*remaining)
            return self._verb_with_schema("select", out, spec)
        if len(args) == 1 and isinstance(args[0], int):
            return self._verb("drop", args[0])
        raise TypeError(
            "drop accepts either one int row count or one-or-more string column names; "
            f"got {args!r}"
        )

    def slice(self, start: int, stop: int) -> "Frame":
        """``->slice(start, stop)`` -- the ``[start, stop)`` row window."""
        return self._verb("slice", start, stop)

    def distinct(self) -> "Frame":
        """``->distinct()`` -- drop duplicate rows."""
        return self._verb("distinct")

    def concatenate(self, other: object) -> "Frame":
        """``->concatenate(other)`` -- union/append another relation's rows.

        ``other`` may be a ``Frame`` / raw node / :func:`tds` / :func:`db_table`.
        If BOTH receiver and ``other`` have schemas, they must match column-for-
        column (name AND type, in order) -- a mismatch raises :class:`SchemaError`.
        If either side lacks a schema, the downstream schema is ``None``.
        """
        out: Schema | None = None
        if self._schema is not None:
            other_schema = _coerce_other_schema(other)
            if other_schema is not None:
                if other_schema.columns != self._schema.columns:
                    raise SchemaError(
                        "concatenate schemas must match (name and type, in order); "
                        f"left={list(self._schema.names())}, "
                        f"right={list(other_schema.names())}"
                    )
                out = self._schema
            # If other has no schema, drop validation downstream.
        return self._verb_with_schema("concatenate", out, _unwrap(other))

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
            # Per-step validation (the chained rename verbs each see the prior
            # step's renamed schema), so the message names the verb correctly.
            frame._validate_columns("rename", [old_name])
            out: Schema | None = None
            if frame._schema is not None:
                out = Schema.from_columns(
                    *(
                        Column(new_name, c.type) if c.name == old_name else c
                        for c in frame._schema.columns
                    )
                )
            frame = frame._verb_with_schema(
                "rename", out, col(old_name), col(new_name)
            )
        return frame

    # -- pivot ---------------------------------------------------------
    def pivot(
        self,
        on: str | list[str],
        aggregation: tuple[str, Callable[[Expr], object], Callable[[Expr], object]],
        out_schema: Schema | None = None,
    ) -> "Frame":
        """``->pivot(~[on], ~name:{r | <map>}:{c | <reduce>})`` -- pivot to columns.

        ``on`` is one pivot column name or a list (always a :func:`cols`
        ``ColSpecArray`` -- the engine's ``pivot`` overload needs it), and
        ``aggregation`` a ``("name", map_lambda, reduce_lambda)`` triple (the same
        shape as :meth:`group_by`'s aggregations) built into an :func:`agg`.

        With a schema attached: pivot column names are validated. The result
        schema fans out by pivot-value (engine-specific, unknown to this layer),
        so the downstream schema is ``out_schema`` if given, else ``None``.
        """
        on_names = [on] if isinstance(on, str) else list(on)
        self._validate_columns("pivot", on_names)
        name, map_fn, reduce_fn = aggregation
        agg_spec = agg(name, lam(_ROW, map_fn), lam(["c"], reduce_fn))
        out = out_schema if self._schema is not None else None
        return self._verb_with_schema("pivot", out, cols(*on_names), agg_spec)

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
        out_schema: Schema | None = None,
    ) -> "Frame":
        """``->extend(over(...), ~name:{p, w, r | <expr>})`` -- a windowed OLAP column.

        ``window`` is an :func:`over` window spec (built with :func:`over` /
        :func:`rows` / :func:`range_` / :func:`unbounded`). ``column`` is either a
        ``("name", lambda p, w, r: <expr>)`` pair -- a :func:`fcol` whose lambda is
        the canonical 3-param window lambda (partition / window / row proxies, wired
        via :func:`lam`) -- or a ``("name", map_lambda, reduce_lambda)`` triple for
        the aggregating window column (an :func:`agg` with a 3-param map lambda and a
        one-collection reduce lambda).

        Schema propagation mirrors :meth:`extend`: the downstream schema is the
        input schema plus ``out_schema`` (if given), else ``None``.
        """
        if len(column) == 2:
            name, fn = column
            spec = fcol(name, lam(_WINDOW, fn))
        else:
            name, map_fn, reduce_fn = column
            spec = agg(name, lam(_WINDOW, map_fn), lam(["y"], reduce_fn))
        out = self._extended_schema(out_schema)
        return self._verb_with_schema("extend", out, window, spec)

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
        receiver and a fresh ``Frame`` is returned (immutability). The receiver's
        schema (if any) is preserved -- pass-through propagation; the per-verb
        wrappers above override via :meth:`_verb_with_schema` when the output
        schema differs.
        """
        return Frame(
            call(name, self._node, *(coerce(a) for a in args)),
            schema=self._schema,
        )

    def _verb_with_schema(
        self, name: str, out_schema: Schema | None, *args: object
    ) -> "Frame":
        """As :meth:`_verb`, but stamps the resulting ``Frame`` with ``out_schema``.

        Used by verbs whose output schema is computed (``select`` / ``drop`` /
        ``rename``) or supplied (``extend(out_schema=...)`` etc.); a pass-through
        verb (``filter`` / ``sort`` / ``limit`` / ``slice`` / ``distinct``) uses
        plain :meth:`_verb` and inherits the receiver's schema unchanged.
        """
        return Frame(
            call(name, self._node, *(coerce(a) for a in args)),
            schema=out_schema,
        )

    def _extended_schema(self, out_schema: Schema | None) -> Schema | None:
        """Combine the receiver's schema with ``out_schema`` for an extend-style verb.

        Returns ``None`` when the receiver has no schema OR ``out_schema`` is
        omitted (schema propagation stops -- the verb adds columns of unknown
        type from a stringly-typed lambda). With both: the receiver's columns
        followed by ``out_schema``'s columns. Duplicates across the two are a
        :class:`SchemaError`.
        """
        if self._schema is None or out_schema is None:
            return None
        existing = set(self._schema.names())
        clashing = [c.name for c in out_schema.columns if c.name in existing]
        if clashing:
            raise SchemaError(
                f"out_schema column(s) {clashing} already in schema "
                f"(verb='extend', existing={list(self._schema.names())})"
            )
        return Schema.from_columns(*self._schema.columns, *out_schema.columns)

    def _joined_schema(
        self, verb: str, other: object, out_schema: Schema | None
    ) -> Schema | None:
        """Output schema for a join / as-of-join.

        With ``out_schema`` given and a left schema, use it directly. Otherwise
        with schemas on both sides, the column union (left then right); a name
        collision raises :class:`SchemaError` naming both sides. With either
        side lacking a schema (and no ``out_schema``), returns ``None``.
        """
        if self._schema is None:
            return None
        if out_schema is not None:
            return out_schema
        other_schema = _coerce_other_schema(other)
        if other_schema is None:
            return None
        left_names = set(self._schema.names())
        collisions = [c.name for c in other_schema.columns if c.name in left_names]
        if collisions:
            raise SchemaError(
                f"join column name collision: {collisions} appear in both sides "
                f"(verb={verb!r}, left={list(self._schema.names())}, "
                f"right={list(other_schema.names())}). Pass out_schema= to override."
            )
        return Schema.from_columns(*self._schema.columns, *other_schema.columns)

    def _validate_columns(self, verb: str, names: list[str]) -> None:
        """Raise :class:`SchemaError` if any ``names`` are not in ``self._schema``.

        No-op when the receiver has no schema -- validation is opt-in (a Frame
        built without a schema preserves the pre-existing behavior byte-for-byte).
        """
        if self._schema is None:
            return
        for n in names:
            if not self._schema.has(n):
                raise SchemaError(
                    f"column {n!r} not in schema "
                    f"(verb={verb!r}, available={list(self._schema.names())})"
                )

    def __repr__(self) -> str:
        return f"Frame({self.to_pure()!r})"


__all__ = ["Frame", "Schema", "Column", "SchemaError"]
