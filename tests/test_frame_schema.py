"""Unit tests for the typed-schema layer (``Column`` / ``Schema`` / ``SchemaError``).

These cover construction (factories, builtin coercion, kwargs ``Schema.of``),
indexing (``int`` / ``str`` / ``of_name``), validation messages, and the pinned
Python-builtin -> Pure-singleton coercion mapping (one assertion per primitive,
shared with :mod:`pure_python.compile.python_to_m3`'s ``_PRIMITIVE``). The
schema layer is purely additive on top of the :class:`Frame` query builder
(see :mod:`tests.test_frame` for the verb-wiring tests).
"""

from __future__ import annotations

import datetime
import decimal

import pytest

from pure_python import m3
from pure_python.compile import Column, Schema, SchemaError


# --- builtin -> singleton coercion mapping (pinned -------------------------

@pytest.mark.parametrize(
    "builtin,singleton",
    [
        (str, m3.String),
        (bool, m3.Boolean),
        (int, m3.Integer),
        (float, m3.Float),
        (decimal.Decimal, m3.Decimal),
        (bytes, m3.Byte),
        (datetime.date, m3.StrictDate),
        (datetime.datetime, m3.DateTime),
        (datetime.time, m3.StrictTime),
    ],
)
def test_column_coerces_builtin_to_primitive_singleton(builtin, singleton):
    c = Column("x", builtin)
    assert c.type is singleton


def test_column_accepts_a_primitive_singleton_directly():
    c = Column("x", m3.Integer)
    assert c.type is m3.Integer


def test_column_rejects_an_unknown_type():
    with pytest.raises(TypeError, match="m3.PrimitiveType"):
        Column("x", object)


def test_column_rejects_an_empty_name():
    with pytest.raises(ValueError, match="non-empty"):
        Column("", int)


# --- factories ----------------------------------------------------------

@pytest.mark.parametrize(
    "factory,singleton",
    [
        ("string", m3.String),
        ("integer", m3.Integer),
        ("float_", m3.Float),
        ("boolean", m3.Boolean),
        ("decimal", m3.Decimal),
        ("byte", m3.Byte),
        ("strict_date", m3.StrictDate),
        ("date_time", m3.DateTime),
        ("strict_time", m3.StrictTime),
    ],
)
def test_column_factories_build_the_named_primitive(factory, singleton):
    c = getattr(Column, factory)("x")
    assert c.name == "x"
    assert c.type is singleton


# --- Schema construction ------------------------------------------------

def test_schema_from_columns_preserves_order():
    s = Schema.from_columns(
        Column.string("cust"), Column.integer("amt"), Column.strict_date("ship_date")
    )
    assert s.names() == ("cust", "amt", "ship_date")
    assert len(s) == 3


def test_schema_of_kwargs_coerces_builtins_in_kwarg_order():
    s = Schema.of(cust=str, amt=int, ship_date=datetime.date)
    assert s.names() == ("cust", "amt", "ship_date")
    assert s.of_name("cust").type is m3.String
    assert s.of_name("amt").type is m3.Integer
    assert s.of_name("ship_date").type is m3.StrictDate


def test_schema_of_accepts_singletons_and_builtins_mixed():
    s = Schema.of(a=m3.String, b=int)
    assert s.of_name("a").type is m3.String
    assert s.of_name("b").type is m3.Integer


def test_schema_rejects_duplicate_column_names():
    with pytest.raises(SchemaError, match="duplicate"):
        Schema.from_columns(Column.string("a"), Column.integer("a"))


# --- indexing & has -----------------------------------------------------

def test_schema_getitem_by_int_returns_positional_column():
    s = Schema.of(cust=str, amt=int)
    assert s[0].name == "cust"
    assert s[1].name == "amt"


def test_schema_getitem_by_str_returns_named_column():
    s = Schema.of(cust=str, amt=int)
    assert s["cust"].type is m3.String
    assert s["amt"].type is m3.Integer


def test_schema_getitem_by_str_missing_raises_schema_error():
    s = Schema.of(cust=str, amt=int)
    with pytest.raises(SchemaError, match="not in schema"):
        s["ammt"]


def test_schema_of_name_missing_raises_schema_error_listing_available():
    s = Schema.of(cust=str, amt=int)
    with pytest.raises(SchemaError) as exc:
        s.of_name("nope")
    msg = str(exc.value)
    assert "'nope'" in msg
    assert "cust" in msg
    assert "amt" in msg


def test_schema_has_reports_presence():
    s = Schema.of(cust=str, amt=int)
    assert s.has("cust")
    assert not s.has("nope")


def test_schema_iter_yields_columns_in_order():
    s = Schema.of(a=str, b=int, c=float)
    names = [c.name for c in s]
    assert names == ["a", "b", "c"]


# --- SchemaError is a ValueError subclass (back-compat with raises checks)

def test_schema_error_is_a_value_error():
    assert issubclass(SchemaError, ValueError)


# --- the existing python_to_m3 mapping is the SOLE source of truth ------

def test_coercion_mapping_matches_python_to_m3_primitive_table():
    from pure_python.compile.python_to_m3 import _PRIMITIVE
    # Each entry there is reachable via Column(builtin) and yields the same singleton.
    for builtin, singleton in _PRIMITIVE.items():
        assert Column("x", builtin).type is singleton
