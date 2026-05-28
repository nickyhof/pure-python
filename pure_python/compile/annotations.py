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
