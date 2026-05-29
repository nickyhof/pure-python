"""Parse a captured Pure expression body back into an ``m3`` graph.

The inverse of :func:`pure_python.compile.m3_to_pure._expression`. The emitter
writes derived-property bodies as variables (``$x``), literals, property access
(``.name``), parenthesized infix for the core binary operators
(``(1 + 2)``) and arrow form for every other function (``->fn(args)``), so this
re-parses those shapes.

We re-parse the captured text with legend-pure's real ``M3CoreParser`` (the same
grammar :mod:`pure_python.codegen.grammar` uses) and lower the parse tree by a
left-fold over each ``expression``'s children, rebuilding nodes with the
builders in :mod:`pure_python.compile.expressions` so the genericType /
multiplicity match exactly.

Per the grammar, arithmetic / comparison operators (``+ - * / < <= > >=``) sit
on ``combinedExpression`` as ``expressionPart -> arithmeticPart``, while
``== !=`` sit on ``expression`` as the trailing ``equalNotEqual``; both are
folded left-associatively into ``call`` nodes. Negative numeric literals arrive
as ``signedExpression`` and are folded into the literal value.
"""

from __future__ import annotations

import datetime
import decimal

from antlr4 import CommonTokenStream, InputStream

from pure_python import m3
from pure_python.codegen._pure_antlr.M3CoreLexer import M3CoreLexer
from pure_python.codegen._pure_antlr.M3CoreParser import M3CoreParser

from .expressions import agg, aggs, call, col, cols, fcol, fcols, lam, lit, prop, tds, var

# Pure infix symbol -> internal core-function name (inverse of
# :data:`pure_python.compile.m3_to_pure._INFIX_OPERATORS`).
_INFIX_FUNCTIONS: dict[str, str] = {
    "+": "plus",
    "-": "minus",
    "*": "times",
    "/": "divide",
    "==": "eq",
    "!=": "notEqual",
    "<": "lessThan",
    "<=": "lessThanEqual",
    ">": "greaterThan",
    ">=": "greaterThanEqual",
}


class _Raise:
    """Turn ANTLR syntax errors into exceptions (mirrors codegen.grammar)."""

    def syntaxError(self, recognizer, symbol, line, column, message, error):
        raise SyntaxError(f"line {line}:{column} {message}")

    def reportAmbiguity(self, *args):
        pass

    def reportAttemptingFullContext(self, *args):
        pass

    def reportContextSensitivity(self, *args):
        pass


def parse_expression(text: str) -> m3.ValueSpecification:
    """Parse a single Pure expression body and lower it to an ``m3`` node."""
    statements = parse_statements(text)
    if not statements:
        raise ValueError(f"empty expression body: {text!r}")
    return statements[0]


def parse_statements(text: str) -> list[m3.ValueSpecification]:
    """Parse a (possibly multi-statement) Pure body, lowering every statement."""
    parser = M3CoreParser(CommonTokenStream(M3CoreLexer(InputStream(text))))
    parser.removeErrorListeners()
    parser.addErrorListener(_Raise())
    block = parser.codeBlock()
    lines = block.programLine()
    if not lines:
        raise ValueError(f"empty expression body: {text!r}")
    return [_lower_combined(line.combinedExpression()) for line in lines]


def _lower_combined(combined) -> m3.ValueSpecification:
    """Lower a ``combinedExpression``: its base plus folded infix operators.

    Arithmetic / comparison operators (``+ - * / < <= > >=``) live here as
    ``expressionPart -> arithmeticPart``; ``== !=`` are folded by
    :func:`_lower_expression` on the trailing ``equalNotEqual`` of the base.
    """
    node = _lower_expression(combined.expressionOrExpressionGroup().expression())
    for part in combined.expressionPart():
        arithmetic = part.arithmeticPart()
        if arithmetic is not None:
            node = _fold_arithmetic(node, arithmetic)
            continue
        boolean = part.booleanPart()
        equal = boolean.equalNotEqual() if boolean is not None else None
        if equal is not None:
            node = _fold_equal_not_equal(node, equal)
            continue
        raise ValueError(f"unsupported expression part: {part.getText()!r}")
    return node


def _lower_expression(expr) -> m3.ValueSpecification:
    """Lower an ``expression``: atomic base, property/function chain, ``== !=``."""
    node = _lower_atomic(expr.nonArrowOrEqualExpression())
    for pof in expr.propertyOrFunctionExpression():
        node = _lower_property_or_function(node, pof)
    equal = expr.equalNotEqual()
    if equal is not None:
        node = _fold_equal_not_equal(node, equal)
    return node


def _fold_arithmetic(node, arithmetic) -> m3.ValueSpecification:
    """Fold one ``arithmeticPart`` (``OP expression (OP expression)*``) onto node."""
    symbol = _arithmetic_symbol(arithmetic)
    opname = _INFIX_FUNCTIONS[symbol]
    for rhs_expr in arithmetic.expression():
        node = call(opname, node, _lower_expression(rhs_expr))
    return node


