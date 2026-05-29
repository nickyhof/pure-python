"""Build Pure M3 expression (``ValueSpecification``) graphs from Python.

Two layers sit here:

* **Explicit builders** -- :func:`lit`, :func:`var`, :func:`call` (alias
  :func:`func`) and :func:`prop` -- each return a raw ``m3`` node. They are the
  primitive constructors for the expression tree; everything else delegates to
  them so the resulting graphs are identical regardless of how they were built.
* **A PyLegend-style DSL** on top: :class:`Expr` wraps a single node, :func:`c`
  wraps a literal, Python operators (``+ - * / == != < <= > >=``) build the
  matching core-function ``call``, and attribute access doubles as property
  access (``this.first``) and fluent function application (``c(4).exp()``)::

      c(4) / 2                      # divide(4, 2)  -> emits (4 / 2)
      c(3) - c(2)                   # minus(3, 2)   -> emits (3 - 2)
      c(6) == 6                     # eq(6, 6)      -> emits (6 == 6)
      c(1.0).exp().log()            # log(exp(1.0)) -> emits 1.0->exp()->log()
      c("hello world").substring(0, 4)

The operator builders all produce a core-function ``call`` node; the binary core
operators emit as parenthesized *infix* and other functions as arrow form (see
:mod:`pure_python.compile.m3_to_pure`). These are the values authored into a
derived-property body via the :class:`pure_python.compile.annotations.Body`
marker, then emitted as real Pure and re-parsed by
:mod:`pure_python.compile.pure_expr`.

A small **relation / TDS layer** builds on the same primitives: :func:`lam`
builds an n-ary ``{p, w, r | body}`` ``LambdaFunction``, :func:`tds` a verbatim
``#TDS{...}#`` relation literal, and :func:`col` / :func:`cols` simple ``~col`` /
``~[a, b]`` column specs (all raw nodes, like the other builders). Wrapping the
source in :class:`Expr` gives the fluent ``->filter`` / ``->select`` arrow
application, expressing relation queries::

    Expr(tds("id,grp\\n1,1\\n2,0")).filter(lam(["r"], lambda r: r.grp > 0))
    # call("filter", <tds>, <lambda>)  -> #TDS{id,grp\\n1,1\\n2,0}#->filter({r | ($r.grp > 0)})
    call("select", tds("id,grp"), cols("id", "grp"))  # -> ...->select(~[id, grp])

:func:`fcol` / :func:`fcols` carry a per-column lambda (``~name:{lambda}``) for the
``extend`` verb; :func:`agg` / :func:`aggs` carry a per-row ``map`` plus a collection
``reduce`` lambda (``~name:{map}:{agg}``) for the ``groupBy`` verb::

    Expr(tds("id,val\\n1,10\\n1,20")).groupBy(
        cols("id"), agg("total", lam(["r"], lambda r: r.val), lam(["c"], lambda c: c.sum()))
    )
    # -> #TDS{id,val\\n1,10\\n1,20}#->groupBy(~[id], ~total:{r | $r.val}:{c | $c->sum()})

:func:`array` builds a collection literal ``[a, b, c]`` (a multi-value
``InstanceValue``), and :func:`asc` / :func:`desc` the ``~col->ascending()`` /
``~col->descending()`` sort directions; the ``sort`` verb takes one direction or
an :func:`array` of them, and the ``pivot`` verb takes a pivot column spec plus
an :func:`agg`::

    Expr(tds("id,grp\\n1,1\\n2,0")).sort(array(asc(col("id")), desc(col("grp"))))
    # -> #TDS{id,grp\\n1,1\\n2,0}#->sort([~id->ascending(), ~grp->descending()])
    Expr(tds("id,prod,amt\\n1,a,10")).pivot(
        cols("prod"), agg("amount", lam(["r"], lambda r: r.amt), lam(["c"], lambda c: c.sum()))
    )
    # -> #TDS{id,prod,amt\\n1,a,10}#->pivot(~[prod], ~amount:{r | $r.amt}:{c | $c->sum()})
"""

