# Accumulator Backend-Native Construction Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build disjoint replacement cells from source rules entirely through Mountainash relations and expressions on the source backend — field boundary sweeps, staged field composition, mask-carried contributor membership, membership decoding, and exact integer folds — producing a `DisjointLattice` whose data never crosses to the host for geometry, membership, or arithmetic.

**Architecture:** Every construction stage is a relation pipeline. Host code parses metadata, assigns stage ordinals (field order), and observes bounded results; it never extracts source rows, regions, masks, or fold inputs. Contributor membership rides through the frontier as scoped multiword int64 masks (`bitwise_and` per word; all three backend families implement it), decoded to UUID edges via an allocation table of host-precomputed literal bit-flags joined per word (the S3-03 long/KVP route; no shift operations required). Unbounded/wildcard regions are stored as null bounds and materialize to the UNKNOWN sentinel, unifying with the filter engine's ternary wildcard semantics. Folds are backend `group_by` sums (the owner's 2026-09-21 integer basis-point domain note accepts ordinary `int64` sum; FLOAT exact-state is the separate §16.2 demonstrated route, out of scope here).

**Tech Stack:** Python 3.12, pytest, `mountainash.relations` (`relation`, `concat`, `cross_join`, `join`, `group_by.agg`, `with_row_index`, `sort`, `unique`, `filter`), `mountainash.expressions` (`bitwise_and`, `greatest`/`least`, `is_null`, `when` chains, `sum`). Backend purity enforced (`tests/test_backend_purity.py`); zero new `# allow:` tags.

**Spec:** Replacement design §§6–9, 11.1 (`../mountainash-central/04.planning/mountainash-rules/superpowers/specs/2026-09-20-accumulator-replacement-design.md`); principles `backend-execution-and-lifetimes.md`, `shared-projections-and-identity.md` (`../mountainash-central/01.principles/mountainash-rules/a.architecture/accumulator/`); disjoint-cell contract (`../mountainash-central/04.planning/mountainash-rules/a.backlog/disjoint-cell-compilation.md`).

## Global Constraints

