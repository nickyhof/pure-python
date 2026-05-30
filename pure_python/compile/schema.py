"""A typed-schema layer for the :class:`Frame` query builder (pylegend-aligned).

A pylegend-style ``Schema`` / ``Column`` pair the :class:`~pure_python.compile.Frame`
sugar layer carries alongside a relation node. Purely *additive*: a ``Frame``
without a schema behaves byte-identically to before; with a schema attached the
verb wrappers do *offline* column-name validation (raising :class:`SchemaError`)
and propagate the schema through column-mechanical verbs (``select`` / ``drop`` /
``rename`` / ``concatenate`` / pass-throughs). Verbs whose output schema cannot be
inferred from inputs alone (``extend`` / ``window_extend`` / ``group_by`` /
``pivot`` / ``join`` / ``as_of_join``) accept an explicit ``out_schema=``; absent
that, the downstream schema is dropped (``None``) and validation stops -- never
guessing.

The schema is **NOT** part of the ``m3`` type system: it does not flow into the
emitted relation node and does not change ``.to_m3()`` / ``.to_pure()`` output.
Column types are Pure primitive *singletons* (``m3.String`` / ``m3.Integer`` /
...); Python builtins (``str`` / ``int`` / ...) are coerced to those singletons
via the EXACT mapping :mod:`pure_python.compile.python_to_m3` already uses for
dataclass-field type hints (no second mapping).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from pure_python import m3

from .python_to_m3 import _PRIMITIVE


def _coerce_primitive(t: object) -> m3.PrimitiveType:
    """Accept a Pure primitive singleton OR a Python builtin; return the singleton.

    Reuses the EXACT ``_PRIMITIVE`` mapping :mod:`python_to_m3` uses for
    dataclass-field type hints (``str`` -> ``String``, ``int`` -> ``Integer``,
    ``bool`` -> ``Boolean``, ``float`` -> ``Float``, ``Decimal`` -> ``Decimal``,
    ``bytes`` -> ``Byte``, ``date`` -> ``StrictDate``, ``datetime`` -> ``DateTime``,
    ``time`` -> ``StrictTime``).
    """
    if isinstance(t, m3.PrimitiveType):
        return t
    if isinstance(t, type) and t in _PRIMITIVE:
        return _PRIMITIVE[t]
    raise TypeError(
        f"column type must be an m3.PrimitiveType singleton or a supported Python "
        f"builtin ({sorted(p.__name__ for p in _PRIMITIVE)}); got {t!r}"
    )


class SchemaError(ValueError):
    """Raised when a verb references a column not in the input schema, or two
    join inputs collide on a column name. A :class:`ValueError` subclass so the
    existing ``raises(ValueError)`` checks keep working.
    """


@dataclass(frozen=True)
class Column:
    """A typed column: a string ``name`` and an ``m3.PrimitiveType`` singleton.

    Construct via the generic ``Column("amt", int)`` / ``Column("amt", m3.Integer)``
    (the type may be a Pure singleton or a Python builtin -- coerced to the
    singleton in ``__post_init__``) or one of the pylegend-style snake_case
    factories mirroring Pure's primitive spellings:

    * :meth:`string` -> ``m3.String``
    * :meth:`integer` -> ``m3.Integer``
    * :meth:`float_` -> ``m3.Float`` (trailing ``_`` since ``float`` would shadow
      the builtin inside the class body)
    * :meth:`boolean` -> ``m3.Boolean``
    * :meth:`decimal` -> ``m3.Decimal``
    * :meth:`byte` -> ``m3.Byte``
    * :meth:`strict_date` -> ``m3.StrictDate``
    * :meth:`date_time` -> ``m3.DateTime``
    * :meth:`strict_time` -> ``m3.StrictTime``
    """

    name: str
    type: m3.PrimitiveType

    def __post_init__(self) -> None:
        # Frozen dataclass: use ``object.__setattr__`` to write the coerced value.
        object.__setattr__(self, "type", _coerce_primitive(self.type))
        if not isinstance(self.name, str) or not self.name:
            raise ValueError(f"column name must be a non-empty string; got {self.name!r}")

    @classmethod
    def string(cls, name: str) -> "Column":
        return cls(name, m3.String)

    @classmethod
    def integer(cls, name: str) -> "Column":
        return cls(name, m3.Integer)

    @classmethod
    def float_(cls, name: str) -> "Column":
        return cls(name, m3.Float)

    @classmethod
    def boolean(cls, name: str) -> "Column":
        return cls(name, m3.Boolean)

    @classmethod
    def decimal(cls, name: str) -> "Column":
        return cls(name, m3.Decimal)

    @classmethod
    def byte(cls, name: str) -> "Column":
        return cls(name, m3.Byte)

    @classmethod
    def strict_date(cls, name: str) -> "Column":
        return cls(name, m3.StrictDate)

    @classmethod
    def date_time(cls, name: str) -> "Column":
        return cls(name, m3.DateTime)

    @classmethod
    def strict_time(cls, name: str) -> "Column":
        return cls(name, m3.StrictTime)


@dataclass(frozen=True)
class Schema:
    """An ordered tuple of :class:`Column`s -- a typed relation header.

    Construct via:

    * positional :meth:`from_columns` -- ``Schema.from_columns(Column.integer("id"),
      Column.string("name"))``;
    * kwargs :meth:`of` -- ``Schema.of(id=int, name=str, ship_date=date)`` (the
      pylegend-aligned shorthand; the type values are coerced to singletons);
    * the raw ``Schema(columns=(...))`` constructor when assembling
      programmatically.

    Column names must be unique (in-schema collisions are rejected on construction).
    """

    columns: tuple[Column, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # Allow a list / iterable for ergonomics; freeze to a tuple.
        if not isinstance(self.columns, tuple):
            object.__setattr__(self, "columns", tuple(self.columns))
        names = [c.name for c in self.columns]
        if len(set(names)) != len(names):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise SchemaError(
                f"duplicate column names in schema: {dupes} (columns={names})"
            )
        for c in self.columns:
            if not isinstance(c, Column):
                raise TypeError(
                    f"Schema.columns entries must be Column instances; got {c!r}"
                )

    def names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    def has(self, name: str) -> bool:
        return any(c.name == name for c in self.columns)

    def of_name(self, name: str) -> Column:
        """The :class:`Column` named ``name``; raises :class:`SchemaError` if missing."""
        for c in self.columns:
            if c.name == name:
                return c
        raise SchemaError(
            f"column {name!r} not in schema (available={list(self.names())})"
        )

    def __iter__(self) -> Iterator[Column]:
        return iter(self.columns)

    def __len__(self) -> int:
        return len(self.columns)

    def __getitem__(self, key: int | str) -> Column:
        if isinstance(key, int):
            return self.columns[key]
        if isinstance(key, str):
            return self.of_name(key)
        raise TypeError(f"Schema index must be int or str; got {key!r}")

    @classmethod
    def from_columns(cls, *columns: Column) -> "Schema":
        """Build a :class:`Schema` from positional :class:`Column`s."""
        return cls(columns=tuple(columns))

    @classmethod
    def of(cls, **typed: object) -> "Schema":
        """Build a :class:`Schema` from ``name=type`` kwargs (pylegend-aligned).

        Each value is a Pure primitive singleton OR a Python builtin (coerced via
        :meth:`Column.__post_init__`)::

            Schema.of(cust=str, amt=int, ship_date=date)

        Insertion order is preserved (Python 3.7+ keyword-arg semantics).
        """
        return cls(columns=tuple(Column(name, t) for name, t in typed.items()))


__all__ = ["Column", "Schema", "SchemaError"]
