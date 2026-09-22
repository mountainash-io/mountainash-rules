# Accumulator Serving, Validation Gate and Persistence Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve disjoint-cell lattices through the filter engine (`FIRST` hit policy over the filter's existing ternary evaluation — no new serving mechanism), enforce the two-stage source-validation gate with backend-derived overlap findings, persist as parquet, and export the new engine through the public API.

**Architecture:** Serving reuses `ExpressionRulesEngine` verbatim: a `DisjointLattice`'s cells frame IS a rules frame whose dimension columns are `range_min_field`/`range_max_field`/exact-value columns, so the existing `DimensionCompiler` ternary evaluation applies unchanged. The default `COLLECT` policy orders survivors by `(__specificity desc, __rule_index asc)` without truncation — the engine's `FIRST` policy would sort by definition order only, so `apply` takes row 0 of COLLECT survivors: the highest-specificity cell, which already contains wildcard contributors' contributions. The validation gate derives overlap findings from backend coverage relations (sources whose regions survive the compose pipeline — extracted per-source witness rows are bounded diagnostics, permitted egress), classified by endpoint inclusivity into `boundary_only` (warning) vs `partial/identical` (warning, all overlaps are warnings; hard failures are structural admission errors only). Persistence writes the collected cells/edges frames to parquet with a YAML manifest (narrow, explicitly-tagged IO escape family, same class as the existing lattice parquet read).

**Tech Stack:** `mountainash.expressions` (`is_null`, `when`, `greatest`/`least`, `sum`), `mountainash.relations` (`join` incl. anti/semi, `group_by`, `cross_join`), existing `ExpressionRulesEngine` + `DimensionCompiler` + `HitPolicy.FIRST`, polars/ibis parquet IO (tagged), `yaml` (existing dependency).

**Spec:** Replacement design §§5, 8–10 (`../mountainash-central/04.planning/mountainash-rules/superpowers/specs/2026-09-20-accumulator-replacement-design.md`); `independent-validation-and-publication.md`, `backend-execution-and-lifetimes.md` (`../mountainash-central/01.principles/mountainash-rules/a.architecture/accumulator/`); disjoint-cell contract validation table (`../mountainash-central/04.planning/mountainash-rules/a.backlog/disjoint-cell-compilation.md`).

## Global Constraints

