"""Tests for DimensionCompiler."""

import ibis
import polars as pl
import pytest

from mountainash.core.types import BackendCapabilityError
from mountainash.relations import relation

from mountainash_rules.core.compiler import DimensionCompiler
from mountainash_rules.core.constants import CTX_PREFIX, NOT_SET, UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_rules.core.dimension import Dimension
from tests.conftest import (
    ALL_BACKENDS,
    build_backend_df,
)


@pytest.fixture
def compiler():
    return DimensionCompiler()


class TestExactCompilation:
    def test_exact_match_produces_true(self, compiler):
        dim = Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": ["AU", "US", "UK"],
            f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values == [1, -1, -1]

    def test_exact_unknown_rule_value_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": ["AU", UNKNOWN, "UK"],
            f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values[0] == 1   # hard match
        assert values[1] == 0   # unknown (wildcard)
        assert values[2] == -1  # non-match

    def test_exact_unknown_context_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": ["AU", "US"],
            f"{CTX_PREFIX}region": [UNKNOWN, UNKNOWN],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values == [0, 0]  # all unknown when context is unknown

    def test_exact_numeric(self, compiler):
        dim = Dimension(dimension_name="tier", match_strategy=MatchStrategy.EXACT, data_type=int)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "tier": [1, 2, UNKNOWN_NUMERIC],
            f"{CTX_PREFIX}tier": [1, 1, 1],
        })
        result = df.with_columns(expr.name.alias("__t_tier").compile(df, booleanizer=None))
        values = result["__t_tier"].to_list()
        assert values[0] == 1   # match
        assert values[1] == -1  # non-match
        assert values[2] == 0   # unknown


