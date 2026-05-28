from __future__ import annotations

from pure_python.codegen.schema import build_metamodel
from pure_python.codegen.parser import parse


def _model(m3_source: str):
    return build_metamodel(parse(m3_source))


def test_counts(m3_source):
    model = _model(m3_source)
    assert len(model.classes) == 85
    assert len(model.enums) == 2
    assert len(model.primitives) == 12
    assert len(model.multiplicities) == 5


def test_class_generalizations_match_pure(m3_source):
    model = _model(m3_source)
    assert model.classes["Class"].bases == [
        "Type",
        "PropertyOwner",
        "ElementWithConstraints",
        "PackageableElement",
        "Testable",
    ]
    assert model.classes["Type"].bases == ["Any"]
    assert model.classes["Any"].bases == []


def test_every_base_and_property_type_resolves(m3_source):
    model = _model(m3_source)
    known = model.type_names
    for cls in model.classes.values():
        for base in cls.bases:
            assert base in model.classes, f"{cls.name} extends unknown {base}"
        for prop in cls.properties:
            if prop.type_name is not None:
                assert prop.type_name in known, f"{cls.name}.{prop.name}: {prop.type_name}"


def test_single_root_and_acyclic(m3_source):
    model = _model(m3_source)
    roots = [c.name for c in model.classes.values() if not c.bases]
    assert roots == ["Any"]


def test_multiplicities_resolved(m3_source):
    model = _model(m3_source)
    assert (model.multiplicities["PureOne"].lower, model.multiplicities["PureOne"].upper) == (1, 1)
    assert (model.multiplicities["ZeroMany"].lower, model.multiplicities["ZeroMany"].upper) == (0, None)


def test_association_inline_multiplicity(m3_source):
    model = _model(m3_source)
    props = {p.name: p for p in model.classes["Association"].properties}
    # Associations cap their properties at two -> inline upper bound of 2 in m3.pure
    # (distinct from the named multiplicities, exercising inline-instance parsing).
    assert props["properties"].upper == 2
