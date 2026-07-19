# Ternary Partition Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalise `LatticeIndex` partition routing from exact dict lookup to ternary + specificity semantics (wildcard/default partitions, load-time ambiguity validation), implemented by an embedded `ExpressionRulesEngine` over a meta rules-table.

**Architecture:** A new `MatchStrategy.EXACT_KEY` (rule-side-wildcard-only exact match) gives routing its asymmetric ternary semantics. `LatticeIndex` builds a meta rules-table (one row per partition) evaluated by an internal filter engine; exact dict hits keep the O(1) fast path; `index()` validates the suite via an exhaustive witness-context matrix routed through the same meta-engine.

**Tech Stack:** Python 3.12, pydantic, mountainash expressions/relations (backend-agnostic), polars in tests only, pytest via hatch.

**Spec:** `docs/superpowers/specs/2026-07-19-ternary-partition-routing-design.md` (read it before starting — it is the authority on semantics).

## Global Constraints

- Branch: `feature/ternary-partition-routing` off `develop` @ `496f84a` (spec header). Create it first if it doesn't exist: `git checkout develop && git checkout -b feature/ternary-partition-routing`.
- **Backend purity (ENFORCED by `tests/test_backend_purity.py`):** no module under `src/mountainash_rules/` may import polars/ibis/narwhals directly. This plan needs **no new `# allow:` tag** — the purity count stays at three.
- Ternary encoding everywhere: 1 = match, 0 = wildcard/unknown, −1 = non-match; survive on `min ≥ 0`; specificity = count of 1s.
- Routing semantics are **asymmetric**: only the rule side (partition key) has a wildcard (UNKNOWN sentinel; `null` for bool). Context-side sentinels (NOT_SET, UNKNOWN, `None`) score −1 against specific keys, 0 against wildcards.
- Partition keys may **not** contain NOT_SET sentinels (`ValueError` at `index()`, always-on).
- `AmbiguousPartitionError` subclasses `KeyError` (deliberate — spec §1/§9).
- No-survivor routing stays a plain `KeyError` whose message starts `"No lattice"` (existing tests match on that prefix).
- TDD: failing test first, then implementation. Test commands: `hatch run test:test-target <path>::<class>::<test>` (single), `hatch run test:test-quick` (suite). Lint: `hatch run ruff:check`. Types: `hatch run mypy:check`.
- Style: `import typing as t`, Google docstrings, ruff-formatted. Match surrounding code.
- Do not modify `Lattice`, `Lattice.save/load`, `build`/`build_all`, or any service code.

## File Structure

| File | Responsibility |
|---|---|
| `src/mountainash_rules/core/constants.py` | add `MatchStrategy.EXACT_KEY` member |
| `src/mountainash_rules/core/compiler.py` | add `_compile_exact_key` + dispatch case |
| `src/mountainash_rules/engines/accumulator/engine.py` | `_extract_partition_key` NOT_SET fill; `_normalize_partition_key`; `index()` gains `validate`/`max_witnesses` |
| `src/mountainash_rules/engines/accumulator/lattice.py` | `AmbiguousPartitionError`; `LatticeIndex` structural checks, meta-engine, `_route`, witness-matrix validation, `apply`/`apply_batch` routing |
| `src/mountainash_rules/__init__.py` | export `AmbiguousPartitionError` |
| `tests/core/test_compiler.py` | `TestExactKeyCompilation` |
| `tests/accumulator/test_apply.py` | `_extract_partition_key` fill tests |
| `tests/accumulator/test_lattice.py` | `TestTernaryRouting`, `TestIndexValidation`, persistence round-trip |
| `tests/filter/test_batch_evaluation.py` | batch routing tests (existing index-batch tests live here) |
| `CLAUDE.md` | strategy table + accumulator section updates |

---

### Task 1: `MatchStrategy.EXACT_KEY` — compiler strategy

**Files:**
- Modify: `src/mountainash_rules/core/constants.py` (MatchStrategy enum, ~line 10)
- Modify: `src/mountainash_rules/core/compiler.py` (dispatch `match` block ~line 40, new method after `_compile_exact`)
- Test: `tests/core/test_compiler.py`

