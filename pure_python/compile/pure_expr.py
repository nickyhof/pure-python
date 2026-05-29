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

from .expressions import (
    agg,
    aggs,
    array,
    call,
    col,
    cols,
    db_table,
    enum_ref,
    fcol,
    fcols,
    lam,
    lit,
    prop,
    tds,
    var,
)

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


class _PendingReference:
    """A bare ``instanceReference`` (``JoinKind``) awaiting its ``.VALUE`` suffix.

    The grammar lexes an enum-value reference ``JoinKind.INNER`` as an
    ``instanceReference`` (``JoinKind``) followed by a ``propertyExpression``
    (``.INNER``). Only the combined form is meaningful here (it is the inverse of
    :func:`pure_python.compile.expressions.enum_ref`), so :func:`_lower_atomic`
    lowers the bare reference *with no ``allOrFunction``* to this carrier and
    :func:`_lower_property_or_function` folds the ``.VALUE`` suffix on, building
    the ``enum_ref`` node. A pending reference left dangling or followed by an
    arrow call ``->fn(...)`` is rejected loudly rather than silently
    mis-lowered.

    The sibling *bare-function-call* form -- an ``instanceReference`` that *does*
    carry an ``allOrFunction`` (``over(...)`` / ``rows(...)`` / ``_range(...)`` /
    ``unbounded()``, the window / OLAP prefix calls) -- is lowered directly by
    :func:`_lower_instance_reference` to a prefix ``call(name, *args)`` node and
    never becomes a pending reference.
    """

    __slots__ = ("path",)

    def __init__(self, path: str):
        self.path = path


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
    # A pending instance reference must have been resolved into an `enum_ref` by a
    # trailing `.VALUE` propertyExpression; a bare `JoinKind` is not a value here.
    if isinstance(node, _PendingReference):
        raise ValueError(
            f"unsupported bare instance reference (expected an enum value such as "
            f"{node.path}.VALUE): {node.path!r}"
        )
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
        # A grouped expression `( ... )`, a signed (`-`/`+`) expression, or a
        # collection literal `[a, b, c]` (an `expressionsArray`).
        combined = nae.combinedExpression()
        if combined is not None:
            return _lower_combined(combined)
        signed = nae.signedExpression()
        if signed is not None:
            return _lower_signed(signed)
        expressions_array = nae.expressionsArray()
        if expressions_array is not None:
            return _lower_expressions_array(expressions_array)
        raise ValueError(f"unsupported expression: {nae.getText()!r}")
    variable = atom.variable()
    if variable is not None:
        return var(variable.identifier().getText())
    literal = atom.instanceLiteralToken()
    if literal is not None:
        return lit(_lower_literal(literal))
    dsl = atom.dsl()
    if dsl is not None:
        # A `#...#` DSL island: the vendored grammar lexes the whole token as one
        # `DSL_TEXT`, so keep its text verbatim and dispatch on the island prefix.
        # `#>{db::Store.table}#` is a database-table relation source (rebuilt with
        # `db_table`, the inverse of its forward builder); `#TDS{...}#` is an inline
        # relation literal (rebuilt with `tds`). Both store the verbatim token so
        # the reparsed node equals the one the builder produced (under `canon`).
        text = dsl.DSL_TEXT().getText()
        if text.startswith("#>{"):
            # `db_table` accepts a full `#>{...}#` token in its first arg (the
            # second is unused), so pass the verbatim text -- no fragile last-`.`
            # split of the `database.table` path is needed for the round trip.
            return db_table(text, "")
        return tds(text)
    column_builders = atom.columnBuilders()
    if column_builders is not None:  # `~col` or `~[a, b]`
        return _lower_column_builders(column_builders)
    any_lambda = atom.anyLambda()
    if any_lambda is not None:  # `{p, w, r | <body>}`
        return _lower_lambda(any_lambda.lambdaFunction())
    instance_reference = atom.instanceReference()
    if instance_reference is not None:
        return _lower_instance_reference(instance_reference)
    raise ValueError(f"unsupported atomic expression: {atom.getText()!r}")