def _fold_equal_not_equal(node, equal) -> m3.ValueSpecification:
    """Fold an ``equalNotEqual`` (``(== | !=) combinedArithmeticOnly``) onto node."""
    symbol = "!=" if equal.TEST_NOT_EQUAL() is not None else "=="
    rhs = _lower_combined_arithmetic_only(equal.combinedArithmeticOnly())
    return call(_INFIX_FUNCTIONS[symbol], node, rhs)


def _lower_combined_arithmetic_only(cao) -> m3.ValueSpecification:
    """Lower a ``combinedArithmeticOnly`` (``expression arithmeticPart*``)."""
    node = _lower_expression(cao.expressionOrExpressionGroup().expression())
    for arithmetic in cao.arithmeticPart():
        node = _fold_arithmetic(node, arithmetic)
    return node


def _arithmetic_symbol(arithmetic) -> str:
    """Read the operator symbol from an ``arithmeticPart`` parse node."""
    if arithmetic.PLUS():
        return "+"
    if arithmetic.STAR():
        return "*"
    if arithmetic.MINUS():
        return "-"
    if arithmetic.DIVIDE():
        return "/"
    if arithmetic.LESSTHANEQUAL() is not None:
        return "<="
    if arithmetic.LESSTHAN() is not None:
        return "<"
    if arithmetic.GREATERTHANEQUAL() is not None:
        return ">="
    if arithmetic.GREATERTHAN() is not None:
        return ">"
    raise ValueError(f"unsupported arithmetic operator: {arithmetic.getText()!r}")


def _lower_atomic(nae) -> m3.ValueSpecification:
    atom = nae.atomicExpression()
    if atom is None:
        # A grouped expression `( ... )` or a signed (`-`/`+`) expression.
        combined = nae.combinedExpression()
        if combined is not None:
            return _lower_combined(combined)
        signed = nae.signedExpression()
        if signed is not None:
            return _lower_signed(signed)
        raise ValueError(f"unsupported expression: {nae.getText()!r}")
    variable = atom.variable()
    if variable is not None:
        return var(variable.identifier().getText())
    literal = atom.instanceLiteralToken()
    if literal is not None:
        return lit(_lower_literal(literal))
    dsl = atom.dsl()
    if dsl is not None:  # a `#TDS{...}#` relation literal: keep its text verbatim
        return tds(dsl.DSL_TEXT().getText())
    column_builders = atom.columnBuilders()
    if column_builders is not None:  # `~col` or `~[a, b]`
        return _lower_column_builders(column_builders)
    any_lambda = atom.anyLambda()
    if any_lambda is not None:  # `{p, w, r | <body>}`
        return _lower_lambda(any_lambda.lambdaFunction())
    raise ValueError(f"unsupported atomic expression: {atom.getText()!r}")


def _lower_column_builders(column_builders):
    """Lower ``~col`` / ``~[a, b]``, ``~c:{r|...}`` and ``~c:{map}:{agg}`` specs.

    Three ``oneColSpec`` kinds map to three node families:

    * simple (no ``anyLambda``, no ``extraFunction``) -> ``ColSpec``;
    * func (``columnName : anyLambda``, no ``extraFunction``) -> ``FuncColSpec``;
    * agg (``columnName : anyLambda`` *and* an ``extraFunction`` carrying the
      reduce ``anyLambda``) -> ``AggColSpec``.

    A single spec yields the scalar form, multiple the matching array
    (``ColSpecArray`` / ``FuncColSpecArray`` / ``AggColSpecArray``). Mixing kinds
    in one ``~[...]`` is rejected.
    """
    specs = column_builders.oneColSpec()
    simple_names: list[str] = []
    func_specs: list[m3.FuncColSpec] = []
    agg_specs: list[m3.AggColSpec] = []
    for spec in specs:
        extra_function = spec.extraFunction()
        any_lambda = spec.anyLambda()
        if extra_function is not None:
            # `~c:{map}:{agg}` -- an AggColSpec: the map lambda is the spec's own
            # `anyLambda`, the reduce lambda hangs off `extraFunction : anyLambda`.
            if any_lambda is None:
                raise ValueError(
                    f"aggregation column spec missing its map lambda: {spec.getText()!r}"
                )
            map_lambda = _agg_lambda(any_lambda, spec)
            reduce_lambda = _agg_lambda(extra_function.anyLambda(), spec)
            agg_specs.append(
                agg(spec.columnName().getText(), map_lambda, reduce_lambda)
            )
        elif any_lambda is not None:
            func_specs.append(
                fcol(spec.columnName().getText(), _agg_lambda(any_lambda, spec))
            )
        else:
            simple_names.append(spec.columnName().getText())
    kinds = [bool(simple_names), bool(func_specs), bool(agg_specs)]
    if sum(kinds) > 1:
        raise ValueError(
            "mixing simple, function-bearing and aggregation column specs in one "
            "~[...] is not supported"
        )
    if agg_specs:
        return agg_specs[0] if len(agg_specs) == 1 else aggs(*agg_specs)
    if func_specs:
        return func_specs[0] if len(func_specs) == 1 else fcols(*func_specs)
    if len(simple_names) == 1:
        return col(simple_names[0])
    return cols(*simple_names)


