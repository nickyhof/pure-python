"""Parse the readable Pure grammar using legend-pure's own ANTLR grammar.

The bootstrap ``m3.pure`` defines the core metamodel in instance syntax (parsed
by :mod:`pure_python.codegen.parser`), but the further platform types
(``relation.pure``, ``variant.pure``, ``milestoning.pure``) -- and any Pure that
``compile.m3_to_pure`` emits -- use Pure's normal grammar::

    Class meta::pure::metamodel::relation::FuncColSpec<Z, T> extends Base
    {
        name : String[1];
        function : Function<Z>[1];
    }

Rather than hand-roll a parser, this walks the parse tree produced by
legend-pure's *real* ``M3CoreParser`` grammar (vendored and generated for the
ANTLR Python target in :mod:`pure_python.codegen._pure_antlr`) and lowers the
``Class`` / ``Association`` / ``Enum`` / ``Profile`` declarations into the
:mod:`pure_python.codegen.schema` dataclasses. ``import`` statements, functions,
primitives, measures and bare instances are ignored -- they are not class-like
types. Type parameters, type arguments, qualified (derived) properties (by
signature), and property-level stereotypes / tagged values are all captured.

Because it is the real grammar it is strict: e.g. an empty qualified-property
body (``foo() {}``) is a syntax error -- ``m3_to_pure`` emits ``foo() { [] }``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener

from ._pure_antlr.M3CoreLexer import M3CoreLexer
from ._pure_antlr.M3CoreParser import M3CoreParser
from .schema import MetaAssociation, MetaClass, MetaEnum, MetaProfile, MetaProperty, TypeRef


@dataclass
class GrammarResult:
    classes: list[MetaClass] = field(default_factory=list)
    enums: list[MetaEnum] = field(default_factory=list)
    profiles: list[MetaProfile] = field(default_factory=list)
    associations: list[MetaAssociation] = field(default_factory=list)


def _mark_type_parameters(ref: TypeRef, params: list[str]) -> None:
    if ref.name in params:
        ref.is_type_parameter = True
    for arg in ref.arguments:
        _mark_type_parameters(arg, params)


class _Raise(ErrorListener):
    def syntaxError(self, recognizer, symbol, line, column, message, error):
        raise SyntaxError(f"line {line}:{column} {message}")


# -- parse-tree -> schema --------------------------------------------------

def _qualified(ctx) -> tuple[str, str]:
    """Return (package, simple name) from a ``qualifiedName`` context."""
    package = ""
    path = ctx.packagePath()
    if path is not None:
        package = "::".join(segment.getText() for segment in path.identifier())
    return package, ctx.identifier().getText()


def _type_ref(tctx) -> TypeRef:
    name = tctx.qualifiedName()
    if name is None:  # a function / column / unit type -- not modelled
        return TypeRef(None)
    _, simple = _qualified(name)
    arguments: list[TypeRef] = []
    type_arguments = tctx.typeArguments()
    if type_arguments is not None:
        arguments = [_type_ref(op.type_()) for op in type_arguments.typeWithOperation()]
    return TypeRef(simple, False, arguments)


def _multiplicity(mctx) -> tuple[int, int | None]:
    argument = mctx.multiplicityArgument()
    if argument.identifier() is not None:  # a multiplicity parameter, e.g. [k]
        return (0, None)
    to_text = argument.toMultiplicity().getText()
    upper = None if to_text == "*" else int(to_text)
    lower_ctx = argument.fromMultiplicity()
    lower = int(lower_ctx.getText()) if lower_ctx is not None else (0 if to_text == "*" else upper)
    return (lower, upper)


def _stereotypes(sctx) -> list[tuple[str, str]]:
    if sctx is None:
        return []
    return [(s.qualifiedName().getText(), s.identifier().getText()) for s in sctx.stereotype()]


def _tagged_values(tctx) -> list[tuple[str, str, str]]:
    if tctx is None:
        return []
    out: list[tuple[str, str, str]] = []
    for tagged in tctx.taggedValue():
        value = "".join(s.getText()[1:-1] for s in tagged.STRING())
        out.append((tagged.qualifiedName().getText(), tagged.identifier().getText(), value))
    return out


def _property_name(pn) -> str:
    return pn.STRING().getText()[1:-1] if pn.STRING() is not None else pn.identifier().getText()


def _return_type(rt) -> tuple[TypeRef, int, int | None]:
    ref = _type_ref(rt.type_())
    lower, upper = _multiplicity(rt.multiplicity())
    return ref, lower, upper


def _property(p) -> MetaProperty:
    ref, lower, upper = _return_type(p.propertyReturnType())
    return MetaProperty(
        _property_name(p.propertyName()), ref.name, lower, upper,
        type_arguments=ref.arguments,
        stereotypes=_stereotypes(p.stereotypes()),
        tagged_values=_tagged_values(p.taggedValues()),
    )


def _qualified_property(q) -> MetaProperty:
    ref, lower, upper = _return_type(q.propertyReturnType())
    return MetaProperty(
        q.identifier().getText(), ref.name, lower, upper,
        type_arguments=ref.arguments,
        stereotypes=_stereotypes(q.stereotypes()),
        tagged_values=_tagged_values(q.taggedValues()),
    )


def _class(c) -> MetaClass:
    package, name = _qualified(c.qualifiedName())
    type_parameters: list[str] = []
    params = c.typeParametersWithContravarianceAndMultiplicityParameters()
    if params is not None and params.contravarianceTypeParameters() is not None:
        type_parameters = [
            p.identifier().getText()
            for p in params.contravarianceTypeParameters().contravarianceTypeParameter()
        ]
    bases = [ref.name for t in c.type_() if (ref := _type_ref(t)).name]
    simple: list[MetaProperty] = []
    qualified: list[MetaProperty] = []
    body = c.classBody()
    if body is not None and body.properties() is not None:
        simple = [_property(p) for p in body.properties().property_()]
        qualified = [_qualified_property(q) for q in body.properties().qualifiedProperty()]
    for prop in simple + qualified:
        if prop.type_name in type_parameters:
            prop.is_type_parameter = True
        for arg in prop.type_arguments:
            _mark_type_parameters(arg, type_parameters)
    return MetaClass(name, package, bases or ["Any"], simple, type_parameters, qualified_properties=qualified)


def _enum(e) -> MetaEnum:
    package, name = _qualified(e.qualifiedName())
    return MetaEnum(name, package, [v.identifier().getText() for v in e.enumValue()])


def _profile(p) -> MetaProfile:
    package, name = _qualified(p.qualifiedName())
    stereotype_defs = p.stereotypeDefinitions()
    tag_defs = p.tagDefinitions()
    stereotypes = [i.getText() for i in stereotype_defs.identifier()] if stereotype_defs is not None else []
    tags = [i.getText() for i in tag_defs.identifier()] if tag_defs is not None else []
    return MetaProfile(name, package, stereotypes, tags)


def _association(a) -> MetaAssociation:
    package, name = _qualified(a.qualifiedName())
    properties: list[MetaProperty] = []
    body = a.associationBody()
    if body is not None and body.properties() is not None:
        properties = [_property(p) for p in body.properties().property_()]
    return MetaAssociation(name, package, properties)


def parse_grammar(text: str) -> GrammarResult:
    """Parse Pure grammar source into the neutral schema dataclasses."""
    parser = M3CoreParser(CommonTokenStream(M3CoreLexer(InputStream(text))))
    parser.removeErrorListeners()
    parser.addErrorListener(_Raise())
    tree = parser.definition()

    result = GrammarResult()
    result.classes = [_class(c) for c in tree.classDefinition()]
    result.enums = [_enum(e) for e in tree.enumDefinition()]
    result.profiles = [_profile(p) for p in tree.profile()]
    result.associations = [_association(a) for a in tree.association()]
    return result
