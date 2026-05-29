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
  `typing.Generic[...]`. It **skips imports and function definitions** (they are
  not types) and class/enum-header `<<stereotype>>` / `{tag}` annotations;
  property-level annotations are captured.
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
  `kw_only=True` dataclasses so inheritance survives the trip.) Generalizations,
  type arguments, qualified properties, and property-level stereotypes / tagged
  values all survive; the only remaining intentional Pure-boundary drops are
  enum-member values (Pure enums are name-only) and class-level annotations
  (never emitted in practice).

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
- [x] **Parse the `Association` grammar.** `grammar.py` parses `Association`
  into a `MetaAssociation` (two end properties), `generate.py` merges them,
  `m3_to_pure` emits `Association` blocks and `pure_to_m3` lifts them back, with
  an `m3 -> Pure -> m3` round-trip test.
- [x] **Parse qualified / derived properties in the readable grammar.**
  `grammar.py` captures them by signature; `m3_to_pure` emits
  `name() { [] } : Type[mult];` and `pure_to_m3` lifts them into
  `m3.QualifiedProperty`, so they now survive the round trip. Their expression
  bodies are now modelled too -- see the expression layer below.
- [x] **Preserve property-level stereotypes and tagged values across the Pure
  boundary.** `grammar.py` now parses `<<profile.value>>` and
  `{profile.tag = 'v'}` annotations (instead of skipping them) and `pure_to_m3`
  rebuilds shared `m3.Profile` / `Stereotype` / `Tag` / `TaggedValue` instances,
  so they survive `Python -> M3 -> Pure -> M3 -> Python` (asserted in
  `test_full_round_trip.py`).
- [x] **Support `Annotated` markers nested inside unions.** `_strip_annotations`
  recursively pulls metadata out of unions, so both `Annotated[str | None, Tag]`
  and `Annotated[str, Tag] | None` work.
- [x] **Preserve Python enum values.** When a member's value differs from its
  name, `python_to_m3` stores it as a tagged value (`pure_python.enumValue`) on
  the `m3.Enum`, and `m3_to_python` emits it back, so `Color.RED = 1` round-trips
  through the Python <-> M3 loop. (Pure enums are name-only, so this enum-member
  value is dropped at the Pure boundary, unlike property-level tags.)
- [x] **Revisit `bytes` mapping.** `m3_to_python._PURE_TO_PY` now maps
  `Byte -> bytes`, so `bytes` fields round-trip (Pure has no richer bytes type).

## Tier 2 -- new capabilities

- **Legend bridge (execution oracle).** `legend-bridge/` is a small JVM harness
  built on the published `legend-engine` artifacts; `pure_python/legend/`
  shells out to it. `parse` validates pure-python's emitted Pure against Legend's
  *real* grammar and returns `PureModelContextData` JSON; `compose` renders it
  back; **`eval` compiles and executes** a Pure expression and returns its value
  (`LegendBridge.evaluate("|1 + 1") == 2`), delegating execution to Legend's
  compiler + plan generation + plan executor rather than reimplementing it.
  Tests in `tests/test_legend_bridge.py` (skipped unless the jar is built with
  `mvn -f legend-bridge package`).
  - [x] **Emit qualified type references in `m3_to_pure`.** The bridge surfaced
    a real gap: `m3_to_pure` emitted unqualified property/supertype names (e.g.
    `addresses : Address[*]`), which Legend's grammar parser accepts but its
    *compiler* rejects with "Can't find type 'Address'". `_type` and
    `_generalization_names` now emit `pkg::Name`, so cross-type models compile
    and `eval` (`tests/test_legend_bridge.py` runs one over a two-class model).
  - [ ] **Richer `eval` results.** Only expressions reducing to a `ConstantResult`
    are returned today; TDS/relation/streaming results need a serializer.

- [ ] **Legend protocol model (`PureModelContextData`).** The deferred fork:
  the legend-engine JSON protocol (Mapping, Connection, Runtime, Service,
  relational stores, ...). Unlocks "all Legend types" and JSON interop /
  validation against the real engine. Largest effort, largest payoff.
- [ ] **JSON (de)serialization of `m3` graphs.** Today `m3` graphs only render
  to source. A `to_json` / `from_json` makes them persistable and is a stepping
  stone toward the protocol model above.
- [x] **Expression representation (slice 1).** `compile/expressions.py` builds
  `ValueSpecification` graphs -- explicit builders (`lit`, `var`, `call`/`func`,
  `prop`) plus a PyLegend-style DSL (`c(...)`, operator overloads, fluent
  `.method(...)`, property access). A `Body(...)` marker on a `@property` return
  type populates a `QualifiedProperty.expressionSequence`; `m3_to_pure` emits the
  body in uniform arrow form (`$this.first->plus(' ')->plus($this.last)`); and
  `compile/pure_expr.py` re-parses the captured body so the graph survives
  `Python -> m3 -> Pure -> m3` (asserted in `tests/test_expressions.py` and the
  full round-trip test). Out of slice 1: collection/list literals, multi-arg
  lambdas, `if`/`case`/`let`, multi-statement bodies, milestoning sugar,
  `Constraint` / `ConcreteFunctionDefinition` bodies, and m3->Python(.py) emission
  of bodies (signature-only there).
- [ ] **Lambda / constraint representation.** `Constraint` and
  `ConcreteFunctionDefinition` bodies, and multi-parameter lambdas, remain
  unmodelled.
- [ ] **Project hygiene.** Add a `py.typed` marker (downstream typing), a CI
  workflow running the tests + drift check, and a console entry point for the
  generator.
