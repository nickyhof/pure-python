"""Emit actual Pure grammar source from Pure M3 instances.

The inverse of :mod:`pure_python.codegen.grammar`: walk an ``m3.Class`` (and the
classes / enumerations it references) and render readable Pure::

    Class demo::Person
    {
        <<pii.sensitive>> firstName : String[1];
        age : Integer[0..1];
        addresses : demo::Address[*];
    }

Type parameters, type arguments, stereotypes and tagged values are all
rendered. Qualified (derived) properties are emitted by signature with an empty
body placeholder (the metamodel instances do not carry an expression body).
Associations are emitted too.
"""

from __future__ import annotations

from pure_python import m3


def _bounds(mult: m3.Multiplicity | None) -> tuple[int, int | None]:
    if mult is None:
        return 1, 1
    lower = mult.lowerBound.value if mult.lowerBound and mult.lowerBound.value is not None else 0
    upper = mult.upperBound.value if mult.upperBound and mult.upperBound.value is not None else None
    return lower, upper


def _multiplicity(mult: m3.Multiplicity | None) -> str:
    lower, upper = _bounds(mult)
    if (lower, upper) == (1, 1):
        return "[1]"
    if (lower, upper) == (0, None):
        return "[*]"
    if upper is None:
        return f"[{lower}..*]"
    if lower == upper:
        return f"[{lower}]"
    return f"[{lower}..{upper}]"


def _type(generic: m3.GenericType | None) -> str:
    if generic is None:
        return "Any"
    if generic.typeParameter is not None:
        return generic.typeParameter.name
    raw = generic.rawType
    base = _qualified_name(raw) if raw is not None and getattr(raw, "name", None) else "Any"
    if generic.typeArguments:
        args = ", ".join(_type(arg) for arg in generic.typeArguments)
        return f"{base}<{args}>"
    return base


def _stereotypes(element: object) -> str:
    stereotypes = getattr(element, "stereotypes", []) or []
    if not stereotypes:
        return ""
    rendered = ", ".join(f"{s.profile.name}.{s.value}" for s in stereotypes)
    return f"<<{rendered}>> "


def _tagged_values(element: object) -> str:
    tagged = getattr(element, "taggedValues", []) or []
    if not tagged:
        return ""
    rendered = ", ".join(f"{t.tag.profile.name}.{t.tag.value} = '{t.value}'" for t in tagged)
    return f"{{{rendered}}} "


def _qualified_name(element: object) -> str:
    package = getattr(element, "package", None)
    return f"{package}::{element.name}" if package else element.name


def _property(prop: m3.Property) -> str:
    annotations = f"{_stereotypes(prop)}{_tagged_values(prop)}"
    return f"    {annotations}{prop.name} : {_type(prop.genericType)}{_multiplicity(prop.multiplicity)};"


def _qualified_property(qp: m3.QualifiedProperty) -> str:
    # `[]` is a syntactically valid placeholder body: the expression layer is
    # not modelled, but real Pure grammars reject an empty `{}` body.
    return f"    {qp.name}() {{ [] }} : {_type(qp.genericType)}{_multiplicity(qp.multiplicity)};"


def _generalization_names(cls: m3.Class) -> list[str]:
    names: list[str] = []
    for generalization in cls.generalizations:
        general = getattr(generalization, "general", None)
        raw = getattr(general, "rawType", None)
        if raw is not None and getattr(raw, "name", None):
            names.append(_qualified_name(raw))
    return names


def to_pure(cls: m3.Class) -> str:
    """Render a single ``Class`` declaration as Pure source."""
    params = ""
    if cls.typeParameters:
        params = "<" + ", ".join(tp.name for tp in cls.typeParameters) + ">"
    bases = _generalization_names(cls)
    extends = f" extends {', '.join(bases)}" if bases else ""
    header = f"Class {_stereotypes(cls)}{_qualified_name(cls)}{params}{extends}"
    body = [_property(p) for p in cls.properties]
    body += [_qualified_property(qp) for qp in cls.qualifiedProperties]
    if body:
        return header + "\n{\n" + "\n".join(body) + "\n}"
    return header + "\n{\n}"


def _association(assoc: m3.Association) -> str:
    body = "\n".join(_property(p) for p in assoc.properties)
    return f"Association {_qualified_name(assoc)}\n{{\n{body}\n}}"


def _enum(enumeration: m3.Enumeration) -> str:
    values = ", ".join(v.name for v in enumeration.values)
    return f"Enum {_qualified_name(enumeration)}\n{{\n    {values}\n}}"


def _profiles_from(classes: list[m3.Class]) -> list[str]:
    """Reconstruct minimal Profile declarations for stereotypes / tags in use."""
    stereotypes: dict[str, list[str]] = {}
    tags: dict[str, list[str]] = {}

    def add(table: dict[str, list[str]], profile: str, value: str) -> None:
        bucket = table.setdefault(profile, [])
        if value not in bucket:
            bucket.append(value)

    for cls in classes:
        for prop in cls.properties:
            for stereotype in getattr(prop, "stereotypes", []) or []:
                add(stereotypes, stereotype.profile.name, stereotype.value)
            for tagged in getattr(prop, "taggedValues", []) or []:
                add(tags, tagged.tag.profile.name, tagged.tag.value)

    blocks: list[str] = []
    for profile in sorted(set(stereotypes) | set(tags)):
        lines = [f"Profile {profile}", "{"]
        if stereotypes.get(profile):
            lines.append(f"    stereotypes: [{', '.join(stereotypes[profile])}];")
        if tags.get(profile):
            lines.append(f"    tags: [{', '.join(tags[profile])}];")
        lines.append("}")
        blocks.append("\n".join(lines))
    return blocks


def _collect(roots: tuple[m3.Type, ...]):
    classes: list[m3.Class] = []
    enums: list[m3.Enumeration] = []
    associations: list[m3.Association] = []
    seen: set[int] = set()
    stack: list[m3.Type] = list(roots)
    while stack:
        node = stack.pop(0)
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, m3.Association):
            associations.append(node)
            for prop in node.properties:
                stack.extend(_referenced(prop.genericType))
        elif isinstance(node, m3.Enumeration):
            enums.append(node)
        elif isinstance(node, m3.Class):
            classes.append(node)
            for prop in node.properties:
                stack.extend(_referenced(prop.genericType))
            for qp in node.qualifiedProperties:
                stack.extend(_referenced(qp.genericType))
            for generalization in node.generalizations:
                raw = generalization.general.rawType if generalization.general else None
                if isinstance(raw, m3.Class):
                    stack.append(raw)
    return classes, enums, associations


def _referenced(generic: m3.GenericType | None) -> list[m3.Type]:
    if generic is None:
        return []
    found: list[m3.Type] = []
    if isinstance(generic.rawType, (m3.Class, m3.Enumeration)):
        found.append(generic.rawType)
    for arg in generic.typeArguments:
        found.extend(_referenced(arg))
    return found


def to_pure_module(*roots: m3.Type) -> str:
    """Render Pure source for the given elements, their dependencies and profiles."""
    classes, enums, associations = _collect(roots)
    blocks = _profiles_from(classes)
    blocks += [_enum(e) for e in enums]
    blocks += [to_pure(c) for c in classes]
    blocks += [_association(a) for a in associations]
    return "\n\n".join(blocks) + "\n"
