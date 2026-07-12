# Package Layout Reorganisation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the flat 16-module package into `core/` + `engines/filter/` + `engines/accumulator/`, with deprecation shims, package-root-only imports for babel, an expanded backend-purity gate, and a mirrored test tree — zero behaviour change.

**Architecture:** Pure file moves (`git mv`) plus mechanical import rewrites, gated by the existing 731-test suite. Downstream-first ordering: babel switches to package-root imports *before* the move so the move is invisible to it. See `docs/superpowers/specs/2026-07-13-package-layout-reorganisation-design.md`.

**Tech Stack:** Python 3.12, hatch, pytest. Repos: `~/git/mountainash-io/mountainash-rules` and `~/git/mountainash-io/mountainash-rules-babel`, both working on `develop`.

## Global Constraints

- No behaviour, signature, or logic changes anywhere — moves and imports only.
- Use `git mv` for every move (history via `git log --follow`).
- Dependency direction: `engines/accumulator` → `engines/filter` → `core`; `core` never imports from `engines/`.
- Public API: every name currently in `__all__` keeps working from the package root; §3 of the spec adds the sentinel names/helpers.
- Suites at every task boundary: rules `hatch run test:test-quick` → 731 passed / 36 skipped / 31 xfailed (plus any tests added by this plan); babel `hatch run test:test-quick` → 79 passed.
- After ANY rules change, babel needs `hatch env prune` (non-editable install) before its suite reflects it.
- Commit per task with trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Push each repo's develop when its work in this plan completes (rules after Task 6, babel after Task 1 and again if touched later).

---

### Task 1: Public-API promotion + babel root-imports (independently shippable)

**Files:**
- Modify: `mountainash-rules/src/mountainash_rules/__init__.py`
- Test: `mountainash-rules/tests/test_public_api.py` (create)
- Modify (babel): every file listed by `grep -rln "from mountainash_rules\." src tests` — the known set is `src/mountainash_rules_babel/{__init__.py, manifest.py, exporters/{base,csv_,dmn}.py, importers/{base,csv_}.py, validators/{base,round_trip}.py, decomposers/base.py}` and `tests/{test_registry,test_csv_exporter,test_csv_importer,test_dmn_exporter,test_validators,test_protocols,test_lattice_schema_contract}.py` — but the grep output is authoritative, not this list.
- Modify (babel docs): `CLAUDE.md` and `docs/lattice-schema.md` cite `mountainash_rules.constants.sentinels_for` — reword to cite the package-root name (`mountainash_rules.sentinels_for`, available after Step 5).

**Interfaces:**
- Produces: `mountainash_rules` package root additionally exports `UNKNOWN, NOT_SET, UNKNOWN_NUMERIC, NOT_SET_NUMERIC, UNKNOWN_DATE, NOT_SET_DATE, UNKNOWN_DATETIME, NOT_SET_DATETIME, sentinels_for, unknown_sentinel_for, not_set_sentinel_for`. Babel contains zero `from mountainash_rules.<module>` imports.

- [ ] **Step 1: Write the failing test** (rules repo, new file `tests/test_public_api.py`)

```python
"""The package root is the only public import surface."""

import mountainash_rules as mr


PUBLIC_NAMES = (
    # engines & results
    "ExpressionRulesEngine", "AccumulatorEngine", "RuleResult",
    "AccumulatorResult", "BatchRuleResult",
    # metadata
    "Dimension", "DimensionsMetadata", "Aggregate",
    "MatchStrategy", "DimensionRole", "HitPolicy", "DataType",
    # lattice
    "Lattice", "LatticeIndex",
    # selection
    "SelectionInfo", "HitPolicyViolationError",
    # compiler
    "DimensionCompiler",
    # sentinels & helpers (promoted by this task)
    "UNKNOWN", "NOT_SET", "UNKNOWN_NUMERIC", "NOT_SET_NUMERIC",
    "UNKNOWN_DATE", "NOT_SET_DATE", "UNKNOWN_DATETIME", "NOT_SET_DATETIME",
    "sentinels_for", "unknown_sentinel_for", "not_set_sentinel_for",
    # metadata
    "__version__",
)


def test_all_public_names_importable_from_root():
    missing = [n for n in PUBLIC_NAMES if not hasattr(mr, n)]
    assert not missing, f"Missing from package root: {missing}"


def test_all_public_names_in_dunder_all():
    missing = [n for n in PUBLIC_NAMES if n not in mr.__all__]
    assert not missing, f"Missing from __all__: {missing}"


def test_every_dunder_all_name_resolves():
    broken = [n for n in mr.__all__ if not hasattr(mr, n)]
    assert not broken, f"__all__ names that do not resolve: {broken}"
```