from __future__ import annotations

import datetime
import decimal
from typing import Any, Callable

from pure_python import m3

from .python_to_m3 import _PRIMITIVE

# A ``FunctionExpression`` requires a ``func`` and an ``importGroup`` that we do
# not model at the expression level (the function/property name carries all the
# meaning). Share one sentinel of each so equality and identity stay cheap and
# emitters/comparers can ignore them.
_FUNC_SENTINEL = m3.Function()
_IMPORT_GROUP_SENTINEL = m3.ImportGroup()

# A shared marker ``GenericType`` whose ``rawType`` is a ``RelationType``. It
# discriminates a ``#TDS{...}#`` relation literal from an ordinary string
# ``InstanceValue`` so the emitter renders the text verbatim (unquoted) instead
# of as a quoted string. ``pure_expr`` reuses the same marker on the way back so
# the two sides agree under ``canon``.
_TDS_GENERIC_TYPE = m3.GenericType(rawType=m3.RelationType())

# A shared marker ``GenericType`` whose ``rawType`` is an ``Enumeration``. It
# discriminates an enum-value reference (``JoinKind.INNER``) from an ordinary
# string ``InstanceValue`` (rawType ``String``) and from a ``#TDS{...}#`` literal
# (rawType ``RelationType``) so the emitter renders the stored qualified-name
# text verbatim (``JoinKind.INNER``) instead of as a quoted string. ``pure_expr``
# reuses the same marker when it reconstructs the reference so the two sides agree
# under ``canon``. (The metamodel has no ``JoinKind`` enum, so this reuses the
# existing ``tds`` pattern -- a verbatim token on an ``InstanceValue`` -- rather
# than adding any m3 type.)
_ENUM_REF_GENERIC_TYPE = m3.GenericType(rawType=m3.Enumeration())


def _primitive_for(value: object) -> m3.PrimitiveType:
    """Map a Python literal to its Pure primitive, reusing ``python_to_m3``."""
    primitive = _PRIMITIVE.get(type(value))
    if primitive is None:
        raise TypeError(f"cannot map literal {value!r} to a Pure primitive type")
    return primitive


def lit(value: object) -> m3.InstanceValue:
    """A primitive literal, e.g. ``lit(4)`` / ``lit('x')`` / ``lit(True)``."""
    return m3.InstanceValue(
        values=[value],
        genericType=m3.GenericType(rawType=_primitive_for(value)),
        multiplicity=m3.PureOne,
    )


def var(name: str) -> m3.VariableExpression:
    """A variable reference, e.g. ``var('this')`` -> ``$this``."""
    return m3.VariableExpression(
        name=name,
        genericType=m3.GenericType(),
        multiplicity=m3.PureOne,
    )


# Relation-layer argument nodes that are *not* ``ValueSpecification`` subclasses
# (``LambdaFunction`` is a ``FunctionDefinition``; ``ColSpec`` / ``ColSpecArray``
# / ``FuncColSpec`` / ``FuncColSpecArray`` derive from ``Any``) but are still
# valid function arguments -- a verb such as ``filter`` / ``select`` / ``extend``
# takes them as a ``parametersValues`` entry.
_PASSTHROUGH_NODES = (
    m3.LambdaFunction,
    m3.ColSpec,
    m3.ColSpecArray,
    m3.FuncColSpec,
    m3.FuncColSpecArray,
    m3.AggColSpec,
    m3.AggColSpecArray,
)


def coerce(value: object) -> m3.ValueSpecification:
    """Turn an ``Expr`` into its node, an ``m3`` node through, scalars into ``lit``."""
    if isinstance(value, Expr):
        return value.node
    if isinstance(value, (m3.ValueSpecification, *_PASSTHROUGH_NODES)):
        return value
    return lit(value)


