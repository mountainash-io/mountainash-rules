# Lattice Snapshot Save/Load — Design

> **Backlog card:** mountainash-central/01.principles/mountainash-rules/h.backlog/lattice-snapshot-save-load.md (P3)
> **Consumer:** mountainash-rules-service `LatticeRegistry` (currently hand-rolls this exact load)
> **Date:** 2026-07-16

## Problem

Build-offline → serve-online has no first-class persistence. Babel CSV is
not an option: its importer strips `__agg_*` by contract, so imported
lattices cannot serve accumulated values. Consumers hand-roll parquet +
sidecar today.

## Design

Two methods on `Lattice` (`engines/accumulator/lattice.py`):

```python
def save(self, dir_path: str | Path) -> Path          # writes the directory
@classmethod
def load(cls, dir_path: str | Path) -> "Lattice"
```

Directory layout (identical to what mountainash-rules-service already
serves):

```
<dir>/
├── lattice.parquet   # combinations frame verbatim — co_*, __agg_*,
│                     # __prime_product, __level all preserved
└── manifest.yaml     # dimensions (DimensionsMetadata JSON-mode dump),
                      # aggregates (list of {column_name, operation}),
                      # partition_key (dict | null)
```

- **Manifest compatibility (one-way, by design):** the YAML is a strict
  superset of babel's `LatticeManifest` — `dimensions` + `aggregates` keys
  are shape-identical, so babel and service tooling can **read**
  rules-written manifests (pydantic ignores the extra `partition_key`),
  without babel appearing in rules' dependencies. The reverse is lossy:
  babel's model has no `partition_key` field, so a babel load→re-emit cycle
  drops it. Acceptable now — every current snapshot is unpartitioned
  (`partition_key: null`; partitioned save/load is a non-goal below) — but
  if partitioned snapshots ever flow through babel, `LatticeManifest`
  needs an optional `partition_key` field first (noted on the babel
  export-serving-names card).
- **Backend purity:** `save` stays pure — `relation(self._df).to_polars().
  write_parquet(...)` uses only the mountainash API plus a method on the
  returned native object (no backend import). `load` needs one
  `import polars as pl  # allow: lattice snapshot parquet read pending backend-agnostic file IO`
  — the package's third allow-tag, joining the two existing ones (per-row
  REGEX fallback in `core/compiler.py`, empty-build schema seed in
  `engines/accumulator/engine.py`).
- Round-trips `is_composed` honestly (`__prime_product` travels in the
  parquet) and `partition_key` (JSON-serialisable values only for now —
  date-typed CONTEXT_KEY partition keys are out of scope until one exists).

## Non-goals

- `LatticeIndex` save/load (rehydrate by loading each lattice and calling
  `engine.index(lattices)`).
- Versioning/migration of the manifest schema (add a `snapshot_version` key
  only when a second version exists — YAGNI).

## Acceptance

- build → save → load → apply equals build → apply (same combinations,
  accumulated values, provenance); `is_composed` True after round-trip;
  backend-purity suite green with the single new allow-tag.