- **Placement law:** geometry, membership, and arithmetic run on the backend through Mountainash relations/expressions. Host may: parse metadata, advance field ordinals, and collect final/bounded results. `to_dicts()` mid-pipeline, Python region math, Python set intersection, and host fold loops are prohibited (anti-patterns, backend-execution principle).
- **Identity law:** source UUIDs are never merged by predicate equality; duplicates are an admission error. A source reached through several regions contributes once per membership (distinct-edge decode + `unique`).
- **Scope:** single outcome scope, single semantic partition, one dimension per physical field (alias conjoin deferred), strategies EXACT/RANGE/GREATER_THAN/LESS_THAN via range lowering. SET_*/NOT_EQUAL/text strategies and alias conjoin raise explicit capability errors — deferred slices, never silent approximations. Partial-context (§7.2) serving is a separate future plan.
- SQLite excluded (owner authorization 2026-09-21); `ibis-polars` construction tests carry the existing strict `with_row_index` xfail (mountainash#78).
- Expression-builder chain shapes (`when/then/otherwise`) are asserted by Task 1's smoke test before any consumer code.
- `import typing as t`; Google-style docstrings; tests `@pytest.mark.unit` unless marked integration.

## Review Focus

1. **Inclusive-touching singleton** — `[0,10]`+`[10,20]` must yield region `{10}` with both contributors; a positive-width-only emptiness test deletes it. Pinned by Task 7.
2. **Zero contributor is a hit** — source Z contributing 0 must appear in edges and leave the fold unchanged, never be pruned as empty. Pinned by Task 8.
3. **Mask zero ≠ region empty** — a nonempty region whose mask is zero is implicit no-hit (pruned), while a nonempty mask on an empty region is a compiler defect (post-build validator, Plan B). Conflating them hides lost contributions. Pinned by Task 4 pruning tests.
4. **Multiword boundary** — ≥129 contributors span ≥3 words; word 0/62/63/126 positions must decode exactly (`2**62+1` bit). Pinned by Task 8.
5. **Sentinel unbounded ≠ sentinel value match** — materialized null bounds become UNKNOWN sentinels meaning "no constraint" (ternary 0), not a matchable literal. A context equal to the sentinel value must not match a specific cell through a bound. Pinned by Task 6.

---

### Task 1: Expression-API smoke test and source lowering

**Files:**
- Create: `src/mountainash_rules/engines/accumulator/construction.py`
- Create: `tests/accumulator/test_construction_lowering.py`

**Interfaces:**
- Consumes: `relation`, `concat` from `mountainash.relations`; `ma` expressions; `Dimension`, `DimensionsMetadata` from `core.dimension`; `MatchStrategy`, `DataType`, `unknown_sentinel_for` from `core.constants`
- Produces: `lower_sources(metadata, rules) -> Relation` with schema `(uuid: str, field: str, lo, hi, lo_incl: bool, hi_incl: bool)` — one row per (source, range-kind field); null lo/hi = unbounded; rule-side sentinels mapped to null.

- [ ] **Step 1: Write the API smoke test (backend-parametrized)**

```python
# tests/accumulator/test_construction_lowering.py
"""Backend-execution smoke tests for the construction expression vocabulary."""
import pytest
import mountainash.expressions as ma
from mountainash.relations import relation

from tests.conftest import build_backend_df

# ibis-polars cannot compile with_row_index (mountainash#78); construction
# inherits that limitation until the upstream fix lands.
CONSTRUCTION_BACKENDS = ["polars", "pandas", "narwhals-polars",
                         "narwhals-pandas", "ibis-duckdb"]


@pytest.fixture(params=CONSTRUCTION_BACKENDS)
def backend(request):
    return request.param


@pytest.mark.unit
def test_conditional_and_bitwise_vocabulary_executes(backend):
    """when-chains, bitwise_and, greatest/least, is_null run on the backend."""
    df = build_backend_df(backend, {"a": [1, 5, 10], "b": [4, 5, 20],
                                    "ia": [1, 1, 1], "ib": [1, 0, 1]})
    rel = relation(df).with_columns(
        ma.when(ma.col("a").ge(ma.col("b")))
        .then(ma.col("a"))
        .otherwise(ma.col("b"))
        .alias("g"),
        ma.col("ia").bitwise_and(ma.col("ib")).alias("w"),
    )
    out = rel.collect().to_dict()
    assert out["g"] == [4, 5, 20]        # greatest via when-chain (null-safe later)
    assert out["w"] == [1, 0, 1]         # backend-executed bitwise AND
```

- [ ] **Step 2: Run it — verify it fails or reveals builder-shape mismatches**

Run: `hatch run test:test-target tests/accumulator/test_construction_lowering.py::test_conditional_and_bitwise_vocabulary_executes -v`
Expected: may FAIL on builder chain shape (e.g. positional `then`). If so, adjust to the form used by `core/compiler.py` ternaries and re-run until PASS on all five backends before writing any consumer code.

- [ ] **Step 3: Write the lowering test**

```python
# tests/accumulator/test_construction_lowering.py — append
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.core.constants import MatchStrategy, DataType, UNKNOWN_NUMERIC
from mountainash_rules.engines.accumulator.construction import lower_sources


@pytest.mark.unit
def test_lower_sources_range_and_wildcards(backend):
    md = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="tier", match_strategy=MatchStrategy.RANGE,
                  data_type=DataType.INT,
                  range_min_field="tier_min", range_max_field="tier_max"),
        Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT,
                  data_type=DataType.STR),
    ])
    rules = build_backend_df(backend, {
        "uuid": ["A", "B", "W"],
        "tier_min": [0, 5, UNKNOWN_NUMERIC],
        "tier_max": [10, 15, UNKNOWN_NUMERIC],
        "region": ["EU", "US", "EU"],
    })
    lowered = lower_sources(md, rules).collect().to_dict()
    # RANGE field lowered for all three; W's sentinel bounds -> null (wildcard)
    tiers = {u: (lo, hi) for u, lo, hi in
             zip(lowered["uuid"], lowered["lo"], lowered["hi"])
             if lowered["field"][lowered["uuid"].index(u)] == "tier"}
    assert tiers["A"] == (0, 10)
    assert tiers["W"][0] is None and tiers["W"][1] is None
    # EXACT field lowered as a point: lo == hi == value, both inclusive
    regions = {(f, lo, hi) for f, lo, hi in
               zip(lowered["field"], lowered["lo"], lowered["hi"])}
    assert ("region", "EU", "EU") in regions


@pytest.mark.unit
def test_lower_sources_rejects_deferred_strategies():
    md = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="r", match_strategy=MatchStrategy.REGEX,
                  data_type=DataType.STR, regex_pattern="a.*"),
    ])
    rules = build_backend_df("polars", {"uuid": ["A"], "r": ["abc"]})
    with pytest.raises(NotImplementedError, match="REGEX"):
        lower_sources(md, rules)
```

- [ ] **Step 4: Run — expect FAIL (module missing)**

Run: `hatch run test:test-target tests/accumulator/test_construction_lowering.py -v`
Expected: FAIL — `ModuleNotFoundError: ... construction`

- [ ] **Step 5: Implement `lower_sources`**

```python
# src/mountainash_rules/engines/accumulator/construction.py
"""Backend-native disjoint-cell construction: relation pipelines only.

Host responsibilities are limited to metadata parsing, field stage
ordinals and bounded result collection. All geometry, membership and
arithmetic run through Mountainash relations/expressions on the source
backend (backend-execution-and-lifetimes principle).
"""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash.relations import Relation, concat, relation

from mountainash_rules.core.constants import (
    MatchStrategy,
    unknown_sentinel_for,
)

if t.TYPE_CHECKING:
    from mountainash_rules.core.dimension import Dimension, DimensionsMetadata

_SUPPORTED = {
    MatchStrategy.EXACT,
    MatchStrategy.RANGE,
    MatchStrategy.GREATER_THAN,
    MatchStrategy.LESS_THAN,
}


def _null_if_sentinel(col: t.Any, data_type: t.Any) -> t.Any:
    """Rule-side UNKNOWN sentinel means 'no constraint' -> null bound."""
    sentinel = unknown_sentinel_for(data_type)
    return (
        ma.when(col.eq(ma.lit(sentinel))).then(ma.lit(None)).otherwise(col)
    )


def _lower_dimension(dim: "Dimension", rules_rel: Relation) -> Relation:
    """One dimension's per-source predicate bounds, long form."""
    dt = dim.data_type
    uuid_col = ma.col("uuid")
    strat = dim.match_strategy
    if strat not in _SUPPORTED:
        raise NotImplementedError(
            f"Strategy {strat} is a deferred construction slice; "
            "supported: EXACT, RANGE, GREATER_THAN, LESS_THAN"
        )
    if strat in (MatchStrategy.EXACT,):
        field = dim.resolved_rule_field
        v = _null_if_sentinel(ma.col(field), dt)
        sel = [uuid_col, ma.lit(dim.dimension_name).alias("field"),
               v.alias("lo"), v.alias("hi"),
               ma.lit(True).alias("lo_incl"), ma.lit(True).alias("hi_incl")]
    elif strat is MatchStrategy.RANGE:
        lo = _null_if_sentinel(ma.col(dim.range_min_field), dt)
        hi = _null_if_sentinel(ma.col(dim.range_max_field), dt)
        sel = [uuid_col, ma.lit(dim.dimension_name).alias("field"),
               lo.alias("lo"), hi.alias("hi"),
               ma.lit(dim.range_min_inclusive).alias("lo_incl"),
               ma.lit(dim.range_max_inclusive).alias("hi_incl")]
    else:  # GREATER_THAN / LESS_THAN
        field = dim.resolved_rule_field
        thr = _null_if_sentinel(ma.col(field), dt)
        if strat is MatchStrategy.GREATER_THAN:
            sel = [uuid_col, ma.lit(dim.dimension_name).alias("field"),
                   thr.alias("lo"), ma.lit(None).alias("hi"),
                   ma.lit(False).alias("lo_incl"), ma.lit(True).alias("hi_incl")]
        else:
            sel = [uuid_col, ma.lit(dim.dimension_name).alias("field"),
                   ma.lit(None).alias("lo"), thr.alias("hi"),
                   ma.lit(True).alias("lo_incl"), ma.lit(False).alias("hi_incl")]
    return rules_rel.select(*sel)


def lower_sources(metadata: "DimensionsMetadata", rules: t.Any) -> Relation:
    """Lower every constraint dimension to per-source predicate bounds.

    Produces the shared source-applicability projection used by the
    boundary sweep (Task 2-3) and the independent validator (Plan B).
    One dimension per physical field in this slice; aliases are rejected.
    """
    rel = relation(rules)
    fields: dict[str, str] = {}
    dims = [d for d in metadata.dimensions
            if d.role.value == "constraint"]
    parts = []
    for dim in dims:
        field = dim.resolved_rule_field if dim.match_strategy is not MatchStrategy.RANGE \
            else dim.range_min_field
        if field in fields and fields[field] != dim.dimension_name:
            raise NotImplementedError(
                f"Aliased field {field!r} ({fields[field]} / {dim.dimension_name}) "
                "is a deferred construction slice"
            )
        fields[field] = dim.dimension_name
        parts.append(_lower_dimension(dim, rel))
    if not parts:
        raise ValueError("Construction requires at least one constraint dimension")
    return concat(parts)




> **Step 6 note (allocation relation):** `allocation_relation` cannot build a literal frame backend-neutrally. Instead derive it from the source relation so the backend never changes: build `(word_index, bitflag)` pairs by an index join against a host-supplied **list of literal structs is also native construction** — the portable route is a `bind`-style literal table, which Mountainash may not expose. Therefore: compute the allocation **frame on the source backend via the engine's own relation over a one-column uuid relation cross-joined to a host-supplied python list is prohibited**. The accepted route: the caller (engine host, control plane) supplies `allocation` as a frame built by `tests.conftest.build_backend_df`-equivalent **in the engine's public API** — i.e. `DisjointAccumulatorEngine.build(rules, *, backend_frame_factory)`, where the factory is the one narrow, explicitly-documented host boundary for *literal metadata tables* (same class as context literal binding in `ExpressionRulesEngine._scored_relation`, which already binds host literals via `ctx_columns`). Implement `allocation_relation(rules_rel, allocation_frame)` as a pure relation join; frame creation lives in the engine constructor's factory argument. Delete the `raise AssertionError` block above and finish:

```python
# construction.py — replace the tail of allocation_relation with:

def allocation_relation(rules: t.Any, allocation_frame: t.Any,
                        word_bits: int = 63) -> Relation:
    """Join the host-supplied literal allocation frame into a relation.

    `allocation_frame` (uuid, word_index, bitflag) is built by the
    engine's injected frame factory — literal control-plane metadata,
    the same class of host binding as context literal columns. The
    backend never sees Python iteration over source rows.
    """
    alloc = relation(allocation_frame)
    uuids = relation(rules).select(ma.col("uuid")).unique()
    # every source uuid must have exactly one allocation row
    missing = uuids.join(alloc, left_on=["uuid"], right_on=["uuid"],
                         how="anti")
    if missing.count_rows() > 0:
        raise ValueError("Source UUID missing from allocation map")
    return alloc
```

- [ ] **Step 7: Run lowering tests to green**

Run: `hatch run test:test-target tests/accumulator/test_construction_lowering.py -v`
Expected: all PASS (smoke + lowering + rejection) on all five backends.

- [ ] **Step 8: Commit**

```bash
git add src/mountainash_rules/engines/accumulator/construction.py \
        tests/accumulator/test_construction_lowering.py
git commit -m "feat(accumulator): backend-native source lowering and allocation join"
```

---

### Task 2: Boundary sweep — atomic regions per field

**Files:**
- Modify: `src/mountainash_rules/engines/accumulator/construction.py`
- Modify: `tests/accumulator/test_construction_lowering.py` (rename file in commit to `test_construction_sweep.py` optional; keep one file)

**Interfaces:**
- Consumes: `lower_sources`
- Produces: `field_regions(lowered, field) -> Relation` with schema `(lo, hi, lo_incl, hi_incl)` — the disjoint atomic regions of one field: interior points, open intervals between consecutive boundaries, and the two exterior slivers (unbounded side toward domain edge, emitted only when a source is unbounded on that side). Wildcard catch-all `(null, null)` is added by Task 3, not here.

- [ ] **Step 1: Write failing test**

```python
# tests/accumulator/test_construction_lowering.py — append
from mountainash_rules.engines.accumulator.construction import field_regions


def _lowered(backend, rows):
    md = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="tier", match_strategy=MatchStrategy.RANGE,
                  data_type=DataType.INT,
                  range_min_field="lo", range_max_field="hi")])
    rules = build_backend_df(backend, {"uuid": [r["uuid"] for r in rows],
                                       "lo": [r["lo"] for r in rows],
                                       "hi": [r["hi"] for r in rows]})
    lowered = lower_sources(md, rules)
    return lowered.filter(ma.col("field").eq(ma.lit("tier")))