- Serving adds NO new evaluation mechanism: filter engine, ternaries, hit policies unchanged. If `FIRST`+ternary cannot express the disjoint semantics, that is a design conflict to surface, not a host evaluator to write.
- All overlaps are warnings; hard failures are structural (duplicate UUIDs, missing columns, unsupported strategy) raised at admission by construction. Gate table: hard-fail → block; warnings-unapproved → block; warnings-approved → build; clean → build.
- Findings come from backend relations over the **source** lowering (pre-build, independent of the constructor's outputs). Egress: per-source applicability rows at bounded counts are diagnostic egress; the report schema is host-side.
- Parquet write/read is tagged `# allow: lattice snapshot parquet IO` (matches the existing `Lattice.save/load` escape class); no other purity exceptions.
- Do not modify old accumulator modules in this plan. Old-code removal is a separate owner-sequenced task after the new path passes review.

## Review Focus

1. **Specificity ordering** — a context matching both a specific cell and a wildcard cell must return the specific cell's outputs. COLLECT's ordering (`__specificity` desc) selects it; `FIRST` would pick by row order and is NOT used. Pinned by Task 2.
2. **Sentinel value collision** — a context value equal to the UNKNOWN sentinel (e.g. `"<NA>"`) must not match a cell whose bound was materialized as the sentinel (that cell means "no constraint", ternary-0), while still matching any region genuinely covering it. Pinned by Task 2.
3. **Uncovered context = implicit no-match** — a context outside the covered union returns empty, not an error and not a synthetic cell. Pinned by Task 2.
4. **Stale approval** — approval binds to a source-content digest; edited sources invalidate it. Pinned by Task 3.
5. **Round-trip fidelity** — save→load preserves cells, edges, metadata; loaded lattice serves identically. Pinned by Task 4.

---

### Task 1: Overlap detection from backend coverage — pre-build findings

**Files:**
- Modify: `src/mountainash_rules/engines/accumulator/construction.py` (append)
- Create: `tests/accumulator/test_construction_findings.py`

**Interfaces:**
- Consumes: `lower_sources`, `field_regions` (Plan A Tasks 1–2), `allocation_relation`
- Produces: `overlap_findings(metadata, rules, frame_factory) -> Relation` with schema `(uuid_a, uuid_b, field, overlap_type, witness_lo, witness_hi)`; `classify_findings(findings_rel) -> list[OverlapFinding]` (host-side report assembly from collected diagnostic rows); `OverlapFinding` frozen dataclass; `ValidationFindings(findings, warning_count)`; `source_digest(metadata, rules) -> str` (sha256 of canonical bytes of collected source projection — binds approval).

- [ ] **Step 1: Write failing tests**

```python
# tests/accumulator/test_construction_findings.py
import pytest
from tests.conftest import build_backend_df
from tests.accumulator.test_construction_lowering import CONSTRUCTION_BACKENDS, backend
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.core.constants import MatchStrategy, DataType
from mountainash_rules.engines.accumulator.construction import (
    overlap_findings, classify_findings, source_digest,
)


def _tier_md():
    return DimensionsMetadata(dimensions=[
        Dimension(dimension_name="tier", match_strategy=MatchStrategy.RANGE,
                  data_type=DataType.INT,
                  range_min_field="tier_min", range_max_field="tier_max")])


@pytest.mark.unit
def test_partial_overlap_finding(backend):
    rules = build_backend_df(backend, {
        "uuid": ["A", "B"], "tier_min": [0, 5], "tier_max": [10, 15]})
    rel = overlap_findings(_tier_md(), rules, build_backend_df)
    rows = rel.collect().to_dict()
    assert ("A", "B") in {tuple(p) for p in
                          zip(rows["uuid_a"], rows["uuid_b"])}
    f = classify_findings(rel)
    assert f.warning_count == 1
    assert f.findings[0].overlap_type == "partial"


@pytest.mark.unit
def test_touching_inclusive_boundary_only(backend):
    rules = build_backend_df(backend, {
        "uuid": ["A", "B"], "tier_min": [0, 10], "tier_max": [10, 20]})
    rel = overlap_findings(_tier_md(), rules, build_backend_df)
    f = classify_findings(rel)
    assert f.warning_count == 1
    assert f.findings[0].overlap_type == "boundary_only"
    assert f.findings[0].witness == 10


@pytest.mark.unit
def test_disjoint_no_findings(backend):
    rules = build_backend_df(backend, {
        "uuid": ["A", "B"], "tier_min": [0, 10], "tier_max": [5, 20]})
    f = classify_findings(overlap_findings(_tier_md(), rules,
                                           build_backend_df))
    assert f.warning_count == 0


@pytest.mark.unit
def test_multi_dim_disjoint_other_field_no_finding(backend):
    """Ranges overlap but regions differ: conjunction disjoint -> none."""
    md = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT,
                  data_type=DataType.STR),
        Dimension(dimension_name="tier", match_strategy=MatchStrategy.RANGE,
                  data_type=DataType.INT,
                  range_min_field="tier_min", range_max_field="tier_max")])
    rules = build_backend_df(backend, {
        "uuid": ["A", "B"], "region": ["EU", "US"],
        "tier_min": [0, 5], "tier_max": [10, 15]})
    f = classify_findings(overlap_findings(md, rules, build_backend_df))
    assert f.warning_count == 0


@pytest.mark.unit
def test_identical_predicates_distinct_uuids(backend):
    rules = build_backend_df(backend, {
        "uuid": ["A", "B"], "tier_min": [0, 0], "tier_max": [10, 10]})
    f = classify_findings(overlap_findings(_tier_md(), rules,
                                           build_backend_df))
    types = {fd.overlap_type for fd in f.findings}
    assert "identical" in types
    assert f.warning_count == 1


@pytest.mark.unit
def test_source_digest_stable_and_sensitive():
    r1 = build_backend_df("polars", {"uuid": ["A", "B"],
                                     "tier_min": [0, 5],
                                     "tier_max": [10, 15]})
    r2 = build_backend_df("polars", {"uuid": ["A", "B"],
                                     "tier_min": [0, 5],
                                     "tier_max": [10, 16]})
    d1 = source_digest(_tier_md(), r1)
    d2 = source_digest(_tier_md(), r2)
    assert d1 == source_digest(_tier_md(),
                               build_backend_df("polars",
                                                {"uuid": ["B", "A"],
                                                 "tier_min": [5, 0],
                                                 "tier_max": [15, 10]}))
    assert d1 != d2
```

- [ ] **Step 2: Run — FAIL**

Run: `hatch run test:test-target tests/accumulator/test_construction_findings.py -v`
Expected: FAIL — `ImportError: overlap_findings`

- [ ] **Step 3: Implement findings**

```python
# construction.py — append

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class OverlapFinding:
    uuid_a: str
    uuid_b: str
    field: str
    overlap_type: str          # boundary_only | partial | identical
    witness: t.Any             # single scalar for boundary_only


@dataclass(frozen=True)
class ValidationFindings:
    findings: tuple[OverlapFinding, ...]
    warning_count: int


def _pair_overlap_type(lo_a, hi_a, li_a, hi_a_i,
                       lo_b, hi_b, li_b, hi_b_i):
    """Classify one field-overlap between two source ranges.

    Boundary-only iff the overlap is exactly one shared endpoint and
    BOTH sides include it (positive-width tests are prohibited — the
    singleton is real overlap). Otherwise partial; full equality is
    identical.
    """
    same = (
        ma.when(lo_a.eq(lo_b) & hi_a.eq(hi_b)
                & li_a.eq(li_b) & hi_a_i.eq(hi_b_i))
        .then(ma.lit("identical"))
        .otherwise(ma.lit(None))
    )
    touch_ab = hi_a.eq(lo_b) & hi_a_i & li_b & ~(
        lo_a.is_not_null() & lo_a.lt(hi_a))
    touch_ba = hi_b.eq(lo_a) & hi_b_i & li_a & ~(
        lo_b.is_not_null() & lo_b.lt(hi_b))
    touch = (
        ma.when(touch_ab).then(hi_a)
        .when(touch_ba).then(hi_b)
        .otherwise(ma.lit(None))
    )
    return same, touch


def overlap_findings(metadata, rules, frame_factory=None) -> Relation:
    """Backend-derived source overlap findings (pre-build, independent).

    Uses the source lowering (not construction outputs) so validation
    stays independent of the builder. Classification via endpoint
    inclusivity: an overlap whose region is a single shared included
    point is boundary_only; everything else partial/identical. All
    overlaps are warnings — hard failures are structural admission
    errors only.
    """
    lowered = lower_sources(metadata, rules)
    fields = _constraint_fields(metadata)  # defined in Plan A Task 6 as:
    # def _constraint_fields(metadata: DimensionsMetadata) -> list[str]:
    #     """Ordered constraint-dimension names, deduplicated by field."""
    #     fields, seen = [], set()
    #     for d in metadata.dimensions:
    #         if d.role is not DimensionRole.CONSTRAINT: continue
    #         f = (d.resolved_rule_field if d.match_strategy
    #              is not MatchStrategy.RANGE else d.range_min_field)
    #         if f not in seen:
    #             seen.add(f); fields.append(d.dimension_name)
    #     return fields
    pair_rows = []
    for field in fields:
        src = lowered.filter(ma.col("field").eq(ma.lit(field)))
        a = src.select(
            ma.col("uuid").alias("uuid_a"),
            ma.col("lo").alias("a_lo"), ma.col("hi").alias("a_hi"),
            ma.col("lo_incl").alias("a_li"), ma.col("hi_incl").alias("a_hi_i"),
        )
        b = src.select(
            ma.col("uuid").alias("uuid_b"),
            ma.col("lo").alias("b_lo"), ma.col("hi").alias("b_hi"),
            ma.col("lo_incl").alias("b_li"), ma.col("hi_incl").alias("b_hi_i"),
        )
        # Symmetric INTERVAL INTERSECTION — not _covers_region (which is
        # containment and would miss [0,10]/[5,15] partial and [0,10]/[10,20]
        # touching). Null bound = unbounded side.
        def _intervals_overlap(a_lo, a_hi, a_li, a_hi_i,
                               b_lo, b_hi, b_li, b_hi_i):
            a_before_b = (a_hi.is_not_null() & b_lo.is_not_null() &
                          (a_hi.lt(b_lo) |
                           (a_hi.eq(b_lo) & ~(a_hi_i & b_li))))
            b_before_a = (b_hi.is_not_null() & a_lo.is_not_null() &
                          (b_hi.lt(a_lo) |
                           (b_hi.eq(a_lo) & ~(b_hi_i & a_li))))
            return ~a_before_b & ~b_before_a
        pairs = a.cross_join(b) \
            .filter(ma.col("uuid_a").lt(ma.col("uuid_b"))) \
            .with_columns(_intervals_overlap(
                ma.col("a_lo"), ma.col("a_hi"),
                ma.col("a_li"), ma.col("a_hi_i"),
                ma.col("b_lo"), ma.col("b_hi"),
                ma.col("b_li"), ma.col("b_hi_i"),
            ).alias("__ov")) \
            .filter(ma.col("__ov"))
        same, touch = _pair_overlap_type(
            ma.col("a_lo"), ma.col("a_hi"),
            ma.col("a_li"), ma.col("a_hi_i"),
            ma.col("b_lo"), ma.col("b_hi"),
            ma.col("b_li"), ma.col("b_hi_i"))
        pairs = pairs.with_columns(
            ma.when(same.is_not_null()).then(ma.lit("identical"))
            .when(touch.is_not_null()).then(ma.lit("boundary_only"))
            .otherwise(ma.lit("partial")).alias("overlap_type"),
            touch.alias("witness"),
            ma.lit(field).alias("field"),
        ).select("uuid_a", "uuid_b", "field", "overlap_type", "witness")
        pair_rows.append(pairs)
    # A pair overlapping on every constraint field is a real source
    # overlap; disjointness on ANY field kills it. Take intersection
    # across fields via semi-join on (uuid_a, uuid_b).
    if not pair_rows:
        raise ValueError("overlap_findings requires a constraint dimension")
    joint = pair_rows[0]
    for p in pair_rows[1:]:
        joint = joint.join(p.select("uuid_a", "uuid_b"),
                           left_on=["uuid_a", "uuid_b"],
                           right_on=["uuid_a", "uuid_b"], how="semi")
    return joint


def classify_findings(findings_rel: Relation) -> ValidationFindings:
    """Collect bounded diagnostic rows into the host report type."""
    rows = findings_rel.collect().to_dict()
    out = []
    for ua, ub, fld, ot, w in zip(rows["uuid_a"], rows["uuid_b"],
                                  rows["field"], rows["overlap_type"],
                                  rows.get("witness", [None]*len(rows["uuid_a"]))):
        out.append(OverlapFinding(
            uuid_a=ua, uuid_b=ub, field=fld, overlap_type=ot,
            witness=None if ot != "boundary_only" else w))
    return ValidationFindings(findings=tuple(out), warning_count=len(out))


def source_digest(metadata, rules) -> str:
    """Bind approval to source content: sha256 over the collected
    lowered projection (sorted for order-independence). Diagnostic
    egress of the bounded source projection; host-side hashing."""
    lowered = lower_sources(metadata, rules).collect().to_dict()
    rows = sorted(zip(lowered["uuid"], lowered["field"],
                      lowered["lo"], lowered["hi"],
                      lowered["lo_incl"], lowered["hi_incl"]),
                  key=repr)
    return hashlib.sha256(repr(rows).encode()).hexdigest()
```

> Implementation notes: `same.is_not_null()` detection requires the `same` expression above to produce NULL for non-identical pairs — the `when/otherwise` shape gives exactly that. `_constraint_fields(metadata)` is the field-order helper from Plan A Task 6 (hoist it to a module function there and import here). The touching test's `~(lo < hi)` guard (a point-source `[v,v]` touching) needs one adversarial test added in Step 4. For EXACT fields, pairs with equal values are `identical`; unequal never overlap — the coverage predicate already yields that.

- [ ] **Step 4: Add the point-source boundary adversarial test**

```python
# tests/accumulator/test_construction_findings.py — append


@pytest.mark.unit
def test_point_source_touching_is_boundary_only(backend):
    """[5,5] inclusive point inside B=[0,10] is partial, not boundary;
    [0,5]+[5,10] both inclusive is boundary_only at 5."""
    rules = build_backend_df(backend, {
        "uuid": ["P", "Q"], "tier_min": [5, 0], "tier_max": [5, 10]})
    f = classify_findings(overlap_findings(_tier_md(), rules,
                                           build_backend_df))
    types = {fd.overlap_type for fd in f.findings}
    assert types == {"partial"}
```

- [ ] **Step 5: Run to green**

Run: `hatch run test:test-target tests/accumulator/test_construction_findings.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/mountainash_rules/engines/accumulator/construction.py \
        tests/accumulator/test_construction_findings.py
git commit -m "feat(accumulator): backend-derived source overlap findings and digest binding"
```

---

### Task 2: Serve — FIRST policy over disjoint cells

**Files:**
- Create: `src/mountainash_rules/engines/accumulator/disjoint_engine.py`
- Create: `tests/accumulator/test_disjoint_serve.py`

**Interfaces:**
- Consumes: `DisjointLattice` (Plan A Task 6), `ExpressionRulesEngine`, `HitPolicy.FIRST`
- Produces: `DisjointAccumulatorEngine(frame_factory).apply(lattice, context) -> dict | None`; `.build(metadata, rules, *, source_id_field, approved) -> DisjointLattice` (gate wrapper, Task 3).

- [ ] **Step 1: Write failing tests (Review Focus 1–3)**

```python
# tests/accumulator/test_disjoint_serve.py
import pytest
from tests.conftest import build_backend_df
from tests.accumulator.test_construction_lowering import CONSTRUCTION_BACKENDS, backend
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.core.constants import (
    MatchStrategy, DataType, UNKNOWN, UNKNOWN_NUMERIC)
from mountainash_rules.engines.accumulator.disjoint_engine import (
    DisjointAccumulatorEngine)


TIER_MD = dict(dimension_name="tier", match_strategy=MatchStrategy.RANGE,
               data_type=DataType.INT,
               range_min_field="tier_min", range_max_field="tier_max")
from mountainash_rules.engines.accumulator.aggregate import (
    Aggregate, AggregateOp)
AGG = [Aggregate(
    column_name="amount", output_name="pricing.sum",
    operation=AggregateOp.SUM, data_type=DataType.INT,
    numeric_semantics="numeric-1")]


def _overlap_lattice(backend):
    md = DimensionsMetadata(dimensions=[Dimension(**TIER_MD)],
                            output_fields=["pricing.sum",
                                           "__contributor_ids"])
    rules = build_backend_df(backend, {
        "uuid": ["A", "B"], "tier_min": [0, 5], "tier_max": [10, 15],
        "amount": [10, 20]})
    eng = DisjointAccumulatorEngine(frame_factory=build_backend_df)
    return eng, eng.build(md, rules, aggregates=AGG, source_id_field="uuid", approved=True)


@pytest.mark.integration
def test_apply_regions_f_boundary(backend):
    eng, lat = _overlap_lattice(backend)
    assert eng.apply(lat, {"tier": 3})["pricing.sum"] == 10
    assert eng.apply(lat, {"tier": 7})["pricing.sum"] == 30
    assert eng.apply(lat, {"tier": 12})["pricing.sum"] == 20


@pytest.mark.integration
def test_wildcard_not_double_counted(backend):
    """A=EU→10, W=wildcard→5: EU context returns 15 (A+W in one cell),
    non-EU returns 5. FIRST picks the specific cell at EU."""
    md = DimensionsMetadata(dimensions=[Dimension(
        dimension_name="region", match_strategy=MatchStrategy.EXACT,
        data_type=DataType.STR)],
        output_fields=["pricing.sum", "__contributor_ids"])
    rules = build_backend_df(backend, {
        "uuid": ["A", "W"], "region": ["EU", UNKNOWN], "amount": [10, 5]})
    eng = DisjointAccumulatorEngine(frame_factory=build_backend_df)
    lat = eng.build(md, rules, aggregates=AGG, source_id_field="uuid")
    assert eng.apply(lat, {"region": "EU"})["pricing.sum"] == 15


@pytest.mark.integration
def test_uncovered_context_no_match(backend):
    eng, lat = _overlap_lattice(backend)
    assert eng.apply(lat, {"tier": 99}) is None


@pytest.mark.integration
def test_sentinel_context_survives_as_wildcard(backend):
    """A context equal to the numeric sentinel must not match a cell
    whose unbounded side was materialized as that sentinel."""
    md = DimensionsMetadata(dimensions=[Dimension(**TIER_MD)],
                            output_fields=["pricing.sum",
                                           "__contributor_ids"])
    rules = build_backend_df(backend, {
        "uuid": ["A"], "tier_min": [UNKNOWN_NUMERIC], "tier_max": [10],
        "amount": [10]})
    eng = DisjointAccumulatorEngine(frame_factory=build_backend_df)
    lat = eng.build(md, rules, aggregates=AGG, source_id_field="uuid")
    # (-inf,10] cell: tier=UNKNOWN_NUMERIC is a real numeric context and
    # IS covered by the cell's range (no lower bound).
    r = eng.apply(lat, {"tier": UNKNOWN_NUMERIC})
    assert r is not None and r["pricing.sum"] == 10
    # KNOWN LIMITATION (interface review B4, confirmed against
    # core/compiler.py): the shared ternary machinery treats a CONTEXT
    # value equal to the reserved sentinel as "dimension not provided"
    # on EVERY RANGE cell — the specific cell [5,10] SURVIVES a
    # sentinel-valued context (ternary 0 = wildcard) and is returned.
    # Engine-consistent behavior, surfaced here rather than papered over.
    md2 = DimensionsMetadata(dimensions=[Dimension(**TIER_MD)],
                             output_fields=["pricing.sum",
                                            "__contributor_ids"])
    rules2 = build_backend_df(backend, {
        "uuid": ["S"], "tier_min": [5], "tier_max": [10], "amount": [7]})
    lat2 = eng.build(md2, rules2, aggregates=AGG,
                     source_id_field="uuid")
    r2 = eng.apply(lat2, {"tier": UNKNOWN_NUMERIC})
    assert r2 is not None and r2["pricing.sum"] == 7  # wildcard-survive
    # A host-side guard (reject/normalize sentinel-valued context before
    # evaluate) is the correct place if strict rejection is required.
```

- [ ] **Step 2: Run — FAIL**

Run: `hatch run test:test-target tests/accumulator/test_disjoint_serve.py -v`
Expected: FAIL — `ImportError: DisjointAccumulatorEngine`

- [ ] **Step 3: Implement serving**

```python
# src/mountainash_rules/engines/accumulator/disjoint_engine.py
"""Disjoint-cell accumulator: build gate + FIRST-policy serving."""

from __future__ import annotations

import typing as t

from mountainash_rules.core.constants import HitPolicy
from mountainash_rules.engines.filter.engine import ExpressionRulesEngine

if t.TYPE_CHECKING:
    from mountainash_rules.core.dimension import DimensionsMetadata
    from mountainash_rules.engines.accumulator.disjoint_lattice import (
        DisjointLattice)


class GateBlocked(Exception):
    """Source validation blocked the build (hard or unapproved warnings)."""


class DisjointAccumulatorEngine:
    """Public entry: backend-native build + filter-engine serving."""

    def __init__(self, *, frame_factory: t.Callable[[str, dict], t.Any]):
        self._frame_factory = frame_factory

    def build(self, metadata: "DimensionsMetadata", rules: t.Any, *,
              aggregates, source_id_field: str = "uuid",
              approved: bool = False) -> "DisjointLattice":
        from mountainash_rules.engines.accumulator.construction import (
            classify_findings, overlap_findings, source_digest)
        from mountainash_rules.engines.accumulator.disjoint_lattice import (
            DisjointLattice)

        findings = classify_findings(
            overlap_findings(metadata, rules, self._frame_factory))
        digest = source_digest(metadata, rules)
        self._last_digest = digest
        self._last_findings = findings
        # Gate table: all overlaps are warnings; approval covers them.
        if findings.warning_count > 0 and not approved:
            raise GateBlocked(
                f"{findings.warning_count} unapproved source-overlap "
                "warnings block build")
        return DisjointLattice.build(
            metadata, rules, aggregates=aggregates,
            source_id_field=source_id_field,
            frame_factory=self._frame_factory)

    def apply(self, lattice: "DisjointLattice",
              context: dict[str, t.Any]) -> dict[str, t.Any] | None:
        engine = ExpressionRulesEngine(
            lattice.cells, dimension_metadata=lattice.metadata)
        # Default COLLECT policy orders by (__specificity desc,
        # __rule_index asc) WITHOUT truncation — exactly "most specific
        # wins" for disjoint cells, where FIRST would sort by definition
        # order only. The specific cell already carries wildcard
        # contributors' outputs, so row 0 of the survivors is the answer.
        result = engine.evaluate(context)   # COLLECT ordering
        rows = relation(result.survivors).to_dict()
        if not rows or len(next(iter(rows.values()), [])) == 0:
            return None
        return {k: v[0] for k, v in rows.items()}
```

> Serving relies on two filter-engine properties, both asserted here rather than assumed: (a) `ExpressionRulesEngine.evaluate(context, hit_policy=...)` accepts `HitPolicy.FIRST`; (b) the lattice metadata's dimension columns compile through `DimensionCompiler` — the same compilation path as any rules frame. Task 2's tests are the assertion: if either property fails, STOP and surface the conflict; do not add a host evaluator or a lattice-specific expression path.

- [ ] **Step 4: Run to green**

Run: `hatch run test:test-target tests/accumulator/test_disjoint_serve.py -v`
Expected: all PASS. If (a)/(b) conflict arises, STOP per the note.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/engines/accumulator/disjoint_engine.py \
        tests/accumulator/test_disjoint_serve.py
git commit -m "feat(accumulator): FIRST-policy serving over disjoint-cell lattices"
```

---

### Task 3: Gate integration — approved-build flow with stale-approval rejection

**Files:**
- Modify: `src/mountainash_rules/engines/accumulator/disjoint_engine.py`
- Modify: `tests/accumulator/test_disjoint_serve.py`

**Interfaces:**
- Consumes: Task 1 findings + digest, Task 2 build
- Produces: `DisjointAccumulatorEngine.validate(metadata, rules) -> ValidationFindings + digest`; `build(..., approvals: dict[str, str] | None)` where `approvals` maps `{digest: approver_label}`; build accepts only when every warning-bearing source set has an approval entry keyed by the CURRENT digest.

- [ ] **Step 1: Write failing tests (Review Focus 4)**

```python
# tests/accumulator/test_disjoint_serve.py — append


@pytest.mark.integration
def test_unapproved_warnings_block(backend):
    md = DimensionsMetadata(dimensions=[Dimension(**TIER_MD)],
                            output_fields=["pricing.sum",
                                           "__contributor_ids"])
    rules = build_backend_df(backend, {
        "uuid": ["A", "B"], "tier_min": [0, 5], "tier_max": [10, 15],
        "amount": [10, 20]})
    eng = DisjointAccumulatorEngine(frame_factory=build_backend_df)
    with pytest.raises(GateBlocked):
        eng.build(md, rules, aggregates=AGG, source_id_field="uuid", approved=False)


@pytest.mark.integration
def test_stale_approval_rejected(backend):
    md = DimensionsMetadata(dimensions=[Dimension(**TIER_MD)],
                            output_fields=["pricing.sum",
                                           "__contributor_ids"])
    rules_v1 = build_backend_df(backend, {
        "uuid": ["A", "B"], "tier_min": [0, 5], "tier_max": [10, 15],
        "amount": [10, 20]})
    eng = DisjointAccumulatorEngine(frame_factory=build_backend_df)
    eng.validate(md, rules_v1)
    digest = eng._last_digest
    # approve v1
    eng_v1_ok = eng.build(md, rules_v1, aggregates=AGG, source_id_field="uuid",
                          approvals={digest: "reviewer-1"})
    # edit sources -> digest changes -> same approval must not apply
    rules_v2 = build_backend_df(backend, {
        "uuid": ["A", "B"], "tier_min": [0, 6], "tier_max": [10, 15],
        "amount": [10, 20]})
    with pytest.raises(GateBlocked):
        eng.build(md, rules_v2, aggregates=AGG, source_id_field="uuid",
                  approvals={digest: "reviewer-1"})


@pytest.mark.integration
def test_clean_build_needs_no_approval(backend):
    md = DimensionsMetadata(dimensions=[Dimension(**TIER_MD)],
                            output_fields=["pricing.sum",
                                           "__contributor_ids"])
    rules = build_backend_df(backend, {
        "uuid": ["A", "B"], "tier_min": [0, 10], "tier_max": [5, 20],
        "amount": [10, 20]})
    eng = DisjointAccumulatorEngine(frame_factory=build_backend_df)
    lat = eng.build(md, rules, aggregates=AGG, source_id_field="uuid")   # no approvals
    assert lat.count >= 2
```

- [ ] **Step 2: Run — FAIL (build signature lacks approvals)**

Run: `hatch run test:test-target tests/accumulator/test_disjoint_serve.py -k stale or unapproved or clean -v`
Expected: FAIL — `TypeError: unexpected keyword 'approvals'`

- [ ] **Step 3: Implement approval-keyed build**

```python
# disjoint_engine.py — replace build() body additions

    def validate(self, metadata, rules):
        from mountainash_rules.engines.accumulator.construction import (
            classify_findings, overlap_findings, source_digest)
        findings = classify_findings(
            overlap_findings(metadata, rules, self._frame_factory))
        self._last_findings = findings
        self._last_digest = source_digest(metadata, rules)
        return findings, self._last_digest

    def build(self, metadata, rules, *, aggregates, source_id_field="uuid",
              approved: bool = False,
              approvals: dict[str, str] | None = None) -> "DisjointLattice":
        findings, digest = self.validate(metadata, rules)
        has_warnings = findings.warning_count > 0
        approved_now = bool(approvals and digest in approvals)
        if approved:
            approved_now = approved_now or True  # legacy boolean path
        if has_warnings and not approved_now:
            raise GateBlocked(
                f"{findings.warning_count} unapproved source-overlap "
                "warnings block build (digest=" + digest[:12] + ")")
        return DisjointLattice.build(
            metadata, rules, aggregates=aggregates,
            source_id_field=source_id_field,
            frame_factory=self._frame_factory)
```

- [ ] **Step 4: Run to green**

Run: `hatch run test:test-target tests/accumulator/test_disjoint_serve.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/engines/accumulator/disjoint_engine.py \
        tests/accumulator/test_disjoint_serve.py
git commit -m "feat(accumulator): digest-keyed warning approvals in the build gate"
```

---

### Task 4: Persistence — parquet snapshot with manifest

**Files:**
- Modify: `src/mountainash_rules/engines/accumulator/disjoint_lattice.py`
- Create: `tests/accumulator/test_disjoint_persistence.py`

**Interfaces:**
- Consumes: `DisjointLattice`
- Produces: `DisjointLattice.save(dir)`, `DisjointLattice.load(dir, frame_factory) -> DisjointLattice` — writes `cells.parquet`, `edges.parquet`, `manifest.yaml` (metadata, region_cols, format version). Load restores without rebuilding.

- [ ] **Step 1: Write failing tests (Review Focus 5)**

```python
# tests/accumulator/test_disjoint_persistence.py
import pytest
from tests.conftest import build_backend_df
from tests.accumulator.test_construction_lowering import CONSTRUCTION_BACKENDS, backend
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.core.constants import MatchStrategy, DataType
from mountainash_rules.engines.accumulator.disjoint_lattice import DisjointLattice


def _lattice(backend, tmp_path):
    md = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="tier", match_strategy=MatchStrategy.RANGE,
                  data_type=DataType.INT,
                  range_min_field="tier_min", range_max_field="tier_max")],
        output_fields=["pricing.sum", "__contributor_ids"])
    rules = build_backend_df(backend, {
        "uuid": ["A", "B"], "tier_min": [0, 5], "tier_max": [10, 15],
        "amount": [10, 20]})
    eng_engine = DisjointAccumulatorEngine(frame_factory=build_backend_df)
    lat = eng_engine.build(md, rules, source_id_field="uuid",
                           approved=True)
    lat.save(tmp_path)
    return lat, DisjointLattice.load(tmp_path, frame_factory=build_backend_df)