def call(name: str, *args: object) -> m3.SimpleFunctionExpression:
    """A function application ``arg0->name(arg1, ...)`` (alias :func:`func`).

    ``name`` is the Pure core function simple name (``plus``, ``minus``,
    ``times``, ``divide``, ``eq``, ``lessThan``, ``exp``, ``substring`` ...).
    """
    return m3.SimpleFunctionExpression(
        func=_FUNC_SENTINEL,
        importGroup=_IMPORT_GROUP_SENTINEL,
        functionName=name,
        parametersValues=[coerce(a) for a in args],
        genericType=m3.GenericType(),
        multiplicity=m3.PureOne,
    )


func = call


def prop(receiver: object, name: str) -> m3.SimpleFunctionExpression:
    """Property access ``receiver.name`` (``propertyName`` set, no ``functionName``)."""
    return m3.SimpleFunctionExpression(
        func=_FUNC_SENTINEL,
        importGroup=_IMPORT_GROUP_SENTINEL,
        propertyName=m3.InstanceValue(
            values=[name],
            genericType=m3.GenericType(rawType=m3.String),
            multiplicity=m3.PureOne,
        ),
        parametersValues=[coerce(receiver)],
        genericType=m3.GenericType(),
        multiplicity=m3.PureOne,
    )


def not_(expr: object) -> "Expr":
    """Boolean negation ``expr->not()``."""
    return Expr(call("not", coerce(expr)))


class Expr:
    """A thin DSL wrapper around a single ``m3`` expression node.

    Operators and attribute access build new ``Expr`` instances by delegating to
    the explicit builders. ``__eq__`` is overloaded for the DSL, so ``Expr`` is
    unhashable and must never be compared for equality via ``==`` in code/tests;
    use a structural projection instead.
    """

    __slots__ = ("node",)
    __hash__ = None  # __eq__ builds expressions, so Expr is not hashable

    def __init__(self, node: m3.ValueSpecification):
        self.node = node

    def __bool__(self) -> bool:
        # `__eq__`/`__lt__`/... return an `Expr`, so chained comparisons
        # (`a < b < c`) and `if expr:` would silently misbehave. Refuse instead.
        raise TypeError(
            "Expr has no truth value; chained comparisons and boolean use are "
            "unsupported -- build calls explicitly"
        )

    # -- arithmetic ----------------------------------------------------
    def __add__(self, other: object) -> "Expr":
        return Expr(call("plus", self.node, coerce(other)))

    def __radd__(self, other: object) -> "Expr":
        return Expr(call("plus", coerce(other), self.node))

    def __sub__(self, other: object) -> "Expr":
        return Expr(call("minus", self.node, coerce(other)))

    def __rsub__(self, other: object) -> "Expr":
        return Expr(call("minus", coerce(other), self.node))

    def __mul__(self, other: object) -> "Expr":
        return Expr(call("times", self.node, coerce(other)))

    def __rmul__(self, other: object) -> "Expr":
        return Expr(call("times", coerce(other), self.node))

    def __truediv__(self, other: object) -> "Expr":
        return Expr(call("divide", self.node, coerce(other)))

    def __rtruediv__(self, other: object) -> "Expr":
        return Expr(call("divide", coerce(other), self.node))

    # -- comparison ----------------------------------------------------
    def __eq__(self, other: object) -> "Expr":  # type: ignore[override]
        return Expr(call("eq", self.node, coerce(other)))

    def __ne__(self, other: object) -> "Expr":  # type: ignore[override]
        # A dedicated `notEqual` so `!=` emits and round-trips as infix.
        return Expr(call("notEqual", self.node, coerce(other)))

    def __lt__(self, other: object) -> "Expr":
        return Expr(call("lessThan", self.node, coerce(other)))

    def __le__(self, other: object) -> "Expr":
        return Expr(call("lessThanEqual", self.node, coerce(other)))

    def __gt__(self, other: object) -> "Expr":
        return Expr(call("greaterThan", self.node, coerce(other)))

    def __ge__(self, other: object) -> "Expr":
        return Expr(call("greaterThanEqual", self.node, coerce(other)))

    def __invert__(self) -> "Expr":
        return not_(self)

    # -- explicit escape hatches --------------------------------------
    def prop(self, name: str) -> "Expr":
        """Unambiguous property access: ``expr.prop('first')``."""
        return Expr(prop(self.node, name))

    def call(self, name: str, *args: object) -> "Expr":
        """Unambiguous function application: ``expr.call('exp')``."""
        return Expr(call(name, self.node, *args))

    # -- dual property/function attribute access ----------------------
    def __getattr__(self, name: str) -> "_Accessor":
        if name.startswith("__") or name in Expr.__slots__:
            raise AttributeError(name)
        return _Accessor(self.node, name)

    def __repr__(self) -> str:
        return f"Expr({self.node!r})"


