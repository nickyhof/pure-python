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
  from, so all four emit prefix (and only prefix is reverse-parsed). The named OLAP
  convenience functions are now **DELIVERED** (see "named OLAP functions" below);
  **DEFERRED**: the `_RangeInterval` duration-unit frame variant
  (`_range(n, DurationUnit, ...)`); the coherent compilable subset above (partition
  + sort + `rows`/`_range` frame + windowed `extend`) is delivered.
- [x] **legendql-style `Frame` query builder (slice 3).** A branded, **immutable**
  fluent facade over the relation verbs, all in `compile/frame.py` (re-exported as
  `compile.Frame`). It adds **no new representation**: a `Frame` wraps one relation
  `ValueSpecification` node and every verb returns a NEW `Frame` wrapping
  `call("verb", self._node, *args)` (never mutating the receiver), lowering via the
  existing `_expression` / `to_pure` path. Row-proxy lambdas are wired through the
  existing `lam` -- **no AST / lambda-source introspection**: a per-row method
  (`filter` / `extend` / the `group_by` agg map) calls `lam(["r"], f)`, a join
  condition `lam(["l", "r"], f)`, and the windowed-extend column lambda the
  canonical `lam(["p", "w", "r"], f)`; arity is fixed per method, so the parameter
  names are explicit (as `lam` already requires) and the user just writes
  `lambda r: r.amt > 5`. Construct with `Frame.from_tds(text)` (inline `#TDS{...}#`
  literal -- the primary, fully working source) or `Frame.from_db(database, table)`
  (a `#>{database.table}#` database-table source -- see below). Methods (each
  delegates to the existing builder): `from_tds` / `from_db`; `filter(p)`;
  `select(*names)` (one name -> `col`, many -> `cols`); `extend(*("name", fn))`
  (one -> `fcol`, many -> `fcols`); `group_by(keys, *("name", map, reduce))` (keys
  a name or list -> `cols`; aggs -> `agg` / `aggs`); `join(other, on, kind=INNER)` +
  `inner_join` / `left_join` / `right_join` / `full_join` convenience;
  `as_of_join(other, on)`; `sort(*specs)` (each an `asc("c")` / `desc("c")` or a
  bare name defaulting ascending; one -> scalar, many -> `array` list);
  `limit(n)` / `drop(n)` / `slice(start, stop)` / `distinct()` /
  `concatenate(other)`; `rename(old, new)`; `pivot(on, ("name", map, reduce))`;
  `window_extend(over_spec, ("name", fn) | ("name", map, reduce))` (the chosen
  window shape -- an explicit `over(...)` spec + a 3-param window lambda column,
  reusing `over` / `rows` / `range_` / `unbounded`); and the readers `to_m3()`
  (the node) / `to_pure()` (emitted Pure) / `__repr__`. A relation-valued `other`
  in `join` / `as_of_join` / `concatenate` may be a `Frame`, a raw node, or a
  `tds` / `db_table` source (a `Frame` is unwrapped to its node). To support the
  bare-name sort sugar, `asc` / `desc` now also accept a column-*name* string
  (promoting it to a `col`, backward-compatible with the existing `asc(col(...))`).
  **Validation**: `tests/test_frame.py` (jar-free) asserts, per verb, that
  `Frame(...).to_m3()` equals the equivalent hand-written `call(...)` / builder
  graph under the shared `canon` (proving the facade is faithful sugar over the
  verbs), that a verb is immutable (it does not mutate the receiver), and that
  `.to_pure()` emits the exact expected strings (per verb + a multi-verb
  `filter -> extend -> group_by -> sort -> limit` chain, a join, a windowed
  extend, and the whole chain's graph vs the builder graph). The real engine
  (`tests/test_legend_bridge.py`) PARSES + COMPILES a non-trivial `Frame` chain
  (`filter -> groupBy -> sort -> limit`), a `filter -> inner_join` chain, and a
  `window_extend`, each hitting the same plan-gen execution boundary as the free
  builders. (An `extend(~c:{r | $r.amt * 2})` step BEFORE a `groupBy` was probed
  and is a genuine engine *compile* constraint -- the engine infers the derived
  column `[0..1]` and rejects "Collection element must have a multiplicity [1]" --
  so it is excluded from the compilable engine chain; the `Frame.extend` sugar
  itself is exercised jar-free.)
- [x] **`from_db` / `#>{db.table}#` database-table source (slice 3).**
  `db_table(database, table)` (in `compile/expressions.py`, re-exported) builds a
  verbatim `#>{database.table}#` source -- mirroring the `tds` pattern: an
  `InstanceValue` discriminated by a `Relation`-rawType marker (distinct from the
  `tds` `RelationType` and the `enum_ref` `Enumeration`), emitted unquoted by
  `m3_to_pure._is_db_table`; no path is parsed and no m3 store type is added.
  `Frame.from_db(database, table)` wraps it. The real engine **PARSES** this source
  (it becomes a `classInstance` of `type ">"` whose value is the `[database, table]`
  path, and relation verbs resolve over it -- pinned in `tests/test_legend_bridge.py`),
  but it only **COMPILES once the named store is defined**: with no database it
  fails compile with `The store '<database>' can't be found.` (a DISTINCT, earlier
  error than the relation plan-gen "not supported yet" boundary -- also pinned).
  This sugar layer deliberately does NOT fabricate a database/store definition;
  real `from_db` execution needs a modelled relational store + connection + runtime.
  **Reverse-parse now lands:** the vendored `M3CoreParser` grammar lexes
  `#>{db::Store.table}#` as one `DSL_TEXT` `dsl()` island (the SAME accessor as a
  `#TDS{...}#` literal -- the whole `#...#` token, `::` and interior `.` intact),
  so `pure_expr._lower_atomic` dispatches the `#>{...}#` prefix to `db_table`
  (passing the verbatim token, no fragile last-`.` path split) and `#TDS{...}#` to
  `tds`. A bare `db_table(...)` and a `db_table(...)->filter(...)->limit(...)` chain
  now round-trip `Python -> m3 -> Pure -> m3` byte-faithfully and canon-equal the
  forward builder (and a `Frame.from_db(...)` chain too) -- pinned in
  `tests/test_relation.py`.
  Follow-ons:
    - **a store-aware `from_db` compile/execute path** -- model a relational store +
      connection + runtime so a `#>{db.table}#` query compiles and executes against
      a real database (needs the protocol-model / store work, Tier 2).
  Deferred follow-ons:
    - **a batched `evalMany` bridge command** -- compile the model once and
      evaluate many expressions in one JVM (a Java-side change, explicitly *not*
      bundled with this slice).
- [x] **Named OLAP functions (slice 4).** The relation window functions, written
  the pylegend way as methods on the partial-frame proxy `p` inside a windowed
  `extend` column lambda `{p, w, r | ...}`. No new m3 type and no new emit/lowering
  -- they are ordinary `call(...)` arrow graphs that already round-trip through
  `pure_expr`; the ONLY new machinery is a tiny snake->camel alias map. The crux:
  pylegend spells the multi-word functions snake_case (`row_number` / `dense_rank`),
  but Pure's are camelCase (`rowNumber` / `denseRank`) and the Legend engine
  REJECTS the snake forms ("Function does not exist 'row_number'"). So
  `_OLAP_METHOD_ALIASES = {"row_number": "rowNumber", "dense_rank": "denseRank"}`
  is applied in `_Accessor.__call__` (the method-CALL path) ONLY -- never in
  `__getattr__` property access (`r.order_id` stays the column), and never for
  non-OLAP calls (`$c->sum()` / `$x->ascending()` are untouched). Single-word names
  (`rank` / `lag` / `lead`) and the direct camelCase (`p.rowNumber(r)`) already
  match Pure and pass through unchanged. **Engine-resolved compilable forms** (each
  reaches the `extend_..._Window_..._{FuncColSpec,AggColSpec}_...` plan-gen boundary
  via the Legend bridge): `rowNumber($p, $r)` (p, r -- NOT p, w, r),
  `rank($p, $w, $r)`, `denseRank($p, $w, $r)`, `lag($p, $r[, offset])` /
  `lead($p, $r[, offset])` (p, r + optional `Integer` offset), and `percentRank` /
  `cumulativeDistribution($p, $w, $r)` / `ntile($p, $r, n)`. A **windowed aggregate**
  (cumulative sum etc.) is the existing agg-colspec windowed `extend` column
  `~c:{p, w, r | $r.i}:{y | $y->sum()}` (resolving the `AggColSpec` overload) -- NOT
  a `$p->sum(...)` proxy call (the engine REJECTS that; only the collection
  `sum(Number[*])` matches). Validation: `tests/test_relation.py` (jar-free) asserts
  the snake spelling emits the camelCase function, equals the direct camelCase under
  `canon`, leaves property access / non-OLAP calls untouched, and round-trips each
  OLAP windowed `extend` `Python -> m3 -> Pure -> m3`; `tests/test_legend_bridge.py`
  PARSES + COMPILES each surviving OLAP function and the windowed aggregate via the
  real engine and pins the snake-name rejection.
- [x] **pylegend `legendql_api`-additive `Frame` alignment (slice 4).** All in
  `compile/`, all ADDITIVE -- the Pure-native `Frame` forms keep working unchanged.
  Aligns the `Frame` surface to FINOS pylegend's `legendql_api` primary spellings:
    - **Subscript columns** -- `Expr.__getitem__` so `r["Order Id"]` reaches a
      column with spaces / keywords, building the SAME node as attribute access
      `r.amt` (canon-equal), alongside it.
    - **String join kinds** -- `Frame.join(..., kind=...)` (and the convenience
      family) accept pylegend's strings `'INNER'` / `'LEFT_OUTER'` / `'RIGHT_OUTER'`
      / `'FULL'` (case-insensitive) via `join_kind`, mapping the SQL-ish names to
      Pure members (`LEFT_OUTER` -> `JoinKind.LEFT`, `RIGHT_OUTER` -> `JoinKind.RIGHT`);
      the Pure-native `JoinKind.*` enum-ref still passes through.
    - **`window(partition_by=, order_by=, frame=)`** -- a helper (re-exported and as
      `Frame.window`) returning the same `over(...)` node, so the pylegend two-step
      `f.window_extend(f.window(...), ("rn", lambda p, w, r: p.row_number(r)))`
      works; `partition_by` is a name / list / spec, `order_by` an `asc`/`desc` or
      bare name (ascending) or a list of them. Direct `over(...)` still usable.
    - **`Frame.rename`** -- accepts a mapping (`rename({"old": "new"})`) or kwargs
      (`rename(old="new")`) alongside the positional `rename(old, new)`; several
      pairs are emitted as a CHAIN of `->rename(~old, ~new)` verbs (one per entry --
      the chained form compiles, verified via the bridge).
    - **`Frame.as_of_join`** -- accepts pylegend's optional
      `(other, match_function, join_condition=None)`, wiring the 4-arg `asOfJoin`
      overload when given (and keeping the 3-arg form when omitted); both compile.
    - A `Frame` passed as `other` is still unwrapped to its node everywhere.
  Validation: `tests/test_frame.py` (jar-free) asserts each additive form builds the
  same graph as the Pure-native one under `canon` (subscript == attribute, string
  kind == enum kind, `window()` == `over()`, dict/kwargs `rename` == chained
  positional, 4-arg `as_of_join`), exact `.to_pure()` per form and a full
  pylegend-style chain; `tests/test_legend_bridge.py` PARSES + COMPILES the
  pylegend-style chain (subscript + string join kind + `window()` + an OLAP column)
  and the 4-arg `as_of_join` via the real engine.
- [x] **Typed `Schema` / `Column` layer for `Frame` (Tier 1, slice 5).** A
  pylegend-style typed-schema layer the `Frame` carries alongside its node, in a
  new `compile/schema.py` (re-exported from `compile` and `compile.frame`). New
  classes: `Column(name, type)` (the `type` is an `m3.PrimitiveType` singleton or
  a Python builtin, **coerced via the EXACT `python_to_m3._PRIMITIVE` mapping**
  -- no second source of truth), with pylegend-style snake_case factories
  `Column.string` / `.integer` / `.float_` / `.boolean` / `.decimal` / `.byte` /
  `.strict_date` / `.date_time` / `.strict_time`; `Schema(columns=...)` with
  `Schema.from_columns(*cols)` and the kwargs `Schema.of(cust=str, amt=int,
  ship_date=date)` constructor; `SchemaError(ValueError)` for the validation
  raise (a `ValueError` subclass so existing `raises(ValueError)` checks keep
  working). `Frame.from_db` / `Frame.from_tds` gain an optional `schema=` kwarg,
  and the new `Frame.from_rows(schema, rows)` builds the `#TDS{...}#` literal
  from typed rows (tuples or dicts) -- each cell serialized in its column's
  Pure-canonical inner-text form (`Integer`/`Byte` -> `str(int)`, `Float` -> `str(float)`,
  `Boolean` -> `true`/`false`, `String` -> raw text (rejects `,`/`\n`/`#`),
  `StrictDate` -> `YYYY-MM-DD` (`.isoformat()`), `DateTime` -> `YYYY-MM-DDTHH:MM:SS`
  (`.isoformat(sep="T")`, microseconds dropped), `StrictTime` -> `HH:MM:SS`).
  Every verb is **additively** schema-aware: with `schema=None` the `Frame`
  emits BYTE-IDENTICAL Pure to before (the full prior fast + integration suites
  still pass unchanged); with a schema attached, verbs validate explicit string
  column args BEFORE building the m3 node and propagate per a rule table:
  pass-through (`filter`, `sort`, `limit`, `slice`, `distinct`, `drop(n)`),
  computed mechanically (`select` -> selected, `drop(*names)` -> remaining via
  `select(~[remaining])`, `rename` -> one-for-one renamed, `concatenate` ->
  left when both schemas equal else SchemaError on mismatch / `None` if either
  unknown), and unknown-by-default with an `out_schema=` kwarg
  (`extend` / `window_extend` / `group_by` / `pivot` / `join` / `as_of_join`).
  Joins with both schemas known infer the column union and raise SchemaError on
  a name collision; `out_schema=` overrides. Validation errors carry the verb
  name, the bad column, and the available columns. Validation: `tests/test_frame_schema.py`
  (NEW) pins the singleton/builtin coercion (one assertion per primitive),
  factory construction, `Schema.of(**typed)`, `__getitem__` int/str, and
  `SchemaError` on missing column; `tests/test_frame.py` (extended) covers
  `from_db`/`from_tds`/`from_rows` schema carry, the getters, every verb's
  positive + negative cases, propagation per rule, `concatenate` schema
  mismatch raise, `join` collision raise (both sides named in the message),
  and -- as the "no regression" proof -- a typed chain emits the SAME Pure
  text + m3 graph (under `canon`) as the untyped equivalent. Bridge spot
  checks (`tests/test_legend_bridge.py`): a `from_rows`-built TDS with mixed
  primitives + `select` PARSES + COMPILES via the real engine (pins the
  per-primitive serialization choice -- a future engine that rejected one
  would surface here), and a typed `from_db` + filter + `out_schema`-bearing
  `extend` + `select` + `sort` chain PARSES + COMPILES. The schema is NOT in
  the m3 type system: no codegen / metamodel changes.
- [ ] **Schema-aware row proxy / typed expression bodies (Tier 2).** The next
  layer on top of the typed `Schema`: thread the column types into the
  row-proxy `Expr` so `r.amt` carries the column's primitive (typing lambda
  bodies), validate column names *inside* lambda bodies against the schema
  (catching `r.ammt` at build-time, not just explicit verb-arg strings), and
  infer the output type of derived columns (`r.amt * 2` -> `Integer`) so
  `extend(...)` and `group_by(...)` *autocompute* their `out_schema` instead
  of requiring it. Out of this slice; the Tier 1 layer above is the
  foundation.
- [ ] **Lambda / constraint representation.** `Constraint` and
  `ConcreteFunctionDefinition` bodies remain unmodelled. (Multi-parameter
  lambdas are now built by `lam` -- see the relation/TDS foundation above.)
- [ ] **Project hygiene.** Add a `py.typed` marker (downstream typing), a CI
  workflow running the tests + drift check, and a console entry point for the
  generator.
