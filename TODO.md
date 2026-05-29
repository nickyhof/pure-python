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
  - [ ] **Reuse the JVM across bridge calls (`eval` is the suite bottleneck).**
    Every `LegendBridge` call spawns a fresh `java -jar` that re-initializes the
    whole Legend engine; the `eval` calls (~4.4s each) dominate test time.
    *Done:* the bridge tests are now opt-in (`-m integration`, excluded by
    default) and the bootstrap metamodel is shared via a session fixture, so the
    default loop is fast. *Next, with the relation/TDS work:* a batched
    `evalMany` command (compile the model once, evaluate many expressions in one
    JVM). *Bigger follow-on:* a **persistent JVM daemon** -- `bridge.py` boots
    the engine once and streams requests over stdin/socket (one init for the
    whole session) instead of one process per call.

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
  body with binary core operators as parenthesized infix
  (`(($this.first + ' ') + $this.last)`) -- the form Legend's stdlib executes --
  and other functions in arrow form; and `compile/pure_expr.py` re-parses the
  captured body (including infix and negative literals) so the graph survives
  `Python -> m3 -> Pure -> m3` (asserted in `tests/test_expressions.py` and the
  full round-trip test, and executed via the Legend bridge). Out of slice 1
  (multi-arg lambdas landed in the relation/TDS foundation, slice 2, below):
  collection/list literals, `if`/`case`/`let`, milestoning sugar,
  `Constraint` / `ConcreteFunctionDefinition` bodies, and m3->Python(.py) emission
  of bodies (signature-only there).
