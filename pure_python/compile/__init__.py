"""Bidirectional bridge between plain Python dataclasses and the Pure M3 metamodel.

``python_to_m3`` turns dataclasses/enums into ``m3`` instances;
``m3_to_python`` renders ``m3`` instances back into dataclass source.
"""

from __future__ import annotations

from .m3_to_python import to_module, to_source
from .python_to_m3 import Compiler, compile_class, compile_enumeration

__all__ = [
    "Compiler",
    "compile_class",
    "compile_enumeration",
    "to_module",
    "to_source",
]