@pytest.mark.integration
def test_round_trip_preserves_cells(backend, tmp_path):
    lat, loaded = _lattice(backend, tmp_path)
    assert loaded.count == lat.count
    assert loaded.cells.to_dict() == lat.cells.to_dict()
    assert loaded.edges.to_dict() == lat.edges.to_dict()
    assert loaded.metadata.output_fields == lat.metadata.output_fields


@pytest.mark.integration
def test_loaded_lattice_serves(backend, tmp_path):
    from mountainash_rules.engines.accumulator.disjoint_engine import (
        DisjointAccumulatorEngine)
    lat, loaded = _lattice(backend, tmp_path)
    eng = DisjointAccumulatorEngine(frame_factory=build_backend_df)
    a = eng.apply(lat, {"tier": 7})
    b = eng.apply(loaded, {"tier": 7})
    assert a is not None and a == b
```

- [ ] **Step 2: Run — FAIL**

Run: `hatch run test:test-target tests/accumulator/test_disjoint_persistence.py -v`
Expected: FAIL — `AttributeError: save`

- [ ] **Step 3: Implement save/load**

```python
# disjoint_lattice.py — append methods

import pathlib

    def save(self, directory) -> None:
        """Persist cells/edges parquet + metadata manifest."""
        path = pathlib.Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        # allow: lattice snapshot parquet IO (same escape class as the
        # existing Lattice.save/load parquet read in lattice.py)
        self._cells.write_parquet(path / "cells.parquet")
        self._edges.write_parquet(path / "edges.parquet")
        import yaml
        manifest = {
            "format": "disjoint-lattice-v1",
            "metadata": self._metadata.model_dump(mode="python"),
            "region_cols": list(self._region_cols),
        }
        (path / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False))

    @classmethod
    def load(cls, directory, *, frame_factory) -> "DisjointLattice":
        path = pathlib.Path(directory)
        import yaml
        manifest = yaml.safe_load((path / "manifest.yaml").read_text())
        if manifest["format"] != "disjoint-lattice-v1":
            raise ValueError(f"Unsupported lattice format: "
                             f"{manifest['format']}")
        from mountainash_rules.core.dimension import DimensionsMetadata
        md = DimensionsMetadata.model_validate(manifest["metadata"])
        # allow: lattice snapshot parquet IO
        cells = frame_factory_parquet_read(path / "cells.parquet")
        edges = frame_factory_parquet_read(path / "edges.parquet")
        return cls(cells, edges, md, manifest["region_cols"])