- [x] **Relation / TDS query foundation (slice 2).** Builds on slice 1, all in
  `compile/`. New builders in `compile/expressions.py`: `lam(names, build)` for
  n-ary `{p, w, r | body}` lambdas (an `m3.LambdaFunction`; the parameter names
  round-trip via `openVariables` as a pragmatic carrier -- a native
  `FunctionType` would also need a returnType / returnMultiplicity we do not
  model at the expression level), `tds(text)` for a verbatim `#TDS{...}#`
  relation literal (an `InstanceValue` discriminated from a string by a shared
  `RelationType`-rawType marker so the emitter renders it unquoted; the CSV is
  never parsed), and `col(name)` / `cols(*names)` for simple `~col` / `~[a, b]`
  column specs (the name-only `m3.ColSpec` / `m3.ColSpecArray`). The `filter`
  and `select` verbs are the existing `call(...)` / fluent `_Accessor` arrow
  application -- `coerce` now passes lambda / column-spec nodes through. The
  `Body` capture path is generalized to 0- or 1-parameter functions (arity read
  from the signature; no second mechanism). `m3_to_pure._expression` emits all
  the new forms; `compile/pure_expr._lower_atomic` re-parses them via the real
  `M3CoreParser` (`dsl()` / `columnBuilders()` / `anyLambda()`), so a relation
  query survives `Python -> m3 -> Pure -> m3` (jar-free, `tests/test_relation.py`;
  the lambda + `filter` + `size` machinery is also executed through the Legend
  bridge). The `legend-bridge` jar now bundles the
  `legend-engine-xt-tds-{grammar,compiler}` extensions, so the real engine
  **parses and compiles** `#TDS{...}#` queries; the remaining gap is upstream --
  the engine's Java execution codegen does not yet implement the relation
  reducers (`relation::size ... is not supported yet`), so a TDS query cannot be
  executed down to a constant on this build (both the parse/compile success and
  the execution boundary are pinned in `tests/test_legend_bridge.py`).
  Investigated routing relation queries through an embedded **DuckDB** store
  executor (the path Legend's own relation PCT tests use): the wiring works (the
  `RelationalStoreExecutorBuilder` auto-discovers, the DuckDB native lib loads,
  and a TDS literal materializes and runs), but composing an operator over an
  *inline* `#TDS{}#` literal fails in 4.129.8 SQL generation -- `processTdsFilter`
  can't cast a literal source (`ClassInstanceHolder`) to a `SelectWithCursor`.
  The only workaround (the PCT `testAdapterForRelationalWithDuckDBExecution`)
  needs `meta::pure::extension::runtime::getExtensions` from the engine-internal
  `core_external_extensions` repo, which is not a published Maven artifact. So
  real relation execution needs a newer legend-engine (with inline-TDS SQL
  generation) or a full engine/server deployment. Extends the foundation:
  **`FuncColSpec` (`~c:{r|...}`) + `extend`** -- `fcol(name, lam(...))` builds a
  function-bearing `m3.FuncColSpec` and `fcols(*specs)` a `FuncColSpecArray`
  (the `~[a:{...}, b:{...}]` bracket form); `coerce` passes both through, so the
  derived-column verb is just `call("extend", rel, fcol(...))` / fluent
  `rel.extend(fcol(...))`. `m3_to_pure._expression` emits `~name:{r | <body>}`
  (lambda reused from the `LambdaFunction` emit) and `~[a:{...}, b:{...}]`, and
  `pure_expr._lower_column_builders` re-parses them (rejecting mixed-kind
  brackets), so a single func spec / a func-spec array / an `extend` query survive
  `Python -> m3 -> Pure -> m3` (jar-free, `tests/test_relation.py`); an `extend`
  query also parses + compiles via the real engine (`tests/test_legend_bridge.py`,
  execution still blocked upstream as above). Also extends the foundation with
  **`AggColSpec` (`~name:{map}:{agg}`) + `groupBy`** -- `agg(name, map, reduce)`
  builds an aggregation `m3.AggColSpec` (the per-row `map` lambda + the collection
  `reduce` lambda) and `aggs(*specs)` an `AggColSpecArray` (the
  `~[a:{...}:{...}, b:{...}:{...}]` bracket form); `coerce` passes both through,
  so the grouped-aggregation verb is just
  `call("groupBy", rel, cols(...), agg(...))` / fluent
  `rel.groupBy(cols(...), agg(...))` (the grouping `ColSpecArray` first, then the
  agg colspec/array). `m3_to_pure._expression` emits `~name:{map}:{agg}` (each
  lambda reused from the `LambdaFunction` emit) and the `~[...]` array, and
  `pure_expr._lower_column_builders` now re-parses the `extraFunction` (reduce)
  lambda -- a `oneColSpec` with both an `anyLambda` (map) and an `extraFunction`
  (reduce) lowers to an `AggColSpec` -- so a single agg spec / an agg-spec array /
  a `groupBy` query survive `Python -> m3 -> Pure -> m3` (jar-free,
  `tests/test_relation.py`); a `groupBy` query also parses + compiles via the real
  engine (`tests/test_legend_bridge.py`; the engine resolves
  `groupBy_Relation_1__ColSpecArray_1__AggColSpec_1__Relation_1_` and only fails
  in plan generation -- `relation::groupBy ... is not supported yet` -- the same
  execution boundary as above). Also adds the **simple relation verbs**
  `limit(n)` / `drop(n)` / `slice(start, stop)` / `distinct()` /
  `concatenate($other)` / `rename(~old, ~new)` -- these need NO new lowering or m3
  types: they are plain `SimpleFunctionExpression` arrow calls over already-handled
  atomics (int literals, `#TDS{}#` relations, name-only `~col` colspecs), so they
  ride the existing `call(...)` / fluent `_Accessor` path (the two-arg `slice` /
  `rename` exercise the `*args` passthrough) and `pure_expr` re-parses them, so each
  verb (and a `drop->slice->distinct->limit` chain) survives `Python -> m3 -> Pure
  -> m3` (jar-free, `tests/test_relation.py`). The real engine confirms each
  resolves to a `meta::pure::functions::relation::<verb>` function
  (`limit_Relation_1__Integer_1__Relation_1_`,
  `drop_Relation_1__Integer_1__Relation_1_`,
  `slice_Relation_1__Integer_1__Integer_1__Relation_1_`,
  `distinct_Relation_1__Relation_1_`,
  `concatenate_Relation_1__Relation_1__Relation_1_`,
  `rename_Relation_1__ColSpec_1__ColSpec_1__Relation_1_`) -- a representative chain
  parses + compiles and only fails in plan generation (same execution boundary as
  above; `tests/test_legend_bridge.py`). The candidate `take(n)` was probed and
  **REJECTED**: the engine has no relation overload for it, so it matched the
  *collection* `take` and failed with `Unhandled value type:
  meta::pure::metamodel::relation::TDS` -- it is not a relation verb and is excluded.
  Also adds **`sort` + `pivot`** and the underlying **`[...]` collection literal +
  `ascending` / `descending` directions** they need. A `sort` direction is just an
  arrow call `~col->ascending()` / `~col->descending()` (a `SimpleFunctionExpression`,
  no new m3 type) -- `asc(colspec)` / `desc(colspec)` are thin helpers over `call`.
  `sort` takes one direction or a collection of them: `array(*exprs)` builds a
  `[a, b]` collection literal (a multi-value / `ZeroMany` `m3.InstanceValue` whose
  `values` are the coerced element nodes -- no new m3 type, reusing the node the
  emitter already rendered for scalar `[1, 2, 3]` lists), and
  `pure_expr._lower_atomic` now lowers the `expressionsArray` `[...]` grammar
  (lowering each element via `_lower_combined`), so an `array(...)` survives
  `Python -> m3 -> Pure -> m3`. `pivot(~[grp], ~agg:{map}:{reduce})` is a free
  fluent / `call` verb over a pivot `ColSpecArray` + an `AggColSpec`. The real
  engine confirms the resolved overloads -- `sort_Relation_1__SortInfo_MANY__Relation_1_`
  (both the scalar `~col->ascending()` and the bracketed multi
  `[~a->ascending(), ~b->descending()]` forms compile via the `SortInfo[*]`
  signature) and `pivot_Relation_1__ColSpecArray_1__AggColSpec_1__Relation_1_` --
  with `asc` / `desc` probed and REJECTED (no relation overload; the names are
  `ascending` / `descending`), a bare `~col` / `~[cols]` sort REJECTED (sort needs
  a `SortInfo`, i.e. `->ascending()`), and the two-`ColSpecArray` pivot arity
  REJECTED (no such overload). Each parses + compiles and only fails in plan
  generation (same execution boundary as above; `tests/test_legend_bridge.py`).
  **Resolved the single-element `~[a]` bracket asymmetry**: bracket presence *is*
  recoverable from the parse tree (`columnBuilders.BRACKET_OPEN`), and the real
  engine keeps `~[prod]` a one-element `ColSpecArray` (the `pivot` overload needs
  it), so `_lower_column_builders` now returns the *Array* family
  (`ColSpecArray` / `FuncColSpecArray` / `AggColSpecArray`) for a single
  *bracketed* `~[a]` while a bracketless `~a` still lowers to the scalar -- a shared
  change exercised by select / extend / groupBy (all existing round trips still
  pass). Also adds **`join` / `asOfJoin` + enum-value references** -- the new
  capability is representing an ENUM-VALUE REFERENCE (`JoinKind.INNER`) as a
  `ValueSpecification`: the second relation is just a value (another `#TDS{}#`
  literal or a `$var`) and the condition is the already-supported multi-param
  lambda (`{l, r | $l.id == $r.rid}`). The metamodel has no `JoinKind` enum, so --
  mirroring the `tds` pattern -- `enum_ref(enumeration, value)` stores the verbatim
  emit text (`"JoinKind.INNER"`) on an `InstanceValue` discriminated by an
  `Enumeration`-rawType marker (distinct from a string `lit`'s `String` and a TDS
  literal's `RelationType`), so the emitter renders it unquoted and `canon`
  projects it to a distinct `("enumref", ...)` shape; `JoinKind.INNER` / `.LEFT` /
  `.RIGHT` / `.FULL` are exposed as ready-made constants (a small `JoinKind`
  namespace). `m3_to_pure._expression` emits the reference verbatim, and
  `pure_expr` adds an `instanceReference` lowering path: `_lower_atomic` lowers a
  bare `instanceReference` (`JoinKind`) to a lightweight `_PendingReference`
  carrying the qualified-name text, and `_lower_property_or_function` folds a
  trailing parameterless `.VALUE` propertyExpression onto it to build the
  `enum_ref` node; a pending reference left dangling or followed by an arrow call
  (`JoinKind->...`) is rejected loudly, while a *prefix* function call
  (`over(...)`) is a sibling shape lowered directly (see the window/OLAP slice
  below). `coerce` passes
  the enum-ref + relation + lambda through, so the verbs are free fluent / `call`
  forms: `rel.join(other, JoinKind.INNER, lam(["l","r"], ...))` and
  `rel.asOfJoin(other, lam(["l","r"], ...))`. A bare `enum_ref`, a `join`, and an
  `asOfJoin` survive `Python -> m3 -> Pure -> m3` (jar-free, `tests/test_relation.py`).
  The real engine confirms the resolved overloads
  (`join_Relation_1__Relation_1__JoinKind_1__Function_1__Relation_1_` and the
  3-arg `asOfJoin_Relation_1__Relation_1__Function_1__Relation_1_`; a 4-arg
  `asOfJoin(rel, rel, matchCond, joinCond)` also compiles) and that **bare**
  `JoinKind.INNER` both PARSES and COMPILES (the compiler resolves the enumeration;
  valid members INNER/LEFT/RIGHT/FULL each reach the plan-gen boundary, while
  `OUTER` is REJECTED with "Can't find enum value 'OUTER'" -- proving the reference
  is genuinely resolved, not ignored). Each query parses + compiles and only fails
  in plan generation (same execution boundary as above; `tests/test_legend_bridge.py`).
  Finally adds **window / OLAP + the `Frame` constructors** -- a windowed `extend`
  adds an OLAP column over a window spec: `rel.extend(over(~p, sort, frame),
  ~name:{p, w, r | <body>})`. The genuinely new machinery is the
  **bare-function-call (prefix) form**: `over(...)` / `rows(...)` / `_range(...)` /
  `unbounded()` are prefix calls whose first argument is NOT a relation receiver,
  so (a) `m3_to_pure` gained a tight `_PREFIX_FUNCTIONS` set
  (`{over, rows, _range, unbounded}`, mirroring the infix-operator set) that emits
  them `fn(a, b, c)` rather than the default arrow `a->fn(b, c)`, and (b)
  `pure_expr._lower_instance_reference` now lowers an `instanceReference` that
  carries a `functionExpressionParameters` `allOrFunction` to a plain
  `call(name, *args)` (the trailing `identifier` is the simple name; zero args ->
  `unbounded()`), keeping the Slice-D enum-ref behavior intact -- a bare
  `instanceReference` with NO params still becomes a `_PendingReference`
  (completed only by a `.VALUE` suffix -> `enum_ref`), and a bare ref left
  dangling / followed by an arrow call still raises. No new m3 type: the whole
  window is an ordinary function-call graph over the existing colspec / array /
  lambda nodes. `over(partition, sort=None, frame=None)` builds the prefix
  `over(...)` (only the supplied args are emitted, matching the engine's overload
  set); `rows(from, to)` / `range_(from, to)` (the value-range, emitted as the
  engine's `_range`, since the bare `range` is the *collection* function) build
  the frame, and `unbounded()` is the `UnboundedFrameValue` bound sentinel
  (negative=preceding, positive=following, 0=current row). The windowed `extend`
  column reuses the existing `fcol` (`~name:{lambda}`) or `agg`
  (`~name:{map}:{reduce}`) spec with the canonical 3-param window lambda.
  **SUPPORTED** (each survives `Python -> m3 -> Pure -> m3` jar-free in
  `tests/test_relation.py`, and PARSES + COMPILES via the real engine to the
  plan-gen boundary in `tests/test_legend_bridge.py`): `over` with a `ColSpec` or
  `ColSpecArray` partition, an optional single `SortInfo` (`~col->ascending()`) or
  bracketed `SortInfo[*]` list, and an optional `rows` / `_range` frame
  (`over(cols)`, `over(cols, sort)`, `over(cols, frame)`, `over(cols, sort,
  frame)`); `rows` / `_range` with integer offsets or `unbounded()` bounds; and a
  windowed `extend` with either a `FuncColSpec`
  (`extend_Relation_1___Window_1__FuncColSpec_1__Relation_1_`) or an `AggColSpec`
  (`extend_Relation_1___Window_1__AggColSpec_1__Relation_1_`) column. The engine
  ALSO accepts the arrow spelling `~grp->over(...)`, but prefix is the engine's own
  canonical OLAP form and the frame/bound constructors have no receiver to arrow
  from, so all four emit prefix (and only prefix is reverse-parsed). **DEFERRED**:
  the named OLAP convenience functions (`rank`, `denseRank`, `rowNumber`,
  `lag`/`lead`, running `cumulativeDistribution`, etc. -- these layer on top of the
  same `over` window and can be added as plain verbs/calls later) and the
  `_RangeInterval` duration-unit frame variant (`_range(n, DurationUnit, ...)`);
  the coherent compilable subset above (partition + sort + `rows`/`_range` frame +
  windowed `extend`) is delivered.
  Deferred follow-ons:
    - **a `Frame` fluent class** -- a higher-level relation-query builder over
      the free verbs (the free `over` / `rows` / `range_` / `unbounded` builders
      are in place).
    - **a batched `evalMany` bridge command** -- compile the model once and
      evaluate many expressions in one JVM (a Java-side change, explicitly *not*
      bundled with this slice).
- [ ] **Lambda / constraint representation.** `Constraint` and
  `ConcreteFunctionDefinition` bodies remain unmodelled. (Multi-parameter
  lambdas are now built by `lam` -- see the relation/TDS foundation above.)
- [ ] **Project hygiene.** Add a `py.typed` marker (downstream typing), a CI
  workflow running the tests + drift check, and a console entry point for the
  generator.
