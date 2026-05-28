# TODO

Follow-on work for the Python representation of the Pure (FINOS Legend) M3
metamodel. Grouped by area, with pointers to the relevant code.

## Codegen / metamodel coverage

- [ ] **Parse the `Association` grammar.** `codegen/grammar.py` currently parses
  `Association` for shape but discards the result (`_GrammarParser.parse`).
  Map it to `m3.Association` (two properties, the inline `[2]` upper bound) and
  merge it like classes.
- [ ] **Parse qualified / derived properties in the readable grammar.**
  `_parse_property` skips anything with a `(` (see the early `return None`).
  Capture them as `m3.QualifiedProperty` (name, return type, multiplicity),
  mirroring what the compile layer already does for Python `@property`.
- [ ] **Preserve type arguments in the generated metamodel.**
  `grammar._type_ref` drops `<...>` and `emit` uses base names only, so
  `function : Function<Z>[1]` becomes `function: Function`. Carry type
  arguments on `MetaProperty` and emit subscripted annotations
  (`Function[Z]`), as the compile layer already does via `GenericType.typeArguments`.

## Compile layer (Python <-> M3)

- [ ] **Map Python class inheritance to generalizations.** `python_to_m3.to_class`
  reads `dataclasses.fields`, which flattens inherited fields, and sets no
  bases. Detect base dataclasses, set `Class.generalizations`, and only emit a
  class's own fields. `m3_to_python` should then emit the base list.
- [ ] **Class-level stereotypes & tagged values.** Only property-level markers
  (via `typing.Annotated`) are supported today. Add a class decorator (or
  convention) so `Class.stereotypes` / `Class.taggedValues` round-trip too.
- [ ] **Support `Annotated` markers nested inside unions.** `_split_annotated`
  only handles a top-level `Annotated`, so markers must wrap the whole type
  (`Annotated[str | None, Tag(...)]`). Also unwrap `Annotated[str, Tag(...)] | None`.
- [ ] **Preserve Python enum values.** Enum members round-trip by name; a Python
  value such as `Color.RED = 1` is lost (Pure enums are name-only). Decide on a
  tagged-value convention to carry the value.
- [ ] **Revisit `bytes` mapping.** `bytes -> Byte` maps back to `int` (`Byte`
  has no distinct Python type in `_PURE_TO_PY`), so a `bytes` field does not
  round-trip exactly. Decide whether to preserve a distinct bytes mapping.

## New capability

- [x] **Emit Pure source from `m3`.** `compile/m3_to_pure.py` renders `m3`
  instances as Pure grammar (classes with generics/type-args, stereotypes,
  tagged values, multiplicities; enumerations; reconstructed profiles). A
  reverse round-trip test feeds the output back through `codegen/grammar.py`
  and compares signatures. Still TODO: emit qualified (derived) properties
  (needs a function body) and a full same-type `m3 -> Pure -> m3` loop once
  the grammar parser produces `m3` instances / keeps type args & annotations.