@pytest.mark.unit
def test_atomic_regions_overlap(backend):
    """[0,10]+[5,15] -> boundaries 0,5,10,15 -> points + 3 open intervals
    + no exteriors (all sources bounded)."""
    lowered = _lowered(backend, [
        {"uuid": "A", "lo": 0, "hi": 10},
        {"uuid": "B", "lo": 5, "hi": 15},
    ])
    regions = field_regions(lowered, "tier").collect().to_dict()
    points = {(lo, hi) for lo, hi in zip(regions["lo"], regions["hi"])
              if lo == hi}
    opens = {(lo, hi) for lo, hi in zip(regions["lo"], regions["hi"])
             if lo is not None and hi is not None and lo != hi}
    assert points == {(0, 0), (5, 5), (10, 10), (15, 15)}
    assert opens == {(0, 5), (5, 10), (10, 15)}


@pytest.mark.unit
def test_exterior_slivers_when_unbounded(backend):
    """A source with null lo emits the left exterior sliver."""
    lowered = _lowered(backend, [
        {"uuid": "A", "lo": None, "hi": 10},
        {"uuid": "B", "lo": 5, "hi": 15},
    ])
    regions = field_regions(lowered, "tier").collect().to_dict()
    lefts = [hi for lo, hi in zip(regions["lo"], regions["hi"])
             if lo is None and hi is not None]
    assert lefts == [0]  # (-inf, 0) exists because A is unbounded below
```

- [ ] **Step 2: Run — FAIL**

Run: `hatch run test:test-target tests/accumulator/test_construction_lowering.py -k atomic or exterior -v`
Expected: FAIL — `ImportError: field_regions`

- [ ] **Step 3: Implement `field_regions`**

```python
# construction.py — append

def field_regions(lowered: Relation, field: str) -> Relation:
    """Disjoint atomic regions of one field via backend boundary sweep.

    Boundaries = unique non-null lo/hi values of the field's sources.
    Regions: each boundary as a point, each consecutive pair as an open
    interval (consecutive via index+1 self-join), plus exterior slivers
    only when some source is unbounded on that side. No host row data
    crosses; the only egress in tests is the final collected assertion.
    """
    src = lowered.filter(ma.col("field").eq(ma.lit(field)))
    los = src.select(ma.col("lo").alias("b")).filter(
        ma.col("b").is_not_null())
    his = src.select(ma.col("hi").alias("b")).filter(
        ma.col("b").is_not_null())
    bounds = concat([los, his], distinct=True).sort("b").with_row_index(
        name="__i")

    # consecutive pairs: __i joins __i - 1 on the right side
    right = bounds.select(
        ma.col("__i").sub(ma.lit(1)).alias("__j"),
        ma.col("b").alias("b2"),
    )
    pairs = bounds.join(right, left_on=["__i"], right_on=["__j"],
                        how="inner")
    intervals = pairs.select(
        ma.col("b").alias("lo"), ma.col("b2").alias("hi"),
        ma.lit(False).alias("lo_incl"), ma.lit(False).alias("hi_incl"),
    )
    points = bounds.select(
        ma.col("b").alias("lo"), ma.col("b").alias("hi"),
        ma.lit(True).alias("lo_incl"), ma.lit(True).alias("hi_incl"),
    )

    has_unbounded_lo = src.filter(ma.col("lo").is_null()).count_rows() > 0
    has_unbounded_hi = src.filter(ma.col("hi").is_null()).count_rows() > 0

    parts = [points, intervals]
    first = bounds.sort("__i").first()
    last = bounds.sort("__i", descending=True).first()
    if has_unbounded_lo:
        parts.append(first.select(
            ma.lit(None).alias("lo"), ma.col("b").alias("hi"),
            ma.lit(True).alias("lo_incl"), ma.lit(False).alias("hi_incl")))
    if has_unbounded_hi:
        parts.append(last.select(
            ma.col("b").alias("lo"), ma.lit(None).alias("hi"),
            ma.lit(False).alias("lo_incl"), ma.lit(True).alias("hi_incl")))
    return concat(parts)
```

- [ ] **Step 4: Run to green**

Run: `hatch run test:test-target tests/accumulator/test_construction_lowering.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/engines/accumulator/construction.py \
        tests/accumulator/test_construction_lowering.py
git commit -m "feat(accumulator): backend boundary sweep to atomic field regions"
```

---

### Task 3: Coverage masks — one-hot SUM per word, wildcard union, zero-prune

**Files:**
- Modify: `src/mountainash_rules/engines/accumulator/construction.py`
- Modify: tests (same file)

**Interfaces:**
- Consumes: `field_regions`, `lower_sources`, `allocation_relation`
- Produces: `project_field(lowered, field, alloc) -> Relation` schema `(lo, hi, lo_incl, hi_incl, word_index, word_value)` — long-form masked regions of one field. Wildcard sources (both bounds null) are OR-unioned into every region's mask and contribute the `(null, null)` catch-all region. Regions whose mask is zero are pruned (implicit no-hit); the zero test is per-state `max(word_value) > 0` across words, never per-row.

- [ ] **Step 1: Write failing tests (Review Focus 2, 3)**

```python
# tests/accumulator/test_construction_lowering.py — append
from mountainash_rules.engines.accumulator.construction import (
    project_field, allocation_relation,
)


def _project(backend, rows, uuids=None):
    lowered = _lowered(backend, rows)
    rules = build_backend_df(backend, {
        "uuid": [r["uuid"] for r in rows]})
    alloc = allocation_relation(rules, build_backend_df(backend, {
        "uuid": [r["uuid"] for r in rows],
        "word_index": [0 for _ in rows],
        "bitflag": [1 << i for i in range(len(rows))],
    }))
    return project_field(lowered, "tier", alloc)


@pytest.mark.unit
def test_masks_and_wildcard_union(backend):
    """A=[0,10], B=[5,15], W=wildcard: [5,10] mask has A+B+W; catch-all W."""
    proj = _project(backend, [
        {"uuid": "A", "lo": 0, "hi": 10},
        {"uuid": "B", "lo": 5, "hi": 15},
        {"uuid": "W", "lo": None, "hi": None},
    ])
    rows = proj.collect().to_dict()
    def mask_at(lo, hi):
        return sum(v for l, h, w in zip(rows["lo"], rows["hi"], rows["word_value"])
                   if (l, h) == (lo, hi))
    assert mask_at(5, 10) == (1 << 0) | (1 << 1) | (1 << 2)  # A+B+W
    assert mask_at(0, 5) == (1 << 0) | (1 << 2)              # A+W
    assert mask_at(None, None) == (1 << 2)                    # catch-all W


@pytest.mark.unit
def test_zero_mask_regions_pruned(backend):
    """[0,5]+[10,15] disjoint: the (5,10) gap region has mask 0 and is gone."""
    proj = _project(backend, [
        {"uuid": "A", "lo": 0, "hi": 5},
        {"uuid": "B", "lo": 10, "hi": 15},
    ])
    rows = proj.collect().to_dict()
    gaps = [(l, h) for l, h in zip(rows["lo"], rows["hi"])
            if l == 5 and h == 10]
    assert gaps == []          # implicit no-hit gap pruned
    assert (0, 5) in list(zip(rows["lo"], rows["hi"]))
    assert (10, 15) in list(zip(rows["lo"], rows["hi"]))


@pytest.mark.unit
def test_gt_source_covers_its_own_sliver(backend):
    """Regression (math review M3): 'x>0' must cover its (0,+inf) sliver
    and must NOT cover the excluded point {0}."""
    proj = _project(backend, [
        {"uuid": "A", "lo": 0, "hi": None},   # GT 0: (0, inf)
    ])
    rows = proj.collect().to_dict()
    lo_list, hi_list = rows["lo"], rows["hi"]
    w_list = rows["word_value"]
    def mask_at(lo, hi):
        return sum(w for l, h, w in zip(lo_list, hi_list, w_list)
                   if (l, h) == (lo, hi))
    # point {0} excluded by the exclusive lower bound
    assert mask_at(0, 0) == 0
    # the (0, +inf) sliver (or its merge with exteriors) carries A's bit
    sliver = [w for l, h, w in zip(lo_list, hi_list, w_list)
              if l == 0 and h is None]
    assert sliver and all(w == 1 for w in sliver)