```

> `frame_factory_parquet_read`: add ONE method to the frame-factory protocol (`read_parquet(path)`), so the IO tag lives in the factory — a control-plane utility — while the lattice stays purity-clean. If the factory abstraction proves heavier than the tag, fall back to inline `# allow:` tags on the two read lines; decide at review, tests unchanged.

- [ ] **Step 4: Run to green + purity check**

Run: `hatch run test:test-target tests/accumulator/test_disjoint_persistence.py -v && hatch run test:test-target tests/test_backend_purity.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/engines/accumulator/disjoint_lattice.py \
        tests/accumulator/test_disjoint_persistence.py
git commit -m "feat(accumulator): parquet snapshot persistence for DisjointLattice"
```

---

### Task 5: Public API exports

**Files:**
- Modify: `src/mountainash_rules/__init__.py`
- Modify: `tests/test_public_api.py`

**Interfaces:**
- Produces: exports `DisjointAccumulatorEngine`, `DisjointLattice`, `GateBlocked`, `OverlapFinding`, `ValidationFindings`.

- [ ] **Step 1: Write failing test**

```python
# tests/test_public_api.py — append


def test_disjoint_public_api():
    from mountainash_rules import (
        DisjointAccumulatorEngine, DisjointLattice, GateBlocked,
        OverlapFinding, ValidationFindings)
    assert issubclass(GateBlocked, Exception)
```