class _Accessor(Expr):
    """The result of ``expr.<name>``: both a property-access ``Expr`` and callable.

    As an ``Expr`` it represents ``receiver.name`` (so ``this.first + 'x'``
    works); called, it builds ``receiver->name(args...)`` (so ``c(4).exp()`` and
    ``x.substring(0, 43)`` work).
    """

    __slots__ = ("_receiver", "_name")

    def __init__(self, receiver: m3.ValueSpecification, name: str):
        object.__setattr__(self, "_receiver", receiver)
        object.__setattr__(self, "_name", name)
        super().__init__(prop(receiver, name))

    def __call__(self, *args: object) -> Expr:
        return Expr(call(self._name, self._receiver, *args))


def c(value: object) -> Expr:
    """Wrap a Python literal as an ``Expr``: ``c(4)``, ``c(1.0)``, ``c('x')``."""
    return Expr(lit(value))


# --- relation / TDS layer ---------------------------------------------------

def lam(param_names: list[str], build: Callable[..., object]) -> m3.LambdaFunction:
    """Build an n-ary ``LambdaFunction`` ``{p, w, r | <body>}``.

    A ``VariableExpression`` is created per name and passed (wrapped as an
    ``Expr``) to ``build``; the returned ``Expr``/node becomes the single body
    statement. Param names are explicit (no ``inspect.signature`` magic).
    """
    params = [Expr(var(name)) for name in param_names]
    body = coerce(build(*params))
    # The parameter NAMES round-trip via ``openVariables``: a pragmatic
    # foundation carrier (a native ``FunctionType`` would also require a
    # returnType / returnMultiplicity we do not model at this level).
    return m3.LambdaFunction(openVariables=list(param_names), expressionSequence=[body])


def tds(text: str) -> m3.InstanceValue:
    """A ``#TDS{...}#`` relation literal carrying its verbatim text.

    Accepts either the inner CSV (``"id,grp\\n1,1\\n2,0"``) or a full
    ``#TDS{...}#`` token; both are normalized to the wrapped token and stored on
    an ``InstanceValue`` discriminated by :data:`_TDS_GENERIC_TYPE` so the
    emitter renders it verbatim. The CSV is never parsed.
    """
    inner = text[len("#TDS{") : -len("}#")] if text.startswith("#TDS{") else text
    if "#" in inner:
        # The Pure `DSL_TEXT` token is `'#' .*? '#'` (non-greedy), so an interior
        # `#` ends the token early and truncates it on re-parse. This grammar
        # cannot round-trip such content, so reject it rather than corrupt it.
        raise ValueError(
            "a #TDS{...} literal cannot contain '#' in its content "
            "(the Pure DSL_TEXT token is '#'-delimited and would truncate)"
        )
    token = text if text.startswith("#TDS{") else f"#TDS{{{text}}}#"
    return m3.InstanceValue(
        values=[token],
        genericType=_TDS_GENERIC_TYPE,
        multiplicity=m3.PureOne,
    )