@pytest.mark.unit
def test_zero_contributor_is_a_hit(backend):
    """Z with contribution 0 still gets a bit; its regions survive."""
    proj = _project(backend, [
        {"uuid": "A", "lo": 0, "hi": 10},
        {"uuid": "Z", "lo": 0, "hi": 10},   # same predicate, distinct uuid
    ])
    rows = proj.collect().to_dict()
    m = sum(v for l, h, v in zip(rows["lo"], rows["hi"], rows["word_value"])
            if (l, h) == (0, 10))
    assert m == (1 << 0) | (1 << 1)  # both bits set; identities not merged
```

- [ ] **Step 2: Run — FAIL**

Run: `hatch run test:test-target tests/accumulator/test_construction_lowering.py -k mask or zero -v`
Expected: FAIL — `ImportError: project_field`

- [ ] **Step 3: Implement `project_field`**

```python
# construction.py — append

def _covers_region() -> dict[str, t.Any]:
    """Coverage predicate column builders shared by sweep validation.

    Returns a callable (src_cols, region_cols) -> boolean expression.
    Kept as one function so the independent validator (Plan B) composes
    the same primitive without duplicating semantics.
    """
    def covers(s_lo, s_hi, s_li, s_hi_i, r_lo, r_hi, r_li, r_hi_i):
        # Point region (r_lo == r_hi): inclusive-bound test, null-safe.
        # Open interval / sliver: source spans it iff
        #   (s_lo is null OR s_lo <= r_lo) AND (s_hi is null OR s_hi >= r_hi)
        # with inclusivity mattering only when the bound coincides and
        # the region side is inclusive.
        lo_ok = (
            ma.when(s_lo.is_null()).then(ma.lit(True))
            .when(s_lo.lt(r_lo) | (s_lo.eq(r_lo) & (~r_li | s_li)))
            .then(ma.lit(True))
            .otherwise(ma.lit(False))
        )
        hi_ok = (
            ma.when(s_hi.is_null()).then(ma.lit(True))
            .when(s_hi.gt(r_hi) | (s_hi.eq(r_hi) & (~r_hi_i | s_hi_i)))
            .then(ma.lit(True))
            .otherwise(ma.lit(False))
        )
        return lo_ok & hi_ok
    return covers


def project_field(lowered: Relation, field: str, alloc: Relation) -> Relation:
    """Masked atomic regions of one field, long form (one row per word).

    Coverage is a backend cross join of regions x sources filtered by
    the shared coverage predicate; masks are one-hot SUMs of allocation
    bitflags grouped per region per word (the evidenced grouped
    reconstruction route). Wildcard sources (both bounds null) are
    unioned into every region and emit the (null, null) catch-all.
    """
    src = lowered.filter(ma.col("field").eq(ma.lit(field)))
    regions = field_regions(lowered, field)

    covers = _covers_region()
    cov = regions.cross_join(src.select(
        ma.col("uuid"),
        ma.col("lo").alias("__s_lo"), ma.col("hi").alias("__s_hi"),
        ma.col("lo_incl").alias("__s_li"), ma.col("hi_incl").alias("__s_hi_i"),
    )).with_columns(covers(
        ma.col("__s_lo"), ma.col("__s_hi"),
        ma.col("__s_li"), ma.col("__s_hi_i"),
        ma.col("lo"), ma.col("hi"),
        ma.col("lo_incl"), ma.col("hi_incl"),
    ).alias("__cov")).filter(ma.col("__cov"))

    # catch-all region when wildcards exist
    wildcards = src.filter(ma.col("lo").is_null() & ma.col("hi").is_null())
    has_wild = wildcards.count_rows() > 0
    if has_wild:
        catch = wildcards.select(
            ma.lit(None).alias("lo"), ma.lit(None).alias("hi"),
            ma.lit(True).alias("lo_incl"), ma.lit(True).alias("hi_incl"),
            ma.col("uuid"),
        )
        cov = concat([cov.select("lo", "hi", "lo_incl", "hi_incl", "uuid"),
                      catch])

    # one-hot SUM per region per word
    covered = cov.join(alloc, left_on=["uuid"], right_on=["uuid"],
                       how="inner")
    masks = covered.group_by("lo", "hi", "lo_incl", "hi_incl", "word_index") \
        .agg(ma.col("bitflag").sum().alias("word_value"))

    # wildcard union into every region (sum == or for disjoint bits)
    if has_wild:
        wild_mask = wildcards.join(alloc, left_on=["uuid"],
                                   right_on=["uuid"], how="inner") \
            .group_by("word_index") \
            .agg(ma.col("bitflag").sum().alias("__wild"))
        masks = masks.join(wild_mask, left_on=["word_index"],
                           right_on=["word_index"], how="inner") \
            .with_columns(ma.col("word_value")
                          .bitwise_or(ma.col("__wild"))
                          .alias("word_value")) \
            .drop("__wild")

    # zero-mask prune: per region, any nonzero word keeps the state
    nonzero = masks.group_by("lo", "hi", "lo_incl", "hi_incl") \
        .agg(ma.col("word_value").max().alias("__any")) \
        .filter(ma.col("__any").gt(ma.lit(0))) \
        .select("lo", "hi", "lo_incl", "hi_incl")
    return masks.join(nonzero, left_on=["lo", "hi", "lo_incl", "hi_incl"],
                      right_on=["lo", "hi", "lo_incl", "hi_incl"],
                      how="semi")
```

- [ ] **Step 4: Run to green**

Run: `hatch run test:test-target tests/accumulator/test_construction_lowering.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/engines/accumulator/construction.py \
        tests/accumulator/test_construction_lowering.py
git commit -m "feat(accumulator): one-hot SUM coverage masks with wildcard union and zero-prune"
```

---

### Task 4: Staged field composition — the §8.2 frontier

**Files:**
- Modify: `src/mountainash_rules/engines/accumulator/construction.py`
- Modify: tests (same file)

**Interfaces:**
- Consumes: `project_field`
- Produces: `compose_fields(lowered, fields, alloc) -> Relation` — completed states as long-form rows `(word_index, word_value, r0_lo, r0_lo_incl, r0_hi, r0_hi_incl, r1_lo, ...)`. ONE canonical `word_index`/`word_value` pair runs through every stage (never renamed). The k-th field owns `r{k}_*`; the running intersection lives in `r0_*` (field 0's columns). Word rows join aligned on `word_index`. Intersection is null-aware; emptiness and zero masks prune per-state. The caller maps `dimension_name -> r{k}_*` from the field list it supplied.

- [ ] **Step 1: Write failing tests**

```python
# tests/accumulator/test_construction_lowering.py — append
from mountainash_rules.engines.accumulator.construction import compose_fields


@pytest.mark.unit
def test_two_field_composition(backend):
    """region(EU/US) x tier([0,10],[5,15]) -> cells only where sources exist."""
    md = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT,
                  data_type=DataType.STR),
        Dimension(dimension_name="tier", match_strategy=MatchStrategy.RANGE,
                  data_type=DataType.INT,
                  range_min_field="tier_min", range_max_field="tier_max"),
    ])
    rules = build_backend_df(backend, {
        "uuid": ["A", "B"],
        "region": ["EU", "US"],
        "tier_min": [0, 5], "tier_max": [10, 15],
    })
    lowered = lower_sources(md, rules)
    alloc = allocation_relation(rules, build_backend_df(backend, {
        "uuid": ["A", "B"], "word_index": [0, 0],
        "bitflag": [1, 2]}))
    cells = compose_fields(lowered, ["region", "tier"], alloc) \
        .filter(ma.col("word_index").eq(ma.lit(0))) \
        .collect().to_dict()
    combos = list(zip(cells["r0_lo"], cells["r1_lo"],
                      cells["r1_hi"], cells["word_value"]))
    # EU x [5,10] would need both sources; A is EU-only, B US-only ->
    # no (EU, [5,10]) with both bits; expected cells:
    assert ("EU", 0, 5, 1) in combos          # A only (A covers [0,10])
    assert ("US", 5, 10, 2) in combos         # B
    assert ("US", 10, 15, 2) in combos
    # (EU, [5,10]) has mask 0 -> absent
    assert ("EU", 5, 10, 0) not in combos
    assert ("EU", 5, 10, 3) not in combos