def _agg_lambda(any_lambda, spec) -> m3.LambdaFunction:
    """Lower the ``anyLambda`` of a column spec, requiring the ``{params | body}`` form.

    A bare ``{| ...}`` pipe / ``p | ...`` form is not produced by ``fcol`` / ``agg``,
    so reject anything but the ``lambdaFunction`` alternative.
    """
    lambda_function = any_lambda.lambdaFunction() if any_lambda is not None else None
    if lambda_function is None:
        raise ValueError(
            f"unsupported column spec lambda: {spec.getText()!r}"
        )
    return _lower_lambda(lambda_function)


def _lower_lambda(lambda_function) -> m3.LambdaFunction:
    """Lower a ``lambdaFunction`` (``{params | body}``) back to a ``LambdaFunction``.

    Re-feeds the body ``codeBlock`` text through :func:`parse_statements` and
    rebuilds via :func:`pure_python.compile.expressions.lam` so the names carrier
    (``openVariables``) and body graph match the forward builder.
    """
    names = [p.identifier().getText() for p in lambda_function.lambdaParam()]
    body_text = lambda_function.lambdaPipe().codeBlock().getText()
    statements = parse_statements(body_text)
    if len(statements) != 1:
        # `lam` builds single-statement bodies; fail loud rather than silently
        # dropping the trailing statements of a multi-statement lambda body.
        raise ValueError(f"multi-statement lambda body is not supported: {body_text!r}")
    # `lam` calls `build(*params)`; the body is independent of the fresh vars it
    # passes (it was parsed from text), so return the single parsed statement.
    return lam(names, lambda *_: statements[0])


def _lower_signed(signed) -> m3.ValueSpecification:
    """Lower a ``signedExpression`` (leading ``-``/``+``); negate numeric literals."""
    inner = _lower_expression(signed.expression())
    if signed.MINUS() is None:  # a unary `+` is a no-op
        return inner
    if isinstance(inner, m3.InstanceValue) and len(inner.values) == 1:
        value = inner.values[0]
        if isinstance(value, bool):
            raise ValueError("cannot negate a boolean literal")
        if isinstance(value, (int, float, decimal.Decimal)):
            return lit(-value)
    raise ValueError(f"unsupported signed expression: {signed.getText()!r}")


def _lower_property_or_function(receiver, pof) -> m3.ValueSpecification:
    property_expr = pof.propertyExpression()
    if property_expr is not None:
        name = _property_name(property_expr.propertyName())
        return prop(receiver, name)
    function_expr = pof.functionExpression()
    # One functionExpression can chain `->f(..)->g(..)`: fold each pair on.
    names = function_expr.qualifiedName()
    params_list = function_expr.functionExpressionParameters()
    node = receiver
    for name_ctx, params in zip(names, params_list):
        simple_name = name_ctx.identifier().getText()
        args = [_lower_combined(c) for c in params.combinedExpression()]
        node = call(simple_name, node, *args)
    return node


def _property_name(pn) -> str:
    string = pn.STRING()
    if string is not None:
        return string.getText()[1:-1]
    return pn.identifier().getText()


def _unescape_string(text: str) -> str:
    """Reverse :func:`pure_python.compile.m3_to_pure._escape_string`.

    Walk the body of a single-quoted Pure literal, turning the backslash escapes
    the emitter writes (``\\\\ \\' \\n \\t \\r``) back into their characters.
    """
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _lower_literal(literal_token) -> object:
    if literal_token.STRING() is not None:
        return _unescape_string(literal_token.STRING().getText()[1:-1])
    if literal_token.BOOLEAN() is not None:
        return literal_token.BOOLEAN().getText() == "true"
    if literal_token.INTEGER() is not None:
        return int(literal_token.INTEGER().getText())
    if literal_token.FLOAT() is not None:
        return float(literal_token.FLOAT().getText())
    if literal_token.DECIMAL() is not None:
        return decimal.Decimal(literal_token.DECIMAL().getText().rstrip("dD"))
    if literal_token.DATE() is not None:
        return _lower_date(literal_token.DATE().getText())
    raise ValueError(f"unsupported literal: {literal_token.getText()!r}")


def _lower_date(text: str) -> datetime.date | datetime.datetime:
    body = text.lstrip("%")
    if "T" in body:
        return datetime.datetime.fromisoformat(body)
    return datetime.date.fromisoformat(body)


__all__ = ["parse_expression", "parse_statements"]