def enum_ref(enumeration: str, value: str) -> m3.InstanceValue:
    """An enum-value reference ``Enumeration.VALUE`` (e.g. ``JoinKind.INNER``).

    The second relation of a ``join`` is just a value (another ``#TDS{}#`` or a
    ``$var``) and the ``JoinKind`` argument is a *reference* to an enumeration
    value, which Pure spells ``JoinKind.INNER`` (or a qualified path
    ``meta::pure::functions::relation::JoinKind.INNER``). The metamodel has no
    ``JoinKind`` enum, so -- mirroring :func:`tds` -- store the verbatim emit text
    (``"JoinKind.INNER"``) on an ``InstanceValue`` discriminated by
    :data:`_ENUM_REF_GENERIC_TYPE` (a ``RelationType``-style marker, here an
    ``Enumeration`` rawType) so the emitter renders it verbatim and
    :mod:`pure_python.compile.pure_expr` reconstructs the same node.

    ``enumeration`` is the qualified-or-bare enumeration name, ``value`` the
    member name; the engine accepts the bare ``JoinKind.INNER`` (verified via the
    Legend bridge -- it both parses and compiles), so that is what is emitted.
    """
    return m3.InstanceValue(
        values=[f"{enumeration}.{value}"],
        genericType=_ENUM_REF_GENERIC_TYPE,
        multiplicity=m3.PureOne,
    )


class JoinKind:
    """Ready-made :func:`enum_ref` constants for the ``JoinKind`` enumeration.

    The Legend engine resolves the bare ``JoinKind`` enumeration and accepts
    these four members (``OUTER`` was probed and rejected -- it is not a member);
    each compiles to the ``meta::pure::functions::relation::join`` overload. Use
    as ``rel.join(other, JoinKind.INNER, cond)``.
    """

    INNER = enum_ref("JoinKind", "INNER")
    LEFT = enum_ref("JoinKind", "LEFT")
    RIGHT = enum_ref("JoinKind", "RIGHT")
    FULL = enum_ref("JoinKind", "FULL")


def col(name: str) -> m3.ColSpec:
    """A single column spec ``~name`` (a name-only ``m3.ColSpec``)."""
    return m3.ColSpec(name=name)


def cols(*names: str) -> m3.ColSpecArray:
    """A column-spec array ``~[a, b]`` (a name-only ``m3.ColSpecArray``)."""
    return m3.ColSpecArray(names=list(names))


def fcol(name: str, function: m3.Function) -> m3.FuncColSpec:
    """A function-bearing column spec ``~name:{r | <body>}`` (``m3.FuncColSpec``).

    ``function`` is the derived-column expression, typically a :func:`lam`-built
    ``LambdaFunction`` (e.g. ``fcol("doubled", lam(["r"], lambda r: r.id * 2))``
    emits ``~doubled:{r | ($r.id * 2)}``). The ``extend`` verb takes one of these
    (or a :func:`fcols` array) as its argument.
    """
    if not isinstance(function, m3.Function):
        raise TypeError(
            f"fcol expects a Function (e.g. a lam(...) LambdaFunction), got {function!r}"
        )
    return m3.FuncColSpec(name=name, function=function)


def fcols(*funcspecs: m3.FuncColSpec) -> m3.FuncColSpecArray:
    """A func-column-spec array ``~[a:{...}, b:{...}]`` (``m3.FuncColSpecArray``)."""
    for spec in funcspecs:
        if not isinstance(spec, m3.FuncColSpec):
            raise TypeError(
                f"fcols expects FuncColSpec entries (build with fcol), got {spec!r}"
            )
    return m3.FuncColSpecArray(funcSpecs=list(funcspecs))


def agg(name: str, map: m3.Function, reduce: m3.Function) -> m3.AggColSpec:
    """An aggregation column spec ``~name:{map}:{agg}`` (``m3.AggColSpec``).

    ``map`` is the per-row lambda producing a value; ``reduce`` is the lambda run
    over that collection producing the aggregate -- both typically :func:`lam`-built
    ``LambdaFunction``s (e.g.
    ``agg("total", lam(["r"], lambda r: r.val), lam(["c"], lambda c: c.sum()))``
    emits ``~total:{r | $r.val}:{c | $c->sum()}``). The ``groupBy`` verb takes one
    of these (or an :func:`aggs` array) alongside its grouping column specs.
    """
    if not isinstance(map, m3.Function):
        raise TypeError(
            f"agg expects a Function map (e.g. a lam(...) LambdaFunction), got {map!r}"
        )
    if not isinstance(reduce, m3.Function):
        raise TypeError(
            f"agg expects a Function reduce (e.g. a lam(...) LambdaFunction), got {reduce!r}"
        )
    return m3.AggColSpec(name=name, map=map, reduce=reduce)


