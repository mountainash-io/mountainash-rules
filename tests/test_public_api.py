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
