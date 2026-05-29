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
"""

from __future__ import annotations

import datetime
import decimal
from typing import Any

from pure_python import m3

from .python_to_m3 import _PRIMITIVE

# A ``FunctionExpression`` requires a ``func`` and an ``importGroup`` that we do
# not model at the expression level (the function/property name carries all the
# meaning). Share one sentinel of each so equality and identity stay cheap and
# emitters/comparers can ignore them.
_FUNC_SENTINEL = m3.Function()
_IMPORT_GROUP_SENTINEL = m3.ImportGroup()


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


def coerce(value: object) -> m3.ValueSpecification:
    """Turn an ``Expr`` into its node, an ``m3`` node through, scalars into ``lit``."""
    if isinstance(value, Expr):
        return value.node
    if isinstance(value, m3.ValueSpecification):
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
]