- [ ] **Step 2: Run to verify failure**

Run: `hatch run test:test-target tests/test_public_api.py`
Expected: FAIL — the 11 sentinel names are missing.

- [ ] **Step 3: Promote the names** in `src/mountainash_rules/__init__.py`: extend the existing `from mountainash_rules.constants import ...` line with the 11 names, and append them to `__all__` (keep it sorted).

- [ ] **Step 4: Run** `hatch run test:test-target tests/test_public_api.py` → PASS; then full `hatch run test:test-quick` → baseline + 2.

- [ ] **Step 5: Commit (rules)**

```bash
git add src/mountainash_rules/__init__.py tests/test_public_api.py
git commit -m "feat: promote sentinel constants and helpers to the public API

Module paths are becoming private (package layout reorganisation);
downstream consumers import from the package root only.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push origin develop
```

- [ ] **Step 6: Switch babel to root imports.** In the babel repo, first `hatch env prune` (pick up the pushed rules change). Then for every match of `grep -rn "from mountainash_rules\." src tests`, rewrite to a single package-root import. Example (`src/mountainash_rules_babel/exporters/dmn.py`):

```python
# before
from mountainash_rules.constants import DataType, HitPolicy, MatchStrategy
from mountainash_rules.dimension import Dimension
from mountainash_rules.lattice import Lattice
# after
from mountainash_rules import DataType, Dimension, HitPolicy, Lattice, MatchStrategy
```

Apply the same pattern in every file; multi-line grouped imports collapse the same way. `mountainash_rules.aggregate.Aggregate`, `.accumulator_engine.AccumulatorEngine`, `.dimension.DimensionsMetadata`, `.constants.<anything>`, `.lattice.Lattice` are all available at the root after Step 5. TYPE_CHECKING-guarded imports in `__init__.py` rewrite identically.

- [ ] **Step 7: Verify zero deep imports remain**

Run: `grep -rn "from mountainash_rules\." src tests` → no output.
Run: `grep -rn "mountainash_rules\.[a-z_]*\." CLAUDE.md docs README.md | grep -v rules_babel` → no output (docs swept too).
Run: `hatch run test:test-quick` → 79 passed.

- [ ] **Step 8: Add the import rule to both CLAUDE.md files.** Rules CLAUDE.md, under "Code Style", and babel CLAUDE.md, under "Dependencies", add: *"`mountainash_rules` module paths are private — import public names from the package root only (`from mountainash_rules import Lattice`)."*

- [ ] **Step 9: Commit (babel) and push**

```bash
git add -A src tests CLAUDE.md
git commit -m "refactor: import mountainash-rules public API from package root only

Prepares for the rules package layout reorganisation; module paths
are private.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push origin develop
```