@pytest.mark.unit
def test_empty_intersection_pruned(backend):
    """age>10 GT and age<5 LT on the same field conjoin to nothing —
    represented here as two fields whose cross leaves no survivor."""
    md = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="a1", match_strategy=MatchStrategy.GREATER_THAN,
                  data_type=DataType.INT),
        Dimension(dimension_name="a2", match_strategy=MatchStrategy.LESS_THAN,
                  data_type=DataType.INT),
    ])
    rules = build_backend_df(backend, {
        "uuid": ["A", "B"], "a1": [10, 0], "a2": [100, 5]})
    lowered = lower_sources(md, rules)
    alloc = allocation_relation(rules, build_backend_df(backend, {
        "uuid": ["A", "B"], "word_index": [0, 0], "bitflag": [1, 2]}))
    cells = compose_fields(lowered, ["a1", "a2"], alloc)
    assert cells.count_rows() == 0   # A:(10,inf) n B:(-inf,5) = empty
```

- [ ] **Step 2: Run — FAIL**

Run: `hatch run test:test-target tests/accumulator/test_construction_lowering.py -k compose -v`
Expected: FAIL — `ImportError: compose_fields`

- [ ] **Step 3: Implement `compose_fields`**

```python
# construction.py — append

def _null_aware_min_side(a, a_incl, b, b_incl, pick_hi: bool):
    """Intersection of two (possibly unbounded) one-sided bounds.

    Unbounded (null) yields to the bounded side; if both bounded, the
    intersection takes max (for lo) / min (for hi) with the matching
    inclusivity flag carried along.
    """
    if pick_hi:
        choose_b = a.is_null() | b.lt(a) | (b.eq(a) & ~a_incl & b_incl)
    else:
        choose_b = a.is_null() | b.gt(a) | (b.eq(a) & ~a_incl & b_incl)
    val = ma.when(a.is_null()).then(b).when(choose_b).then(b).otherwise(a)
    incl = ma.when(a.is_null()).then(b_incl).when(choose_b).then(b_incl) \
        .otherwise(a_incl)
    return val, incl


def compose_fields(lowered: Relation, fields: list[str],
                   alloc: Relation) -> Relation:
    """Staged §8.2 composition: extend the frontier field by field.

    Output schema — one row per completed state per word (explicit, no
    schema introspection):
        word_index, word_value,            (single canonical pair)
        r{k}_lo, r{k}_lo_incl, r{k}_hi, r{k}_hi_incl   for each field k
    Word rows are aligned by word_index at every stage join — masks AND
    only within the same word. The k-th entry of `fields` owns r{k}_*.
    """
    frontier = None
    for k, field in enumerate(fields):
        proj = project_field(lowered, field, alloc)
        # rename ONLY the region columns; word_index/word_value keep
        # their canonical names across all stages.
        proj = proj.rename({c: f"r{k}_{c}" for c in
                            ("lo", "hi", "lo_incl", "hi_incl")})
        if frontier is None:
            frontier = proj
            continue
        joined = frontier.cross_join(proj).filter(
            ma.col("word_index").eq(ma.col(f"r{k}_word_index")))
        lo, lo_i = _null_aware_min_side(
            ma.col(f"r0_lo"), ma.col(f"r0_lo_incl"),
            ma.col(f"r{k}_lo"), ma.col(f"r{k}_lo_incl"), pick_hi=False)
        hi, hi_i = _null_aware_min_side(
            ma.col(f"r0_hi"), ma.col(f"r0_hi_incl"),
            ma.col(f"r{k}_hi"), ma.col(f"r{k}_hi_incl"), pick_hi=True)
        emptiness = (
            ma.col(f"r0_lo").is_not_null()
            & ma.col(f"r0_hi").is_not_null()
            & (ma.col(f"r0_lo").gt(ma.col(f"r0_hi"))
               | (ma.col(f"r0_lo").eq(ma.col(f"r0_hi"))
                  & ~(ma.col(f"r0_lo_incl") & ma.col(f"r0_hi_incl"))))
        )
        frontier = (joined
                    .with_columns(lo.alias(f"r0_lo"),
                                  lo_i.alias(f"r0_lo_incl"),
                                  hi.alias(f"r0_hi"),
                                  hi_i.alias(f"r0_hi_incl"))
                    .with_columns(ma.col("word_value")
                                  .bitwise_and(ma.col(f"r{k}_word_value"))
                                  .alias("word_value"))
                    .filter(~emptiness))
    # zero-mask prune per completed state (region across all its words)
    n = len(fields)
    region_cols = [f"r{k}_{c}" for k in range(n)
                   for c in ("lo", "lo_incl", "hi", "hi_incl")]
    nonzero = frontier.group_by("word_index", *region_cols) \
        .agg(ma.col("word_value").max().alias("__any")) \
        .filter(ma.col("__any").gt(ma.lit(0))) \
        .select("word_index", *region_cols)
    return frontier.join(nonzero, left_on=["word_index", *region_cols],
                         right_on=["word_index", *region_cols], how="semi")
```

> **Step 4 (schema check):** the output schema of `compose_fields` is explicit in its docstring — `(word_index, word_value, r0_lo, r0_lo_incl, r0_hi, r0_hi_incl, r1_*, ...)` with ONE canonical `word_index`/`word_value` pair (never renamed; folded into `r0_*` — the running intersection). Add one structural test asserting exactly that schema and that a two-field join stays word-aligned. Explicit column lists throughout — no schema introspection.

```


- [ ] **Step 5: Run composition tests to green**

Run: `hatch run test:test-target tests/accumulator/test_construction_lowering.py -k compose -v`
Expected: PASS both.

- [ ] **Step 6: Commit**

```bash
git add src/mountainash_rules/engines/accumulator/construction.py \
        tests/accumulator/test_construction_lowering.py
git commit -m "feat(accumulator): staged backend field composition frontier"
```

---

### Task 5: Membership decode and exact integer folds

**Files:**
- Modify: `src/mountainash_rules/engines/accumulator/construction.py`
- Create: `tests/accumulator/test_construction_folds.py`

**Interfaces:**
- Consumes: `compose_fields`, `allocation_relation`
- Produces: `decode_edges(completed, alloc, region_cols) -> Relation` `(uuid, *region_cols)` distinct; `fold_outputs(edges, rules, region_cols, aggregates) -> Relation` — per (region-tuple, output_name) exact fold; INT sum via backend `sum` (integer basis-point domain note, 2026-09-21); FLOAT raises `NotImplementedError` pointing at the §16.2 route.

- [ ] **Step 1: Write failing tests (F-boundary oracle)**

```python
# tests/accumulator/test_construction_folds.py
import pytest
import mountainash.expressions as ma
from tests.conftest import build_backend_df
from tests.accumulator.test_construction_lowering import (
    CONSTRUCTION_BACKENDS, backend, _lowered,  # reuse fixtures/helpers
)
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.core.constants import MatchStrategy, DataType
from mountainash_rules.engines.accumulator.construction import (
    lower_sources, allocation_relation, compose_fields, decode_edges,
    fold_outputs,
)


def _completed_tier(backend):
    md = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="tier", match_strategy=MatchStrategy.RANGE,
                  data_type=DataType.INT,
                  range_min_field="tier_min", range_max_field="tier_max")])
    rules = build_backend_df(backend, {
        "uuid": ["A", "B"], "tier_min": [0, 5], "tier_max": [10, 15],
        "amount": [10, 20]})
    lowered = lower_sources(md, rules)
    alloc = allocation_relation(rules, build_backend_df(backend, {
        "uuid": ["A", "B"], "word_index": [0, 0], "bitflag": [1, 2]}))
    return rules, compose_fields(lowered, ["tier"], alloc)


@pytest.mark.unit
def test_decode_edges_f_boundary(backend):
    rules, completed = _completed_tier(backend)
    alloc = allocation_relation(rules, build_backend_df(backend, {
        "uuid": ["A", "B"], "word_index": [0, 0], "bitflag": [1, 2]}))
    edges = decode_edges(completed, alloc, ["r0_lo", "r0_lo_incl", "r0_hi", "r0_hi_incl"]).collect().to_dict()
    got = {(lo, hi, u) for lo, hi, u in
           zip(edges["r0_lo"], edges["r0_hi"], edges["uuid"])}
    # point regions carry lo==hi; intervals open
    assert (5, 10, "A") in got or (5, 10, "A") in {
        (lo, hi, u) for lo, hi, u in got}
    assert (0, 5, "A") in got
    assert (10, 15, "B") in got
    assert (5, 10, "B") in got
    # A must NOT appear on (10,15)
    assert ((10, 15, "A")) not in got


@pytest.mark.unit
def test_fold_sum_f_boundary(backend):
    rules, completed = _completed_tier(backend)
    alloc = allocation_relation(rules, build_backend_df(backend, {
        "uuid": ["A", "B"], "word_index": [0, 0], "bitflag": [1, 2]}))
    edges = decode_edges(completed, alloc, ["r0_lo", "r0_lo_incl", "r0_hi", "r0_hi_incl"])
    out = fold_outputs(
        edges, rules, ["r0_lo", "r0_lo_incl", "r0_hi", "r0_hi_incl"],
        [(ma.col("amount").sum().alias("pricing.sum"),
          ["r0_lo", "r0_lo_incl", "r0_hi", "r0_hi_incl"], DataType.INT)],
    ).collect().to_dict()
    folds = {(lo, hi): v for lo, hi, v in
             zip(out["r0_lo"], out["r0_hi"], out["pricing.sum"])}
    assert folds[(0, 5)] == 10      # A
    assert folds[(5, 10)] == 30     # A+B
    assert folds[(10, 15)] == 20    # B
    assert folds[(5, 5)] == 30 and folds[(10, 10)] == 30  # points incl both


@pytest.mark.unit
def test_float_fold_defers_to_native_route(backend):
    rules, completed = _completed_tier(backend)
    alloc = allocation_relation(rules, build_backend_df(backend, {
        "uuid": ["A", "B"], "word_index": [0, 0], "bitflag": [1, 2]}))
    edges = decode_edges(completed, alloc, ["r0_lo", "r0_lo_incl", "r0_hi", "r0_hi_incl"])
    with pytest.raises(NotImplementedError, match="16.2"):
        fold_outputs(edges, rules,
                     [(ma.col("amount").sum().alias("pricing.sum"),
                       ["r0_lo", "r0_lo_incl", "r0_hi", "r0_hi_incl"], DataType.FLOAT)])
```

