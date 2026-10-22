"""The package root is the only public import surface, and its export set is exact."""

import importlib.util

import pytest

import mountainash_rules as mr

PUBLIC_NAMES = frozenset(
    {
        # filter engine and its results
        "ExpressionRulesEngine", "RuleResult", "BatchRuleResult", "ExplainResult",
        "SelectionInfo", "HitPolicyViolationError", "DimensionCompiler",
        # metadata, dimension and aggregate types, enums
        "Dimension", "DimensionsMetadata", "ContextField", "Aggregate", "AggregateOp",
        "MatchStrategy", "HitPolicy", "DataType", "BooleanCoercion",
        # conventional wildcard markers
        "UNKNOWN", "UNKNOWN_NUMERIC", "UNKNOWN_DATE", "UNKNOWN_DATETIME",
        "unknown_sentinel_for",
        # DecisionTable: prevalidation, construction, application, persistence
        "prevalidate", "ValidatedRules", "Finding", "DataContractError",
        "build_decision_table", "DecisionTable", "MatchOperation", "MatchResult",
        "DecisionTableError", "verify_saved_table", "VerificationReport",
        # context validation
        "check_context_schema", "validate_contexts",
        "__version__",
    }
)

RETIRED_NAMES = (
    "Lattice", "LatticeIndex", "AccumulatorEngine", "AccumulatorResult",
    "AmbiguousPartitionError", "ExactLimits",
    "NOT_SET", "NOT_SET_NUMERIC", "NOT_SET_DATE", "NOT_SET_DATETIME",
    "sentinels_for", "not_set_sentinel_for",
)


def test_dunder_all_is_exactly_the_supported_surface():
    assert set(mr.__all__) == PUBLIC_NAMES
    assert len(mr.__all__) == len(set(mr.__all__)), "duplicate names in __all__"


def test_every_dunder_all_name_resolves():
    broken = [n for n in mr.__all__ if not hasattr(mr, n)]
    assert not broken, f"__all__ names that do not resolve: {broken}"


def test_retired_names_are_not_exposed():
    leaked = [n for n in RETIRED_NAMES if hasattr(mr, n)]
    assert not leaked, f"Retired names still on the package root: {leaked}"


@pytest.mark.parametrize(
    "module",
    [
        "mountainash_rules._native",
        "mountainash_rules.engines.accumulator",
        "mountainash_rules.engines.accumulate",
        "mountainash_rules.core.contracts",
        "mountainash_rules.core.predicates",
        "mountainash_rules.core.reasoner",
        "mountainash_rules.core.normalization",
        "mountainash_rules.core.validation",
        "mountainash_rules.core.language",
        "mountainash_rules.core.codec",
        "mountainash_rules.core.scalar",
    ],
)
def test_retired_modules_do_not_exist(module):
    assert importlib.util.find_spec(module) is None
