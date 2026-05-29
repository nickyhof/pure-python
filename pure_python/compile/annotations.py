"""User-facing markers for attaching Pure stereotypes and tagged values.

Use them inside ``typing.Annotated`` on dataclass fields::

    firstName: Annotated[str, Stereotype("pii", "sensitive")]
    ssn: Annotated[str, Tag("doc", "description", "social security number")]

``python_to_m3`` reads these onto the resulting ``m3.Property``; ``m3_to_python``
renders them back. Their ``repr`` is valid Python that reconstructs them, which
is how the emitter writes them out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .expressions import Expr

# Convention for carrying a Python enum member's value (when it differs from the
# member name) across the name-only Pure enumeration model, as a tagged value.
ENUM_VALUE_PROFILE = "pure_python"
ENUM_VALUE_TAG = "enumValue"


@dataclass(frozen=True)
class Stereotype:
    """A Pure stereotype reference, i.e. ``<<profile.value>>``."""

    profile: str
    value: str


@dataclass(frozen=True)
class Tag:
    """A Pure tagged value, i.e. ``{profile.name = value}``."""

    profile: str
    name: str
    value: str


@dataclass(frozen=True)
class Body:
    """A derived-property body, authored as a DSL function.

    Annotate the ``@property`` return type to attach an expression body::

        @property
        def full_name(self) -> Annotated[str, Body(lambda this: this.first + this.last)]:
            ...

    ``python_to_m3`` reads the function's arity: a one-parameter function is
    called with ``Expr(var('this'))`` (the conventional ``$this``), a
    zero-parameter function with no arguments. The resulting node (an ``Expr`` or
    a raw ``m3`` node) becomes the qualified property's ``expressionSequence``.
    The getter body is never executed.
    """

    fn: "Callable[..., Expr | object]"