- [ ] **Step 2: Run — FAIL**

Run: `hatch run test:test-target tests/accumulator/test_construction_folds.py -v`
Expected: FAIL — `ImportError: decode_edges`

- [ ] **Step 3: Implement decode + folds**

```python
# construction.py — append

def decode_edges(completed: Relation, alloc: Relation,
                 region_cols: list[str]) -> Relation:
    """Decode mask words to distinct (uuid, region) edges on the backend.

    For each completed long row, join the allocation map on word_index
    and keep rows where the word has the allocation's bit set
    (bitwise_and != 0). One uuid per word; across words distinct —
    unique() enforces exactly-once edges regardless of word count.
    """
    joined = completed.join(alloc, left_on=["word_index"],
                            right_on=["word_index"], how="inner") \
        .with_columns(ma.col("word_value")
                      .bitwise_and(ma.col("bitflag"))
                      .alias("__hit")) \
        .filter(ma.col("__hit").ne(ma.lit(0))) \
        .select("uuid", *region_cols)
    return joined.unique()


def fold_outputs(edges: Relation, rules: t.Any, region_cols: list[str],
                 aggregates: list[tuple[t.Any, list[str], t.Any]]) -> Relation:
    """Fold contributor contributions once per (region, output).

    aggregates: list of (expression, key_cols, data_type). INT uses the
    backend sum directly (owner domain note 2026-09-21: integer basis
    points; ordinary int64 sum meets the numeric-1 int64-range clause).
    FLOAT requires the §16.2 backend-owned exact-state route and is an
    explicit deferred capability here, never an ordinary float sum.
    """
    for _, _, dt in aggregates:
        if dt is not DataType.INT:
            raise NotImplementedError(
                f"Exact {dt} folds require the §16.2 backend-owned "
                "exact-state route; deferred slice")
    contrib = relation(rules).select("uuid", *[
        c for expr, _, _ in aggregates
        for c in []] )  # contributions referenced by the caller's exprs
    # contribution columns are whatever the aggregate expressions read;
    # join edges to the full source row on uuid, then group and fold.
    joined = edges.join(relation(rules), left_on=["uuid"],
                        right_on=["uuid"], how="inner")
    return joined.group_by(*region_cols).agg(
        *[expr for expr, _, _ in aggregates])
```

> **Schema introspection note (same as Task 4):** replace both `collect_schema_or_list(...)` placeholders with the explicitly threaded `region_cols` list from Task 4's frontier construction (pass it through as a parameter: `decode_edges(completed, alloc, region_cols)`). Explicit lists over schema reflection — decide at implementation, tests pin the behavior.

- [ ] **Step 4: Run folds tests to green**

Run: `hatch run test:test-target tests/accumulator/test_construction_folds.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/engines/accumulator/construction.py \
        tests/accumulator/test_construction_folds.py
git commit -m "feat(accumulator): backend mask decode to UUID edges and int64 exact folds"
```

---

### Task 6: Lattice materialization — cells, sentinel bounds, inclusivity adjustment

**Files:**
- Create: `src/mountainash_rules/engines/accumulator/disjoint_lattice.py`
- Create: `tests/accumulator/test_disjoint_lattice.py`

**Interfaces:**
- Consumes: Task 5 outputs
- Produces: `DisjointLattice` holding `cells` (collected native frame: region cols + output cols), `edges` (collected: uuid + region cols), `metadata` (lattice DimensionsMetadata derived host-side — permitted metadata assembly). Null region bounds → `unknown_sentinel_for(type)` (ternary-0 wildcard at serve time; Review Focus 5). INT cells whose flags mismatch the declared dimension inclusivity adjust by ±1 via backend `when` expressions. Empty results carry schema via `sources.filter(lit(False))` transformation — no native empty-frame construction, no new purity tag.

- [ ] **Step 1: Write failing tests**

```python
# tests/accumulator/test_disjoint_lattice.py
import pytest
from tests.conftest import build_backend_df
from tests.accumulator.test_construction_lowering import CONSTRUCTION_BACKENDS, backend
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.core.constants import (
    MatchStrategy, DataType, UNKNOWN_NUMERIC)
from mountainash_rules.engines.accumulator.disjoint_lattice import DisjointLattice
from mountainash_rules.engines.accumulator.aggregate import (
    Aggregate, AggregateOp)

AGG_AMOUNT = [Aggregate(
    column_name="amount", output_name="pricing.sum",
    operation=AggregateOp.SUM, data_type=DataType.INT,
    numeric_semantics="numeric-1")]


def _build(backend):
    md = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="tier", match_strategy=MatchStrategy.RANGE,
                  data_type=DataType.INT,
                  range_min_field="tier_min", range_max_field="tier_max")],
        output_fields=["pricing.sum", "__contributor_ids"])
    rules = build_backend_df(backend, {
        "uuid": ["A", "B"], "tier_min": [0, 5], "tier_max": [10, 15],
        "amount": [10, 20]})
    return md, rules


@pytest.mark.unit
def test_materialize_cells_with_outputs(backend):
    md, rules = _build(backend)
    lat = DisjointLattice.build(md, rules, aggregates=AGG_AMOUNT, source_id_field="uuid",
                                frame_factory=build_backend_df)
    assert lat.count == 5  # 2 points + 3 intervals incl exteriors? see note
    cells = lat.cells.to_dict()
    assert "pricing.sum" in cells
    # edges decode: membership preserved
    edges = lat.edges.to_dict()
    assert set(edges["uuid"]) == {"A", "B"}


@pytest.mark.unit
def test_sentinel_bounds_materialize(backend):
    """Wildcard catch-all rows carry UNKNOWN sentinels, not nulls."""
    md = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT,
                  data_type=DataType.STR)],
        output_fields=["pricing.sum", "__contributor_ids"])
    rules = build_backend_df(backend, {
        "uuid": ["W"], "region": ["EU"], "amount": [5]})
    # W is NOT wildcard here (region=EU specific) -> no catch-all; use a
    # real wildcard: region=UNKNOWN sentinel
    rules = build_backend_df(backend, {
        "uuid": ["W"], "region": ["<NA>"], "amount": [5]})
    lat = DisjointLattice.build(md, rules, aggregates=AGG_AMOUNT, source_id_field="uuid",
                                frame_factory=build_backend_df)
    cells = lat.cells.to_dict()
    assert cells["region"] == ["<NA>"]  # sentinel, not None


def test_empty_source_set_schema_carry(backend):
    """Zero-cell build yields an empty lattice with correct schema.
    Exercises the internal _build_from_lowered with a false-filtered
    source relation — no test plumbing in the public build signature."""
    md, rules = _build(backend)
    empty_rules = relation(rules).filter(ma.lit(False))
    lat = DisjointLattice._build_from_lowered(
        md, empty_rules, aggregates=AGG_AMOUNT,
        frame_factory=build_backend_df)
    assert lat.count == 0
    assert "pricing.sum" in lat.cells.columns
```

- [ ] **Step 2: Run — FAIL**

