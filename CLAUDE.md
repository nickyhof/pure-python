# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python representation of the Pure (FINOS Legend) M3 metamodel. It (a) **generates** Python dataclasses for the Pure type system from the upstream `legend-pure` sources, and (b) provides a **compile layer** that converts between plain Python dataclasses and `m3` instances, and renders `m3` back out as Python source or as actual Pure grammar.

## Commands

```bash
pip install -e ".[dev]"        # Python >= 3.10; installs pytest
python -m pytest -q            # run all tests
python -m pytest tests/test_compile.py::test_rich_round_trip_preserves_generics_annotations_and_qualified_properties -q   # single test
python -m pure_python.codegen.generate   # regenerate pure_python/m3/metamodel.py from vendored .pure sources
mvn -f legend-bridge package             # (optional, needs JDK 21 + Maven) build the Legend bridge jar
```

There is no separate lint step configured. The Legend bridge is optional: its
tests (`tests/test_legend_bridge.py`) skip unless the jar exists (or
`PURE_PYTHON_LEGEND_BRIDGE_JAR` points at one).

## Critical: the metamodel is generated, not hand-written

`pure_python/m3/metamodel.py` is **emitted** by `pure_python.codegen` and must never be edited by hand. `tests/test_generated.py::test_committed_metamodel_matches_generator` asserts the committed file is byte-for-byte equal to `codegen.generate.render()`. Therefore **any change to a parser, the schema, or the emitter requires regenerating and committing the new `metamodel.py`** (`python -m pure_python.codegen.generate`) or that test fails.

`vendor/legend-pure/*.pure` are verbatim FINOS source files pinned to a specific commit (see `vendor/legend-pure/SOURCE.txt`). To track upstream changes, re-copy them at the new commit and regenerate.

## Architecture

Three core layers / packages, plus an optional fourth (the Legend bridge).

**1. `pure_python/codegen/` — source → generated metamodel.** Two upstream formats feed one neutral model, each parsed by legend-pure's *own* ANTLR grammar (vendored `.g4` + generated Python-target parser in `codegen/_pure_antlr/`; see its docstring):

- `parser.py` lifts the bootstrap `m3.pure`, whose ~85 core metaclasses are written in Pure's low-level *instance-construction* syntax (`^Class … { Root.children[…].properties[x] : value }`), into an `Instance`/`Ref` graph. It walks legend-pure's **M4** grammar (`M4AntlrParser`); the semantic `_split_lhs` interpretation of the `<owner>.properties[<prop>]` assignment paths lives here.
- `grammar.py` parses the *readable* Pure class grammar (`Class p::N<T> extends B { x : T[1]; }`) used by `relation.pure`, `variant.pure`, `milestoning.pure`, by walking legend-pure's `M3CoreParser` grammar. It captures classes, associations, enums, profiles, type parameters, type arguments, qualified/derived properties (by signature), and **property-level** `<<stereotype>>` / `{tagged value}` annotations; imports, functions and class/enum-header annotations are ignored. Being the real grammar it is strict (e.g. an empty `foo() {}` derived-property body is a syntax error — `m3_to_pure` emits `foo() { [] }`).
- `schema.py` (`build_metamodel`) lowers the instance graph into format-neutral dataclasses: `MetaClass` / `MetaProperty` / `MetaEnum` / `MetaPrimitive` / `MetaMultiplicity` / `MetaProfile` inside a `MetaModel`.
- `generate.py` is the orchestrator: `build_model()` parses the bootstrap then merges the grammar files into one `MetaModel`; `render()` returns the emitted module source (no write — used by the drift test); `generate()` writes `metamodel.py`.
- `emit.py` turns a `MetaModel` into Python source: `@dataclass(kw_only=True)` classes topologically sorted by inheritance, with `typing.TypeVar` declarations and `typing.Generic[...]` bases for parameterised types.

**2. `pure_python/m3/` — the generated type system.** `metamodel.py` defines the dataclasses (`Class`, `Property`, `Type`, `GenericType`, `Multiplicity`, `Enumeration`, `Association`, the `Function`/`ValueSpecification` trees, plus the relation/variant/milestoning types). `__init__.py` re-exports everything and also exposes multiplicity singletons (`PureOne`, `ZeroOne`, `ZeroMany`, `OneMany`, `PureZero`) and primitive singletons (`String`, `Integer`, …).

