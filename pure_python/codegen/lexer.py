"""Tokenizer for the Pure M3 bootstrap instance syntax (``m3.pure``).

The bootstrap file is plain instance data: ``^`` instance markers, brace
bodies, bracket collections / path keys, ``@`` package clauses, string and
integer literals, dotted reference paths, and ``//`` line comments. There are
no lambdas, operators, or block comments, so a flat token stream is enough.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class Kind(Enum):
    CARET = auto()
    LBRACE = auto()
    RBRACE = auto()
    LBRACK = auto()
    RBRACK = auto()
    AT = auto()
    COLON = auto()
    COMMA = auto()
    DOT = auto()
    STRING = auto()
    NUMBER = auto()
    IDENT = auto()
    EOF = auto()


_PUNCT = {
    "^": Kind.CARET,
    "{": Kind.LBRACE,
    "}": Kind.RBRACE,
    "[": Kind.LBRACK,
    "]": Kind.RBRACK,
    "@": Kind.AT,
    ":": Kind.COLON,
    ",": Kind.COMMA,
    ".": Kind.DOT,
}


@dataclass(frozen=True)
class Token:
    kind: Kind
    value: str
    line: int


def _is_ident_start(ch: str) -> bool:
    return ch.isalpha() or ch == "_"


def _is_ident_part(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    line = 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch.isspace():
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "'":
            i += 1
            chars: list[str] = []
            while i < n and text[i] != "'":
                if text[i] == "\\" and i + 1 < n:
                    chars.append(text[i + 1])
                    i += 2
                    continue
                if text[i] == "\n":
                    line += 1
                chars.append(text[i])
                i += 1
            i += 1  # closing quote
            tokens.append(Token(Kind.STRING, "".join(chars), line))
            continue
        if ch.isdigit() or (ch == "-" and i + 1 < n and text[i + 1].isdigit()):
            start = i
            i += 1
            while i < n and (text[i].isdigit() or text[i] == "."):
                i += 1
            tokens.append(Token(Kind.NUMBER, text[start:i], line))
            continue
        if _is_ident_start(ch):
            start = i
            i += 1
            while i < n and _is_ident_part(text[i]):
                i += 1
            tokens.append(Token(Kind.IDENT, text[start:i], line))
            continue
        if ch in _PUNCT:
            tokens.append(Token(_PUNCT[ch], ch, line))
            i += 1
            continue
        raise SyntaxError(f"Unexpected character {ch!r} at line {line}")
    tokens.append(Token(Kind.EOF, "", line))
    return tokens
