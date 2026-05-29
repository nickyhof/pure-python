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

def canon(vs):
    if isinstance(vs, m3.VariableExpression):
        return ("var", vs.name)
    if isinstance(vs, m3.SimpleFunctionExpression):
        if vs.propertyName is not None:
            return ("prop", vs.propertyName.values[0], canon(vs.parametersValues[0]))
        return ("call", vs.functionName, tuple(canon(p) for p in vs.parametersValues))
    # relation-layer nodes (lambda / column specs) -- not ValueSpecifications
    if isinstance(vs, m3.LambdaFunction):
        return (
            "lambda",
            tuple(vs.openVariables),
            tuple(canon(b) for b in vs.expressionSequence),
        )
    if isinstance(vs, m3.ColSpec):
        return ("colspec", vs.name)
    if isinstance(vs, m3.ColSpecArray):
        return ("colspecarray", tuple(vs.names))
    if isinstance(vs, m3.FuncColSpec):
        return ("funccolspec", vs.name, canon(vs.function))
    if isinstance(vs, m3.FuncColSpecArray):
        return ("funccolspecarray", tuple(canon(s) for s in vs.funcSpecs))
    if isinstance(vs, m3.InstanceValue):
        # A `#TDS{...}#` literal is discriminated by a RelationType rawType marker.
        if isinstance(vs.genericType.rawType, m3.RelationType):
            return ("tds", tuple(vs.values))
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


def test_dsl_ne_is_not_equal():
    assert canon((c(6) != 7).node) == canon(call("notEqual", lit(6), lit(7)))


def test_dsl_comparison_operators():
    assert (c(1) < 2).node.functionName == "lessThan"
    assert (c(1) <= 2).node.functionName == "lessThanEqual"
    assert (c(1) > 2).node.functionName == "greaterThan"
    assert (c(1) >= 2).node.functionName == "greaterThanEqual"


def test_dsl_explicit_prop_and_call_escape_hatches():
    this = Expr(var("this"))
    assert canon(this.prop("first").node) == ("prop", "first", ("var", "this"))
    assert canon(c(4).call("exp").node) == ("call", "exp", (("lit", "Integer", (4,)),))


def test_dsl_each_operator_equals_builder():
    assert canon((c(1) + c(2)).node) == canon(call("plus", lit(1), lit(2)))
    assert canon((c(3) - c(1)).node) == canon(call("minus", lit(3), lit(1)))
    assert canon((c(2) * c(3)).node) == canon(call("times", lit(2), lit(3)))
    assert canon((c(4) / c(2)).node) == canon(call("divide", lit(4), lit(2)))
    assert canon((c(6) == c(6)).node) == canon(call("eq", lit(6), lit(6)))
    assert canon((c(6) != c(7)).node) == canon(call("notEqual", lit(6), lit(7)))
    assert canon((c(1) < c(2)).node) == canon(call("lessThan", lit(1), lit(2)))
    assert canon((c(1) <= c(2)).node) == canon(call("lessThanEqual", lit(1), lit(2)))
    assert canon((c(1) > c(2)).node) == canon(call("greaterThan", lit(1), lit(2)))
    assert canon((c(1) >= c(2)).node) == canon(call("greaterThanEqual", lit(1), lit(2)))


def test_dsl_reflected_add_keeps_operand_order():
    assert canon((3 + c(2)).node) == canon(call("plus", lit(3), lit(2)))


def test_dsl_reflected_multiply_keeps_operand_order():
    assert canon((3 * c(2)).node) == canon(call("times", lit(3), lit(2)))


def test_dsl_not_and_invert_equal_builder():
    assert canon(not_(c(6) == c(7)).node) == canon(call("not", call("eq", lit(6), lit(7))))
    assert canon((~(c(6) == c(7))).node) == canon(call("not", call("eq", lit(6), lit(7))))


def test_expr_is_unhashable():
    assert Expr.__hash__ is None


def test_expr_has_no_truth_value():
    import pytest

    with pytest.raises(TypeError):
        bool(c(1) < c(2))
    with pytest.raises(TypeError):
        if c(1):  # noqa: SIM103 -- the point is that this raises
            pass


# --- literal escaper --------------------------------------------------------

def test_literal_escaper_forms():
    assert _literal(True) == "true"
    assert _literal(False) == "false"
    assert _literal(1.0) == "1.0"  # float keeps its decimal point
    assert _literal(2.5) == "2.5"
    assert _literal("o'clock") == "'o\\'clock'"
    assert _literal(Decimal("1.5")) == "1.5D"


def test_literal_escaper_backslash_and_controls():
    # Pure processes C-style backslash escapes, so the backslash doubles and the
    # quote / control characters escape (verified against Legend).
    assert _literal("a\\b") == "'a\\\\b'"
    assert _literal("c:\\path") == "'c:\\\\path'"
    assert _literal("line1\nline2") == "'line1\\nline2'"
    assert _literal("tab\there") == "'tab\\there'"


