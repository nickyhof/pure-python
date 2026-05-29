"""Emit actual Pure grammar source from Pure M3 instances.

The inverse of :mod:`pure_python.codegen.grammar`: walk an ``m3.Class`` (and the
classes / enumerations it references) and render readable Pure::

    Class demo::Person
    {
        <<pii.sensitive>> firstName : String[1];
        age : Integer[0..1];
        addresses : demo::Address[*];
        fullName() { ($this.firstName + $this.lastName); } : String[1];
    }

Type parameters, type arguments, stereotypes and tagged values are all
rendered. Qualified (derived) properties carry their ``expressionSequence``
body, emitted by :func:`_expression`. Binary core operators are emitted as
fully parenthesized *infix* (``($this.firstName + ' ')``) -- this is what
Legend's stdlib actually executes, since its arithmetic / comparison functions
bind variadically and the arrow spelling (``1->plus(1)``) has no two-arg match.
Every other function (``exp``, ``substring``, ``not`` ...) keeps the arrow form
(``$this.first->substring(0, 4)``). A signature-only qualified property (no
body) still emits the ``[]`` placeholder. Associations are emitted too.
"""

from __future__ import annotations

import datetime
import decimal
import math

from pure_python import m3

# Core binary functions emitted as parenthesized infix (the inverse table lives
# in :mod:`pure_python.compile.pure_expr`). Anything not here is arrow-emitted.
_INFIX_OPERATORS: dict[str, str] = {
    "plus": "+",
    "minus": "-",
    "times": "*",
    "divide": "/",
    "eq": "==",
    "notEqual": "!=",
    "lessThan": "<",
    "lessThanEqual": "<=",
    "greaterThan": ">",
    "greaterThanEqual": ">=",
}


def _bounds(mult: m3.Multiplicity | None) -> tuple[int, int | None]:
    if mult is None:
        return 1, 1
    lower = mult.lowerBound.value if mult.lowerBound and mult.lowerBound.value is not None else 0
    upper = mult.upperBound.value if mult.upperBound and mult.upperBound.value is not None else None
    return lower, upper


def _multiplicity(mult: m3.Multiplicity | None) -> str:
    lower, upper = _bounds(mult)
    if (lower, upper) == (1, 1):
        return "[1]"
    if (lower, upper) == (0, None):
        return "[*]"
    if upper is None:
        return f"[{lower}..*]"
    if lower == upper:
        return f"[{lower}]"
    return f"[{lower}..{upper}]"


def _type(generic: m3.GenericType | None) -> str:
    if generic is None:
        return "Any"
    if generic.typeParameter is not None:
        return generic.typeParameter.name
    raw = generic.rawType
    base = _qualified_name(raw) if raw is not None and getattr(raw, "name", None) else "Any"
    if generic.typeArguments:
        args = ", ".join(_type(arg) for arg in generic.typeArguments)
        return f"{base}<{args}>"
    return base


def _stereotypes(element: object) -> str:
    stereotypes = getattr(element, "stereotypes", []) or []
    if not stereotypes:
        return ""
    rendered = ", ".join(f"{s.profile.name}.{s.value}" for s in stereotypes)
    return f"<<{rendered}>> "


def _escape_string(value: str) -> str:
    """Escape a Python string for a Pure single-quoted literal.

    Pure processes C-style backslash escapes inside single quotes (verified via
    Legend: ``'a\\\\b'`` -> ``a\\b``, ``'o\\'clock'`` -> ``o'clock``), so the
    backslash must be doubled first, then the quote and the control characters
    escaped. :func:`pure_python.compile.pure_expr._unescape_string` is the exact
    inverse so strings round-trip and stay Legend-acceptable.
    """
    return (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )


