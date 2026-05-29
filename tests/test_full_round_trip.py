"""End-to-end showcase: Python -> M3 -> Pure -> M3 -> Python.

Drives one domain model through every converter in :mod:`pure_python.compile`:

    Python dataclasses
        --compile_class-->          M3 graph A
        --to_pure_module-->         Pure grammar source
        --from_pure-->              M3 graph B
        --to_module-->              Python source
        --import + compile_class--> M3 graph C

Two kinds of guarantee are asserted, so nothing is silently ignored:

1. **Everything structural round-trips.** A *complete* canonical of the M3
   graph -- package, type parameters, generalizations, and every property's
   full (nested) type, multiplicity and aggregation, plus enumeration values --
   is identical at all three M3 stages (A == B == C).

2. **Stereotypes and tagged values survive the Pure boundary.** They are present
   in graph A, written to the Pure source (with reconstructed `Profile` blocks),
   captured by `from_pure` into graph B, re-emitted as `typing.Annotated` markers
   and recovered in graph C. (Qualified/derived properties also survive by
   signature.)
"""

from __future__ import annotations

import dataclasses
import enum
import importlib.util
import sys
import typing
from decimal import Decimal

from pure_python import m3
from pure_python.compile import Compiler, Stereotype, Tag, from_pure, to_module, to_pure_module
from pure_python.compile.annotations import Body
from pure_python.compile.m3_to_pure import _expression

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
    id: typing.Annotated[str, Stereotype("id", "primaryKey")]  # stereotype
    balance: Money  # nested class reference
    tags: list[str]  # [0..*]
    suit: Suit  # enumeration
    nickname: typing.Annotated[str | None, Tag("doc", "about", "display name")] = None  # tag + [0..1]
    featured: Wrapper[str] | None = None  # type arguments

    @property
    def label(  # qualified (derived) property with a modelled body
        self,
    ) -> typing.Annotated[str, Body(lambda this: this.id + " (" + this.suit + ")")]:
        ...


@dataclasses.dataclass
class SavingsAccount(Account):  # inheritance
    rate: float = 0.0


ELEMENT_NAMES = {"Money", "Wrapper", "Account", "SavingsAccount", "Suit"}


def _load_module(source: str, name: str):
    spec = importlib.util.spec_from_loader(name, loader=None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile(source, f"<{name}>", "exec"), module.__dict__)
    return module


# --- a complete, cycle-safe canonical of the structural M3 surface ----------

def _generic(generic: m3.GenericType | None):
    if generic is None:
        return None
    if generic.typeParameter is not None:
        return ("param", generic.typeParameter.name)
    raw = generic.rawType
    name = getattr(raw, "name", None) or ("Any" if isinstance(raw, m3.Any) else None)
    return ("raw", name, tuple(_generic(a) for a in generic.typeArguments))


def _property(prop: m3.Property):
    upper = prop.multiplicity.upperBound.value if prop.multiplicity.upperBound else None
    return (_generic(prop.genericType), prop.multiplicity.lowerBound.value, upper, prop.aggregation.value)


def _qualified(qp: m3.QualifiedProperty):
    upper = qp.multiplicity.upperBound.value if qp.multiplicity.upperBound else None
    return (_generic(qp.genericType), qp.multiplicity.lowerBound.value, upper)


def _canonical(types) -> dict:
    out: dict = {}
    for t in types:
        if isinstance(t, m3.Enumeration):
            out[t.name] = ("enum", t.package, tuple(v.name for v in t.values))
        elif isinstance(t, m3.Class):
            out[t.name] = (
                "class",
                t.package,
                tuple(tp.name for tp in t.typeParameters),
                tuple(
                    sorted(
                        g.general.rawType.name
                        for g in t.generalizations
                        if isinstance(g.general.rawType, m3.Class)
                    )
                ),
                {p.name: _property(p) for p in t.properties},
                {q.name: _qualified(q) for q in t.qualifiedProperties},
            )
    return out


def _annotations_of(types, class_name: str, prop_name: str):
    """Return (stereotypes, tagged values) of a property as comparable tuples."""
    cls = next(t for t in types if isinstance(t, m3.Class) and t.name == class_name)
    prop = next(p for p in cls.properties if p.name == prop_name)
    stereotypes = [(s.profile.name, s.value) for s in prop.stereotypes]
    tagged = [(t.tag.profile.name, t.tag.value, t.value) for t in prop.taggedValues]
    return stereotypes, tagged


def test_python_m3_pure_m3_python_round_trip():
    # Stage 1: Python -> M3 (graph A). Compiling the leaf cascades through its
    # base, field types, enum and generic references.
    forward = Compiler(package="demo")
    forward.to_class(SavingsAccount)
    graph_a = list(forward.classes.values()) + list(forward.enums.values())

    # Stage 2: M3 -> Pure source.
    pure_source = to_pure_module(forward.classes[SavingsAccount])

    # Stage 3: Pure -> M3 (graph B).
    registry_b = from_pure(pure_source)
    graph_b = list(registry_b.values())

    # Stage 4: M3 -> Python source.
    python_source = to_module(*[t for t in graph_b if isinstance(t, m3.Class)])

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

    # (1) The COMPLETE structural canonical is identical at every M3 stage, and
    #     every element survives -- nothing is dropped or invented.
    canonical_a, canonical_b, canonical_c = _canonical(graph_a), _canonical(graph_b), _canonical(graph_c)
    assert set(canonical_a) == ELEMENT_NAMES
    assert canonical_a == canonical_b == canonical_c

    # (2a) Qualified/derived properties survive by signature: the canonical above
    #      already requires Account.label to match across A, B and C.
    assert [q.name for q in forward.classes[Account].qualifiedProperties] == ["label"]
    assert canonical_c["Account"][5]["label"] == (("raw", "String", ()), 1, 1)

    # (2c) The derived-property body graph survives Python -> m3 -> Pure -> m3.
    expected_body = "$this.id->plus(' (')->plus($this.suit)->plus(')')"
    assert f"label() {{ {expected_body} }} : String[1];" in pure_source
    label_a = forward.classes[Account].qualifiedProperties[0]
    label_b = registry_b["Account"].qualifiedProperties[0]
    assert _expression(label_a.expressionSequence[0]) == expected_body
    assert _expression(label_b.expressionSequence[0]) == expected_body

    # (2b) Stereotypes and tagged values survive the Pure boundary: present in A,
    #      written to the Pure source (with Profile blocks), and recovered in B and C.
    stereotypes, tagged = ([("id", "primaryKey")], [("doc", "about", "display name")])
    assert _annotations_of(graph_a, "Account", "id") == (stereotypes, [])
    assert _annotations_of(graph_a, "Account", "nickname") == ([], tagged)
    assert "<<id.primaryKey>> id : String[1];" in pure_source
    assert "{doc.about = 'display name'} nickname : String[0..1];" in pure_source
    assert "Profile id" in pure_source and "stereotypes: [primaryKey];" in pure_source
    assert "Profile doc" in pure_source and "tags: [about];" in pure_source
    for graph in (graph_b, graph_c):
        assert _annotations_of(graph, "Account", "id") == (stereotypes, [])
        assert _annotations_of(graph, "Account", "nickname") == ([], tagged)

    # The regenerated module is usable.
    account = module.SavingsAccount(
        id="acc-1",
        balance=module.Money(amount=Decimal("10.00"), currency="USD"),
        tags=["vip"],
        suit=module.Suit.HEARTS,
        rate=0.05,
    )
    assert account.id == "acc-1" and account.suit is module.Suit.HEARTS