def aggs(*aggspecs: m3.AggColSpec) -> m3.AggColSpecArray:
    """An agg-column-spec array ``~[a:{...}:{...}, b:{...}:{...}]`` (``m3.AggColSpecArray``)."""
    for spec in aggspecs:
        if not isinstance(spec, m3.AggColSpec):
            raise TypeError(
                f"aggs expects AggColSpec entries (build with agg), got {spec!r}"
            )
    return m3.AggColSpecArray(aggSpecs=list(aggspecs))


def array(*elements: object) -> m3.InstanceValue:
    """A collection literal ``[a, b, c]`` (a multi-value ``m3.InstanceValue``).

    Each element is :func:`coerce`d (an ``Expr`` unwrapped, an ``m3`` node passed
    through, a scalar wrapped as a :func:`lit`) and stored on an ``InstanceValue``
    with ``ZeroMany`` multiplicity -- the same node the emitter already renders as
    ``[a, b, c]`` (the inverse of :mod:`pure_python.compile.pure_expr`'s
    ``expressionsArray`` lowering). Holds either scalars (``array(1, 2, 3)`` ->
    ``[1, 2, 3]``) or sub-expressions (``array(asc(col("a")), desc(col("b")))`` ->
    ``[~a->ascending(), ~b->descending()]``, the list form a ``sort`` takes).
    """
    return m3.InstanceValue(
        values=[coerce(e) for e in elements],
        genericType=m3.GenericType(),
        multiplicity=m3.ZeroMany,
    )


def asc(colspec: object) -> m3.SimpleFunctionExpression:
    """A ``SortInfo`` ascending direction ``~col->ascending()`` (the engine's
    canonical spelling -- ``asc`` has no relation overload).

    ``colspec`` is typically a :func:`col` ``~col``; passed through :func:`coerce`."""
    return call("ascending", coerce(colspec))


def desc(colspec: object) -> m3.SimpleFunctionExpression:
    """A ``SortInfo`` descending direction ``~col->descending()`` (the engine's
    canonical spelling -- ``desc`` has no relation overload)."""
    return call("descending", coerce(colspec))


# --- window / OLAP layer ----------------------------------------------------
# A windowed `extend` adds an OLAP column: `$t->extend(over(~grp, sort, frame),
# ~name:{p, w, r | <body>})`. The window spec `over(...)` and the frame
# constructors `rows(...)` / `range_(...)` (which emits the engine's `_range`)
# and the `unbounded()` frame-bound sentinel are all PREFIX function calls -- the
# engine writes `over(~grp, ...)` / `rows(-1, 0)`, not the arrow form -- so they
# are plain `call(...)` nodes whose function names are in
# :data:`pure_python.compile.m3_to_pure._PREFIX_FUNCTIONS` (emitted prefix-style;
# reverse-lowered by :mod:`pure_python.compile.pure_expr`). The windowed `extend`
# column itself reuses the existing :func:`fcol` (`~name:{lambda}`) or :func:`agg`
# (`~name:{map}:{reduce}`) spec, just with a multi-param window lambda. No new m3
# type is introduced: the whole window is an ordinary function-call graph over the
# existing colspec / array / lambda nodes.
#
# Engine-resolved signatures (from `meta::pure::functions::relation`, verified via
# the Legend bridge -- each compiles to the
# `extend_Relation_1___Window_1__{FuncColSpec,AggColSpec}_1__Relation_1_` plan-gen
# boundary):
#   over(cols: ColSpec|ColSpecArray[1])
#   over(cols, frame: Rows)                       -- ColSpec/ColSpecArray + frame
#   over(cols, sortInfo: SortInfo[*])             -- partition + sort
#   over(cols, sortInfo, frame: Rows|_Range)      -- partition + sort + frame
#   rows(offsetFrom: Integer|UnboundedFrameValue[1], offsetTo: Integer|UnboundedFrameValue[1]): Rows[1]
#   _range(offsetFrom: Number|UnboundedFrameValue[1], offsetTo: Number|UnboundedFrameValue[1]): _Range[1]
#   unbounded(): UnboundedFrameValue[1]           -- negative=preceding, positive=following, 0=current row


