"""Lift the Pure M3 bootstrap instance syntax (``m3.pure``) into an object graph.

``m3.pure`` is plain instance data -- ``^`` instance markers, brace bodies,
bracket collections / path keys, ``@`` package clauses, string / number
literals and dotted reference paths::

    ^Package meta @Root.children
    {
        Root.children[meta]...properties[name] : 'meta',
        Package.properties[children] : []
    }

Rather than hand-roll a tokenizer + parser, this walks the parse tree produced
by legend-pure's own **M4** ANTLR grammar (``M4AntlrParser`` / ``M4AntlrLexer``,
vendored and generated for the ANTLR Python target in
:mod:`pure_python.codegen._pure_antlr`) and lowers it into the ``Instance`` /
``Ref`` / ``Path`` graph that :mod:`pure_python.codegen.schema` consumes. The
semantic interpretation -- in particular ``_split_lhs``, which reads the
``<owner>.properties[<prop>]`` assignment paths -- is unchanged.

Every metaclass in ``m3.pure`` is an ``instance`` whose classifier path ends in
``[Class]`` / ``[Enumeration]`` / ``[PrimitiveType]`` etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener

from ._pure_antlr.M4AntlrLexer import M4AntlrLexer
from ._pure_antlr.M4AntlrParser import M4AntlrParser


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


def _split_lhs(lhs: Path) -> tuple[str | None, str]:
    """Read ``<owner>.properties[<prop>]`` into (owner type, property name)."""
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


# -- ANTLR parse tree -> object graph --------------------------------------

def _unescape(inner: str) -> str:
    """Drop backslash escapes (``\\'`` -> ``'``), matching the original lexer."""
    out: list[str] = []
    i = 0
    while i < len(inner):
        if inner[i] == "\\" and i + 1 < len(inner):
            out.append(inner[i + 1])
            i += 2
        else:
            out.append(inner[i])
            i += 1
    return "".join(out)


def _path(ctx) -> Path:
    """Build a Path from a ``path`` / ``instance`` / ``nameSpace`` context."""
    steps: list[tuple[str, str | None]] = [(ctx.name().getText(), None)]
    for owner in ctx.classifierOwner():
        in_array = owner.keyInArray()
        steps.append((owner.key().getText(), in_array.getText() if in_array is not None else None))
    return Path(steps)


def _literal(lit) -> Value:
    if lit.STRING() is not None:
        return _unescape(lit.STRING().getText()[1:-1])
    if lit.INTEGER() is not None:
        return int(lit.INTEGER().getText())
    if lit.FLOAT() is not None:
        return float(lit.FLOAT().getText())
    if lit.BOOLEAN() is not None:
        return lit.BOOLEAN().getText() == "true"
    return lit.getText()  # DATE / STRICTTIME -- not used by m3.pure


def _element(element) -> Value:
    if element.metaClass() is not None:
        return _instance(element.metaClass())
    if element.literalElement() is not None:
        return _literal(element.literalElement())
    if element.instance() is not None:  # a bare reference path
        return Ref(_path(element.instance()))
    return Ref(Path([("self", None)]))  # SELF -- not used by m3.pure


def _right_side(rs) -> Value:
    if rs.BRACKET_OPEN() is not None:  # a collection, possibly empty
        return [_element(e) for e in rs.element()]
    return _element(rs.element(0))


def _instance(mc) -> Instance:
    classifier = _path(mc.instance())
    name = mc.newTypeStr().getText() if mc.newTypeStr() is not None else None
    package = _path(mc.nameSpace()) if mc.nameSpace() is not None else None
    body: list[Assignment] = []
    for prop in mc.properties():
        owner, name_ = _split_lhs(_path(prop.path()))
        body.append(Assignment(owner, name_, _right_side(prop.rightSide())))
    return Instance(classifier, name, package, body)


class _Raise(ErrorListener):
    def syntaxError(self, recognizer, symbol, line, column, message, error):
        raise SyntaxError(f"line {line}:{column} {message}")


def parse(text: str) -> list[Instance]:
    parser = M4AntlrParser(CommonTokenStream(M4AntlrLexer(InputStream(text))))
    parser.removeErrorListeners()
    parser.addErrorListener(_Raise())
    return [_instance(mc) for mc in parser.definition().metaClass()]
