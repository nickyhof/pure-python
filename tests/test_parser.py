from __future__ import annotations

import collections

from pure_python.codegen.parser import Instance, Ref, parse


def test_lexer_and_parser_on_small_instance():
    src = """
    ^Root.children[meta].children[type].children[Class] Foo @Root.children[meta].children[type]
    {
        Root.children[meta].children[ModelElement].properties[name] : 'Foo',
        Root.children[meta].children[type].children[Class].properties[properties] :
        [
            ^Root.children[meta].children[property].children[Property] bar
            {
                Root.children[meta].children[ModelElement].properties[name] : 'bar',
                Root.children[meta].children[property].children[AbstractProperty].properties[multiplicity] : Root.children[meta].children[multiplicity].children[PureOne]
            }
        ]
    }
    """
    (inst,) = parse(src)
    assert inst.kind == "Class"
    assert inst.name == "Foo"
    assert inst.package.qualified == "meta::type"
    assert inst.get("name") == "Foo"
    props = inst.get("properties")
    assert isinstance(props, list) and len(props) == 1
    bar = props[0]
    assert isinstance(bar, Instance) and bar.name == "bar"
    mult = bar.get("multiplicity")
    assert isinstance(mult, Ref) and mult.target == "PureOne"


def test_path_helpers():
    (inst,) = parse("^Package meta @Root.children { Package.properties[children] : [] }")
    assert inst.kind == "Package"
    classifier = inst.classifier
    assert classifier.target == "Package"
    assert inst.get("children") == []


def test_full_bootstrap_top_level_counts(m3_source):
    instances = parse(m3_source)
    counts = collections.Counter(i.kind for i in instances)
    assert counts["Class"] == 85
    assert counts["PrimitiveType"] == 12
    assert counts["Enumeration"] == 2
    assert counts["PackageableMultiplicity"] == 5
    assert counts["Package"] == 23
    assert counts["Profile"] == 1
    assert counts["ImportGroup"] == 1


def test_string_escapes_and_numbers():
    (inst,) = parse(
        "^Root.children[X] N { Root.children[A].properties[v] : 0, "
        "Root.children[A].properties[s] : 'a\\'b' }"
    )
    assert inst.get("v") == 0
    assert inst.get("s") == "a'b"
