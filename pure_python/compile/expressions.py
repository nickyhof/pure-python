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
# derive from ``Any``) but are still valid function arguments -- a verb such as
# ``filter`` / ``select`` takes them as a ``parametersValues`` entry.
_PASSTHROUGH_NODES = (m3.LambdaFunction, m3.ColSpec, m3.ColSpecArray)


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


def col(name: str) -> m3.ColSpec:
    """A single column spec ``~name`` (a name-only ``m3.ColSpec``)."""
    return m3.ColSpec(name=name)


def cols(*names: str) -> m3.ColSpecArray:
    """A column-spec array ``~[a, b]`` (a name-only ``m3.ColSpecArray``)."""
    return m3.ColSpecArray(names=list(names))


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
    "col",
    "cols",
]