**3. `pure_python/compile/` — bidirectional bridge.**

- `python_to_m3.py` (`Compiler`, `compile_class`): a plain dataclass → `m3.Class`; each field → `m3.Property` with `genericType` + `multiplicity` inferred from the type hint. The `Compiler` **caches** built types so self/mutual references share one instance (cycles are fine — dataclass `repr` is recursion-guarded and `owner` back-refs are set). Understands `typing.Generic[T]` → `typeParameters`, `TypeVar` fields → `GenericType.typeParameter`, subscripted refs (`Box[int]`) → `typeArguments`, `typing.Annotated[...]` markers, and `@property` → `QualifiedProperty`.
- `m3_to_python.py` (`to_module`, `to_source`): `m3` → a self-contained importable module of ordinary (positional) dataclasses.
- `m3_to_pure.py` (`to_pure`, `to_pure_module`): `m3` → actual Pure grammar source (the inverse of `codegen/grammar.py`).
- `annotations.py`: the `Stereotype` and `Tag` markers used inside `typing.Annotated` to attach Pure stereotypes / tagged values to fields.

**4. `pure_python/legend/` + `legend-bridge/` — bridge to the real Legend engine (optional).** `legend-bridge/` is a tiny Java CLI built (shaded jar) on the published `org.finos.legend.engine` grammar + compiler + plan-execution artifacts; `pure_python/legend/bridge.py` (`LegendBridge`) shells out to it one request at a time (command in argv, payload on stdin, result on stdout). `parse` runs pure-python's emitted Pure through Legend's *real* `PureGrammarParser` and returns `PureModelContextData` JSON; `compose` renders that JSON back via `PureGrammarComposer`; `eval` compiles a model + lambda (`Compiler` → `PlanGenerator` → `PlanExecutor`) and returns the executed value. This makes Legend itself the oracle for what pure-python emits/represents, and seeds the Tier 2 protocol-model work. Notes: the plan client version is `vX_X_X` (concrete versions like `v1_33_0` are not registered in the bundled core platform); `eval` currently returns only `ConstantResult` values; and the harness silences stdout during compilation so engine logging can't corrupt the JSON result. The package degrades gracefully: `LegendBridge.available()` is `False` when the jar/JVM is absent, so tests skip.

The Python ↔ Pure round-trip that matters here (Python dataclasses → `m3` → Pure → back) is covered by `tests/test_full_round_trip.py` and `tests/test_pure_emit.py`, with emitted Pure validated against the real engine by the bridge. Legend's **reverse-PCT** corpus for the Python targets (`rev('<pure>', '<python>')` pairs — PyLegend-flavoured) is vendored, **dormant**, at `vendor/legend-engine/reverse-pct/` (see its `SOURCE.txt` and `TODO.md`) for a future pure-python reverse-PCT adapter; nothing consumes it yet.

## Conventions worth knowing

- **Multiplicity from type hints:** bare `X` → `[1..1]`, `X | None` → `[0..1]`, `list[X]` → `[0..*]`.
- **Primitive mapping:** `str/bool/int/float/Decimal/bytes/date/datetime/time` ↔ Pure `String/Boolean/Integer/Float/Decimal/Byte/StrictDate/DateTime/StrictTime`. (`bytes`→`Byte`→`int` is lossy on the way back.)
- **Keyword escaping:** a Pure name that is a Python keyword gets a trailing underscore in emitted Python (e.g. `from` → `from_`).
- **Dataclass style differs by layer:** generated `m3` classes are `kw_only=True`; the compile layer emits ordinary dataclasses with required fields ordered before defaulted ones.
- **Round-trip tests are the spec.** Forward = `Python → m3 → Python` (`test_compile.py`); reverse = `m3 → Pure → grammar parser` (`test_pure_emit.py`); the full `Python → m3 → Pure → m3 → Python` loop is in `test_full_round_trip.py`. Type arguments, qualified properties and property-level stereotypes / tagged values all survive the reverse loop; the remaining Pure-boundary drops are enum-member values (Pure enums are name-only) and class-level annotations.

`TODO.md` tracks the known fidelity gaps and planned follow-ons.

## Git

- Do not include the Claude Code session URL (the `https://claude.ai/code/session_…` trailer) in commit messages or PR descriptions.
