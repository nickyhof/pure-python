"""Tests for the expression layer: builders, DSL, emit and reverse parse."""

from __future__ import annotations

from decimal import Decimal

from pure_python import m3
from pure_python.compile import pure_expr
from pure_python.compile.expressions import (
    Expr,
    c,
    call,
    coerce,
    func,
    lit,
    not_,
    prop,
    var,
)
from pure_python.compile.m3_to_pure import _expression, _literal


# --- a canonical projection of a ValueSpecification subtree -----------------
# m3 graphs carry owner back-refs / sentinels; project to nested tuples so we
# can compare body graphs without recursing into func / importGroup / owner.

def canon(vs: m3.ValueSpecification):
    if isinstance(vs, m3.VariableExpression):
        return ("var", vs.name)
    if isinstance(vs, m3.SimpleFunctionExpression):
        if vs.propertyName is not None:
            return ("prop", vs.propertyName.values[0], canon(vs.parametersValues[0]))
        return ("call", vs.functionName, tuple(canon(p) for p in vs.parametersValues))
    if isinstance(vs, m3.InstanceValue):
        raw = vs.genericType.rawType
        return ("lit", getattr(raw, "name", None), tuple(vs.values))
    raise TypeError(f"unexpected node {vs!r}")


# --- builders ---------------------------------------------------------------

def test_lit_builds_instance_value_with_primitive():
    node = lit(4)
    assert isinstance(node, m3.InstanceValue)
    assert node.values == [4]
    assert node.genericType.rawType is m3.Integer
    assert node.multiplicity is m3.PureOne


def test_lit_primitive_mapping_covers_scalars():
    assert lit("x").genericType.rawType is m3.String
    assert lit(True).genericType.rawType is m3.Boolean
    assert lit(1.5).genericType.rawType is m3.Float
    assert lit(Decimal("1.5")).genericType.rawType is m3.Decimal


def test_var_builds_variable_expression():
    node = var("this")
    assert isinstance(node, m3.VariableExpression)
    assert node.name == "this"
    assert node.multiplicity is m3.PureOne


def test_call_builds_simple_function_expression():
    node = call("plus", 1, 2)
    assert isinstance(node, m3.SimpleFunctionExpression)
    assert node.functionName == "plus"
    assert node.propertyName is None
    assert [p.values for p in node.parametersValues] == [[1], [2]]


def test_func_is_alias_of_call():
    assert func is call


def test_prop_builds_property_access():
    node = prop(var("this"), "first")
    assert isinstance(node, m3.SimpleFunctionExpression)
    assert node.functionName is None
    assert node.propertyName.values == ["first"]
    assert node.propertyName.genericType.rawType is m3.String
    assert node.parametersValues[0].name == "this"


def test_coerce_unwraps_expr_and_wraps_scalars():
    assert coerce(c(4)).values == [4]
    assert coerce(7).values == [7]
    existing = var("x")
    assert coerce(existing) is existing


# --- DSL equals the builders ------------------------------------------------

def test_dsl_divide_equals_builder():
    assert canon((c(4) / 2).node) == canon(call("divide", lit(4), lit(2)))


def test_dsl_eq_equals_builder():
    assert canon((c(6) == 6).node) == canon(call("eq", lit(6), lit(6)))


def test_dsl_fluent_chain_equals_builder():
    assert canon(c(1.0).exp().log().node) == canon(
        call("log", call("exp", lit(1.0)))
    )


def test_dsl_reflected_divide_keeps_operand_order():
    assert canon((3 / c(2)).node) == canon(call("divide", lit(3), lit(2)))


def test_dsl_reflected_subtract_keeps_operand_order():
    assert canon((6 - c(2)).node) == canon(call("minus", lit(6), lit(2)))


def test_dsl_property_access_then_arithmetic():
    this = Expr(var("this"))
    node = (this.first + " " + this.last).node
    assert canon(node) == (
        "call",
        "plus",
        (
            ("call", "plus", (("prop", "first", ("var", "this")), ("lit", "String", (" ",)))),
            ("prop", "last", ("var", "this")),
        ),
    )


def test_dsl_ne_is_not_of_eq():
    assert canon((c(6) != 7).node) == canon(not_(call("eq", lit(6), lit(7))).node)


def test_dsl_comparison_operators():
    assert (c(1) < 2).node.functionName == "lessThan"
    assert (c(1) <= 2).node.functionName == "lessThanEqual"
    assert (c(1) > 2).node.functionName == "greaterThan"
    assert (c(1) >= 2).node.functionName == "greaterThanEqual"


def test_dsl_explicit_prop_and_call_escape_hatches():
    this = Expr(var("this"))
    assert canon(this.prop("first").node) == ("prop", "first", ("var", "this"))
    assert canon(c(4).call("exp").node) == ("call", "exp", (("lit", "Integer", (4,)),))


def test_expr_is_unhashable():
    assert Expr.__hash__ is None


# --- literal escaper --------------------------------------------------------

def test_literal_escaper_forms():
    assert _literal(True) == "true"
    assert _literal(False) == "false"
    assert _literal(1.0) == "1.0"  # float keeps its decimal point
    assert _literal(2.5) == "2.5"
    assert _literal("o'clock") == "'o\\'clock'"
    assert _literal(Decimal("1.5")) == "1.5D"


# --- emit -------------------------------------------------------------------

def test_emit_arrow_form_for_known_body():
    this = Expr(var("this"))
    node = (this.first + " " + this.last).node
    assert _expression(node) == "$this.first->plus(' ')->plus($this.last)"


def test_emit_property_access():
    assert _expression(prop(var("this"), "age")) == "$this.age"


def test_emit_fluent_chain():
    assert _expression(c(1.0).exp().log().node) == "1.0->exp()->log()"


def test_emit_substring():
    assert _expression(c("hello world").substring(0, 4).node) == (
        "'hello world'->substring(0, 4)"
    )


# --- reverse parse (expression-level round trip) ----------------------------

def test_parse_expression_round_trips_property_concat():
    this = Expr(var("this"))
    node = (this.first + " " + this.last).node
    emitted = _expression(node)
    assert canon(pure_expr.parse_expression(emitted)) == canon(node)


def test_parse_expression_round_trips_arithmetic():
    node = (c(4) / 2).node
    emitted = _expression(node)
    assert canon(pure_expr.parse_expression(emitted)) == canon(node)


def test_parse_expression_round_trips_fluent_chain():
    node = c(1.0).exp().log().node
    emitted = _expression(node)
    parsed = pure_expr.parse_expression(emitted)
    assert canon(parsed) == canon(node)
    assert _expression(parsed) == emitted


def test_parse_expression_round_trips_literals():
    for value in [c(4), c(1.0), c("hi"), c(True), c(Decimal("2.5"))]:
        emitted = _expression(value.node)
        assert canon(pure_expr.parse_expression(emitted)) == canon(value.node)


def test_parse_expression_round_trips_comparison():
    node = (c(6) == 6).node
    emitted = _expression(node)
    assert canon(pure_expr.parse_expression(emitted)) == canon(node)
