"""Bidirectional bridge between plain Python dataclasses and the Pure M3 metamodel.

``python_to_m3`` turns dataclasses/enums into ``m3`` instances;
``m3_to_python`` renders ``m3`` instances back into dataclass source.
"""

from __future__ import annotations

from .annotations import Body, Stereotype, Tag
from .expressions import (
    Expr,
    JoinKind,
    agg,
    aggs,
    array,
    asc,
    c,
    call,
    coerce,
    col,
    cols,
    desc,
    enum_ref,
    fcol,
    fcols,
    func,
    lam,
    lit,
    not_,
    prop,
    tds,
    var,
)
from .m3_to_pure import to_pure, to_pure_module
from .m3_to_python import to_module, to_source
from .pure_expr import parse_expression
from .pure_to_m3 import from_pure
from .python_to_m3 import Compiler, compile_class, compile_enumeration

__all__ = [
    "Compiler",
    "compile_class",
    "compile_enumeration",
    "to_module",
    "to_source",
    "to_pure",
    "to_pure_module",
    "from_pure",
    "parse_expression",
    "Stereotype",
    "Tag",
    "Body",
    # expression layer (builders + DSL)
    "Expr",
    "c",
    "lit",
    "var",
    "call",
    "func",
    "prop",
    "coerce",
    "not_",
    "lam",
    "tds",
    "enum_ref",
    "JoinKind",
    "col",
    "cols",
    "fcol",
    "fcols",
    "agg",
    "aggs",
    "array",
    "asc",
    "desc",
]