**Interfaces:**
- Consumes: existing `DimensionCompiler.compile_dimension`, `unknown_sentinel_for`, `CTX_PREFIX`.
- Produces: `MatchStrategy.EXACT_KEY = "exact_key"`; `DimensionCompiler._compile_exact_key(dim) -> BaseExpressionAPI`. Semantics: rule value == UNKNOWN sentinel → 0; rule == context → 1; otherwise (including context-side NOT_SET/UNKNOWN/null) → −1. Bool: rule null → 0; else eq → 1; else −1.

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_compiler.py` (imports of `NOT_SET` may need adding to the existing `from mountainash_rules.core.constants import ...` line):

```python
class TestExactKeyCompilation:
    """EXACT_KEY: rule-side wildcard only — context sentinels are non-matches."""

    def test_rule_unknown_is_wildcard(self, compiler):
        dim = Dimension(
            dimension_name="region",
            match_strategy=MatchStrategy.EXACT_KEY,
            data_type="str",
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "region": ["AU", UNKNOWN, "UK"],
            f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        assert result["__t_region"].to_list() == [1, 0, -1]

    def test_context_not_set_never_matches_specific(self, compiler):
        # The asymmetry that distinguishes EXACT_KEY from EXACT:
        # a NOT_SET context is -1 against specific keys (EXACT gives 0).
        dim = Dimension(
            dimension_name="region",
            match_strategy=MatchStrategy.EXACT_KEY,
            data_type="str",
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "region": ["AU", UNKNOWN],
            f"{CTX_PREFIX}region": [NOT_SET, NOT_SET],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        assert result["__t_region"].to_list() == [-1, 0]

    def test_context_unknown_never_matches_specific(self, compiler):
        dim = Dimension(
            dimension_name="region",
            match_strategy=MatchStrategy.EXACT_KEY,
            data_type="str",
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "region": ["AU", UNKNOWN],
            f"{CTX_PREFIX}region": [UNKNOWN, UNKNOWN],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        assert result["__t_region"].to_list() == [-1, 0]

    def test_numeric_sentinels(self, compiler):
        dim = Dimension(
            dimension_name="product_id",
            match_strategy=MatchStrategy.EXACT_KEY,
            data_type="int",
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "product_id": [1, UNKNOWN_NUMERIC, 2],
            f"{CTX_PREFIX}product_id": [1, 1, 1],
        })
        result = df.with_columns(expr.name.alias("__t_product_id").compile(df, booleanizer=None))
        assert result["__t_product_id"].to_list() == [1, 0, -1]

    def test_bool_rule_null_is_wildcard_context_null_is_not(self, compiler):
        dim = Dimension(
            dimension_name="flag",
            match_strategy=MatchStrategy.EXACT_KEY,
            data_type="bool",
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "flag": [True, None, True, None],
            f"{CTX_PREFIX}flag": [True, True, None, None],
        })
        result = df.with_columns(expr.name.alias("__t_flag").compile(df, booleanizer=None))
        # rule null -> 0 regardless of context; context null vs specific -> -1
        assert result["__t_flag"].to_list() == [1, 0, -1, 0]
```

`NOT_SET` must be added to the constants import at the top of the file if not present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target tests/core/test_compiler.py::TestExactKeyCompilation -v`
Expected: FAIL — `AttributeError: EXACT_KEY` (enum member doesn't exist).

- [ ] **Step 3: Implement**

In `src/mountainash_rules/core/constants.py`, add to `MatchStrategy` after `NOT_EQUAL`:

```python
    EXACT_KEY = "exact_key"
```

In `src/mountainash_rules/core/compiler.py`:

Add a dispatch case in `compile_dimension`'s `match` block, next to the EXACT case:

```python
            case MatchStrategy.EXACT_KEY:
                return self._compile_exact_key(dim)
```

Add the method directly after `_compile_exact` (it needs `unknown_sentinel_for` added to the existing `constants` import):

```python
    def _compile_exact_key(self, dim: Dimension) -> BaseExpressionAPI:
        """Rule-side-wildcard-only exact match (partition-key routing).

        Only the rule side has a wildcard (UNKNOWN sentinel; null for
        bool). A context-side sentinel is an ordinary non-matching value:
        specific keys must never match an unknown/unset context.
        """
        rule_col = ma.col(dim.resolved_rule_field)
        ctx_col = ma.col(CTX_PREFIX + dim.dimension_name)
        if dim.data_type is DataType.BOOL:
            wildcard = rule_col.is_null()
        else:
            wildcard = rule_col.__eq__(
                ma.lit(unknown_sentinel_for(dim.data_type))
            )
        return (
            ma.when(wildcard).then(0)
            .when(rule_col.__eq__(ctx_col)).then(1)
            .otherwise(-1)
        )
```

(A null comparison result falls through `when` to `otherwise(-1)`, which is exactly the required context-null behaviour — same pattern as the existing string-strategy sentinel wrapper at `_wrap`.)

No `dimension.py` validation changes: `EXACT_KEY` accepts every data type and has no strategy-specific fields, like `EXACT`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch run test:test-target tests/core/test_compiler.py::TestExactKeyCompilation -v`
Expected: 5 PASS.

- [ ] **Step 5: Run the quick suite, lint, and commit**

Run: `hatch run test:test-quick && hatch run ruff:check`
Expected: all pass (the parametrised purity and strategy tests must stay green).

```bash
git add src/mountainash_rules/core/constants.py src/mountainash_rules/core/compiler.py tests/core/test_compiler.py
git commit -m "feat: add MatchStrategy.EXACT_KEY — rule-side-wildcard-only exact match"
```

---

### Task 2: `_extract_partition_key` — NOT_SET fill for missing/null fields

**Files:**
- Modify: `src/mountainash_rules/engines/accumulator/engine.py:548-565` (`_extract_partition_key`)
- Test: `tests/accumulator/test_apply.py`

**Interfaces:**
- Consumes: `not_set_sentinel_for` from `core.constants` (already importable), `DataType`.
- Produces: `AccumulatorEngine._extract_partition_key(context) -> tuple` — missing **or explicitly-None** context field becomes `not_set_sentinel_for(d.data_type)` (`None` for bool) instead of raising `KeyError`. Also `AccumulatorEngine._normalize_partition_key(key: tuple) -> tuple` applying the same None→sentinel rule to an already-extracted tuple (used by batch routing in Task 4).

- [ ] **Step 1: Write the failing tests**

Append to `tests/accumulator/test_apply.py` (add `NOT_SET_NUMERIC` to the constants import at the top):

```python
class TestExtractPartitionKey:
    def _engine(self):
        metadata = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="product_id",
                match_strategy=MatchStrategy.EXACT,
                data_type=int,
                role=DimensionRole.CONTEXT_KEY,
            ),
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        return AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )

    def test_missing_key_field_fills_not_set(self):
        key = self._engine()._extract_partition_key({"channel": "BROKER"})
        assert key == (NOT_SET_NUMERIC,)

    def test_explicit_none_fills_not_set(self):
        key = self._engine()._extract_partition_key(
            {"product_id": None, "channel": "BROKER"}
        )
        assert key == (NOT_SET_NUMERIC,)

    def test_present_value_passes_through(self):
        key = self._engine()._extract_partition_key(
            {"product_id": 7, "channel": "BROKER"}
        )
        assert key == (7,)

    def test_normalize_partition_key(self):
        engine = self._engine()
        assert engine._normalize_partition_key((None,)) == (NOT_SET_NUMERIC,)
        assert engine._normalize_partition_key((7,)) == (7,)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target tests/accumulator/test_apply.py::TestExtractPartitionKey -v`
Expected: FAIL — `KeyError: 'product_id'` on the first two, `AttributeError: _normalize_partition_key` on the last.

- [ ] **Step 3: Implement**

Replace the body of `_extract_partition_key` in `src/mountainash_rules/engines/accumulator/engine.py` and add the helper. Ensure `DataType` and `not_set_sentinel_for` are in the module's `core.constants` import:

```python
    def _extract_partition_key(self, context: t.Any) -> tuple:
        """Extract the partition key tuple from a context object.

        Missing or explicitly-null key fields become the typed NOT_SET
        sentinel (None for bool) so the context can still route — a
        NOT_SET value matches wildcard partitions only.
        """
        if isinstance(context, BaseModel):
            raw = context.model_dump()
        elif isinstance(context, dict):
            raw = context
        else:
            raise TypeError(
                f"Context must be a BaseModel or dict, got {type(context).__name__}"
            )
        return self._normalize_partition_key(tuple(
            raw.get(d.resolved_context_field)
            for d in self._context_key_dims
        ))

    def _normalize_partition_key(self, key: tuple) -> tuple:
        """Map None key values to the typed NOT_SET sentinel (None for bool)."""
        return tuple(
            v if v is not None or d.data_type is DataType.BOOL
            else not_set_sentinel_for(d.data_type)
            for v, d in zip(key, self._context_key_dims)
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch run test:test-target tests/accumulator/test_apply.py::TestExtractPartitionKey -v`
Expected: 4 PASS. Then `hatch run test:test-quick` — the existing `test_apply_auto_missing_key_raises` must still pass (context `product_id=99` is present, so it still misses via the dict → `KeyError: "No lattice..."`).

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/engines/accumulator/engine.py tests/accumulator/test_apply.py
git commit -m "feat: NOT_SET fill for missing/null partition key fields"
```

---

### Task 3: Meta-engine routing — `AmbiguousPartitionError`, structural checks, `_route`, `apply`

**Files:**
- Modify: `src/mountainash_rules/engines/accumulator/lattice.py` (`LatticeIndex.__init__`, `apply`; new exception; new `_route`)
- Modify: `src/mountainash_rules/__init__.py` (export)
- Test: `tests/accumulator/test_lattice.py`

**Interfaces:**
- Consumes: `MatchStrategy.EXACT_KEY` (Task 1), `engine._extract_partition_key` (Task 2), `ExpressionRulesEngine` (filter layer), `relation` (already imported in lattice.py).
- Produces:
  - `AmbiguousPartitionError(KeyError)` in `lattice.py`, exported from package root.
  - `LatticeIndex.__init__(engine, lattices, context_key_dims)` — raises `ValueError` on empty list, duplicate keys, NOT_SET-bearing keys, or a `partition_key=None` lattice when key dims exist; builds `self._lattices` (list), `self._map` (dict), `self._meta_engine` (or `None` when there are no key dims).
  - `LatticeIndex._route(key: tuple) -> Lattice` — meta-engine evaluate; unique top-specificity survivor wins; 0 survivors → `KeyError` starting `"No lattice"` and listing served keys; ≥2 tied → `AmbiguousPartitionError`.
  - `LatticeIndex.apply(context, dimensions=None)` — dict fast path, `_route` on miss.
  - Validation (Task 5) reuses `self._meta_engine` and `self._lattices`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/accumulator/test_lattice.py`. The file already imports `Lattice`; extend imports as shown:

```python
from pydantic import BaseModel

from mountainash_rules import AmbiguousPartitionError
from mountainash_rules.engines.accumulator.engine import AccumulatorEngine
from mountainash_rules.engines.accumulator.aggregate import Aggregate
from mountainash_rules.core.constants import (
    UNKNOWN,
    UNKNOWN_NUMERIC,
    NOT_SET,
    DimensionRole,
    MatchStrategy,
)
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata


class RoutingContext(BaseModel):
    region: str | None = None
    channel: str | None = None
    product: str


def _routing_engine():
    metadata = DimensionsMetadata(dimensions=[
        Dimension(
            dimension_name="region",
            match_strategy=MatchStrategy.EXACT,
            role=DimensionRole.CONTEXT_KEY,
        ),
        Dimension(
            dimension_name="channel",
            match_strategy=MatchStrategy.EXACT,
            role=DimensionRole.CONTEXT_KEY,
        ),
        Dimension(dimension_name="product", match_strategy=MatchStrategy.EXACT),
    ])
    return AccumulatorEngine(
        dimension_metadata=metadata,
        aggregates=[Aggregate(column_name="margin")],
    )


def _routing_rules(keys: list[tuple[str, str]]) -> pl.DataFrame:
    """One rule per (region, channel) partition key; UNKNOWN = wildcard."""
    return pl.DataFrame({
        "region": [k[0] for k in keys],
        "channel": [k[1] for k in keys],
        "rule_name": [f"r{i}" for i in range(len(keys))],
        "product": ["GOLD"] * len(keys),
        "margin": [1.0] * len(keys),
    })


def _index_for(keys, validate=True):
    engine = _routing_engine()
    lattices = engine.build_all(_routing_rules(keys))
    return engine, engine.index(lattices, validate=validate)


class TestTernaryRouting:
    def test_default_partition_catches_unmatched_context(self):
        engine, index = _index_for([("AU", "BROKER"), (UNKNOWN, UNKNOWN)])
        result = index.apply(RoutingContext(region="NZ", channel="DIRECT", product="GOLD"))
        assert result.count >= 1  # routed to the all-wildcard default

    def test_partial_specific_miss_falls_to_default(self):
        engine, index = _index_for([("AU", "BROKER"), (UNKNOWN, UNKNOWN)])
        # Exercise _route (not the dict fast path): (AU, DIRECT) is not an
        # exact key; (AU, BROKER) dies on channel; default survives.
        result = index.apply(RoutingContext(region="AU", channel="DIRECT", product="GOLD"))
        assert result.count >= 1

    def test_exact_hit_uses_fast_path_and_wins(self):
        engine, index = _index_for([("AU", "BROKER"), (UNKNOWN, UNKNOWN)])
        result = index.apply(RoutingContext(region="AU", channel="BROKER", product="GOLD"))
        assert result.count >= 1

    def test_runtime_ambiguity_raises(self):
        engine, index = _index_for(
            [("AU", UNKNOWN), (UNKNOWN, "BROKER")], validate=False
        )
        with pytest.raises(AmbiguousPartitionError):
            index.apply(RoutingContext(region="AU", channel="BROKER", product="GOLD"))

    def test_missing_key_field_routes_to_default(self):
        engine, index = _index_for([("AU", "BROKER"), (UNKNOWN, UNKNOWN)])
        result = index.apply(RoutingContext(product="GOLD"))  # region/channel None
        assert result.count >= 1

    def test_missing_key_field_without_default_raises(self):
        engine, index = _index_for([("AU", "BROKER")])
        with pytest.raises(KeyError, match="No lattice"):
            index.apply(RoutingContext(product="GOLD"))

    def test_context_unknown_sentinel_is_wildcard_only(self):
        engine, index = _index_for([("AU", "BROKER"), (UNKNOWN, UNKNOWN)])
        result = index.apply(
            RoutingContext(region=UNKNOWN, channel="BROKER", product="GOLD")
        )
        # UNKNOWN region kills (AU, BROKER); only the default survives.
        assert result.count >= 1

    def test_wildcard_free_index_miss_raises_keyerror(self):
        engine, index = _index_for([("AU", "BROKER")])
        with pytest.raises(KeyError, match="No lattice"):
            index.apply(RoutingContext(region="US", channel="X", product="GOLD"))

    def test_ambiguous_is_a_keyerror(self):
        assert issubclass(AmbiguousPartitionError, KeyError)


class TestIndexStructuralChecks:
    def test_empty_index_raises(self):
        engine = _routing_engine()
        with pytest.raises(ValueError, match="at least one lattice"):
            engine.index([])

    def test_duplicate_keys_raise(self):
        engine = _routing_engine()
        lattices = engine.build_all(_routing_rules([("AU", "BROKER")]))
        with pytest.raises(ValueError, match="[Dd]uplicate"):
            engine.index(lattices + lattices)

    def test_not_set_key_raises(self):
        engine = _routing_engine()
        lattices = engine.build_all(_routing_rules([(NOT_SET, "BROKER")]))
        with pytest.raises(ValueError, match="NOT_SET"):
            engine.index(lattices)

    def test_keyless_lattice_with_key_dims_raises(self):
        engine = _routing_engine()
        (good,) = engine.build_all(_routing_rules([("AU", "BROKER")]))
        flat = Lattice(
            dataframe=good.combinations,
            metadata=good.metadata,
            aggregates=good.aggregates,
            partition_key=None,
        )
        with pytest.raises(ValueError, match="partition_key"):
            engine.index([good, flat])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target tests/accumulator/test_lattice.py::TestTernaryRouting tests/accumulator/test_lattice.py::TestIndexStructuralChecks -v`
Expected: FAIL — `ImportError: cannot import name 'AmbiguousPartitionError'`.

- [ ] **Step 3: Implement**

In `src/mountainash_rules/engines/accumulator/lattice.py`:

Extend the module imports:

```python
from mountainash_rules.core.constants import (
    DataType,
    DimensionRole,
    HitPolicy,
    MatchStrategy,
    not_set_sentinel_for,
    unknown_sentinel_for,
)
from mountainash_rules.core.dimension import Dimension
```

(`DimensionsMetadata` is already imported. Importing `ExpressionRulesEngine` at module top is fine — `engines/filter` only imports `core`, no cycle: `from mountainash_rules.engines.filter.engine import ExpressionRulesEngine`.)

Add the exception above `class LatticeIndex`:

```python
class AmbiguousPartitionError(KeyError):
    """Two or more partitions tie at top specificity for a context.

    Subclasses KeyError so existing partition-miss handlers keep working;
    reachable at runtime only when index() validation was opted out.
    """
```

Replace `LatticeIndex.__init__` and `apply`, and add `_route` and the meta-engine builder:

```python
class LatticeIndex:
    """Partition-key routing over a set of built lattices, built once.

    Routing uses the same ternary + specificity semantics as the
    constraint layer, evaluated by an embedded ExpressionRulesEngine over
    a meta rules-table (one row per partition). Exact key hits keep an
    O(1) dict fast path.
    """

    def __init__(self, engine, lattices: list["Lattice"], context_key_dims) -> None:
        self._engine = engine
        self._context_key_dims = list(context_key_dims)
        self._lattices = list(lattices)
        if not self._lattices:
            raise ValueError("index() requires at least one lattice")

        self._map: dict[tuple, Lattice] = {}
        for lattice in self._lattices:
            if lattice.partition_key is not None:
                key = tuple(
                    lattice.partition_key[d.dimension_name]
                    for d in self._context_key_dims
                )
            elif self._context_key_dims:
                raise ValueError(
                    "Lattice without a partition_key cannot be indexed "
                    "alongside CONTEXT_KEY dimensions"
                )
            else:
                key = ()
            for d, v in zip(self._context_key_dims, key):
                if (
                    d.data_type is not DataType.BOOL
                    and v == not_set_sentinel_for(d.data_type)
                ):
                    raise ValueError(
                        f"Partition key {key!r} contains the NOT_SET "
                        f"sentinel for dimension '{d.dimension_name}'; "
                        f"NOT_SET is a context-side sentinel and would "
                        f"collide with missing-field routing"
                    )
            if key in self._map:
                raise ValueError(f"Duplicate partition key {key!r}")
            self._map[key] = lattice

        self._meta_engine = (
            self._build_meta_engine() if self._context_key_dims else None
        )

    def _build_meta_engine(self) -> "ExpressionRulesEngine":
        """One meta-rule row per partition; EXACT_KEY per key dim."""
        columns: dict[str, list] = {
            d.dimension_name: [] for d in self._context_key_dims
        }
        columns["__partition_idx"] = []
        for idx, lattice in enumerate(self._lattices):
            for d in self._context_key_dims:
                columns[d.dimension_name].append(
                    lattice.partition_key[d.dimension_name]
                )
            columns["__partition_idx"].append(idx)
        meta_metadata = DimensionsMetadata(
            dimensions=[
                Dimension(
                    dimension_name=d.dimension_name,
                    context_field=d.resolved_context_field,
                    match_strategy=MatchStrategy.EXACT_KEY,
                    data_type=d.data_type,
                )
                for d in self._context_key_dims
            ],
            hit_policy=HitPolicy.COLLECT,
        )
        return ExpressionRulesEngine(
            rules=relation(columns).collect(),
            dimension_metadata=meta_metadata,
        )

    def _route(self, key: tuple) -> "Lattice":
        """Ternary + specificity routing for a normalised key tuple."""
        ctx = {
            d.resolved_context_field: key[i]
            for i, d in enumerate(self._context_key_dims)
        }
        rows = relation(self._meta_engine.evaluate(ctx).survivors).to_dict()
        idxs = rows["__partition_idx"]
        if not idxs:
            raise KeyError(
                f"No lattice for partition key {key!r}; served partition "
                f"keys: {sorted(self._map)!r}"
            )
        top = rows["__specificity"][0]  # survivors are rank-sorted
        tied = [
            i for i, s in zip(idxs, rows["__specificity"]) if s == top
        ]
        if len(tied) > 1:
            tied_keys = [
                tuple(
                    self._lattices[i].partition_key[d.dimension_name]
                    for d in self._context_key_dims
                )
                for i in tied
            ]
            raise AmbiguousPartitionError(
                f"Context key {key!r} ties {len(tied)} partitions at "
                f"specificity {top}: {tied_keys!r}"
            )
        return self._lattices[tied[0]]

    def apply(self, context, dimensions=None):
        key = self._engine._extract_partition_key(context)
        lattice = self._map.get(key)
        if lattice is None:
            lattice = self._route(key)
        return self._engine.apply(lattice, context, dimensions=dimensions)
```

In `src/mountainash_rules/__init__.py`: add `AmbiguousPartitionError` to the `lattice` import line and to `__all__` (alphabetical — right after `Aggregate`).

Note: the test helper above already passes `validate=`, so give `engine.index()` its final signature now — the parameters are accepted but not yet forwarded (Task 5 threads them into `LatticeIndex`):

```python
    def index(
        self,
        lattices: list[Lattice],
        validate: bool = True,
        max_witnesses: int = 1_000_000,
    ) -> LatticeIndex:
        """Build a partition-key routing index over pre-built lattices."""
        from mountainash_rules.engines.accumulator.lattice import LatticeIndex
        return LatticeIndex(self, lattices, self._context_key_dims)
```

(Task 5 threads `validate`/`max_witnesses` into `LatticeIndex`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch run test:test-target tests/accumulator/test_lattice.py::TestTernaryRouting tests/accumulator/test_lattice.py::TestIndexStructuralChecks -v`
Expected: all PASS.

- [ ] **Step 5: Run the quick suite and commit**

Run: `hatch run test:test-quick && hatch run ruff:check`
Expected: green — in particular `tests/accumulator/test_apply.py::TestApplyAutoWithPartitions` (fast path + `KeyError` message prefix preserved) and `tests/test_backend_purity.py`.

```bash
git add src/mountainash_rules/engines/accumulator/lattice.py src/mountainash_rules/engines/accumulator/engine.py src/mountainash_rules/__init__.py tests/accumulator/test_lattice.py
git commit -m "feat: ternary + specificity partition routing via embedded meta-engine"
```

---

### Task 4: `apply_batch` routing through `_route`

**Files:**
- Modify: `src/mountainash_rules/engines/accumulator/lattice.py` (`LatticeIndex.apply_batch`, the combo-loop only)
- Test: `tests/accumulator/test_lattice.py`

**Interfaces:**
- Consumes: `_route` and `_map` (Task 3), `engine._normalize_partition_key` (Task 2).
- Produces: `apply_batch(contexts, **kwargs)` — per unique key combo: normalise (None → NOT_SET), dict hit first, `_route` on miss; combos routing to the same lattice batch together; unroutable combo raises the same errors as `apply`. Null combo values filter their partition slice with `is_null`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/accumulator/test_lattice.py`:

```python
class TestTernaryRoutingBatch:
    def test_batch_mixes_specific_and_default(self):
        engine, index = _index_for([("AU", "BROKER"), (UNKNOWN, UNKNOWN)])
        contexts = pl.DataFrame({
            "region": ["AU", "NZ", None],
            "channel": ["BROKER", "DIRECT", None],
            "product": ["GOLD", "GOLD", "GOLD"],
        })
        result = index.apply_batch(contexts)
        rows = relation(result.survivors).to_dict()
        # every context produced at least one survivor row
        assert set(rows["__lattice_ctx_id"]) == {0, 1, 2}

    def test_batch_unroutable_combo_raises(self):
        engine, index = _index_for([("AU", "BROKER")])
        contexts = pl.DataFrame({
            "region": ["AU", "US"],
            "channel": ["BROKER", "X"],
            "product": ["GOLD", "GOLD"],
        })
        with pytest.raises(KeyError, match="No lattice"):
            index.apply_batch(contexts)

    def test_batch_ambiguous_combo_raises(self):
        engine, index = _index_for(
            [("AU", UNKNOWN), (UNKNOWN, "BROKER")], validate=False
        )
        contexts = pl.DataFrame({
            "region": ["AU"],
            "channel": ["BROKER"],
            "product": ["GOLD"],
        })
        with pytest.raises(AmbiguousPartitionError):
            index.apply_batch(contexts)
```

`relation` is already imported at the top of `test_lattice.py` (`from mountainash.relations import relation`); add it if absent.

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target tests/accumulator/test_lattice.py::TestTernaryRoutingBatch -v`
Expected: `test_batch_mixes_specific_and_default` FAILS with `KeyError` (null combo has no exact dict entry); the unroutable test may already pass — that's fine, the mixed test is the driver.

- [ ] **Step 3: Implement**

In `LatticeIndex.apply_batch`, replace the combo loop body:

```python
        for i in range(n):
            raw_key = tuple(combos[f][i] for f in key_fields)
            key = self._engine._normalize_partition_key(raw_key)
            lattice = self._map.get(key)
            if lattice is None:
                lattice = self._route(key)
            part = rel
            for f, v in zip(key_fields, raw_key):
                part = part.filter(
                    ma.col(f).is_null() if v is None
                    else ma.col(f).eq(ma.lit(v))
                )
            engine = self._engine._filter_engine_for(lattice)
            frames.append(relation(engine.evaluate_batch(
                part.collect(), **kwargs
            ).survivors))
```

(Only the key resolution and the null-aware filter change; the id synthesis, concat, and `BatchRuleResult` wrap are untouched.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch run test:test-target tests/accumulator/test_lattice.py::TestTernaryRoutingBatch -v`
Expected: 3 PASS. Then `hatch run test:test-quick` — `tests/filter/test_batch_evaluation.py` index-batch tests must stay green.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/engines/accumulator/lattice.py tests/accumulator/test_lattice.py
git commit -m "feat: route apply_batch combos through the shared ternary resolver"
```

---

### Task 5: Load-time ambiguity validation (witness matrix)

**Files:**
- Modify: `src/mountainash_rules/engines/accumulator/lattice.py` (`LatticeIndex.__init__` signature + `_validate_ambiguity`)
- Modify: `src/mountainash_rules/engines/accumulator/engine.py` (`index` threads `validate`/`max_witnesses`)
- Test: `tests/accumulator/test_lattice.py`

**Interfaces:**
- Consumes: `self._meta_engine.evaluate_batch`, `unknown_sentinel_for`, `not_set_sentinel_for` (all in place after Tasks 1–3).
- Produces: `LatticeIndex.__init__(engine, lattices, context_key_dims, validate=True, max_witnesses=1_000_000)`; private `_validate_ambiguity(max_witnesses)` raising `AmbiguousPartitionError` (with one witness context in the message) or `ValueError` on witness-cap overflow. `AccumulatorEngine.index(lattices, validate=True, max_witnesses=1_000_000)` passes both through.

- [ ] **Step 1: Write the failing tests**

Append to `tests/accumulator/test_lattice.py`:

```python
class TestIndexValidation:
    def test_crossing_pair_without_cover_raises_at_index(self):
        engine = _routing_engine()
        lattices = engine.build_all(
            _routing_rules([("AU", UNKNOWN), (UNKNOWN, "BROKER")])
        )
        with pytest.raises(AmbiguousPartitionError) as exc:
            engine.index(lattices)
        # message carries a witness context
        assert "AU" in str(exc.value) and "BROKER" in str(exc.value)

    def test_crossing_pair_with_cover_validates_and_routes(self):
        engine = _routing_engine()
        lattices = engine.build_all(_routing_rules([
            ("AU", UNKNOWN), (UNKNOWN, "BROKER"), ("AU", "BROKER"),
        ]))
        index = engine.index(lattices)  # must NOT raise (false-positive guard)
        result = index.apply(
            RoutingContext(region="AU", channel="BROKER", product="GOLD")
        )
        assert result.count >= 1

    def test_validate_false_defers_to_runtime(self):
        engine = _routing_engine()
        lattices = engine.build_all(
            _routing_rules([("AU", UNKNOWN), (UNKNOWN, "BROKER")])
        )
        index = engine.index(lattices, validate=False)  # no raise here
        with pytest.raises(AmbiguousPartitionError):
            index.apply(RoutingContext(region="AU", channel="BROKER", product="GOLD"))

    def test_witness_cap_overflow_raises(self):
        engine = _routing_engine()
        lattices = engine.build_all(
            _routing_rules([("AU", "BROKER"), ("NZ", "DIRECT")])
        )
        # 3 classes per dim (AU, NZ, OTHER) x (BROKER, DIRECT, OTHER) = 9 > 4
        with pytest.raises(ValueError, match="max_witnesses"):
            engine.index(lattices, max_witnesses=4)

    def test_bool_key_dim_full_domain_validates(self):
        metadata = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="flag",
                match_strategy=MatchStrategy.EXACT,
                data_type="bool",
                role=DimensionRole.CONTEXT_KEY,
            ),
            Dimension(dimension_name="product", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        rules = pl.DataFrame({
            "flag": [True, False, None],   # null rule value = bool wildcard
            "rule_name": ["r0", "r1", "r2"],
            "product": ["GOLD"] * 3,
            "margin": [1.0] * 3,
        })
        index = engine.index(engine.build_all(rules))  # OTHER = None, no raise
        result = index.apply({"product": "GOLD"})      # flag missing -> wildcard
        assert result.count >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target tests/accumulator/test_lattice.py::TestIndexValidation -v`
Expected: `test_crossing_pair_without_cover_raises_at_index` and `test_witness_cap_overflow_raises` FAIL (no validation exists yet); the others may pass incidentally.

- [ ] **Step 3: Implement**

In `lattice.py`, extend `LatticeIndex.__init__`'s signature and tail:

```python
    def __init__(
        self,
        engine,
        lattices: list["Lattice"],
        context_key_dims,
        validate: bool = True,
        max_witnesses: int = 1_000_000,
    ) -> None:
```

and after `self._meta_engine = ...`:

```python
        if validate and self._meta_engine is not None:
            self._validate_ambiguity(max_witnesses)
```

Add the method (module needs `import itertools` at the top):

```python
    _WITNESS_CHUNK = 100_000

    def _validate_ambiguity(self, max_witnesses: int) -> None:
        """Exhaustive witness-matrix ambiguity check (spec §5).

        Per key dim the reachable context values collapse into finitely
        many equivalence classes: each specific key value, plus OTHER —
        represented by the typed NOT_SET sentinel (None for bool), which
        matches no specific key and every wildcard. The cross-product of
        classes is routed through the meta-engine in chunks; any witness
        with >= 2 top-specificity survivors is a reachable runtime tie.
        """
        classes: list[list] = []
        for i, d in enumerate(self._context_key_dims):
            if d.data_type is DataType.BOOL:
                wildcard, other = None, None
            else:
                wildcard = unknown_sentinel_for(d.data_type)
                other = not_set_sentinel_for(d.data_type)
            specifics = sorted(
                {key[i] for key in self._map if key[i] != wildcard},
                key=repr,
            )
            classes.append(specifics + [other])

        total = 1
        for c in classes:
            total *= len(c)
        if total > max_witnesses:
            raise ValueError(
                f"Ambiguity validation needs {total} witness contexts, "
                f"over max_witnesses={max_witnesses}; pass a higher "
                f"max_witnesses, validate=False (accepting the runtime "
                f"tie check), or restructure the key dimensions"
            )

        fields = [d.resolved_context_field for d in self._context_key_dims]
        witnesses = itertools.product(*classes)
        while True:
            chunk = list(itertools.islice(witnesses, self._WITNESS_CHUNK))
            if not chunk:
                return
            contexts = relation({
                "__witness_id": list(range(len(chunk))),
                **{
                    f: [w[i] for w in chunk]
                    for i, f in enumerate(fields)
                },
            }).collect()
            survivors = relation(
                self._meta_engine.evaluate_batch(
                    contexts, context_id_field="__witness_id"
                ).survivors
            ).to_dict()
            best: dict[int, int] = {}
            tied: dict[int, list[int]] = {}
            for wid, spec, pidx in zip(
                survivors["__witness_id"],
                survivors["__specificity"],
                survivors["__partition_idx"],
            ):
                if wid not in best or spec > best[wid]:
                    best[wid] = spec
                    tied[wid] = [pidx]
                elif spec == best[wid]:
                    tied[wid].append(pidx)
            for wid, parts in tied.items():
                if len(parts) > 1:
                    witness_ctx = dict(zip(fields, chunk[wid]))
                    tied_keys = [
                        tuple(
                            self._lattices[p].partition_key[d.dimension_name]
                            for d in self._context_key_dims
                        )
                        for p in parts
                    ]
                    raise AmbiguousPartitionError(
                        f"Partition suite is ambiguous: witness context "
                        f"{witness_ctx!r} ties partitions {tied_keys!r}"
                    )
```

**Caveat for the implementer:** `evaluate_batch`'s `_check_reserved` rejects context frames containing reserved column prefixes. `__witness_id` is not in `_BATCH_RESERVED` and does not start with `__t_` or `__ctx_`, so it passes — verify this when running the tests; if it collides, rename to `witness_id` everywhere in this method.

In `engine.py`, thread the parameters through `index`:

```python
    def index(
        self,
        lattices: list[Lattice],
        validate: bool = True,
        max_witnesses: int = 1_000_000,
    ) -> LatticeIndex:
        """Build a partition-key routing index over pre-built lattices.

        Args:
            lattices: List of Lattice objects from build_all() or load().
            validate: Run the exhaustive load-time ambiguity check
                (structural checks — empty/duplicate/NOT_SET keys — run
                regardless).
            max_witnesses: Ceiling on the validation matrix size; above
                it index() raises ValueError rather than sampling.
        """
        from mountainash_rules.engines.accumulator.lattice import LatticeIndex
        return LatticeIndex(
            self, lattices, self._context_key_dims,
            validate=validate, max_witnesses=max_witnesses,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch run test:test-target tests/accumulator/test_lattice.py::TestIndexValidation -v`
Expected: 5 PASS. **Important:** earlier routing tests built ambiguous suites with `validate=False` — re-run the whole file: `hatch run test:test-target tests/accumulator/test_lattice.py -v`. All green.

- [ ] **Step 5: Run the full quick suite, lint, mypy, and commit**

Run: `hatch run test:test-quick && hatch run ruff:check && hatch run mypy:check`
Expected: green.

```bash
git add src/mountainash_rules/engines/accumulator/lattice.py src/mountainash_rules/engines/accumulator/engine.py tests/accumulator/test_lattice.py
git commit -m "feat: exhaustive load-time ambiguity validation for lattice indexes"
```

---

### Task 6: Persistence round-trip test + documentation

**Files:**
- Test: `tests/accumulator/test_lattice.py`
- Modify: `CLAUDE.md` (Match Strategies table; accumulator engine section)

**Interfaces:**
- Consumes: `Lattice.save/load` (unchanged), `engine.index` (Task 5).
- Produces: proof that wildcard sentinels round-trip through parquet + manifest and the loaded suite routes; updated repo docs.

- [ ] **Step 1: Write the round-trip test**

Append to `tests/accumulator/test_lattice.py` (the file already imports `Lattice`; `tmp_path` is a pytest builtin fixture):

```python
class TestRoutingPersistence:
    def test_saved_wildcard_suite_routes_after_load(self, tmp_path):
        engine = _routing_engine()
        built = engine.build_all(
            _routing_rules([("AU", "BROKER"), (UNKNOWN, UNKNOWN)])
        )
        loaded = [
            Lattice.load(lattice.save(tmp_path / f"part{i}"))
            for i, lattice in enumerate(built)
        ]
        index = engine.index(loaded)
        result = index.apply(
            RoutingContext(region="NZ", channel="DIRECT", product="GOLD")
        )
        assert result.count >= 1  # sentinel key survived the round-trip
```

- [ ] **Step 2: Run it**

Run: `hatch run test:test-target tests/accumulator/test_lattice.py::TestRoutingPersistence -v`
Expected: PASS (no production change in this task — this is the spec §8 persistence guarantee). If it fails, stop and diagnose (likely sentinel mangling in manifest YAML); do not weaken the test.

- [ ] **Step 3: Update CLAUDE.md**

In the Match Strategies table, after the `exact` / `not_equal` row, add:

```markdown
| `exact_key` | scalar | rule-side wildcard only (UNKNOWN → 0; context sentinel vs specific → −1); powers partition routing |
```

In the "Accumulator engine" section, replace the sentence beginning "`DimensionRole.CONTEXT_KEY` dimensions partition the rule space" with:

```markdown
- `DimensionRole.CONTEXT_KEY` dimensions partition the rule space; `build_all` + `index(lattices)` → `LatticeIndex` routes contexts (single or batch) to the right lattice using ternary + specificity semantics via an embedded meta-engine (`EXACT_KEY` dims): an all-wildcard key is the default/overflow partition, exact hits keep an O(1) dict fast path, ties raise `AmbiguousPartitionError` (a `KeyError` subclass, exported from the package root). `index(lattices, validate=True, max_witnesses=1_000_000)` runs an exhaustive witness-matrix ambiguity check at load time; empty/duplicate/NOT_SET-bearing keys are always rejected. See `docs/superpowers/specs/2026-07-19-ternary-partition-routing-design.md`.
```

- [ ] **Step 4: Final full verification**

Run: `hatch run test:test && hatch run ruff:check && hatch run mypy:check`
Expected: full suite + coverage green.

- [ ] **Step 5: Commit**

```bash
git add tests/accumulator/test_lattice.py CLAUDE.md
git commit -m "test: routing persistence round-trip; docs: ternary partition routing"
```

---

## Post-implementation

Per the standard delivery flow: adversarial post-implementation review (`/codex:rescue` on the branch diff), then PR targeting `develop` (`gh pr create --base develop`). Do not push to `develop` or `main` directly.