(Also commit the one-line rules CLAUDE.md addition in the rules repo — fold into Task 6's doc commit if preferred, or commit now; either is fine.)

---

### Task 2: Move modules into core/ + engines/ and rewrite internal imports

**Files:**
- Create: `src/mountainash_rules/{core,engines,engines/filter,engines/accumulator}/__init__.py` (empty, or one-line docstring)
- Move (git mv): see table below
- Modify: every moved module's internal imports + root `__init__.py`
- Test: existing suite (this task adds none — the 731 tests are the net)

**Interfaces:**
- Produces: the spec §2 layout. New canonical import paths: `mountainash_rules.core.<m>` and `mountainash_rules.engines.{filter,accumulator}.<m>`. Old paths are BROKEN at the end of this task and restored by Task 3's shims — Tasks 2 and 3 land as consecutive commits without pushing in between.

- [ ] **Step 1: Create the package dirs and git mv** (from `src/mountainash_rules/`):

| Old | New |
|---|---|
| `constants.py` | `core/constants.py` |
| `dimension.py` | `core/dimension.py` |
| `context.py` | `core/context.py` |
| `compiler.py` | `core/compiler.py` |
| `result.py` | `core/result.py` |
| `hit_policy.py` | `core/hit_policy.py` |
| `batch_result.py` | `core/batch_result.py` |
| `engine.py` | `engines/filter/engine.py` |
| `accumulator_engine.py` | `engines/accumulator/engine.py` |
| `accumulator_compiler.py` | `engines/accumulator/compiler.py` |
| `accumulator_result.py` | `engines/accumulator/result.py` |
| `lattice.py` | `engines/accumulator/lattice.py` |
| `aggregate.py` | `engines/accumulator/aggregate.py` |
| `primes.py` | `engines/accumulator/primes.py` |

```bash
cd src/mountainash_rules
mkdir -p core engines/filter engines/accumulator
for d in core engines engines/filter engines/accumulator; do : > "$d/__init__.py"; done
git mv constants.py dimension.py context.py compiler.py result.py hit_policy.py batch_result.py core/
git mv engine.py engines/filter/engine.py
git mv accumulator_engine.py engines/accumulator/engine.py
git mv accumulator_compiler.py engines/accumulator/compiler.py
git mv accumulator_result.py engines/accumulator/result.py
git mv lattice.py aggregate.py primes.py engines/accumulator/
git add core/__init__.py engines/__init__.py engines/filter/__init__.py engines/accumulator/__init__.py
```

- [ ] **Step 2: Rewrite internal imports** in the moved files and root `__init__.py` using this exact mapping (old module → new dotted path):

```
mountainash_rules.constants            → mountainash_rules.core.constants
mountainash_rules.dimension            → mountainash_rules.core.dimension
mountainash_rules.context              → mountainash_rules.core.context
mountainash_rules.compiler             → mountainash_rules.core.compiler
mountainash_rules.result               → mountainash_rules.core.result
mountainash_rules.hit_policy           → mountainash_rules.core.hit_policy
mountainash_rules.batch_result         → mountainash_rules.core.batch_result
mountainash_rules.engine               → mountainash_rules.engines.filter.engine
mountainash_rules.accumulator_engine   → mountainash_rules.engines.accumulator.engine
mountainash_rules.accumulator_compiler → mountainash_rules.engines.accumulator.compiler
mountainash_rules.accumulator_result   → mountainash_rules.engines.accumulator.result
mountainash_rules.lattice              → mountainash_rules.engines.accumulator.lattice
mountainash_rules.aggregate            → mountainash_rules.engines.accumulator.aggregate
mountainash_rules.primes               → mountainash_rules.engines.accumulator.primes
```

Mechanically:

```bash
cd src/mountainash_rules
python3 - <<'PY'
import pathlib, re
MAP = {
 "constants":"core.constants","dimension":"core.dimension","context":"core.context",
 "compiler":"core.compiler","result":"core.result","hit_policy":"core.hit_policy",
 "batch_result":"core.batch_result","engine":"engines.filter.engine",
 "accumulator_engine":"engines.accumulator.engine",
 "accumulator_compiler":"engines.accumulator.compiler",
 "accumulator_result":"engines.accumulator.result",
 "lattice":"engines.accumulator.lattice","aggregate":"engines.accumulator.aggregate",
 "primes":"engines.accumulator.primes",
}
for f in pathlib.Path(".").rglob("*.py"):
    t = f.read_text()
    for old, new in MAP.items():
        t = re.sub(rf"\bmountainash_rules\.{old}\b", f"mountainash_rules.{new}", t)
    f.write_text(t)
PY
```

Longest-name-first is handled by `\b` word boundaries (`accumulator_engine` won't be half-matched as `engine` because the regex anchors the whole segment). Verify with `grep -rn "from mountainash_rules\.\(constants\|dimension\|context\|compiler\|result\|hit_policy\|batch_result\|engine\|accumulator\|lattice\|aggregate\|primes\) " core engines __init__.py` → only new-path matches.

- [ ] **Step 3: Update the rules test suite's imports the same way** — run the same rewrite script over `tests/` (from the repo root, adjust the pathlib glob to `pathlib.Path("tests").rglob("*.py")`). Tests may keep deep imports (they're the package's own tests), but they must point at the new canonical paths, not the shims — otherwise every test run drowns in DeprecationWarnings.

- [ ] **Step 4: Run the suite**

Run: `hatch run test:test-quick`
Expected: baseline green (731+2 passed / 36 skipped / 31 xfailed). Debug import errors before proceeding — typical culprits are missed TYPE_CHECKING imports and string type annotations.

- [ ] **Step 5: Commit** (do NOT push yet — old paths are broken until Task 3)

```bash
git add -A src tests
git commit -m "refactor: reorganise package into core/ and engines/{filter,accumulator}/

Pure git-mv moves plus mechanical import rewrites; no behaviour change.
accumulator_ filename prefixes dropped — the module path carries it.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Deprecation shims for the 14 old module paths

**Files:**
- Create: `src/mountainash_rules/{constants,dimension,context,compiler,result,hit_policy,batch_result,engine,accumulator_engine,accumulator_compiler,accumulator_result,lattice,aggregate,primes}.py` (shims)
- Test: `tests/test_deprecation_shims.py` (create)

**Interfaces:**
- Produces: importing any old path emits exactly one `DeprecationWarning` and yields objects identical (`is`) to the new-path objects. Removal target: the production release after next (note in each shim docstring).

- [ ] **Step 1: Write the failing test**

```python
"""Old top-level module paths must warn and re-export identical objects."""

import importlib
import warnings

import pytest

# old module name → (new module path, representative public name)
SHIMS = {
    "constants": ("mountainash_rules.core.constants", "MatchStrategy"),
    "dimension": ("mountainash_rules.core.dimension", "DimensionsMetadata"),
    "context": ("mountainash_rules.core.context", "extract_context_values"),
    "compiler": ("mountainash_rules.core.compiler", "DimensionCompiler"),
    "result": ("mountainash_rules.core.result", "RuleResult"),
    "hit_policy": ("mountainash_rules.core.hit_policy", "SelectionInfo"),
    "batch_result": ("mountainash_rules.core.batch_result", "BatchRuleResult"),
    "engine": ("mountainash_rules.engines.filter.engine", "ExpressionRulesEngine"),
    "accumulator_engine": ("mountainash_rules.engines.accumulator.engine", "AccumulatorEngine"),
    "accumulator_compiler": ("mountainash_rules.engines.accumulator.compiler", "AccumulatorCompiler"),
    "accumulator_result": ("mountainash_rules.engines.accumulator.result", "AccumulatorResult"),
    "lattice": ("mountainash_rules.engines.accumulator.lattice", "Lattice"),
    "aggregate": ("mountainash_rules.engines.accumulator.aggregate", "Aggregate"),
    "primes": ("mountainash_rules.engines.accumulator.primes", "LatticeWidthExceededError"),
}


@pytest.mark.parametrize("old_name", sorted(SHIMS))
def test_shim_warns_and_reexports_identical_objects(old_name):
    new_path, attr = SHIMS[old_name]
    new_mod = importlib.import_module(new_path)
    # import OUTSIDE the catch window (first import would add a second
    # warning inside it); the reload is then the only execution measured
    old_mod = importlib.import_module(f"mountainash_rules.{old_name}")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.reload(old_mod)
    dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    # exactly one per (re)import — the reload is the only execution in the window
    assert len(dep) == 1, f"expected exactly 1 DeprecationWarning, got {len(dep)}"
    assert new_path in str(dep[0].message)
    # identity for EVERY public name of the new module, not one representative
    public = [n for n in vars(new_mod) if not n.startswith("_")]
    assert attr in public
    mismatched = [
        n for n in public
        if getattr(old_mod, n, None) is not getattr(new_mod, n)
    ]
    assert not mismatched, f"shim re-exports differ for: {mismatched}"
```

- [ ] **Step 2: Run to verify failure** — `hatch run test:test-target tests/test_deprecation_shims.py` → FAIL (ModuleNotFoundError).

- [ ] **Step 3: Create the 14 shims.** Template (`src/mountainash_rules/engine.py`; every shim identical modulo names):

```python
"""Deprecated shim — moved to mountainash_rules.engines.filter.engine.

Emits DeprecationWarning; remove in the production release after next
(shipped 2026-07; remove no earlier than the 2026-08 production release).
"""

import warnings

from mountainash_rules.engines.filter.engine import *  # noqa: F401,F403
from mountainash_rules.engines.filter.engine import ExpressionRulesEngine  # noqa: F401

warnings.warn(
    "mountainash_rules.engine is deprecated; import from "
    "mountainash_rules.engines.filter.engine (or the package root)",
    DeprecationWarning,
    stacklevel=2,
)
```

`import *` without a defined `__all__` re-exports every non-underscore name, so the star import alone usually satisfies the full-namespace identity test; the explicit second line exists for modules that DO define `__all__` (where `*` honours it and may omit names) and as greppable documentation of the primary export. The shim test in Step 1 checks identity for every public name — let it, not a hand-list, tell you if a shim is incomplete.

- [ ] **Step 4: Run** shim test → PASS; full suite → green with **zero DeprecationWarnings in the run output** (proves Task 2 Step 3 caught every internal/test import; if warnings appear, fix the importer, not the filter).

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/*.py tests/test_deprecation_shims.py
git commit -m "feat: deprecation shims for pre-reorganisation module paths

One CalVer cycle; removal note in each shim docstring.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Whole-package backend-purity gate

**Files:**
- Modify: `tests/test_backend_purity.py`
- Modify: `src/mountainash_rules/engines/accumulator/engine.py` (allow-tag one import)

**Interfaces:**
- Produces: every non-shim module under `src/mountainash_rules/` is scanned; prohibited imports fail unless `# allow: <reason>` tagged.

- [ ] **Step 1: Write the failing test change.** Replace the `PURE_FILES` parametrisation with a whole-package walk:

```python
SRC_ROOT = Path(__file__).parent.parent / "src" / "mountainash_rules"
SHIM_FILES = {  # deprecation shims, removed next cycle — exempt
    "constants.py", "dimension.py", "context.py", "compiler.py",
    "result.py", "hit_policy.py", "batch_result.py", "engine.py",
    "accumulator_engine.py", "accumulator_compiler.py",
    "accumulator_result.py", "lattice.py", "aggregate.py", "primes.py",
}

def _pure_files():
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(SRC_ROOT)
        if rel.name.startswith("__"):
            continue
        if len(rel.parts) == 1 and rel.name in SHIM_FILES:
            continue
        yield str(rel)

@pytest.mark.parametrize("filename", list(_pure_files()))
def test_no_direct_backend_imports(filename: str):
    ...  # body unchanged — it already reads SRC_ROOT / filename
```

(Keep the existing line-scan body and `ALLOW_PATTERN` untouched.)

- [ ] **Step 2: Run to verify failure**

Run: `hatch run test:test-target tests/test_backend_purity.py`
Expected: FAIL on `engines/accumulator/engine.py` — the untagged `import polars as pl` (empty-build schema seed, audited 2026-07-13).

- [ ] **Step 3: Tag the known impurity** in `engines/accumulator/engine.py`:

```python
import polars as pl  # allow: empty-build schema seed pending backend-agnostic empty-frame support
```

If Step 2 surfaced any OTHER untagged native import, stop and inspect it — do not blanket-tag; each tag needs a real pending-upstream reason.

- [ ] **Step 4: Run** purity test → PASS (all modules parametrised); full suite green.

- [ ] **Step 5: Commit**

```bash
git add tests/test_backend_purity.py src/mountainash_rules/engines/accumulator/engine.py
git commit -m "test: extend backend-purity gate to every non-shim module

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Mirror tests/ into core/filter/accumulator/benchmarks

**Files:**
- Move (git mv): table below; create `tests/{core,filter,accumulator,benchmarks}/__init__.py`
- Modify: any intra-test imports of `benchmark_data` (grep first)

**Interfaces:**
- Produces: `tests/` mirrors `src/`; conftest.py, test_public_api.py, test_deprecation_shims.py, test_backend_purity.py stay at the root. Xfail registry is path-independent (matches `ClassName::test_name` — verified in spec §6); node-id changes are cosmetic.

- [ ] **Step 1: git mv per this table** (drop `accumulator_` prefixes where the directory disambiguates):

| Old (tests/) | New (tests/) |
|---|---|
| `test_dimension.py` | `core/test_dimension.py` |
| `test_dimension_serialization.py` | `core/test_dimension_serialization.py` |
| `test_context.py` | `core/test_context.py` |
| `test_compiler.py` | `core/test_compiler.py` |
| `test_result.py` | `core/test_result.py` |
| `test_hit_policy.py` | `core/test_hit_policy.py` |
| `test_engine.py` | `filter/test_engine.py` |
| `test_batch_evaluation.py` | `filter/test_batch_evaluation.py` |
| `test_integration.py` | `filter/test_integration.py` |
| `test_upstream_regressions.py` | `filter/test_upstream_regressions.py` |
| `test_accumulator_engine.py` | `accumulator/test_engine.py` |
| `test_accumulator_compiler.py` | `accumulator/test_compiler.py` |
| `test_accumulator_result.py` | `accumulator/test_result.py` |
| `test_accumulator_apply.py` | `accumulator/test_apply.py` |
| `test_accumulator_backends.py` | `accumulator/test_backends.py` |
| `test_accumulator_correctness.py` | `accumulator/test_correctness.py` |
| `test_accumulator_edge_cases.py` | `accumulator/test_edge_cases.py` |
| `test_lattice.py` | `accumulator/test_lattice.py` |
| `test_lattice_properties.py` | `accumulator/test_lattice_properties.py` |
| `test_primes.py` | `accumulator/test_primes.py` |
| `test_benchmarks.py` | `benchmarks/test_benchmarks.py` |
| `test_accumulator_benchmarks.py` | `benchmarks/test_accumulator_benchmarks.py` |
| `benchmark_data.py` | `benchmarks/benchmark_data.py` |
| `test_benchmark_data.py` | `benchmarks/test_benchmark_data.py` |

Then `: > tests/core/__init__.py` etc. for the four new dirs (the suite already has `tests/__init__.py`, so subpackages need their own).

- [ ] **Step 2: Fix `benchmark_data` importers.** `grep -rn "benchmark_data" tests src` — imports like `from tests.benchmark_data import ...` or `from benchmark_data import ...` become `from tests.benchmarks.benchmark_data import ...` (match whichever form the grep shows). Also check `conftest.py` for fixture imports from moved files.

- [ ] **Step 3: Run the full suite** — `hatch run test:test-quick` → identical counts to Task 4's run (same tests, new node ids). Spot-check that the 31 xfails still register: `hatch run test:test-target "tests/filter/test_engine.py" | tail -3` should show xfails on the ibis-polars param, not failures.

- [ ] **Step 4: Widen the CI path filters** (pre-existing gap this task would trip: all three workflows trigger only on `src/mountainash_rules/**`, so a tests-only change runs no CI). In each of `.github/workflows/python-run-pytest.yml`, `python-run-ruff.yml`, `python-run-radon.yml`, extend the `paths:` list:

```yaml
    paths:
      - "src/mountainash_rules/**"
      - "tests/**"
      - "pyproject.toml"
      - "hatch.toml"
```

- [ ] **Step 5: Commit**

```bash
git add -A tests .github/workflows
git commit -m "test: mirror the test tree onto the package layout; CI runs on test changes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Docs, downstream verification, push, card flip

**Files:**
- Modify: `mountainash-rules/CLAUDE.md` (structure tree + import rule if not already committed in Task 1), `mountainash-rules/README.md` (structure references, if any)
- Verify: babel suite; Modify nothing in babel (Task 1 made the move invisible)
- Modify: `mountainash-central/01.principles/mountainash-rules/h.backlog/package-layout-reorganisation.md` + `README.md` index

**Interfaces:**
- Produces: shipped reorganisation on rules develop; babel verified green against it; backlog card DONE.

- [ ] **Step 1: Update rules CLAUDE.md.** Replace the "Package Structure" tree with the spec §2 tree (including shim note: *"top-level `<old>.py` files are deprecation shims, removal note inside; do not add code to them"*). Confirm the private-module-paths rule from Task 1 Step 8 is present. Then confirm babel's docs are already clean from Task 1 Step 7's sweep (`grep -rn "mountainash_rules\.[a-z_]*\." ../mountainash-rules-babel/CLAUDE.md ../mountainash-rules-babel/docs ../mountainash-rules-babel/README.md | grep -v rules_babel` → no output); if anything slipped through, fix it in the babel repo now.

- [ ] **Step 2: Full rules verification**

```bash
hatch run test:test-quick     # green
hatch run ruff:check          # clean (shims may need noqa as shown in the template)
hatch build                   # wheel builds
```

Then smoke-test the built wheel in a scratch venv — proves packaging actually ships the nested subpackages and shims (source-tree tests can't):

```bash
python3 -m venv /tmp/wheel-smoke && /tmp/wheel-smoke/bin/pip -q install dist/*.whl
/tmp/wheel-smoke/bin/python - <<'PY'
import warnings
import mountainash_rules as mr
from mountainash_rules.engines.filter.engine import ExpressionRulesEngine
assert mr.ExpressionRulesEngine is ExpressionRulesEngine
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    import mountainash_rules.engine  # shim
assert any(issubclass(x.category, DeprecationWarning) for x in w)
print("wheel smoke OK")
PY
rm -rf /tmp/wheel-smoke
```

(Dependency imports inside the wheel need the mountainash siblings installed; if the scratch venv can't resolve them, run the smoke test inside the hatch env instead: `hatch run python - <<'PY' ...` after `pip install --force-reinstall --no-deps dist/*.whl` into it, and restore with `hatch env prune` afterwards.)

- [ ] **Step 3: Commit and push rules**

```bash
git add CLAUDE.md README.md
git commit -m "docs: package structure tree for the core/ + engines/ layout

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push origin develop
```

- [ ] **Step 4: Verify babel against the moved package**

```bash
cd ../mountainash-rules-babel
hatch env prune
hatch run test:test-quick    # 79 passed, zero DeprecationWarnings from mountainash_rules
```

Zero warnings is the acceptance signal that Task 1 caught every deep import. If any appear, fix that babel import (root-import form), commit, push babel.

- [ ] **Step 5: Flip the central backlog card** — `package-layout-reorganisation.md` status line → `> **Status:** DONE — implemented <date> on mountainash-rules develop (<first>..<last> commit range); babel unchanged post Task 1` and prefix its README index row with `✅ **DONE <date>**`. Commit and push mountainash-central.

- [ ] **Step 6: Schedule the shim removal.** Add a follow-up card `mountainash-central/01.principles/mountainash-rules/h.backlog/remove-layout-deprecation-shims.md` (P3, one paragraph: delete the 14 root shims + `SHIM_FILES` exemption in test_backend_purity.py + tests/test_deprecation_shims.py; not before the 2026-08 production release), add an index row, commit, push.