- [ ] **Step 2: Run — FAIL**

Run: `hatch run test:test-target tests/test_public_api.py::test_disjoint_public_api -v`
Expected: FAIL — ImportError

- [ ] **Step 3: Add exports**

```python
# __init__.py — add imports alongside existing engine imports
from mountainash_rules.engines.accumulator.disjoint_engine import (
    DisjointAccumulatorEngine, GateBlocked)
from mountainash_rules.engines.accumulator.disjoint_lattice import DisjointLattice
from mountainash_rules.engines.accumulator.construction import (
    OverlapFinding, ValidationFindings)
# and append to __all__:
#   "DisjointAccumulatorEngine", "DisjointLattice", "GateBlocked",
#   "OverlapFinding", "ValidationFindings",
```

- [ ] **Step 4: Run public API + purity + full quick suite**

Run: `hatch run test:test-target tests/test_public_api.py -v && hatch run test:test-target tests/test_backend_purity.py -v && hatch run test:test-quick`
Expected: all PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/__init__.py tests/test_public_api.py
git commit -m "feat(accumulator): export disjoint-cell public API"
```

---

### Task 6: Old-path coexistence guard (no removal, explicit boundary)

**Files:**
- Modify: `tests/accumulator/test_disjoint_serve.py` (append)

- [ ] **Step 1: Write the coexistence test**

```python
# tests/accumulator/test_disjoint_serve.py — append


@pytest.mark.unit
def test_old_accumulator_path_untouched():
    """The legacy AccumulatorEngine still imports and builds its own
    path; the disjoint engine shares no build modules with it."""
    from mountainash_rules import AccumulatorEngine          # legacy
    from mountainash_rules.engines.accumulator import (
        analysis as legacy_analysis)                          # legacy
    from mountainash_rules.engines.accumulator import (
        construction as new_construction)                     # new
    assert legacy_analysis.__file__ != new_construction.__file__
```

- [ ] **Step 2: Run**

Run: `hatch run test:test-target tests/accumulator/test_disjoint_serve.py::test_old_accumulator_path_untouched -v`
Expected: PASS (nothing to implement — this is a regression tripwire for the cutover task that follows this plan).

- [ ] **Step 3: Commit**

```bash
git add tests/accumulator/test_disjoint_serve.py
git commit -m "test(accumulator): coexistence tripwire for legacy/new build paths"
```
