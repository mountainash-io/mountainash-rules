# Lattice Snapshot Save/Load Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `Lattice.save(dir)` / `Lattice.load(dir)` — faithful build-offline/serve-online persistence (parquet combinations frame + manifest.yaml sidecar).

**Architecture:** `save` writes `relation(df).to_polars().write_parquet(...)` (purity-clean — no backend import) plus a YAML manifest (DimensionsMetadata JSON-mode dump, aggregates, partition_key — babel's `LatticeManifest` reads it, ignoring the extra `partition_key`; babel cannot re-emit that key, which is fine while snapshots are unpartitioned). `load` reads them back through the public `Lattice` constructor; its `import polars` line carries the package's third `# allow:` tag (after `core/compiler.py` REGEX fallback and `engines/accumulator/engine.py` empty-build seed).

**Tech Stack:** mountainash relations, pydantic, pyyaml; polars only behind the allow-tag in `load`.

## Global Constraints

- Repo: `/Users/nathanielramm/git/mountainash-io/mountainash-rules`, branch `develop`, commit per task.
- Suite: `hatch run test:test-quick` · single: `hatch run test:test-target <nodeid>` · lint: `hatch run ruff:check`.
- Purity gate: the ONLY new backend import allowed is the tagged one in `load`; the gate self-discovers modules, no test edits needed for it.
- After pushing, run `hatch env prune` in mountainash-rules-babel.
- Commit trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: save/load round-trip

**Files:**
- Modify: `src/mountainash_rules/engines/accumulator/lattice.py`
- Test: `tests/accumulator/test_lattice.py` (append a class; create the file if the suite keeps lattice tests elsewhere — check `ls tests/accumulator/` first and append to the existing lattice test module)

**Interfaces:**
- Consumes: `Lattice(dataframe, metadata, aggregates, partition_key)` (existing constructor); `relation(...).to_polars()`; `DimensionsMetadata.model_validate` / `.model_dump(mode="json", exclude_defaults=True)`; `Aggregate.model_validate` / `.model_dump(mode="json")`.
- Produces: `Lattice.save(dir_path: str | Path) -> Path` and `Lattice.load(dir_path: str | Path) -> Lattice`; directory contract `lattice.parquet` + `manifest.yaml` with keys `dimensions`, `aggregates`, `partition_key`. mountainash-rules-service `LatticeRegistry` replaces its hand-rolled loader with `Lattice.load` once this ships.

- [ ] **Step 1: Write the failing tests**

Append to the lattice test module:

```python
import pytest

from mountainash_rules import (
    AccumulatorEngine,
    Aggregate,
    Dimension,
    DimensionsMetadata,
    Lattice,
    MatchStrategy,
    UNKNOWN,
)


@pytest.fixture
def built_lattice_and_engine():
    import polars as pl

    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type="str"),
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT, data_type="str"),
        ]
    )
    rules = pl.DataFrame(
        {
            "rule_name": ["au_base", "broker_bonus", "au_broker"],
            "region": ["AU", UNKNOWN, "AU"],
            "channel": [UNKNOWN, "BROKER", "BROKER"],
            "discount": [5.0, 2.5, 10.0],
        }
    )
    engine = AccumulatorEngine(
        dimension_metadata=metadata,
        aggregates=[Aggregate(column_name="discount", operation="sum")],
    )
    return engine.build(rules), engine


class TestLatticeSaveLoad:
    def test_round_trip_identity(self, built_lattice_and_engine, tmp_path):
        lattice, _ = built_lattice_and_engine
        out = lattice.save(tmp_path / "snap")
        loaded = Lattice.load(out)
        assert loaded.count == lattice.count
        assert loaded.is_composed is True  # __prime_product travels
        assert loaded.metadata == lattice.metadata
        assert loaded.aggregates == lattice.aggregates
        assert loaded.partition_key == lattice.partition_key

    def test_apply_equivalence(self, built_lattice_and_engine, tmp_path):
        lattice, engine = built_lattice_and_engine
        loaded = Lattice.load(lattice.save(tmp_path / "snap"))
        ctx = {"region": "AU", "channel": "BROKER"}
        original = engine.apply(lattice, ctx)
        reloaded = engine.apply(loaded, ctx)
        assert reloaded.count == original.count
        assert (
            reloaded.accumulated("discount").to_dicts()
            == original.accumulated("discount").to_dicts()
        )

    def test_save_creates_expected_files(self, built_lattice_and_engine, tmp_path):
        lattice, _ = built_lattice_and_engine
        out = lattice.save(tmp_path / "snap")
        assert (out / "lattice.parquet").exists()
        assert (out / "manifest.yaml").exists()

    def test_manifest_is_babel_superset(self, built_lattice_and_engine, tmp_path):
        import yaml

        lattice, _ = built_lattice_and_engine
        out = lattice.save(tmp_path / "snap")
        raw = yaml.safe_load((out / "manifest.yaml").read_text())
        assert set(raw) == {"dimensions", "aggregates", "partition_key"}
        assert raw["aggregates"] == [{"column_name": "discount", "operation": "sum"}]

    def test_load_missing_manifest_raises(self, tmp_path):
        (tmp_path / "empty").mkdir()
        with pytest.raises(FileNotFoundError):
            Lattice.load(tmp_path / "empty")
```

(Backend note: the fixture builds with polars directly — the same pattern the
existing accumulator tests use; a test-module polars import is outside the
purity gate, which scans `src/` only.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/nathanielramm/git/mountainash-io/mountainash-rules && hatch run test:test-target tests/accumulator/test_lattice.py -v`
Expected: the five new tests FAIL — `AttributeError: 'Lattice' object has no attribute 'save'`; existing tests in the module still PASS.

- [ ] **Step 3: Implement**

In `engines/accumulator/lattice.py` (add `pathlib`/`yaml` imports at the top; `Aggregate` and `DimensionsMetadata` are already imported for typing — verify, add if not):

```python
    def save(self, dir_path: "str | pathlib.Path") -> "pathlib.Path":
        """Persist this lattice as a snapshot directory (parquet + manifest).

        The manifest is a strict superset of babel's LatticeManifest YAML,
        so babel/service tooling can read it unchanged.
        """
        dir_path = pathlib.Path(dir_path)
        dir_path.mkdir(parents=True, exist_ok=True)
        relation(self._df).to_polars().write_parquet(dir_path / "lattice.parquet")
        manifest = {
            "dimensions": self._metadata.model_dump(
                mode="json", exclude_defaults=True
            ),
            "aggregates": [
                a.model_dump(mode="json") for a in self._aggregates
            ],
            "partition_key": self._partition_key,
        }
        (dir_path / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
        )
        return dir_path

    @classmethod
    def load(cls, dir_path: "str | pathlib.Path") -> "Lattice":
        """Rehydrate a snapshot written by save(). Preserves __agg_* and
        __prime_product verbatim (is_composed round-trips honestly)."""
        import polars as pl  # allow: lattice snapshot parquet read pending backend-agnostic file IO

        dir_path = pathlib.Path(dir_path)
        manifest_path = dir_path / "manifest.yaml"
        if not manifest_path.exists():
            raise FileNotFoundError(f"No manifest.yaml in {dir_path}")
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        return cls(
            dataframe=pl.read_parquet(dir_path / "lattice.parquet"),
            metadata=DimensionsMetadata.model_validate(raw["dimensions"]),
            aggregates=[
                Aggregate.model_validate(a) for a in raw.get("aggregates", [])
            ],
            partition_key=raw.get("partition_key"),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nathanielramm/git/mountainash-io/mountainash-rules && hatch run test:test-quick && hatch run ruff:check`
Expected: baseline + 5 passed; purity gate green (the allow-tag covers the one import); ruff clean. If `loaded.metadata == lattice.metadata` fails on pydantic equality semantics, compare `model_dump()` on both sides instead and note why.

- [ ] **Step 5: Commit**

```bash
cd /Users/nathanielramm/git/mountainash-io/mountainash-rules
git add src/mountainash_rules/engines/accumulator/lattice.py tests/accumulator/test_lattice.py
git commit -m "feat: Lattice.save/load — parquet + manifest snapshot round-trip

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Docs + push + downstream coordination

**Files:**
- Modify: `CLAUDE.md` (accumulator engine section)

- [ ] **Step 1: Document**

In CLAUDE.md's Accumulator engine section, append:

```markdown
- `Lattice.save(dir)` / `Lattice.load(dir)` — snapshot persistence
  (`lattice.parquet` + `manifest.yaml`, a superset of babel's
  LatticeManifest). `load` carries the package's third `# allow:` tag
  (parquet read). Build offline, `save`, serve `apply` from `load`.
```

Also update the Backend purity section's allow-tag count ("currently two" → "currently three: ... and the snapshot parquet read in `engines/accumulator/lattice.py`").

- [ ] **Step 2: Verify, push, refresh babel, flip cards**

Run: `hatch run test:test-quick && hatch run ruff:check` — green/clean.

```bash
cd /Users/nathanielramm/git/mountainash-io/mountainash-rules
git add CLAUDE.md
git commit -m "docs: Lattice snapshot save/load + third purity allow-tag

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push
cd /Users/nathanielramm/git/mountainash-io/mountainash-rules-babel && hatch env prune && hatch run test:test-quick
```

Expected: babel 79 passed. Then flip `mountainash-central/.../mountainash-rules/h.backlog/lattice-snapshot-save-load.md` to DONE with commit refs, and update the rules-service lattice-serving plan (Task 2's `_load_one` collapses to `Lattice.load(path)`; note it in the plan or apply during execution if the service plan hasn't run yet).
