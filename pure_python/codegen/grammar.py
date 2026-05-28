"""Parser for the ordinary readable Pure class grammar.

The bootstrap ``m3.pure`` defines the core metamodel in instance syntax, but
further platform types (``relation.pure``, ``variant.pure``,
``milestoning.pure``) use Pure's normal grammar::

    Class meta::pure::metamodel::relation::FuncColSpec<Z, T> extends Base
    {
        name : String[1];
        function : Function<Z>[1];
    }

This module parses ``Class`` / ``Association`` / ``Enum`` / ``Profile``
declarations into the same :mod:`pure_python.codegen.schema` dataclasses the
bootstrap parser produces, so both feed one merged metamodel. Qualified
(derived) properties are captured by signature (their parameters and lambda
body are not modelled); ``import`` statements and function definitions are
skipped -- they are not types.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schema import MetaAssociation, MetaClass, MetaEnum, MetaProfile, MetaProperty, TypeRef

_TOKEN_RE = re.compile(
    r"""
      (?P<COMMENT>//[^\n]*)
    | (?P<WS>\s+)
    | (?P<STRING>'(?:\\.|[^'])*')
    | (?P<DCOLON>::)
    | (?P<DOTDOT>\.\.)
    | (?P<LSTER><<)
    | (?P<RSTER>>>)
    | (?P<ARROW>->)
    | (?P<NUMBER>\d+)
    | (?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<OP>[{}\[\]<>:;,=()*.@|])
    """,
    re.VERBOSE,
)


@dataclass
class _Tok:
    kind: str
    value: str


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


def _tokenize(text: str) -> list[_Tok]:
    tokens: list[_Tok] = []
    for m in _TOKEN_RE.finditer(text):
        kind = m.lastgroup
        if kind in ("WS", "COMMENT"):
            continue
        tokens.append(_Tok(kind, m.group()))
    tokens.append(_Tok("EOF", ""))
    return tokens


class _GrammarParser:
    def __init__(self, tokens: list[_Tok]):
        self.toks = tokens
        self.pos = 0

    def _peek(self) -> _Tok:
        return self.toks[self.pos]

    def _next(self) -> _Tok:
        tok = self.toks[self.pos]
        self.pos += 1
        return tok

    def _expect(self, value: str) -> _Tok:
        tok = self._next()
        if tok.value != value:
            raise SyntaxError(f"expected {value!r} but got {tok.value!r}")
        return tok

    def parse(self) -> GrammarResult:
        result = GrammarResult()
        while self._peek().kind != "EOF":
            keyword = self._peek().value
            if keyword == "import":
                self._skip_to_semicolon()
            elif keyword in ("native", "function"):
                self._skip_to_semicolon()
            elif keyword == "Class":
                result.classes.append(self._parse_class())
            elif keyword in ("Enum", "Enumeration"):
                result.enums.append(self._parse_enum())
            elif keyword == "Profile":
                result.profiles.append(self._parse_profile())
            elif keyword == "Association":
                result.associations.append(self._parse_association())
            else:
                self._next()  # be forgiving about anything we do not model yet
        return result

    # -- helpers -------------------------------------------------------
    def _skip_to_semicolon(self) -> None:
        depth = 0
        while True:
            tok = self._next()
            if tok.kind == "EOF":
                return
            if tok.value in ("{", "[", "("):
                depth += 1
            elif tok.value in ("}", "]", ")"):
                depth -= 1
            elif tok.value == ";" and depth <= 0:
                return

    def _skip_stereotypes(self) -> None:
        if self._peek().kind == "LSTER":
            self._next()
            while self._peek().kind not in ("RSTER", "EOF"):
                self._next()
            self._next()  # '>>'

    def _skip_tagged_values(self) -> None:
        """Skip an optional ``{ profile.tag = 'value', ... }`` annotation block."""
        if self._peek().value != "{":
            return
        self._next()
        depth = 1
        while depth > 0 and self._peek().kind != "EOF":
            tok = self._next()
            if tok.value == "{":
                depth += 1
            elif tok.value == "}":
                depth -= 1

    def _qualified_name(self) -> tuple[str, str]:
        """Return (package, simple name)."""
        parts = [self._next().value]
        while self._peek().kind == "DCOLON":
            self._next()
            parts.append(self._next().value)
        return "::".join(parts[:-1]), parts[-1]

    def _consume_gt(self) -> None:
        """Consume one ``>``, splitting a ``>>`` token so the enclosing level sees the other."""
        tok = self._peek()
        if tok.value == ">":
            self._next()
        elif tok.kind == "RSTER":
            tok.kind = "OP"
            tok.value = ">"
        else:
            raise SyntaxError(f"expected '>' but got {tok.value!r}")

    def _type_parameters(self) -> list[str]:
        if self._peek().value != "<":
            return []
        self._next()
        params: list[str] = []
        while self._peek().value not in (">", "") and self._peek().kind != "RSTER":
            tok = self._next()
            if tok.kind == "IDENT":
                params.append(tok.value)
        self._next()  # '>'
        return params

    def _type_ref(self) -> TypeRef:
        """Parse a type reference (simple name + nested type arguments)."""
        _, simple = self._qualified_name()
        arguments: list[TypeRef] = []
        if self._peek().value == "<":
            self._next()
            arguments.append(self._type_ref())
            while self._peek().value == ",":
                self._next()
                arguments.append(self._type_ref())
            self._consume_gt()
        return TypeRef(simple, False, arguments)

    def _multiplicity(self) -> tuple[int, int | None]:
        self._expect("[")
        if self._peek().value == "*":
            self._next()
            self._expect("]")
            return 0, None
        lower = int(self._next().value)
        upper: int | None = lower
        if self._peek().kind == "DOTDOT":
            self._next()
            if self._peek().value == "*":
                self._next()
                upper = None
            else:
                upper = int(self._next().value)
        self._expect("]")
        return lower, upper

    # -- declarations --------------------------------------------------
    def _parse_class(self) -> MetaClass:
        self._next()  # 'Class' / 'Association'
        self._skip_stereotypes()
        self._skip_tagged_values()
        package, name = self._qualified_name()
        type_parameters = self._type_parameters()
        bases: list[str] = []
        if self._peek().value == "extends":
            self._next()
            bases.append(self._type_ref().name)
            while self._peek().value == ",":
                self._next()
                bases.append(self._type_ref().name)
        if self._peek().value == "[":  # constraints block
            self._skip_to_matching_bracket()
        properties, qualified = self._parse_body()
        for prop in properties + qualified:
            if prop.type_name in type_parameters:
                prop.is_type_parameter = True
            for arg in prop.type_arguments:
                _mark_type_parameters(arg, type_parameters)
        return MetaClass(
            name, package, bases or ["Any"], properties, type_parameters, qualified_properties=qualified
        )

    def _parse_association(self) -> MetaAssociation:
        self._next()  # 'Association'
        self._skip_stereotypes()
        self._skip_tagged_values()
        package, name = self._qualified_name()
        if self._peek().value == "[":
            self._skip_to_matching_bracket()
        properties, _ = self._parse_body()  # the two ends; qualified properties n/a
        return MetaAssociation(name, package, properties)

    def _parse_body(self) -> tuple[list[MetaProperty], list[MetaProperty]]:
        """Parse a ``{ ... }`` body, returning (simple properties, qualified properties)."""
        self._expect("{")
        simple: list[MetaProperty] = []
        qualified: list[MetaProperty] = []
        while self._peek().value != "}" and self._peek().kind != "EOF":
            is_qualified, prop = self._parse_property()
            (qualified if is_qualified else simple).append(prop)
        self._expect("}")
        return simple, qualified

    def _skip_to_matching_bracket(self) -> None:
        self._skip_balanced("[", "]")

    def _skip_balanced(self, open_token: str, close_token: str) -> None:
        self._expect(open_token)
        depth = 1
        while depth > 0 and self._peek().kind != "EOF":
            tok = self._next()
            if tok.value == open_token:
                depth += 1
            elif tok.value == close_token:
                depth -= 1

    def _parse_property(self) -> tuple[bool, MetaProperty]:
        """Return (is_qualified, property). Qualified properties keep only their signature."""
        self._skip_stereotypes()
        self._skip_tagged_values()
        name = self._next().value
        qualified = self._peek().value == "("
        if qualified:
            self._skip_balanced("(", ")")  # parameters -- not modelled
            if self._peek().value == "{":
                self._skip_balanced("{", "}")  # lambda body -- not modelled
        self._expect(":")
        ref = self._type_ref()
        lower, upper = self._multiplicity()
        if self._peek().value == ";":
            self._next()
        return qualified, MetaProperty(name, ref.name, lower, upper, type_arguments=ref.arguments)

    def _parse_enum(self) -> MetaEnum:
        self._next()  # 'Enum'
        self._skip_stereotypes()
        package, name = self._qualified_name()
        self._expect("{")
        values: list[str] = []
        while self._peek().value != "}" and self._peek().kind != "EOF":
            self._skip_stereotypes()
            tok = self._next()
            if tok.kind == "IDENT":
                values.append(tok.value)
            if self._peek().value == ",":
                self._next()
        self._expect("}")
        return MetaEnum(name, package, values)

    def _parse_profile(self) -> MetaProfile:
        self._next()  # 'Profile'
        package, name = self._qualified_name()
        self._expect("{")
        stereotypes: list[str] = []
        tags: list[str] = []
        while self._peek().value != "}" and self._peek().kind != "EOF":
            key = self._next().value
            self._expect(":")
            self._expect("[")
            items: list[str] = []
            while self._peek().value != "]" and self._peek().kind != "EOF":
                tok = self._next()
                if tok.kind in ("IDENT", "STRING"):
                    items.append(tok.value.strip("'"))
            self._expect("]")
            if self._peek().value == ";":
                self._next()
            if key == "stereotypes":
                stereotypes = items
            elif key == "tags":
                tags = items
        self._expect("}")
        return MetaProfile(name, package, stereotypes, tags)


def parse_grammar(text: str) -> GrammarResult:
    return _GrammarParser(_tokenize(text)).parse()
