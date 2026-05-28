# TODO

Work for the Python representation of the Pure (FINOS Legend) M3 metamodel.
Grouped by status, with pointers to the relevant code.

## Done

- **Generate the M3 core type system from the bootstrap `m3.pure`.**
  `codegen/lexer.py` + `parser.py` parse the instance syntax; `schema.py`
  lowers it to a neutral `MetaModel`; `emit.py` + `generate.py` render
  `pure_python/m3/metamodel.py`. A drift test enforces that the committed file
  equals the generator output.
- **Parse the readable Pure grammar.** `codegen/grammar.py` parses
  `relation.pure`, `variant.pure`, `milestoning.pure` and merges them in
  (now 101 classes). Generics are emitted as `typing.TypeVar` /
  `typing.Generic[...]`.
- **Bidirectional compile layer.** `compile/python_to_m3.py` (dataclasses ->
  `m3.Class`) and `compile/m3_to_python.py` (`m3` -> importable dataclass
  module), including generics, `typing.Annotated` stereotypes/tags, and
  `@property` -> `QualifiedProperty`.
- **Pure source emitter.** `compile/m3_to_pure.py` renders `m3` as Pure
  grammar, with a reverse round-trip test through `codegen/grammar.py`.
- **Pure -> M3 bridge.** `compile/pure_to_m3.from_pure` lifts Pure grammar
  source into live `m3` instances, closing the loop. `tests/test_full_round_trip.py`
  drives one model through `Python -> M3 -> Pure -> M3 -> Python` and asserts
  the graph is identical at every M3 stage. (`m3_to_python` now emits
  `kw_only=True` dataclasses so inheritance survives the trip.)

## Tier 1 -- finish the round-trip design

- [x] **Map Python class inheritance to generalizations.** Direct dataclass
  bases become `Class.generalizations`; only a class's own fields are emitted.
  `m3_to_python` emits the base list and topologically sorts so bases precede
  subclasses; `m3_to_pure` emits `extends`. Round-trips via import.
- [x] **Preserve type arguments end-to-end in the generated metamodel.** A
  recursive `TypeRef` carries arguments on `MetaProperty`; both parsers capture
  them (the grammar parser splits `>>` to close nested generics) and `emit`
  renders subscripted annotations -- `function : Function<Z>[1]` is now
  `function: Function[Z]`, and `Enumeration<Any>` etc. survive from the bootstrap.
- [ ] **Parse the `Association` grammar.** `codegen/grammar.py` parses
  `Association` for shape but discards it. Represent it (two end properties,
  inline `[2]` bound) and merge like classes.
- [ ] **Parse qualified / derived properties in the readable grammar.**
  `_parse_property` skips anything with a `(`. Capture them as
  `m3.QualifiedProperty`, and have `m3_to_pure` emit derived properties (with a
  body). They (and stereotypes / tagged values) are currently dropped at the
  Pure boundary, so they do not survive the `pure_to_m3` round trip yet.
- [x] **Support `Annotated` markers nested inside unions.** `_strip_annotations`
  recursively pulls metadata out of unions, so both `Annotated[str | None, Tag]`
  and `Annotated[str, Tag] | None` work.
- [ ] **Preserve Python enum values.** Enum members round-trip by name; a Python
  value such as `Color.RED = 1` is lost (Pure enums are name-only). `m3.Enum` is
  an `AnnotatedElement`, so a tagged-value convention could carry the value.
- [x] **Revisit `bytes` mapping.** `m3_to_python._PURE_TO_PY` now maps
  `Byte -> bytes`, so `bytes` fields round-trip (Pure has no richer bytes type).

## Tier 2 -- new capabilities

- [ ] **Legend protocol model (`PureModelContextData`).** The deferred fork:
  the legend-engine JSON protocol (Mapping, Connection, Runtime, Service,
  relational stores, ...). Unlocks "all Legend types" and JSON interop /
  validation against the real engine. Largest effort, largest payoff.
- [ ] **JSON (de)serialization of `m3` graphs.** Today `m3` graphs only render
  to source. A `to_json` / `from_json` makes them persistable and is a stepping
  stone toward the protocol model above.
- [ ] **Expression / lambda / constraint representation.** The
  `ValueSpecification` tree is generated but nothing populates function bodies,
  constraints, or derived-property expressions.
- [ ] **Project hygiene.** Add a `py.typed` marker (downstream typing), a CI
  workflow running the tests + drift check, and a console entry point for the
  generator.