Run: `hatch run test:test-target tests/accumulator/test_disjoint_lattice.py -v`
Expected: FAIL — `ImportError: DisjointLattice`

- [ ] **Step 3: Implement DisjointLattice.build**

```python
# src/mountainash_rules/engines/accumulator/disjoint_lattice.py
"""Immutable disjoint-cell lattice: collected backend relations."""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash.relations import relation

from mountainash_rules.core.constants import (
    DataType,
    DimensionRole,
    MatchStrategy,
    unknown_sentinel_for,
)
from mountainash_rules.engines.accumulator.construction import (
    allocation_relation,
    compose_fields,
    decode_edges,
    fold_outputs,
    lower_sources,
)

if t.TYPE_CHECKING:
    from mountainash_rules.core.dimension import Dimension, DimensionsMetadata


def _lattice_dimension(dim: "Dimension") -> "Dimension":
    """Lattice metadata: GT/LT -> RANGE, NOT_EQUAL/SET_EXCLUSION rejected
    upstream by lowering; EXACT/RANGE preserved."""
    from mountainash_rules.core.dimension import Dimension
    strat = dim.match_strategy
    kwargs: dict[str, t.Any] = {
        "dimension_name": dim.dimension_name,
        "context_field": dim.context_field,
        "match_strategy": strat,
        "data_type": dim.data_type,
        "role": dim.role,
    }
    if strat in (MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN):
        kwargs["match_strategy"] = MatchStrategy.RANGE
        kwargs["range_min_field"] = f"__{dim.dimension_name}_lo"
        kwargs["range_max_field"] = f"__{dim.dimension_name}_hi"
    return Dimension(**kwargs)


class DisjointLattice:
    """Collected disjoint cells + membership edges on the source backend."""

    def __init__(self, cells, edges, metadata, region_cols):
        self._cells = cells
        self._edges = edges
        self._metadata = metadata
        self._region_cols = tuple(region_cols)

    @classmethod
    def build(cls, metadata: "DimensionsMetadata", rules: t.Any, *,
              aggregates: t.Sequence["Aggregate"],
              source_id_field: str = "uuid",
              frame_factory: t.Callable[[str, dict], t.Any]) -> "DisjointLattice":
        """Run the full backend construction pipeline and collect once.

        frame_factory: the engine's injected literal-frame factory
        (control-plane metadata tables only — allocation map). It is the
        single narrow host boundary for building the allocation literal
        table on the source backend.
        """
        rel = relation(rules)
        # rename source id column to uuid for the pipeline
        if source_id_field != "uuid":
            rel = rel.rename({source_id_field: "uuid"})
        lowered = lower_sources(metadata, rel)
        fields = []
        seen: set[str] = set()
        for d in metadata.dimensions:
            if d.role is not DimensionRole.CONSTRAINT:
                continue
            f = d.resolved_rule_field if d.match_strategy is not MatchStrategy.RANGE \
                else d.range_min_field
            if f not in seen:
                seen.add(f)
                fields.append(d.dimension_name)
        uuids = rel.select("uuid").unique().collect().to_dict()["uuid"]
        alloc_rows = {"uuid": [], "word_index": [], "bitflag": []}
        for i, u in enumerate(sorted(uuids)):
            alloc_rows["uuid"].append(u)
            alloc_rows["word_index"].append(i // 63)
            alloc_rows["bitflag"].append(1 << (i % 63))
        alloc = allocation_relation(
            rel, frame_factory("allocation", alloc_rows))

        completed = compose_fields(lowered, fields, alloc)
        # Rename composed region columns (r{k}_*) to each dimension's real
        # bound names so region_cols == field-name columns the lattice and
        # the serving metadata expect. Mapping is positional over `fields`.
        rename_map: dict[str, str] = {}
        for dim in metadata.dimensions:
            if dim.role is not DimensionRole.CONSTRAINT:
                continue
            k = fields.index(dim.dimension_name)
            if dim.match_strategy is MatchStrategy.RANGE:
                pairs = [
                    (f"r{k}_lo", dim.range_min_field),
                    (f"r{k}_lo_incl", f"__{dim.range_min_field}_incl"),
                    (f"r{k}_hi", dim.range_max_field),
                    (f"r{k}_hi_incl", f"__{dim.range_max_field}_incl"),
                ]
            else:  # EXACT / GT / LT lower to point-or-range bounds
                pairs = [
                    (f"r{k}_lo", f"__{dim.dimension_name}_lo"),
                    (f"r{k}_lo_incl", f"__{dim.dimension_name}_lo_incl"),
                    (f"r{k}_hi", f"__{dim.dimension_name}_hi"),
                    (f"r{k}_hi_incl", f"__{dim.dimension_name}_hi_incl"),
                ]
            rename_map.update(pairs)
        completed = completed.rename(rename_map)
        region_cols = [rename_map[c] for c in
                       [f"r{k}_{c}" for k in range(len(fields))
                        for c in ("lo", "lo_incl", "hi", "hi_incl")]]
        edges = decode_edges(completed, alloc, region_cols)

        agg_specs = []
        for agg in aggregates:
            # Declared aggregates (Aggregate model: column_name, operation,
            # output_name, data_type). Sum-only in this slice; other ops
            # and FLOAT exact-state are deferred slices.
            agg_specs.append((
                ma.col(agg.column_name).sum().alias(agg.output_name),
                region_cols, DataType.INT))
        outputs = fold_outputs(edges, rel, region_cols, agg_specs) if agg_specs else None

        cells_rel = outputs if outputs is not None else \
            edges.filter(ma.lit(False)).select(
                *region_cols, ma.lit(None).alias("__placeholder"))

        # null -> sentinel materialization on bound-like region cols
        sent_exprs = []
        for d in metadata.dimensions:
            if d.role is not DimensionRole.CONSTRAINT:
                continue
            # only BOUND columns (lo/hi), not the _incl flags
            for bound in _bound_columns(d, region_cols, with_flags=False):
                sent = unknown_sentinel_for(d.data_type)
                sent_exprs.append(
                    ma.when(ma.col(bound).is_null())
                    .then(ma.lit(sent)).otherwise(ma.col(bound))
                    .alias(bound))
        cells_rel = cells_rel.with_columns(*sent_exprs) if sent_exprs \
            else cells_rel

        # INT inclusivity adjustment to the dimension's declared flags
        adj = []
        for d in metadata.dimensions:
            if d.role is not DimensionRole.CONSTRAINT or \
                    d.match_strategy is not MatchStrategy.RANGE or \
                    d.data_type is not DataType.INT:
                continue
            lo_c, hi_c = _bound_columns(d, region_cols, with_flags=False)
            lo_f, hi_f = (f"__{d.range_min_field}_incl",
                          f"__{d.range_max_field}_incl")
            if lo_c in region_cols and not d.range_min_inclusive:
                adj.append(ma.when(ma.col(lo_f))
                           .then(ma.col(lo_c).sub(ma.lit(1)))
                           .otherwise(ma.col(lo_c)).alias(lo_c))
            if hi_c in region_cols and d.range_max_inclusive:
                adj.append(ma.when(~ma.col(hi_f))
                           .then(ma.col(hi_c).sub(ma.lit(1)))
                           .otherwise(ma.col(hi_c)).alias(hi_c))
        if adj:
            cells_rel = cells_rel.with_columns(*adj)

        lat_md = _lattice_metadata(metadata, [a for a in metadata.output_fields])
        return cls(cells_rel.collect(), edges.collect(), lat_md, region_cols)

    @property
    def cells(self): return self._cells

    @property
    def edges(self): return self._edges

    @property
    def metadata(self): return self._metadata

    @property
    def count(self) -> int:
        return len(self._cells)

    @property
    def is_disjoint(self) -> bool:
        return True
```

> **Helpers to finish in this task:** `_bound_columns(dim)` returns the COMPOSED region-column names for a dimension in `fields` order (`r{k}_lo`, `r{k}_lo_incl`, `r{k}_hi`, `r{k}_hi_incl` where k = fields.index(dim.dimension_name)) — NOT the original rule-field names; the sentinel and adjustment loops below match against these. `_lattice_metadata(metadata, aggregates)` derives the lattice DimensionsMetadata and MUST copy `range_min_field`/`range_max_field`/`range_min_inclusive`/`range_max_inclusive` for every RANGE dimension (not just GT/LT conversions) and `resolved_rule_field` for EXACT — otherwise the reconstructed Dimension cannot compile. `_constraint_fields(metadata)` (module-level in construction.py): ordered constraint-dimension names deduplicated by physical field. `_force_empty_for_test` is REMOVED from the public signature; the empty-schema path is exercised via an internal `_build_from_lowered(lowered, fields, alloc, rel, metadata, aggregates)` that tests call directly with a false-filtered source relation. Aggregates are declared `Aggregate` objects threaded through `build(..., aggregates=...)`; `__contributor_ids` in `metadata.output_fields` is served from edges at apply time, not stored per row.

