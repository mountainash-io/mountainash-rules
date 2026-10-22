"""Authored-rules fixtures with the finite grids the equivalence oracle judges them over.

Each fixture is ``(data, metadata, grid, exact_grid)``.  ``built`` builds one per session and
backend, and ``built_oracle`` shares the oracle over it, so the test modules that only read a
fixture's table (equivalence, application, persistence, lifecycle) pay for it once.  This is a
support module, not a test module, so every importer shares the one cache.
"""

from __future__ import annotations

import functools
from decimal import Decimal

import polars as pl

from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table.build import build_decision_table

from tests.decision_table.construction_support import validated_rules
from tests.decision_table.oracles.finite_completion import FiniteCompletionOracle
from tests.decision_table.test_ordered_and_categorical_fields import (
    fees_rates_metadata,
    fees_rates_rules,
    term_metadata,
    term_rules,
)


M = MatchStrategy
REGION = ContextField(name="region", data_type=DataType.STR, wildcard="*")
TERM = ContextField(name="term", data_type=DataType.INT, wildcard=None, wildcard_declared=True)


def dimension(name, strategy, field, **kwargs):
    return Dimension(dimension_name=name, context_field=field, match_strategy=strategy, **kwargs)


def score(scope="s"):
    return Aggregate(scope=scope, output_name="score", contribution_column="score", data_type=DataType.INT)


def fees_rates_pair():
    """The fees/rates ``(data, metadata)`` for ``build_once``."""
    return fees_rates_rules(), fees_rates_metadata()


# ---------------------------------------------------------------------------
# Fixtures: (data, metadata, grid, exact grid)
# ---------------------------------------------------------------------------


def categorical():
    grid = {"region": ["EU", "US", "APAC"]}
    return fees_rates_rules(), fees_rates_metadata(), grid, grid


def categorical_negation():
    grid = {"region": ["EU", "US", "APAC"]}
    return fees_rates_rules(negation=True), fees_rates_metadata(negation=True), grid, grid


def ordered():
    rate = Aggregate(
        scope="rates", output_name="rate", contribution_column="rate",
        data_type=DataType.DECIMAL, precision=16, scale=2, rounding="half_to_even",
    )
    data = term_rules(T1={"range": (1, 5)}, T2={"exact": 3}, T3={"min": 10})
    data["rate"] = [Decimal("2.00"), Decimal("-0.25"), Decimal("3.00")]
    grid = {"term": [0, 1, 2, 3, 4, 5, 6, 9, 10, 11, 12]}
    return data, term_metadata(outputs=(rate,)), grid, grid


def ordered_negation():
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(TERM,),
        dimensions=(
            dimension("t_range", M.RANGE, "term", range_min_field="lo", range_max_field="hi"),
            dimension("t_ne", M.NOT_EQUAL, "term"),
        ),
        outputs=(score("s"),),
    )
    data = {
        "rule_id": ["T1", "N1"],
        "scope": ["s", "s"],
        "lo": [1, 1],
        "hi": [10, 10],
        "t_ne": pl.Series([None, 5], dtype=pl.Int64),
        "score": [1, 10],
    }
    grid = {"term": [0, 1, 3, 5, 7, 10, 11]}
    return data, metadata, grid, grid


def conjunction():
    """Two fields, overlapping rules, a wildcard rule: conjunction and overlapping entries."""
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(REGION, TERM),
        dimensions=(
            dimension("region", M.EXACT, "region"),
            dimension("term_range", M.RANGE, "term", range_min_field="lo", range_max_field="hi"),
            dimension("term_min", M.MIN, "term"),
        ),
        outputs=(score("s"),),
    )
    data = {
        "rule_id": ["A", "B", "C", "D"],
        "scope": ["s"] * 4,
        "region": ["EU", "*", "US", "EU"],
        "lo": pl.Series([1, None, None, None], dtype=pl.Int64),
        "hi": pl.Series([5, None, None, None], dtype=pl.Int64),
        "term_min": pl.Series([None, 3, None, 4], dtype=pl.Int64),
        "score": [1, 2, 4, 8],
    }
    grid = {"region": ["EU", "US", "APAC"], "term": [0, 1, 3, 4, 5, 6]}
    return data, metadata, grid, grid