def _lower_instance_reference(instance_reference):
    """Lower an ``instanceReference`` -- a pending enum ref, or a prefix call.

    Two shapes are handled, keyed on the trailing ``allOrFunction``:

    * **No ``allOrFunction``** -- a bare qualified name (``JoinKind``). It is the
      receiver half of an enum-value reference, lowered to a :class:`_PendingReference`
      that :func:`_lower_property_or_function` folds the ``.VALUE`` suffix onto
      (the inverse of :func:`pure_python.compile.expressions.enum_ref`). A bare
      reference that is never completed is rejected by :func:`_lower_expression`.
    * **A ``functionExpressionParameters`` ``allOrFunction``** -- a *prefix
      function call* (``over(~grp, ...)`` / ``rows(-1, 0)`` / ``_range(-1, 0)`` /
      ``unbounded()``, the window / OLAP constructors). The qualified name is the
      function's *simple* name and the params are lowered as ordinary args, so it
      becomes a plain ``call(name, *args)`` -- identical to what
      :func:`pure_python.compile.expressions.call` builds and the inverse of the
      prefix emit in :mod:`pure_python.compile.m3_to_pure`. Zero args
      (``unbounded()``) yields ``call(name)``.

    The other ``allOrFunction`` alternatives (``.all()`` / ``.allVersions()`` /
    milestoning) carry no ``functionExpressionParameters`` and are not produced by
    any builder here, so they are rejected. A ``PATH_SEPARATOR``-only or
    ``unitName`` reference is likewise unsupported.
    """
    qualified_name = instance_reference.qualifiedName()
    all_or_function = instance_reference.allOrFunction()
    if all_or_function is not None:
        params = all_or_function.functionExpressionParameters()
        if params is None or qualified_name is None:
            # `.all()` / `.allVersions()` / milestoning suffixes -- no params and
            # not a builder-produced shape.
            raise ValueError(
                f"unsupported instance reference: {instance_reference.getText()!r}"
            )
        # A prefix function call `name(arg, ...)`: the qualified name's trailing
        # `identifier` is the function's simple name (a leading `packagePath` such
        # as `meta::pure::functions::relation` is dropped, matching the arrow-call
        # lowering, which also keys on the simple name). Lower each arg as an
        # ordinary combinedExpression.
        simple_name = qualified_name.identifier().getText()
        args = [_lower_combined(c) for c in params.combinedExpression()]
        return call(simple_name, *args)
    if qualified_name is None:
        raise ValueError(
            f"unsupported instance reference: {instance_reference.getText()!r}"
        )
    return _PendingReference(qualified_name.getText())


def _lower_expressions_array(expressions_array) -> m3.InstanceValue:
    """Lower a collection literal ``[a, b, c]`` (an ``expressionsArray``).

    Each element is an ``expression`` (lowered by :func:`_lower_expression`); the
    elements become the ``values`` of a multi-value ``InstanceValue`` -- the
    inverse of :func:`pure_python.compile.expressions.array` and what the emitter
    renders as ``[a, b, c]``. Used for a ``sort`` direction list
    (``[~a->ascending(), ~b->descending()]``).
    """
    elements = [_lower_expression(e) for e in expressions_array.expression()]
    return array(*elements)


def _lower_column_builders(column_builders):
    """Lower ``~col`` / ``~[a, b]``, ``~c:{r|...}`` and ``~c:{map}:{agg}`` specs.

    Three ``oneColSpec`` kinds map to three node families:

    * simple (no ``anyLambda``, no ``extraFunction``) -> ``ColSpec``;
    * func (``columnName : anyLambda``, no ``extraFunction``) -> ``FuncColSpec``;
    * agg (``columnName : anyLambda`` *and* an ``extraFunction`` carrying the
      reduce ``anyLambda``) -> ``AggColSpec``.

    A scalar spec (no brackets, ``~a`` / ``~a:{...}`` / ``~a:{...}:{...}``) yields
    the scalar form; a bracketed spec (``~[a]`` / ``~[a, b]`` ...) yields the
    matching array (``ColSpecArray`` / ``FuncColSpecArray`` / ``AggColSpecArray``)
    even for a single element -- the real engine keeps ``~[a]`` a one-element
    ``ColSpecArray`` (it resolves ``pivot(Relation, ColSpecArray, ...)``), so the
    bracket presence is read from the parse tree (``BRACKET_OPEN``) and preserved.
    Mixing kinds in one ``~[...]`` is rejected.
    """
    # Bracket presence is recoverable from the tree: `columnBuilders` is
    # `TILDE (oneColSpec | '[' (oneColSpec (',' oneColSpec)*)? ']')`, so a present
    # `BRACKET_OPEN` token means the array form even for a single element.
    bracketed = column_builders.BRACKET_OPEN() is not None
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
        if len(agg_specs) == 1 and not bracketed:
            return agg_specs[0]
        return aggs(*agg_specs)
    if func_specs:
        if len(func_specs) == 1 and not bracketed:
            return func_specs[0]
        return fcols(*func_specs)
    if len(simple_names) == 1 and not bracketed:
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
        if isinstance(receiver, _PendingReference):
            # `JoinKind` (a pending instanceReference) folded with `.INNER` (a
            # parameterless propertyExpression) is an enum-value reference. A
            # parameterized `.prop(args)` form is not an enum value, so reject it.
            if (
                property_expr.functionExpressionParameters() is not None
                or property_expr.functionExpressionLatestMilestoningDateParameter()
                is not None
            ):
                raise ValueError(
                    "unsupported property call on an instance reference: "
                    f"{receiver.path}.{name}(...)"
                )
            return enum_ref(receiver.path, name)
        return prop(receiver, name)
    if isinstance(receiver, _PendingReference):
        # A bare reference (the enum-value-reference receiver, e.g. `JoinKind`)
        # followed by an arrow `->fn(...)` is meaningless -- only `.VALUE` completes
        # it. (A *prefix* function call like `over(~grp)` is lowered directly in
        # `_lower_instance_reference` and never reaches here as a pending reference.)
        raise ValueError(
            f"unsupported function call on an instance reference: {receiver.path}->..."
        )
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
