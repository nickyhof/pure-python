"""End-to-end showcase: Python -> M3 -> Pure -> M3 -> Python.

Drives one domain model through every converter in :mod:`pure_python.compile`
and asserts the metamodel graph is identical at each M3 stage:

    Python dataclasses
        --compile_class-->        M3 graph A
        --to_pure_module-->       Pure grammar source
        --from_pure-->            M3 graph B
        --to_module-->            Python source
        --import + compile_class--> M3 graph C

A, B and C must agree. Fidelity is bounded by the Pure grammar parser, so the
sample exercises everything that survives that boundary: inheritance, generics
with type-parameter fields, nested type arguments, enumerations, nested class
references, and the full range of multiplicities.
"""

from __future__ import annotations

import dataclasses
import enum
import importlib.util
import sys
import typing
from decimal import Decimal

from pure_python import m3
from pure_python.compile import Compiler, from_pure, to_module, to_pure_module

RT = typing.TypeVar("RT")


class Suit(enum.Enum):
    HEARTS = "HEARTS"
    SPADES = "SPADES"


@dataclasses.dataclass
class Money:
    amount: Decimal
    currency: str


@dataclasses.dataclass
class Wrapper(typing.Generic[RT]):
    item: RT
    history: list[RT]


@dataclasses.dataclass
class Account:
    id: str
    balance: Money  # nested class reference
    tags: list[str]  # [0..*]
    suit: Suit  # enumeration
    nickname: str | None = None  # [0..1]
    featured: Wrapper[str] | None = None  # type arguments


@dataclasses.dataclass
class SavingsAccount(Account):  # inheritance
    rate: float = 0.0


def _load_module(source: str, name: str):
    spec = importlib.util.spec_from_loader(name, loader=None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile(source, f"<{name}>", "exec"), module.__dict__)
    return module


def _typesig(generic: m3.GenericType | None) -> str:
    if generic is None:
        return "None"
    if generic.typeParameter is not None:
        return f"param:{generic.typeParameter.name}"
    base = getattr(generic.rawType, "name", None) or "Any"
    if generic.typeArguments:
        return f"{base}<{','.join(_typesig(a) for a in generic.typeArguments)}>"
    return base


def _bounds(mult: m3.Multiplicity) -> tuple[int, int | None]:
    return mult.lowerBound.value, (mult.upperBound.value if mult.upperBound else None)


def _signature(types) -> dict:
    """A package-independent structural fingerprint of an m3 type graph."""
    out: dict = {}
    for t in types:
        if isinstance(t, m3.Enumeration):
            out[t.name] = ("enum", tuple(v.name for v in t.values))
        elif isinstance(t, m3.Class):
            bases = tuple(
                sorted(
                    g.general.rawType.name
                    for g in t.generalizations
                    if isinstance(g.general.rawType, m3.Class)
                )
            )
            props = {p.name: (_typesig(p.genericType), *_bounds(p.multiplicity)) for p in t.properties}
            out[t.name] = ("class", tuple(tp.name for tp in t.typeParameters), bases, props)
    return out


def test_python_m3_pure_m3_python_round_trip():
    # Stage 1: Python -> M3 (graph A). Compiling the leaf class cascades through
    # its base, field types, enum and generic references.
    forward = Compiler(package="demo")
    forward.to_class(SavingsAccount)
    graph_a = list(forward.classes.values()) + list(forward.enums.values())

    # Stage 2: M3 -> Pure source.
    pure_source = to_pure_module(forward.classes[SavingsAccount])
    assert "Class demo::SavingsAccount extends Account" in pure_source
    assert "Class demo::Wrapper<RT>" in pure_source
    assert "featured : Wrapper<String>[0..1];" in pure_source
    assert "Enum demo::Suit" in pure_source

    # Stage 3: Pure source -> M3 (graph B).
    registry_b = from_pure(pure_source)
    graph_b = list(registry_b.values())

    # Stage 4: M3 -> Python source.
    python_source = to_module(*[t for t in graph_b if isinstance(t, m3.Class)])
    assert "class SavingsAccount(Account):" in python_source
    assert "item: RT" in python_source
    assert "featured: Wrapper[str] | None = None" in python_source

    # Stage 5: Python source -> import -> M3 (graph C).
    module = _load_module(python_source, "pure_python_full_round_trip")
    back = Compiler(package="demo")
    for name in registry_b:
        obj = getattr(module, name)
        if dataclasses.is_dataclass(obj):
            back.to_class(obj)
        elif isinstance(obj, type) and issubclass(obj, enum.Enum):
            back.to_enumeration(obj)
    graph_c = list(back.classes.values()) + list(back.enums.values())

    # The metamodel graph is identical at every M3 stage.
    sig_a, sig_b, sig_c = _signature(graph_a), _signature(graph_b), _signature(graph_c)
    assert sig_a == sig_b == sig_c

    # Spot-check the surviving structure and confirm the regenerated module works.
    assert sig_c["SavingsAccount"] == ("class", (), ("Account",), {"rate": ("Float", 1, 1)})
    assert sig_c["Wrapper"][1] == ("RT",)
    assert sig_c["Account"][3]["featured"] == ("Wrapper<String>", 0, 1)

    account = module.SavingsAccount(
        id="acc-1",
        balance=module.Money(amount=Decimal("10.00"), currency="USD"),
        tags=["vip"],
        suit=module.Suit.HEARTS,
        rate=0.05,
    )
    assert account.id == "acc-1" and account.suit is module.Suit.HEARTS