class TestRangeCompilation:
    def test_range_within_bounds_produces_true(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.RANGE,
            data_type=float,
            range_min_field="amount_min",
            range_max_field="amount_max",
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount_min": [0.0, 100.0, 200.0],
            "amount_max": [99.0, 199.0, 299.0],
            f"{CTX_PREFIX}amount": [50.0, 50.0, 50.0],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [1, -1, -1]

    def test_range_boundary_inclusive(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field="amount_min",
            range_max_field="amount_max",
            range_min_inclusive=True,
            range_max_inclusive=True,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount_min": [10, 10],
            "amount_max": [20, 20],
            f"{CTX_PREFIX}amount": [10, 20],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [1, 1]  # both boundaries inclusive

    def test_range_boundary_exclusive(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field="amount_min",
            range_max_field="amount_max",
            range_min_inclusive=False,
            range_max_inclusive=False,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount_min": [10, 10],
            "amount_max": [20, 20],
            f"{CTX_PREFIX}amount": [10, 20],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [-1, -1]  # both boundaries exclusive

    def test_range_unknown_min_produces_unknown(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field="amount_min",
            range_max_field="amount_max",
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount_min": [0, UNKNOWN_NUMERIC],
            "amount_max": [100, 100],
            f"{CTX_PREFIX}amount": [50, 50],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values[0] == 1  # known range, match
        assert values[1] == 0  # unknown min → unknown result


class TestContextRegexCompilation:
    """CONTEXT_REGEX uses a literal pattern from Dimension metadata (not a rule column).

    The ternary outcome is purely context-driven: every rule in the engine
    shares the same +1 / -1 outcome for a CONTEXT_REGEX dimension.
    """

    def test_regex_context_matches_pattern(self, compiler):
        dim = Dimension(
            dimension_name="code",
            match_strategy=MatchStrategy.CONTEXT_REGEX,
            data_type=str,
            regex_pattern="^PRE.*",
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            f"{CTX_PREFIX}code": ["PRE-001", "PRE-999", "POST-001"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        assert result["__t_code"].to_list() == [1, 1, -1]

    def test_regex_search_semantics(self, compiler):
        """regex_contains uses search semantics (match anywhere, not anchored)."""
        dim = Dimension(
            dimension_name="code",
            match_strategy=MatchStrategy.CONTEXT_REGEX,
            data_type=str,
            regex_pattern="123",
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            f"{CTX_PREFIX}code": ["abc-123-def", "xyz"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        assert result["__t_code"].to_list() == [1, -1]

    def test_regex_no_unknown_state(self, compiler):
        """CONTEXT_REGEX has no unknown/0 state — pattern is fixed at metadata time."""
        dim = Dimension(
            dimension_name="code",
            match_strategy=MatchStrategy.CONTEXT_REGEX,
            data_type=str,
            regex_pattern="^AU.*",
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            f"{CTX_PREFIX}code": ["AU-1", "NZ-1"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        # only 1 and -1; never 0
        assert set(result["__t_code"].to_list()) <= {1, -1}


class TestNotEqualCompilation:
    def test_not_equal_mismatch_produces_true(self, compiler):
        dim = Dimension(dimension_name="region", match_strategy=MatchStrategy.NOT_EQUAL, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": ["AU", "US", "UK"],
            f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values == [-1, 1, 1]

    def test_not_equal_unknown_rule_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="region", match_strategy=MatchStrategy.NOT_EQUAL, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": [UNKNOWN, "US"],
            f"{CTX_PREFIX}region": ["AU", "AU"],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values[0] == 0
        assert values[1] == 1


class TestGreaterThanCompilation:
    def test_greater_than_true(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.GREATER_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "amount": [100, 500, 1000],
            f"{CTX_PREFIX}amount": [1500, 1500, 1500],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [1, 1, 1]

    def test_greater_than_false(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.GREATER_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "amount": [100, 500, 1000],
            f"{CTX_PREFIX}amount": [50, 50, 50],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [-1, -1, -1]

    def test_greater_than_equal_is_false(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.GREATER_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "amount": [100],
            f"{CTX_PREFIX}amount": [100],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [-1]

    def test_greater_than_unknown_rule(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.GREATER_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "amount": [UNKNOWN_NUMERIC],
            f"{CTX_PREFIX}amount": [100],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [0]


class TestLessThanCompilation:
    def test_less_than_true(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.LESS_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "amount": [100, 500, 1000],
            f"{CTX_PREFIX}amount": [50, 50, 50],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [1, 1, 1]

    def test_less_than_false(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.LESS_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "amount": [100, 500],
            f"{CTX_PREFIX}amount": [1500, 1500],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [-1, -1]

    def test_less_than_equal_is_false(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.LESS_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "amount": [100],
            f"{CTX_PREFIX}amount": [100],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [-1]

    def test_less_than_unknown_rule(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.LESS_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "amount": [UNKNOWN_NUMERIC],
            f"{CTX_PREFIX}amount": [100],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [0]


class TestPrefixCompilation:
    def test_prefix_match(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.PREFIX, data_type=str)
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "code": ["PRE-", "POST-", "MID-"],
            f"{CTX_PREFIX}code": ["PRE-001", "PRE-001", "PRE-001"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        values = result["__t_code"].to_list()
        assert values == [1, -1, -1]

    def test_prefix_no_match(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.PREFIX, data_type=str)
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "code": ["PRE-"],
            f"{CTX_PREFIX}code": ["XYZ-001"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        assert result["__t_code"].to_list() == [-1]

    def test_prefix_unknown_rule_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.PREFIX, data_type=str)
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "code": ["PRE-", UNKNOWN],
            f"{CTX_PREFIX}code": ["PRE-001", "PRE-001"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        values = result["__t_code"].to_list()
        assert values[0] == 1
        assert values[1] == 0

    def test_prefix_per_row_different_patterns(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.PREFIX, data_type=str)
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "code": ["PRE-", "POST-", "MID-"],
            f"{CTX_PREFIX}code": ["PRE-001", "POST-002", "MID-003"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        assert result["__t_code"].to_list() == [1, 1, 1]


class TestSuffixCompilation:
    def test_suffix_match(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.SUFFIX, data_type=str)
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "code": ["-AUD", "-USD", "-EUR"],
            f"{CTX_PREFIX}code": ["TXN-AUD", "TXN-AUD", "TXN-AUD"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        assert result["__t_code"].to_list() == [1, -1, -1]

    def test_suffix_no_match(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.SUFFIX, data_type=str)
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "code": ["-AUD"],
            f"{CTX_PREFIX}code": ["TXN-USD"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        assert result["__t_code"].to_list() == [-1]

    def test_suffix_unknown_rule_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.SUFFIX, data_type=str)
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "code": ["-AUD", UNKNOWN],
            f"{CTX_PREFIX}code": ["TXN-AUD", "TXN-AUD"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        values = result["__t_code"].to_list()
        assert values[0] == 1
        assert values[1] == 0

    def test_suffix_per_row_different_patterns(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.SUFFIX, data_type=str)
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "code": ["-AUD", "-USD", "-EUR"],
            f"{CTX_PREFIX}code": ["TXN-AUD", "TXN-USD", "TXN-EUR"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        assert result["__t_code"].to_list() == [1, 1, 1]


class TestContainsCompilation:
    def test_contains_match(self, compiler):
        dim = Dimension(dimension_name="tier", match_strategy=MatchStrategy.CONTAINS, data_type=str)
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "tier": ["gold", "silver", "bronze"],
            f"{CTX_PREFIX}tier": ["gold_tier", "gold_tier", "gold_tier"],
        })
        result = df.with_columns(expr.name.alias("__t_tier").compile(df, booleanizer=None))
        assert result["__t_tier"].to_list() == [1, -1, -1]

    def test_contains_no_match(self, compiler):
        dim = Dimension(dimension_name="tier", match_strategy=MatchStrategy.CONTAINS, data_type=str)
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "tier": ["gold"],
            f"{CTX_PREFIX}tier": ["platinum_tier"],
        })
        result = df.with_columns(expr.name.alias("__t_tier").compile(df, booleanizer=None))
        assert result["__t_tier"].to_list() == [-1]

    def test_contains_unknown_rule_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="tier", match_strategy=MatchStrategy.CONTAINS, data_type=str)
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "tier": ["gold", UNKNOWN],
            f"{CTX_PREFIX}tier": ["gold_tier", "gold_tier"],
        })
        result = df.with_columns(expr.name.alias("__t_tier").compile(df, booleanizer=None))
        values = result["__t_tier"].to_list()
        assert values[0] == 1
        assert values[1] == 0

    def test_contains_per_row_different_patterns(self, compiler):
        dim = Dimension(dimension_name="tier", match_strategy=MatchStrategy.CONTAINS, data_type=str)
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "tier": ["gold", "silver", "bronze"],
            f"{CTX_PREFIX}tier": ["gold_tier", "silver_tier", "bronze_tier"],
        })
        result = df.with_columns(expr.name.alias("__t_tier").compile(df, booleanizer=None))
        assert result["__t_tier"].to_list() == [1, 1, 1]


class TestSetMembershipCompilation:
    def test_set_membership_match(self, compiler):
        dim = Dimension(
            dimension_name="region",
            match_strategy=MatchStrategy.SET_MEMBERSHIP,
            data_type=str,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": pl.Series(
                "region",
                [["AU", "NZ", "UK"], ["US", "CA"], ["DE", "FR"]],
                dtype=pl.List(pl.Utf8),
            ),
            f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        # AU in [AU,NZ,UK] → 1; AU in [US,CA] → -1; AU in [DE,FR] → -1
        assert values == [1, -1, -1]

    def test_set_membership_unknown_context(self, compiler):
        dim = Dimension(
            dimension_name="region",
            match_strategy=MatchStrategy.SET_MEMBERSHIP,
            data_type=str,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": pl.Series(
                "region",
                [["AU", "NZ"]],
                dtype=pl.List(pl.Utf8),
            ),
            f"{CTX_PREFIX}region": [UNKNOWN],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values == [0]


class TestSetExclusionCompilation:
    def test_set_exclusion_match(self, compiler):
        dim = Dimension(
            dimension_name="region",
            match_strategy=MatchStrategy.SET_EXCLUSION,
            data_type=str,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": pl.Series(
                "region",
                [["AU", "NZ", "UK"], ["US", "CA"], ["DE", "FR"]],
                dtype=pl.List(pl.Utf8),
            ),
            f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        # AU not in [AU,NZ,UK] → -1; AU not in [US,CA] → 1; AU not in [DE,FR] → 1
        assert values == [-1, 1, 1]

    def test_set_exclusion_unknown_context(self, compiler):
        dim = Dimension(
            dimension_name="region",
            match_strategy=MatchStrategy.SET_EXCLUSION,
            data_type=str,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": pl.Series(
                "region",
                [["AU", "NZ"]],
                dtype=pl.List(pl.Utf8),
            ),
            f"{CTX_PREFIX}region": [UNKNOWN],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values == [0]


class TestBackendAgnosticism:
    """Evaluate ternary results, or report a known backend capability boundary."""

    _SAMPLE_DATA = {
        "row_id": [0, 1],
        "str_col": ["A", "B"],
        "num_col": [10, 20],
        "min_col": [0, 0],
        "max_col": [100, 100],
        "list_col": [["A", "X"], ["B", "Y"]],
        f"{CTX_PREFIX}str_col": ["A", "A"],
        f"{CTX_PREFIX}num_col": [15, 15],
        f"{CTX_PREFIX}list_col": ["A", "A"],
    }

    _NON_LIST_DATA = {k: v for k, v in _SAMPLE_DATA.items() if k != "list_col"}

    @pytest.mark.parametrize("backend_name", ALL_BACKENDS)
    @pytest.mark.parametrize(
        "strategy,field,data_type,extras,expected",
        [
            (MatchStrategy.EXACT, "str_col", str, {}, [1, -1]),
            (MatchStrategy.NOT_EQUAL, "str_col", str, {}, [-1, 1]),
            (
                MatchStrategy.RANGE,
                "num_col",
                int,
                {"range_min_field": "min_col", "range_max_field": "max_col"},
                [1, 1],
            ),
            (MatchStrategy.GREATER_THAN, "num_col", int, {}, [1, -1]),
            (MatchStrategy.LESS_THAN, "num_col", int, {}, [-1, 1]),
            (MatchStrategy.PREFIX, "str_col", str, {}, [1, -1]),
            (MatchStrategy.SUFFIX, "str_col", str, {}, [1, -1]),
            (MatchStrategy.CONTAINS, "str_col", str, {}, [1, -1]),
            (
                MatchStrategy.CONTEXT_REGEX,
                "str_col",
                str,
                {"regex_pattern": "A"},
                [1, 1],
            ),
        ],
    )
    def test_non_set_strategy_evaluates_on_backend(
        self, compiler, backend_name, strategy, field, data_type, extras, expected
    ):
        dim = Dimension(
            dimension_name=field,
            match_strategy=strategy,
            data_type=data_type,
            **extras,
        )
        expr = compiler.compile_dimension(dim)
        df = build_backend_df(backend_name, self._NON_LIST_DATA)
        if backend_name in {"pandas", "narwhals-pandas"} and strategy in {
            MatchStrategy.PREFIX,
            MatchStrategy.SUFFIX,
            MatchStrategy.CONTAINS,
        }:
            # Column-valued string predicates are not supported on pandas.
            with pytest.raises(BackendCapabilityError) as error:
                relation(df).with_columns(expr.alias("__t")).to_dict()
            assert error.value.backend == "narwhals"
            return
        if backend_name == "ibis-polars" and strategy in {
            MatchStrategy.PREFIX,
            MatchStrategy.SUFFIX,
            MatchStrategy.CONTAINS,
        }:
            # Ibis accepts the expression but its Polars translator rejects it.
            with pytest.raises(ibis.common.exceptions.UnsupportedArgumentError):
                relation(df).with_columns(expr.alias("__t")).to_dict()
            return
        result = relation(df).with_columns(expr.alias("__t")).sort("row_id").to_dict()
        assert result["__t"] == expected

    @pytest.mark.parametrize("backend_name", ALL_BACKENDS)
    @pytest.mark.parametrize(
        "strategy",
        [
            MatchStrategy.SET_MEMBERSHIP,
            MatchStrategy.SET_EXCLUSION,
        ],
    )
    def test_set_strategy_evaluates_on_backend(self, compiler, backend_name, strategy):
        if backend_name == "ibis-sqlite":
            pytest.skip("SQLite has no native array/list column type.")
        dim = Dimension(
            dimension_name="list_col",
            match_strategy=strategy,
            data_type=str,
        )
        expr = compiler.compile_dimension(dim)
        df = build_backend_df(
            backend_name,
            {
                "row_id": list(range(7)),
                "list_col": [
                    ["A", "X"],
                    ["B", "Y"],
                    [UNKNOWN],
                    ["A"],
                    ["A"],
                    [],
                    [NOT_SET],
                ],
                f"{CTX_PREFIX}list_col": ["A", "A", "A", UNKNOWN, NOT_SET, "A", "A"],
            },
        )
        if backend_name in {"pandas", "narwhals-polars", "narwhals-pandas"}:
            # List-column membership needs a column-valued needle.
            with pytest.raises(BackendCapabilityError) as error:
                relation(df).with_columns(expr.alias("__t")).to_dict()
            assert error.value.backend == "narwhals"
            return
        result = relation(df).with_columns(expr.alias("__t")).sort("row_id").to_dict()
        expected = [1, -1, 0, 0, 0, -1, -1]
        if strategy == MatchStrategy.SET_EXCLUSION:
            expected = [-value for value in expected]
        assert result["__t"] == expected


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


class TestSetMembershipTernary:
    def _compile(self, dim):
        from mountainash_rules.core.compiler import DimensionCompiler
        return DimensionCompiler().compile_dimension(dim)

    def _dim(self):
        from mountainash_rules.core.constants import DataType
        return Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.STR)

    def test_wildcard_rule_is_ternary_zero(self):
        from mountainash_rules.core.constants import CTX_PREFIX
        expr = self._compile(self._dim())
        df = pl.DataFrame({
            "region": pl.Series("region", [["<NA>"]], dtype=pl.List(pl.Utf8)),
            CTX_PREFIX + "region": ["AU"],
        })
        out = df.with_columns(expr.alias("t").compile(df, booleanizer=None))
        assert out["t"].to_list() == [0]

    def test_context_in_set_is_one(self):
        from mountainash_rules.core.constants import CTX_PREFIX
        expr = self._compile(self._dim())
        df = pl.DataFrame({
            "region": pl.Series("region", [["AU", "NZ"]], dtype=pl.List(pl.Utf8)),
            CTX_PREFIX + "region": ["AU"],
        })
        out = df.with_columns(expr.alias("t").compile(df, booleanizer=None))
        assert out["t"].to_list() == [1]

    def test_context_out_of_set_is_minus_one(self):
        from mountainash_rules.core.constants import CTX_PREFIX
        expr = self._compile(self._dim())
        df = pl.DataFrame({
            "region": pl.Series("region", [["AU", "NZ"]], dtype=pl.List(pl.Utf8)),
            CTX_PREFIX + "region": ["US"],
        })
        out = df.with_columns(expr.alias("t").compile(df, booleanizer=None))
        assert out["t"].to_list() == [-1]

    def test_null_rule_list_normalizes_to_wildcard(self):
        from mountainash_rules.core.constants import CTX_PREFIX
        expr = self._compile(self._dim())
        df = pl.DataFrame({
            "region": pl.Series("region", [None], dtype=pl.List(pl.Utf8)),
            CTX_PREFIX + "region": ["AU"],
        })
        out = df.with_columns(expr.alias("t").compile(df, booleanizer=None))
        assert out["t"].to_list() == [0]


class TestSetExclusionTernary:
    def _compile(self, dim):
        from mountainash_rules.core.compiler import DimensionCompiler
        return DimensionCompiler().compile_dimension(dim)

    def _dim(self):
        from mountainash_rules.core.constants import DataType
        return Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_EXCLUSION, data_type=DataType.STR)

    def test_wildcard_rule_is_zero(self):
        from mountainash_rules.core.constants import CTX_PREFIX
        expr = self._compile(self._dim())
        df = pl.DataFrame({
            "region": pl.Series("region", [["<NA>"]], dtype=pl.List(pl.Utf8)),
            CTX_PREFIX + "region": ["AU"],
        })
        out = df.with_columns(expr.alias("t").compile(df, booleanizer=None))
        assert out["t"].to_list() == [0]

    def test_context_in_excluded_set_is_minus_one(self):
        from mountainash_rules.core.constants import CTX_PREFIX
        expr = self._compile(self._dim())
        df = pl.DataFrame({
            "region": pl.Series("region", [["AU"]], dtype=pl.List(pl.Utf8)),
            CTX_PREFIX + "region": ["AU"],
        })
        out = df.with_columns(expr.alias("t").compile(df, booleanizer=None))
        assert out["t"].to_list() == [-1]

    def test_context_not_in_excluded_set_is_one(self):
        from mountainash_rules.core.constants import CTX_PREFIX
        expr = self._compile(self._dim())
        df = pl.DataFrame({
            "region": pl.Series("region", [["AU"]], dtype=pl.List(pl.Utf8)),
            CTX_PREFIX + "region": ["NZ"],
        })
        out = df.with_columns(expr.alias("t").compile(df, booleanizer=None))
        assert out["t"].to_list() == [1]