def test_literal_rejects_non_finite_floats():
    import pytest

    with pytest.raises(ValueError):
        _literal(float("inf"))
    with pytest.raises(ValueError):
        _literal(float("nan"))


def test_literal_rejects_unemittable_primitives():
    import datetime

    import pytest

    with pytest.raises((ValueError, NotImplementedError)):
        _literal(b"bytes")
    with pytest.raises((ValueError, NotImplementedError)):
        _literal(datetime.time(1, 2, 3))


def test_emit_multi_value_instance_value_is_list_literal():
    node = m3.InstanceValue(
        values=[1, 2, 3],
        genericType=m3.GenericType(rawType=m3.Integer),
        multiplicity=m3.ZeroMany,
    )
    assert _expression(node) == "[1, 2, 3]"


def test_emit_empty_instance_value_is_empty_list():
    node = m3.InstanceValue(values=[], genericType=m3.GenericType(), multiplicity=m3.ZeroMany)
    assert _expression(node) == "[]"


# --- emit -------------------------------------------------------------------

def test_emit_infix_form_for_known_body():
    this = Expr(var("this"))
    node = (this.first + " " + this.last).node
    assert _expression(node) == "(($this.first + ' ') + $this.last)"


def test_emit_operators_are_parenthesized_infix():
    assert _expression((c(1) + c(1)).node) == "(1 + 1)"
    assert _expression((c(3) - c(1)).node) == "(3 - 1)"
    assert _expression((c(2) * c(3)).node) == "(2 * 3)"
    assert _expression((c(4) / c(2)).node) == "(4 / 2)"
    assert _expression((c(6) == c(6)).node) == "(6 == 6)"
    assert _expression((c(6) != c(7)).node) == "(6 != 7)"
    assert _expression((c(1) < c(2)).node) == "(1 < 2)"
    assert _expression((c(1) <= c(2)).node) == "(1 <= 2)"
    assert _expression((c(1) > c(2)).node) == "(1 > 2)"
    assert _expression((c(1) >= c(2)).node) == "(1 >= 2)"


def test_emit_nested_operators_are_fully_parenthesized():
    assert _expression(((c(1) + c(2)) * c(3)).node) == "((1 + 2) * 3)"


def test_emit_not_keeps_arrow_form():
    assert _expression(not_(c(6) == c(7)).node) == "(6 == 7)->not()"


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


def _assert_round_trips(node) -> None:
    emitted = _expression(node)
    assert canon(pure_expr.parse_expression(emitted)) == canon(node)


def test_parse_expression_round_trips_every_binary_operator():
    for node in (
        (c(1) + c(2)).node,
        (c(3) - c(1)).node,
        (c(2) * c(3)).node,
        (c(4) / c(2)).node,
        (c(6) == c(6)).node,
        (c(6) != c(7)).node,
        (c(1) < c(2)).node,
        (c(1) <= c(2)).node,
        (c(1) > c(2)).node,
        (c(1) >= c(2)).node,
    ):
        _assert_round_trips(node)


def test_parse_expression_round_trips_nested_parenthesized():
    _assert_round_trips(((c(1) + c(2)) * c(3)).node)


def test_parse_expression_round_trips_not():
    _assert_round_trips(not_(c(6) == c(7)).node)


def test_parse_expression_round_trips_property_access():
    _assert_round_trips(prop(var("this"), "x"))


def test_parse_expression_round_trips_date_and_datetime():
    import datetime

    _assert_round_trips(lit(datetime.date(2021, 1, 2)))
    _assert_round_trips(lit(datetime.datetime(2021, 1, 2, 3, 4, 5)))


def test_parse_expression_round_trips_false_and_decimal():
    _assert_round_trips(lit(False))
    _assert_round_trips(lit(Decimal("2.5")))


def test_parse_expression_round_trips_negative_literal():
    node = lit(-2)
    emitted = _expression(node)
    assert emitted == "-2"
    parsed = pure_expr.parse_expression(emitted)
    assert canon(parsed) == canon(node)
    assert parsed.values == [-2]


def test_parse_expression_round_trips_strings_with_escapes():
    for text in ["o'clock", "a\\b", "c:\\path\\to", "line1\nline2", "tab\there"]:
        node = lit(text)
        parsed = pure_expr.parse_expression(_expression(node))
        assert parsed.values == [text]


def test_parse_statements_lowers_every_statement():
    nodes = pure_expr.parse_statements("1; 2; (3 + 4);")
    assert [canon(n) for n in nodes] == [
        canon(lit(1)),
        canon(lit(2)),
        canon(call("plus", lit(3), lit(4))),
    ]