- [ ] **Step 4: Run to green; verify purity**

Run: `hatch run test:test-target tests/accumulator/test_disjoint_lattice.py -v && hatch run test:test-target tests/test_backend_purity.py -v`
Expected: all PASS; zero new `# allow:` tags.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/engines/accumulator/disjoint_lattice.py \
        tests/accumulator/test_disjoint_lattice.py
git commit -m "feat(accumulator): DisjointLattice materialization with sentinel bounds"
```

---

### Task 7: F-boundary acceptance — the spec's own examples

**Files:**
- Create: `tests/accumulator/test_construction_acceptance.py`

**Interfaces:**
- Consumes: `DisjointLattice.build`

- [ ] **Step 1: Write the acceptance tests (Review Focus 1)**

```python
# tests/accumulator/test_construction_acceptance.py
"""F-boundary acceptance: canonical regions from the replacement spec."""
import pytest
from tests.conftest import build_backend_df
from tests.accumulator.test_construction_lowering import CONSTRUCTION_BACKENDS, backend
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.core.constants import MatchStrategy, DataType
from mountainash_rules.engines.accumulator.disjoint_lattice import DisjointLattice


def _tier_lattice(backend, rows):
    md = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="tier", match_strategy=MatchStrategy.RANGE,
                  data_type=DataType.INT,
                  range_min_field="tier_min", range_max_field="tier_max")],
        output_fields=["pricing.sum", "__contributor_ids"])
    rules = build_backend_df(backend, {
        "uuid": [r["u"] for r in rows],
        "tier_min": [r["lo"] for r in rows],
        "tier_max": [r["hi"] for r in rows],
        "amount": [r["amt"] for r in rows]})
    return DisjointLattice.build(md, rules, aggregates=AGG_AMOUNT, source_id_field="uuid",
                                 frame_factory=build_backend_df)


@pytest.mark.integration
def test_overlap_three_cells(backend):
    """§8.1: A=[0,10]+10, B=[5,15]+20 -> [0,5):A, [5,10]:A+B, (10,15]:B."""
    lat = _tier_lattice(backend, [
        {"u": "A", "lo": 0, "hi": 10, "amt": 10},
        {"u": "B", "lo": 5, "hi": 15, "amt": 20}])
    cells = lat.cells.to_dict()
    folds = {(lo, hi): v for lo, hi, v in
             zip(cells["r0_lo"], cells["r0_hi"], cells["pricing.sum"])}
    assert folds[(0, 5)] == 10
    assert folds[(5, 10)] == 30
    assert folds[(10, 15)] == 20


@pytest.mark.integration
def test_touching_inclusive_singleton(backend):
    """[0,10]+[10,20] both inclusive -> singleton {10} keeps both."""
    lat = _tier_lattice(backend, [
        {"u": "A", "lo": 0, "hi": 10, "amt": 10},
        {"u": "B", "lo": 10, "hi": 20, "amt": 20}])
    cells = lat.cells.to_dict()
    folds = {(lo, hi): v for lo, hi, v in
             zip(cells["r0_lo"], cells["r0_hi"], cells["pricing.sum"])}
    assert folds[(10, 10)] == 30          # singleton: both contributors
    assert folds[(0, 10)] == 10           # [0,10): A (adjusted bound)
    assert folds[(10, 20)] == 20          # (10,20]: B


@pytest.mark.integration
def test_nested_a_b_distinct_a_only_cells(backend):
    """§9: A=[0,20]+5, B=[5,10]+10 -> two A-only cells, one shared fold."""
    lat = _tier_lattice(backend, [
        {"u": "A", "lo": 0, "hi": 20, "amt": 5},
        {"u": "B", "lo": 5, "hi": 10, "amt": 10}])
    cells = lat.cells.to_dict()
    folds = {(lo, hi): v for lo, hi, v in
             zip(cells["r0_lo"], cells["r0_hi"], cells["pricing.sum"])}
    a_only = [k for k, v in folds.items() if v == 5]
    assert len(a_only) == 2                    # [0,5) and (10,20]
    assert folds[(5, 10)] == 15
```

- [ ] **Step 2: Run on all five backends**

Run: `hatch run test:test-target tests/accumulator/test_construction_acceptance.py -v`
Expected: all PASS. Failures here are algorithm bugs — fix `construction.py`, not the tests.

- [ ] **Step 3: Commit**

```bash
git add tests/accumulator/test_construction_acceptance.py
git commit -m "test(accumulator): F-boundary acceptance on five backends"
```

---

### Task 8: F-mixed composition, wildcard base, zero contributor, 130-wide masks

**Files:**
- Modify: `tests/accumulator/test_construction_acceptance.py`

- [ ] **Step 1: Write the F-mixed acceptance tests (Review Focus 2, 4)**

```python
# tests/accumulator/test_construction_acceptance.py — append
from mountainash_rules.core.constants import UNKNOWN, UNKNOWN_NUMERIC


@pytest.mark.integration
def test_f_mixed_two_scope_single_partition_core(backend):
    """F-mixed core (single scope 'base'): A=true→600, B=x∈[5,10]→10,
    Z=true→0. Regions: D-split at 5,10; folds 600/610/600; Z retained."""
    md = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="x", match_strategy=MatchStrategy.RANGE,
                  data_type=DataType.INT,
                  range_min_field="x_min", range_max_field="x_max")],
        output_fields=["pricing.sum", "__contributor_ids"])
    rules = build_backend_df(backend, {
        "uuid": ["A", "B", "Z"],
        "x_min": [UNKNOWN_NUMERIC, 5, UNKNOWN_NUMERIC],
        "x_max": [UNKNOWN_NUMERIC, 10, UNKNOWN_NUMERIC],
        "bps": [600, 10, 0]})
    # A and Z are wildcards -> whole-domain coverage + catch-all absent
    # (they cover everything); B splits [5,10] out.
    lat = DisjointLattice.build(md, rules, aggregates=AGG_AMOUNT, source_id_field="uuid",
                                frame_factory=build_backend_df)
    cells = lat.cells.to_dict()
    folds = {(lo, hi): v for lo, hi, v in
             zip(cells["r0_lo"], cells["r0_hi"], cells["pricing.sum"])}
    assert folds[(5, 10)] == 610        # A+B+Z
    outside = [v for k, v in folds.items() if k not in ((5, 10),)]
    assert all(v == 600 for v in outside)   # A+Z everywhere else
    edges = lat.edges.to_dict()
    assert "Z" in set(edges["uuid"])     # zero contributor retained


@pytest.mark.integration
def test_f_wide_130_contributors_multiword(backend):
    """130 identical-predicate uuids -> 3 words; every bit decodes;
    fold = 130 * amount. Word positions 0/62/63/125/126/129 exercised."""
    n = 130
    md = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="tier", match_strategy=MatchStrategy.RANGE,
                  data_type=DataType.INT,
                  range_min_field="tier_min", range_max_field="tier_max")],
        output_fields=["pricing.sum", "__contributor_ids"])
    rules = build_backend_df(backend, {
        "uuid": [f"s{i:03d}" for i in range(n)],
        "tier_min": [0] * n, "tier_max": [100] * n,
        "bps": [1] * n})
    lat = DisjointLattice.build(md, rules, aggregates=AGG_AMOUNT, source_id_field="uuid",
                                frame_factory=build_backend_df)
    cells = lat.cells.to_dict()
    assert len(cells["pricing.sum"]) >= 1
    assert all(v == n for v in cells["pricing.sum"])   # 130 ones summed
    edges = lat.edges.to_dict()
    assert len(set(edges["uuid"])) == n
    assert {"s000", "s062", "s063", "s125", "s126", "s129"} <= \
        set(edges["uuid"])
```

- [ ] **Step 2: Run the full construction suite**

Run: `hatch run test:test-target tests/accumulator/test_construction_lowering.py tests/accumulator/test_construction_folds.py tests/accumulator/test_construction_acceptance.py tests/accumulator/test_disjoint_lattice.py -v`
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/accumulator/test_construction_acceptance.py
git commit -m "test(accumulator): F-mixed, wildcard-base, zero-contributor, 130-wide acceptance"
```