def unbounded() -> m3.SimpleFunctionExpression:
    """The unbounded frame-bound sentinel ``unbounded()`` (``UnboundedFrameValue``).

    Used as a :func:`rows` / :func:`range_` bound to express ``UNBOUNDED PRECEDING``
    / ``UNBOUNDED FOLLOWING`` (e.g. ``rows(unbounded(), 0)`` = from the partition
    start through the current row). A zero-arg prefix call; the engine resolves the
    bare ``unbounded`` (no qualified path needed)."""
    return call("unbounded")


def rows(offset_from: object, offset_to: object) -> m3.SimpleFunctionExpression:
    """A physical row frame ``rows(from, to)`` (the engine's ``Rows``).

    Bounds are integer offsets or :func:`unbounded` sentinels: negative = N
    preceding, positive = N following, ``0`` = current row (e.g.
    ``rows(-1, 0)`` = the previous row through the current row,
    ``rows(unbounded(), 0)`` = the partition start through the current row). A
    prefix call passed as the ``over`` frame argument; each bound is
    :func:`coerce`d (an ``unbounded()`` node through, an int wrapped as a literal).
    """
    return call("rows", coerce(offset_from), coerce(offset_to))


def range_(offset_from: object, offset_to: object) -> m3.SimpleFunctionExpression:
    """A logical/value range frame, emitted as the engine's ``_range(from, to)``.

    The value-range counterpart of :func:`rows`: bounds are numeric offsets or
    :func:`unbounded` sentinels relative to the current row's order value. Named
    ``range_`` (not ``range``) because the bare ``range`` resolves to the
    *collection* range function in the engine -- the frame constructor is
    ``_range`` (which is what this emits, via the prefix set). Each bound is
    :func:`coerce`d.
    """
    return call("_range", coerce(offset_from), coerce(offset_to))


def over(
    partition: object,
    sort: object = None,
    frame: object = None,
) -> m3.SimpleFunctionExpression:
    """A window specification ``over(~grp, sort, frame)`` (the engine's ``_Window``).

    Builds the prefix ``over(...)`` call the engine resolves for a windowed
    :func:`fcol` / :func:`agg` column under ``extend``:

    * ``partition`` -- the partition column(s): a :func:`col` ``~grp`` or a
      :func:`cols` ``~[a, b]`` (passed through :func:`coerce`).
    * ``sort`` (optional) -- the order: one :func:`asc` / :func:`desc` ``SortInfo``
      (``~col->ascending()``), an :func:`array` of them (``[~a->ascending(),
      ~b->descending()]``), or ``None`` for no ordering.
    * ``frame`` (optional) -- a :func:`rows` / :func:`range_` frame, or ``None``.

    Only the supplied positional arguments are emitted, matching the engine's
    overload set (``over(~grp)`` / ``over(~grp, sort)`` / ``over(~grp, frame)`` /
    ``over(~grp, sort, frame)``). Passing a ``frame`` without a ``sort`` is
    supported (``over(~grp, rows(-1, 0))``) and resolves the
    ``over(cols, rows)`` overload. The result is a plain ``call("over", ...)`` node.
    """
    args: list[object] = [coerce(partition)]
    if sort is not None:
        args.append(coerce(sort))
    if frame is not None:
        args.append(coerce(frame))
    return call("over", *args)


__all__ = [
    "Expr",
    "c",
    "lit",
    "var",
    "call",
    "func",
    "prop",
    "coerce",
    "not_",
    "lam",
    "tds",
    "enum_ref",
    "JoinKind",
    "col",
    "cols",
    "fcol",
    "fcols",
    "agg",
    "aggs",
    "array",
    "asc",
    "desc",
    "over",
    "rows",
    "range_",
    "unbounded",
]
