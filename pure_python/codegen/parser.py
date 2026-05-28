"""Recursive-descent parser for the Pure M3 bootstrap instance syntax.

Grammar (informal)::

    file        := instance*
    instance    := '^' path IDENT? ('@' path)? body
    body        := '{' (assignment (',' assignment)*)? '}'
    assignment  := path ':' value
    value       := STRING | NUMBER | 'true' | 'false' | instance | collection | ref
    collection  := '[' (value (',' value)*)? ']'
    ref         := path
    path        := IDENT segment*
    segment     := '.' IDENT ('[' key ']')?
    key         := IDENT | NUMBER | STRING

Every metaclass in ``m3.pure`` is an ``instance`` whose ``path`` classifier
ends in ``[Class]`` / ``[Enumeration]`` / ``[PrimitiveType]`` etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from .lexer import Kind, Token, tokenize


@dataclass
class Path:
    """A dotted reference such as ``Root.children[meta].children[type].children[Class]``."""

    steps: list[tuple[str, str | None]]  # (segment name, bracket key)

    @property
    def target(self) -> str:
        """Simple name this path points at (last bracket key, else the head)."""
        name, key = self.steps[-1]
        return key if key is not None else name

    @property
    def child_keys(self) -> list[str]:
        """The ``children[...]`` chain, e.g. ['meta','pure','metamodel','type','Class']."""
        return [key for name, key in self.steps if name == "children" and key is not None]

    @property
    def qualified(self) -> str:
        keys = self.child_keys
        return "::".join(keys) if keys else self.target

    def __str__(self) -> str:
        out = self.steps[0][0]
        for name, key in self.steps[1:]:
            out += f".{name}"
            if key is not None:
                out += f"[{key}]"
        return out


@dataclass
class Ref:
    path: Path

    @property
    def target(self) -> str:
        return self.path.target


@dataclass
class Instance:
    classifier: Path
    name: str | None
    package: Path | None
    body: list["Assignment"] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return self.classifier.target

    def get(self, prop: str) -> "Value | None":
        for a in self.body:
            if a.prop == prop:
                return a.value
        return None


Value = Union[str, int, float, bool, Ref, Instance, list]


@dataclass
class Assignment:
    owner: str | None  # type that declares the property (LHS segment before `properties`)
    prop: str
    value: Value


class Parser:
    def __init__(self, tokens: list[Token]):
        self.toks = tokens
        self.pos = 0

    # -- token helpers -------------------------------------------------
    def _peek(self) -> Token:
        return self.toks[self.pos]

    def _next(self) -> Token:
        tok = self.toks[self.pos]
        self.pos += 1
        return tok

    def _expect(self, kind: Kind) -> Token:
        tok = self._next()
        if tok.kind is not kind:
            raise SyntaxError(f"Expected {kind} but got {tok.kind} ({tok.value!r}) at line {tok.line}")
        return tok

    # -- grammar -------------------------------------------------------
    def parse_file(self) -> list[Instance]:
        instances: list[Instance] = []
        while self._peek().kind is not Kind.EOF:
            instances.append(self.parse_instance())
        return instances

    def parse_instance(self) -> Instance:
        self._expect(Kind.CARET)
        classifier = self.parse_path()
        name: str | None = None
        if self._peek().kind is Kind.IDENT:
            name = self._next().value
        package: Path | None = None
        if self._peek().kind is Kind.AT:
            self._next()
            package = self.parse_path()
        body = self.parse_body()
        return Instance(classifier, name, package, body)

    def parse_body(self) -> list[Assignment]:
        self._expect(Kind.LBRACE)
        assignments: list[Assignment] = []
        if self._peek().kind is not Kind.RBRACE:
            assignments.append(self.parse_assignment())
            while self._peek().kind is Kind.COMMA:
                self._next()
                if self._peek().kind is Kind.RBRACE:  # tolerate trailing comma
                    break
                assignments.append(self.parse_assignment())
        self._expect(Kind.RBRACE)
        return assignments

    def parse_assignment(self) -> Assignment:
        lhs = self.parse_path()
        self._expect(Kind.COLON)
        value = self.parse_value()
        owner, prop = self._split_lhs(lhs)
        return Assignment(owner, prop, value)

    @staticmethod
    def _split_lhs(lhs: Path) -> tuple[str | None, str]:
        prop_idx = next(
            (i for i in range(len(lhs.steps) - 1, -1, -1) if lhs.steps[i][0] == "properties"),
            None,
        )
        if prop_idx is None:
            # e.g. AggregationKind.values[None] should not appear on an LHS, but be safe.
            name, key = lhs.steps[-1]
            return None, (key if key is not None else name)
        prop = lhs.steps[prop_idx][1] or ""
        if prop_idx == 0:
            return None, prop
        owner_name, owner_key = lhs.steps[prop_idx - 1]
        return (owner_key if owner_key is not None else owner_name), prop

    def parse_value(self) -> Value:
        tok = self._peek()
        if tok.kind is Kind.STRING:
            return self._next().value
        if tok.kind is Kind.NUMBER:
            raw = self._next().value
            return float(raw) if "." in raw else int(raw)
        if tok.kind is Kind.CARET:
            return self.parse_instance()
        if tok.kind is Kind.LBRACK:
            return self.parse_collection()
        if tok.kind is Kind.IDENT:
            if tok.value in ("true", "false"):
                self._next()
                return tok.value == "true"
            return Ref(self.parse_path())
        raise SyntaxError(f"Unexpected value token {tok.kind} ({tok.value!r}) at line {tok.line}")

    def parse_collection(self) -> list[Value]:
        self._expect(Kind.LBRACK)
        items: list[Value] = []
        if self._peek().kind is not Kind.RBRACK:
            items.append(self.parse_value())
            while self._peek().kind is Kind.COMMA:
                self._next()
                if self._peek().kind is Kind.RBRACK:
                    break
                items.append(self.parse_value())
        self._expect(Kind.RBRACK)
        return items

    def parse_path(self) -> Path:
        head = self._expect(Kind.IDENT).value
        steps: list[tuple[str, str | None]] = [(head, None)]
        while self._peek().kind is Kind.DOT:
            self._next()
            name = self._expect(Kind.IDENT).value
            key: str | None = None
            if self._peek().kind is Kind.LBRACK:
                self._next()
                ktok = self._next()
                if ktok.kind not in (Kind.IDENT, Kind.NUMBER, Kind.STRING):
                    raise SyntaxError(f"Bad path key {ktok.value!r} at line {ktok.line}")
                key = ktok.value
                self._expect(Kind.RBRACK)
            steps.append((name, key))
        return Path(steps)


def parse(text: str) -> list[Instance]:
    return Parser(tokenize(text)).parse_file()