def _literal(value: object) -> str:
    """Render a Python value as a Pure literal token (the shared escaper)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return "'" + _escape_string(value) + "'"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"cannot emit non-finite float {value!r} as a Pure literal")
        text = repr(value)
        return text if any(c in text for c in ".eEnN") else f"{text}.0"
    if isinstance(value, decimal.Decimal):
        return f"{value}D"
    # `bytes` (Pure `Byte`) and `datetime.time` (Pure `StrictTime`) are buildable
    # in m3 but have no emittable literal grammar here yet.
    if isinstance(value, (bytes, bytearray)):
        raise NotImplementedError("emitting a Pure Byte literal is not supported")
    if isinstance(value, datetime.time):
        raise NotImplementedError("emitting a Pure StrictTime literal is not supported")
    if isinstance(value, datetime.datetime):
        return "%" + value.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(value, datetime.date):
        return "%" + value.strftime("%Y-%m-%d")
    return str(value)


def _tagged_values(element: object) -> str:
    tagged = getattr(element, "taggedValues", []) or []
    if not tagged:
        return ""
    rendered = ", ".join(
        f"{t.tag.profile.name}.{t.tag.value} = {_literal(t.value)}" for t in tagged
    )
    return f"{{{rendered}}} "


def _is_tds(vs: m3.InstanceValue) -> bool:
    """A ``#TDS{...}#`` relation literal is an ``InstanceValue`` whose
    ``genericType.rawType`` is a ``RelationType`` (the discriminating marker set
    by :func:`pure_python.compile.expressions.tds`)."""
    generic = vs.genericType
    return generic is not None and isinstance(generic.rawType, m3.RelationType)


def _func_col_spec(spec: m3.FuncColSpec) -> str:
    """Render a ``FuncColSpec`` as the un-``~``'d ``name:{lambda}`` core form.

    Shared by the bracketless ``~name:{...}`` and the bracketed
    ``~[a:{...}, b:{...}]`` emits so the lambda spelling is identical in both.
    """
    return f"{spec.name}:{_expression(spec.function)}"


def _agg_col_spec(spec: m3.AggColSpec) -> str:
    """Render an ``AggColSpec`` as the un-``~``'d ``name:{map}:{agg}`` core form.

    Shared by the bracketless ``~name:{...}:{...}`` and the bracketed
    ``~[a:{...}:{...}, b:{...}:{...}]`` emits so the lambda spelling is identical
    in both; each lambda reuses the ``LambdaFunction`` emit.
    """
    return f"{spec.name}:{_expression(spec.map)}:{_expression(spec.reduce)}"


def _expression(vs) -> str:
    """Render a ``ValueSpecification`` (or relation-layer node) body as Pure source.

    Binary core operators emit as fully parenthesized infix; property access and
    every other function keep the arrow form. Relation-layer nodes
    (``LambdaFunction``, ``ColSpec`` / ``ColSpecArray``, ``#TDS{}#`` literals)
    have their own forms.
    """
    if isinstance(vs, m3.VariableExpression):
        return f"${vs.name}"
    if isinstance(vs, m3.SimpleFunctionExpression):
        if vs.propertyName is not None:
            receiver = _expression(vs.parametersValues[0])
            return f"{receiver}.{vs.propertyName.values[0]}"
        symbol = _INFIX_OPERATORS.get(vs.functionName)
        if symbol is not None and len(vs.parametersValues) == 2:
            left = _expression(vs.parametersValues[0])
            right = _expression(vs.parametersValues[1])
            return f"({left} {symbol} {right})"
        if not vs.parametersValues:  # defensive -- builders always supply a receiver
            return f"{vs.functionName}()"
        receiver = _expression(vs.parametersValues[0])
        args = ", ".join(_expression(p) for p in vs.parametersValues[1:])
        return f"{receiver}->{vs.functionName}({args})"
    if isinstance(vs, m3.LambdaFunction):
        params = ", ".join(vs.openVariables)
        body = " ".join(_expression(b) for b in vs.expressionSequence)
        # Match the grammar: a parameterless lambda is `{| body}`.
        return f"{{{params} | {body}}}" if params else f"{{| {body}}}"
    if isinstance(vs, m3.ColSpec):
        return f"~{vs.name}"
    if isinstance(vs, m3.ColSpecArray):
        return "~[" + ", ".join(vs.names) + "]"
    if isinstance(vs, m3.FuncColSpec):
        # A function-bearing column spec `~name:{lambda}` (the bracketless form);
        # the lambda reuses the `LambdaFunction` emit above.
        return "~" + _func_col_spec(vs)
    if isinstance(vs, m3.FuncColSpecArray):
        # The grammar carries one leading `~` before the bracket; each inner spec
        # is the un-`~`'d `name:{lambda}` form (`~[ oneColSpec, oneColSpec ]`).
        return "~[" + ", ".join(_func_col_spec(s) for s in vs.funcSpecs) + "]"
    if isinstance(vs, m3.AggColSpec):
        # An aggregation column spec `~name:{map}:{agg}` (the bracketless form);
        # each lambda reuses the `LambdaFunction` emit above.
        return "~" + _agg_col_spec(vs)
    if isinstance(vs, m3.AggColSpecArray):
        # One leading `~` before the bracket; each inner spec is the un-`~`'d
        # `name:{map}:{agg}` form (`~[ oneColSpec, oneColSpec ]`).
        return "~[" + ", ".join(_agg_col_spec(s) for s in vs.aggSpecs) + "]"
    if isinstance(vs, m3.InstanceValue):
        if _is_tds(vs):  # a `#TDS{...}#` literal: emit its text verbatim
            return vs.values[0]
        if not vs.values:
            return "[]"
        if len(vs.values) == 1 and not _is_node(vs.values[0]):
            return _literal(vs.values[0])
        # A collection `[a, b, c]`: each element is either a scalar literal or a
        # nested expression node (e.g. `[~a->ascending(), ~b->descending()]` for a
        # `sort`). `array(...)` builds these; `pure_expr` lowers them back.
        return "[" + ", ".join(_collection_element(v) for v in vs.values) + "]"
    raise TypeError(f"cannot emit value specification {vs!r}")


def _is_node(value: object) -> bool:
    """A collection element that is itself an expression node, not a raw scalar.

    Mirrors :data:`pure_python.compile.expressions._PASSTHROUGH_NODES` plus the
    ``ValueSpecification`` base -- the set of things ``array`` / ``coerce`` keep as
    nodes (so each is emitted via :func:`_expression`, not :func:`_literal`)."""
    return isinstance(
        value,
        (
            m3.ValueSpecification,
            m3.LambdaFunction,
            m3.ColSpec,
            m3.ColSpecArray,
            m3.FuncColSpec,
            m3.FuncColSpecArray,
            m3.AggColSpec,
            m3.AggColSpecArray,
        ),
    )


def _collection_element(value: object) -> str:
    """Emit one ``[...]`` element: a nested node via :func:`_expression`, else a literal."""
    return _expression(value) if _is_node(value) else _literal(value)


def _function_body(fd: m3.FunctionDefinition) -> str:
    """Render an ``expressionSequence``: each statement terminated with ``;``.

    Pure's ``codeBlock`` requires a trailing ``;`` after each non-final line, and
    a single trailing ``;`` is harmless, so terminate every statement -- the
    captured text then re-parses (see :func:`pure_expr.parse_expression`).
    """
    return " ".join(f"{_expression(vs)};" for vs in fd.expressionSequence)


def _qualified_name(element: object) -> str:
    package = getattr(element, "package", None)
    return f"{package}::{element.name}" if package else element.name


def _property(prop: m3.Property) -> str:
    annotations = f"{_stereotypes(prop)}{_tagged_values(prop)}"
    return f"    {annotations}{prop.name} : {_type(prop.genericType)}{_multiplicity(prop.multiplicity)};"


def _qualified_property(qp: m3.QualifiedProperty) -> str:
    # `[]` is a syntactically valid placeholder body for a signature-only
    # qualified property (real Pure grammars reject an empty `{}` body); a
    # modelled `expressionSequence` is emitted in arrow form instead.
    body = _function_body(qp) if qp.expressionSequence else "[]"
    return f"    {qp.name}() {{ {body} }} : {_type(qp.genericType)}{_multiplicity(qp.multiplicity)};"


def _generalization_names(cls: m3.Class) -> list[str]:
    names: list[str] = []
    for generalization in cls.generalizations:
        general = getattr(generalization, "general", None)
        raw = getattr(general, "rawType", None)
        if raw is not None and getattr(raw, "name", None):
            names.append(_qualified_name(raw))
    return names


def to_pure(cls: m3.Class) -> str:
    """Render a single ``Class`` declaration as Pure source."""
    params = ""
    if cls.typeParameters:
        params = "<" + ", ".join(tp.name for tp in cls.typeParameters) + ">"
    bases = _generalization_names(cls)
    extends = f" extends {', '.join(bases)}" if bases else ""
    header = f"Class {_stereotypes(cls)}{_qualified_name(cls)}{params}{extends}"
    body = [_property(p) for p in cls.properties]
    body += [_qualified_property(qp) for qp in cls.qualifiedProperties]
    if body:
        return header + "\n{\n" + "\n".join(body) + "\n}"
    return header + "\n{\n}"


def _association(assoc: m3.Association) -> str:
    body = "\n".join(_property(p) for p in assoc.properties)
    return f"Association {_qualified_name(assoc)}\n{{\n{body}\n}}"


def _enum(enumeration: m3.Enumeration) -> str:
    values = ", ".join(v.name for v in enumeration.values)
    return f"Enum {_qualified_name(enumeration)}\n{{\n    {values}\n}}"


def _profiles_from(classes: list[m3.Class]) -> list[str]:
    """Reconstruct minimal Profile declarations for stereotypes / tags in use."""
    stereotypes: dict[str, list[str]] = {}
    tags: dict[str, list[str]] = {}

    def add(table: dict[str, list[str]], profile: str, value: str) -> None:
        bucket = table.setdefault(profile, [])
        if value not in bucket:
            bucket.append(value)

    for cls in classes:
        for prop in cls.properties:
            for stereotype in getattr(prop, "stereotypes", []) or []:
                add(stereotypes, stereotype.profile.name, stereotype.value)
            for tagged in getattr(prop, "taggedValues", []) or []:
                add(tags, tagged.tag.profile.name, tagged.tag.value)

    blocks: list[str] = []
    for profile in sorted(set(stereotypes) | set(tags)):
        lines = [f"Profile {profile}", "{"]
        if stereotypes.get(profile):
            lines.append(f"    stereotypes: [{', '.join(stereotypes[profile])}];")
        if tags.get(profile):
            lines.append(f"    tags: [{', '.join(tags[profile])}];")
        lines.append("}")
        blocks.append("\n".join(lines))
    return blocks


def _collect(roots: tuple[m3.Type, ...]):
    classes: list[m3.Class] = []
    enums: list[m3.Enumeration] = []
    associations: list[m3.Association] = []
    seen: set[int] = set()
    stack: list[m3.Type] = list(roots)
    while stack:
        node = stack.pop(0)
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, m3.Association):
            associations.append(node)
            for prop in node.properties:
                stack.extend(_referenced(prop.genericType))
        elif isinstance(node, m3.Enumeration):
            enums.append(node)
        elif isinstance(node, m3.Class):
            classes.append(node)
            for prop in node.properties:
                stack.extend(_referenced(prop.genericType))
            for qp in node.qualifiedProperties:
                stack.extend(_referenced(qp.genericType))
            for generalization in node.generalizations:
                raw = generalization.general.rawType if generalization.general else None
                if isinstance(raw, m3.Class):
                    stack.append(raw)
    return classes, enums, associations


def _referenced(generic: m3.GenericType | None) -> list[m3.Type]:
    if generic is None:
        return []
    found: list[m3.Type] = []
    if isinstance(generic.rawType, (m3.Class, m3.Enumeration)):
        found.append(generic.rawType)
    for arg in generic.typeArguments:
        found.extend(_referenced(arg))
    return found


def to_pure_module(*roots: m3.Type) -> str:
    """Render Pure source for the given elements, their dependencies and profiles."""
    classes, enums, associations = _collect(roots)
    blocks = _profiles_from(classes)
    blocks += [_enum(e) for e in enums]
    blocks += [to_pure(c) for c in classes]
    blocks += [_association(a) for a in associations]
    return "\n\n".join(blocks) + "\n"