CODE = ContextField(
    name="code", data_type=DataType.STR, wildcard="*", truthy={"yes": 1, "no": -1, "maybe": 0}
)


def patterns():
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(CODE,),
        dimensions=(
            dimension("code", M.EXACT, "code"),
            dimension("pre", M.PREFIX, "code"),
            dimension("rx", M.REGEX, "code"),
            dimension("anywhere", M.CONTAINS, "code"),
            dimension("upper", M.CONTEXT_REGEX, "code", regex_pattern="^[A-Z]"),
        ),
        outputs=(score("s"),),
    )
    data = {
        "rule_id": ["P1", "P2", "P3", "P4", "P5"],
        "scope": ["s"] * 5,
        "code": ["*", "*", "*", "US-1", "*"],
        "pre": ["EU", "*", "EU", "*", "*"],
        "rx": ["*", "-1$", "-22$", "*", "*"],
        "anywhere": ["*", "*", "*", "*", "U"],
        "score": [1, 2, 4, 8, 16],
    }
    grid = {"code": ["EU-1", "EU-22", "US-1", "xx", "UK-2", None]}
    return data, metadata, grid, {"code": ["EU-1", "EU-22", "US-1", "xx", "UK-2"]}


def truthy():
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(CODE,),
        dimensions=(dimension("agree", M.TRUTHY, "code"),),
        outputs=(score("s"),),
    )
    data = {
        "rule_id": ["Y", "N", "M", "ANY"],
        "scope": ["s"] * 4,
        "agree": ["yes", "no", "maybe", "*"],
        "score": [1, 2, 4, 8],
    }
    return data, metadata, {"code": ["yes", "no", "maybe", "other", None]}, {"code": ["yes", "no"]}


VIP = ContextField(name="vip", data_type=DataType.BOOL, wildcard=None, wildcard_declared=True)


def null_rules():
    """The worked null rules R1-R3 (explicit-dont-care-sentinels.md)."""
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(REGION, VIP),
        dimensions=(
            dimension("region", M.EXACT, "region"),
            dimension("region_presence", M.PRESENCE, "region"),
            dimension("vip", M.EXACT, "vip"),
        ),
        outputs=(score("s"),),
    )
    data = {
        "rule_id": ["R1", "R2", "R3"],
        "scope": ["s"] * 3,
        "region": ["EU", "*", "*"],
        "region_presence": ["*", "IS_NULL", "IS_NOT_NULL"],
        "vip": [None, None, True],
        "score": [1, 2, 4],
    }
    grid = {"region": ["EU", "US", None], "vip": [True, False]}
    return data, metadata, grid, grid


def outcome_rules():
    flag = ContextField(name="flag", data_type=DataType.INT, wildcard=None, wildcard_declared=True)
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(flag,),
        dimensions=(dimension("flag_outcome", M.OUTCOME, "flag"),),
        outputs=(score("s"),),
    )
    data = {"rule_id": ["neg", "zero", "pos"], "scope": ["s"] * 3, "flag_outcome": [-1, 0, 1], "score": [1, 2, 4]}
    return data, metadata, {"flag": [-1, 0, 1]}, {"flag": [-1, 1]}


FIXTURES = {
    "categorical": categorical,
    "categorical_negation": categorical_negation,
    "ordered": ordered,
    "ordered_negation": ordered_negation,
    "conjunction": conjunction,
    "patterns": patterns,
    "truthy": truthy,
    "null_rules": null_rules,
    "outcome": outcome_rules,
}


@functools.cache
def built(backend_name, name):
    """``(validated, table, grid, exact_grid)`` of a fixture, built once per session and backend.

    Tests that call this only read the table (it is frozen); tests that mutate the build, spy on
    its execution or test the build itself build their own.
    """
    data, metadata, grid, exact_grid = FIXTURES[name]()
    validated = validated_rules(backend_name, data, metadata)
    return validated, build_decision_table(validated), grid, exact_grid


@functools.cache
def built_oracle(backend_name, name):
    """The finite-completion oracle of ``built(backend_name, name)``, sharing what it has read."""
    validated, _, grid, exact_grid = built(backend_name, name)
    return FiniteCompletionOracle(validated, grid, exact_grid)

