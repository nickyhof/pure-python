"""The Pure M3 core type system as Python dataclasses.

The contents are generated from ``vendor/legend-pure/m3.pure`` by
``pure_python.codegen``. Regenerate with::

    python -m pure_python.codegen.generate
"""

from __future__ import annotations

from . import metamodel as metamodel
from .metamodel import *  # noqa: F401,F403
from .metamodel import __all__  # noqa: F401
