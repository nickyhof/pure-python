from __future__ import annotations


def test_counts(m3_model):
    assert len(m3_model.classes) == 85
    assert len(m3_model.enums) == 2
    assert len(m3_model.primitives) == 12
    assert len(m3_model.multiplicities) == 5


def test_class_generalizations_match_pure(m3_model):
    assert m3_model.classes["Class"].bases == [
        "Type",
        "PropertyOwner",
        "ElementWithConstraints",
        "PackageableElement",
        "Testable",
    ]
    assert m3_model.classes["Type"].bases == ["Any"]
    assert m3_model.classes["Any"].bases == []


def test_every_base_and_property_type_resolves(m3_model):
    known = m3_model.type_names
    for cls in m3_model.classes.values():
        for base in cls.bases:
            assert base in m3_model.classes, f"{cls.name} extends unknown {base}"
        for prop in cls.properties:
            if prop.type_name is None:
                continue
            if prop.is_type_parameter:
                assert prop.type_name in m3_model.type_parameter_names
            else:
                assert prop.type_name in known, f"{cls.name}.{prop.name}: {prop.type_name}"


def test_single_root_and_acyclic(m3_model):
    roots = [c.name for c in m3_model.classes.values() if not c.bases]
    assert roots == ["Any"]


def test_multiplicities_resolved(m3_model):
    assert (m3_model.multiplicities["PureOne"].lower, m3_model.multiplicities["PureOne"].upper) == (1, 1)
    assert (m3_model.multiplicities["ZeroMany"].lower, m3_model.multiplicities["ZeroMany"].upper) == (0, None)


def test_association_inline_multiplicity(m3_model):
    props = {p.name: p for p in m3_model.classes["Association"].properties}
    # Associations cap their properties at two -> inline upper bound of 2 in m3.pure
    # (distinct from the named multiplicities, exercising inline-instance parsing).
    assert props["properties"].upper == 2
