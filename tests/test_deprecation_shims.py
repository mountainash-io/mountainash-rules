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
