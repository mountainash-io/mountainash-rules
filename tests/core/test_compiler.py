"""Tests for DimensionCompiler."""

import ibis
import polars as pl
import pytest

from mountainash.core.types import BackendCapabilityError

from mountainash_rules.core.compiler import DimensionCompiler
from mountainash_rules import ContextField
from mountainash_rules.core.constants import (
    CTX_PREFIX,
    NOT_SET,
    UNKNOWN,
    UNKNOWN_NUMERIC,
    MatchStrategy,
)
from mountainash_rules.core.dimension import Dimension
from tests.conftest import (
    ALL_BACKENDS,
    build_backend_df,
    bind_compiler_inputs,
)


@pytest.fixture
def compiler():
    return DimensionCompiler()


def _bound_relation(frame, dim):
    if not hasattr(dim, "_field") or dim._field is None:
        ctx = f"{CTX_PREFIX}{dim.dimension_name}"
        if isinstance(frame, pl.DataFrame) and ctx in frame.columns:
            numeric = frame[ctx].dtype.is_numeric()
        else:
            numeric = dim.dimension_name in {"amount", "tier", "num_col"}
        data_type, wildcard = ("int", UNKNOWN_NUMERIC) if numeric else ("str", UNKNOWN)
        object.__setattr__(
            dim,
            "_field",
            ContextField(name=dim.context_field, data_type=data_type, wildcard=wildcard),
        )
    return bind_compiler_inputs(frame, [dim])


class TestExactCompilation:
    def test_exact_match_produces_true(self, compiler):
        dim = Dimension(
            dimension_name="region", context_field="region",
            match_strategy=MatchStrategy.EXACT,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame(
            {
                "region": ["AU", "US", "UK"],
                f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_region"))
            .to_polars()
        )
        values = result["__t_region"].to_list()
        assert values == [1, -1, -1]

    def test_exact_unknown_rule_value_produces_unknown(self, compiler):
        dim = Dimension(
            dimension_name="region", context_field="region",
            match_strategy=MatchStrategy.EXACT,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame(
            {
                "region": ["AU", UNKNOWN, "UK"],
                f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_region"))
            .to_polars()
        )
        values = result["__t_region"].to_list()
        assert values[0] == 1  # hard match
        assert values[1] == 0  # unknown (wildcard)
        assert values[2] == -1  # non-match

    def test_exact_unknown_context_produces_unknown(self, compiler):
        dim = Dimension(
            dimension_name="region", context_field="region",
            match_strategy=MatchStrategy.EXACT,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame(
            {
                "region": ["AU", "US"],
                f"{CTX_PREFIX}region": [UNKNOWN, UNKNOWN],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_region"))
            .to_polars()
        )
        values = result["__t_region"].to_list()
        assert values == [0, 0]  # all unknown when context is unknown

    def test_exact_numeric(self, compiler):
        dim = Dimension(
            dimension_name="tier", context_field="tier",
            match_strategy=MatchStrategy.EXACT,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame(
            {
                "tier": [1, 2, UNKNOWN_NUMERIC],
                f"{CTX_PREFIX}tier": [1, 1, 1],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_tier"))
            .to_polars()
        )
        values = result["__t_tier"].to_list()
        assert values[0] == 1  # match
        assert values[1] == -1  # non-match
        assert values[2] == 0  # unknown


class TestRangeCompilation:
    def test_range_within_bounds_produces_true(self, compiler):
        dim = Dimension(
            dimension_name="amount", context_field="amount",
            match_strategy=MatchStrategy.RANGE,
            range_min_field="amount_min",
            range_max_field="amount_max",
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame(
            {
                "amount_min": [0, 100, 200],
                "amount_max": [99, 199, 299],
                f"{CTX_PREFIX}amount": [50, 50, 50],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_amount"))
            .to_polars()
        )
        values = result["__t_amount"].to_list()
        assert values == [1, -1, -1]

    def test_range_boundary_inclusive(self, compiler):
        dim = Dimension(
            dimension_name="amount", context_field="amount",
            match_strategy=MatchStrategy.RANGE,
            range_min_field="amount_min",
            range_max_field="amount_max",
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame(
            {
                "amount_min": [10, 10],
                "amount_max": [20, 20],
                f"{CTX_PREFIX}amount": [10, 20],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_amount"))
            .to_polars()
        )
        values = result["__t_amount"].to_list()
        assert values == [1, 1]  # both boundaries inclusive

    def test_range_unknown_min_produces_unknown(self, compiler):
        dim = Dimension(
            dimension_name="amount", context_field="amount",
            match_strategy=MatchStrategy.RANGE,
            range_min_field="amount_min",
            range_max_field="amount_max",
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame(
            {
                "amount_min": [0, UNKNOWN_NUMERIC],
                "amount_max": [100, 100],
                f"{CTX_PREFIX}amount": [50, 50],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_amount"))
            .to_polars()
        )
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
            dimension_name="code", context_field="code",
            match_strategy=MatchStrategy.CONTEXT_REGEX,
            regex_pattern="^PRE.*",
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame(
            {
                f"{CTX_PREFIX}code": ["PRE-001", "PRE-999", "POST-001"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_code"))
            .to_polars()
        )
        assert result["__t_code"].to_list() == [1, 1, -1]

    def test_regex_search_semantics(self, compiler):
        """regex_contains uses search semantics (match anywhere, not anchored)."""
        dim = Dimension(
            dimension_name="code", context_field="code",
            match_strategy=MatchStrategy.CONTEXT_REGEX,
            regex_pattern="123",
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame(
            {
                f"{CTX_PREFIX}code": ["abc-123-def", "xyz"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_code"))
            .to_polars()
        )
        assert result["__t_code"].to_list() == [1, -1]

    def test_regex_no_unknown_state(self, compiler):
        """CONTEXT_REGEX has no unknown/0 state — pattern is fixed at metadata time."""
        dim = Dimension(
            dimension_name="code", context_field="code",
            match_strategy=MatchStrategy.CONTEXT_REGEX,
            regex_pattern="^AU.*",
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame(
            {
                f"{CTX_PREFIX}code": ["AU-1", "NZ-1"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_code"))
            .to_polars()
        )
        # only 1 and -1; never 0
        assert set(result["__t_code"].to_list()) <= {1, -1}


class TestNotEqualCompilation:
    def test_not_equal_mismatch_produces_true(self, compiler):
        dim = Dimension(
            dimension_name="region", context_field="region",
            match_strategy=MatchStrategy.NOT_EQUAL,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame(
            {
                "region": ["AU", "US", "UK"],
                f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_region"))
            .to_polars()
        )
        values = result["__t_region"].to_list()
        assert values == [-1, 1, 1]

    def test_not_equal_unknown_rule_produces_unknown(self, compiler):
        dim = Dimension(
            dimension_name="region", context_field="region",
            match_strategy=MatchStrategy.NOT_EQUAL,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame(
            {
                "region": [UNKNOWN, "US"],
                f"{CTX_PREFIX}region": ["AU", "AU"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_region"))
            .to_polars()
        )
        values = result["__t_region"].to_list()
        assert values[0] == 0
        assert values[1] == 1


class TestPrefixCompilation:
    def test_prefix_match(self, compiler):
        dim = Dimension(
            dimension_name="code", context_field="code",
            match_strategy=MatchStrategy.PREFIX,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame(
            {
                "code": ["PRE-", "POST-", "MID-"],
                f"{CTX_PREFIX}code": ["PRE-001", "PRE-001", "PRE-001"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_code"))
            .to_polars()
        )
        values = result["__t_code"].to_list()
        assert values == [1, -1, -1]

    def test_prefix_no_match(self, compiler):
        dim = Dimension(
            dimension_name="code", context_field="code",
            match_strategy=MatchStrategy.PREFIX,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame(
            {
                "code": ["PRE-"],
                f"{CTX_PREFIX}code": ["XYZ-001"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_code"))
            .to_polars()
        )
        assert result["__t_code"].to_list() == [-1]

    def test_prefix_unknown_rule_produces_unknown(self, compiler):
        dim = Dimension(
            dimension_name="code", context_field="code",
            match_strategy=MatchStrategy.PREFIX,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame(
            {
                "code": ["PRE-", UNKNOWN],
                f"{CTX_PREFIX}code": ["PRE-001", "PRE-001"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_code"))
            .to_polars()
        )
        values = result["__t_code"].to_list()
        assert values[0] == 1
        assert values[1] == 0

    def test_prefix_per_row_different_patterns(self, compiler):
        dim = Dimension(
            dimension_name="code", context_field="code",
            match_strategy=MatchStrategy.PREFIX,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame(
            {
                "code": ["PRE-", "POST-", "MID-"],
                f"{CTX_PREFIX}code": ["PRE-001", "POST-002", "MID-003"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_code"))
            .to_polars()
        )
        assert result["__t_code"].to_list() == [1, 1, 1]


class TestSuffixCompilation:
    def test_suffix_match(self, compiler):
        dim = Dimension(
            dimension_name="code", context_field="code",
            match_strategy=MatchStrategy.SUFFIX,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame(
            {
                "code": ["-AUD", "-USD", "-EUR"],
                f"{CTX_PREFIX}code": ["TXN-AUD", "TXN-AUD", "TXN-AUD"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_code"))
            .to_polars()
        )
        assert result["__t_code"].to_list() == [1, -1, -1]

    def test_suffix_no_match(self, compiler):
        dim = Dimension(
            dimension_name="code", context_field="code",
            match_strategy=MatchStrategy.SUFFIX,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame(
            {
                "code": ["-AUD"],
                f"{CTX_PREFIX}code": ["TXN-USD"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_code"))
            .to_polars()
        )
        assert result["__t_code"].to_list() == [-1]

    def test_suffix_unknown_rule_produces_unknown(self, compiler):
        dim = Dimension(
            dimension_name="code", context_field="code",
            match_strategy=MatchStrategy.SUFFIX,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame(
            {
                "code": ["-AUD", UNKNOWN],
                f"{CTX_PREFIX}code": ["TXN-AUD", "TXN-AUD"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_code"))
            .to_polars()
        )
        values = result["__t_code"].to_list()
        assert values[0] == 1
        assert values[1] == 0

    def test_suffix_per_row_different_patterns(self, compiler):
        dim = Dimension(
            dimension_name="code", context_field="code",
            match_strategy=MatchStrategy.SUFFIX,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame(
            {
                "code": ["-AUD", "-USD", "-EUR"],
                f"{CTX_PREFIX}code": ["TXN-AUD", "TXN-USD", "TXN-EUR"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_code"))
            .to_polars()
        )
        assert result["__t_code"].to_list() == [1, 1, 1]


class TestContainsCompilation:
    def test_contains_match(self, compiler):
        dim = Dimension(
            dimension_name="tier", context_field="tier",
            match_strategy=MatchStrategy.CONTAINS,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame(
            {
                "tier": ["gold", "silver", "bronze"],
                f"{CTX_PREFIX}tier": ["gold_tier", "gold_tier", "gold_tier"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_tier"))
            .to_polars()
        )
        assert result["__t_tier"].to_list() == [1, -1, -1]

    def test_contains_no_match(self, compiler):
        dim = Dimension(
            dimension_name="tier", context_field="tier",
            match_strategy=MatchStrategy.CONTAINS,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame(
            {
                "tier": ["gold"],
                f"{CTX_PREFIX}tier": ["platinum_tier"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_tier"))
            .to_polars()
        )
        assert result["__t_tier"].to_list() == [-1]

    def test_contains_unknown_rule_produces_unknown(self, compiler):
        dim = Dimension(
            dimension_name="tier", context_field="tier",
            match_strategy=MatchStrategy.CONTAINS,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame(
            {
                "tier": ["gold", UNKNOWN],
                f"{CTX_PREFIX}tier": ["gold_tier", "gold_tier"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_tier"))
            .to_polars()
        )
        values = result["__t_tier"].to_list()
        assert values[0] == 1
        assert values[1] == 0

    def test_contains_per_row_different_patterns(self, compiler):
        dim = Dimension(
            dimension_name="tier", context_field="tier",
            match_strategy=MatchStrategy.CONTAINS,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame(
            {
                "tier": ["gold", "silver", "bronze"],
                f"{CTX_PREFIX}tier": ["gold_tier", "silver_tier", "bronze_tier"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_tier"))
            .to_polars()
        )
        assert result["__t_tier"].to_list() == [1, 1, 1]


class TestSetMembershipCompilation:
    def test_set_membership_match(self, compiler):
        dim = Dimension(
            dimension_name="region", context_field="region",
            match_strategy=MatchStrategy.SET_MEMBERSHIP,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame(
            {
                "region": pl.Series(
                    "region",
                    [["AU", "NZ", "UK"], ["US", "CA"], ["DE", "FR"]],
                    dtype=pl.List(pl.Utf8),
                ),
                f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_region"))
            .to_polars()
        )
        values = result["__t_region"].to_list()
        # AU in [AU,NZ,UK] → 1; AU in [US,CA] → -1; AU in [DE,FR] → -1
        assert values == [1, -1, -1]

    def test_set_membership_unknown_context(self, compiler):
        dim = Dimension(
            dimension_name="region", context_field="region",
            match_strategy=MatchStrategy.SET_MEMBERSHIP,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame(
            {
                "region": pl.Series(
                    "region",
                    [["AU", "NZ"]],
                    dtype=pl.List(pl.Utf8),
                ),
                f"{CTX_PREFIX}region": [UNKNOWN],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_region"))
            .to_polars()
        )
        values = result["__t_region"].to_list()
        assert values == [0]


class TestSetExclusionCompilation:
    def test_set_exclusion_match(self, compiler):
        dim = Dimension(
            dimension_name="region", context_field="region",
            match_strategy=MatchStrategy.SET_EXCLUSION,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame(
            {
                "region": pl.Series(
                    "region",
                    [["AU", "NZ", "UK"], ["US", "CA"], ["DE", "FR"]],
                    dtype=pl.List(pl.Utf8),
                ),
                f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_region"))
            .to_polars()
        )
        values = result["__t_region"].to_list()
        # AU not in [AU,NZ,UK] → -1; AU not in [US,CA] → 1; AU not in [DE,FR] → 1
        assert values == [-1, 1, 1]

    def test_set_exclusion_unknown_context(self, compiler):
        dim = Dimension(
            dimension_name="region", context_field="region",
            match_strategy=MatchStrategy.SET_EXCLUSION,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame(
            {
                "region": pl.Series(
                    "region",
                    [["AU", "NZ"]],
                    dtype=pl.List(pl.Utf8),
                ),
                f"{CTX_PREFIX}region": [UNKNOWN],
            }
        )
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.name.alias("__t_region"))
            .to_polars()
        )
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
            context_field=field,
            match_strategy=strategy,
            **extras,
        )
        object.__setattr__(dim, "_field", ContextField(name=field, data_type=data_type))
        expr = compiler.compile_dimension(dim)
        df = build_backend_df(backend_name, self._NON_LIST_DATA)
        if backend_name in {"pandas", "narwhals-pandas"} and strategy in {
            MatchStrategy.PREFIX,
            MatchStrategy.SUFFIX,
            MatchStrategy.CONTAINS,
        }:
            # Column-valued string predicates are not supported on pandas.
            rel = _bound_relation(df, dim).with_columns(expr.alias("__t"))
            with pytest.raises(BackendCapabilityError) as error:
                rel.to_dict()
            assert error.value.backend == "narwhals"
            return
        if backend_name == "ibis-polars" and strategy in {
            MatchStrategy.PREFIX,
            MatchStrategy.SUFFIX,
            MatchStrategy.CONTAINS,
        }:
            # Ibis accepts the expression but its Polars translator rejects it.
            rel = _bound_relation(df, dim).with_columns(expr.alias("__t"))
            with pytest.raises(ibis.common.exceptions.UnsupportedArgumentError):
                rel.to_dict()
            return
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.alias("__t"))
            .sort("row_id")
            .to_dict()
        )
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
            dimension_name="list_col", context_field="list_col",
            match_strategy=strategy,
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
                    [UNKNOWN],
                ],
                f"{CTX_PREFIX}list_col": ["A", "A", "A", UNKNOWN, None, "A", "A"],
            },
        )
        if backend_name in {"pandas", "narwhals-polars", "narwhals-pandas"}:
            # List-column membership needs a column-valued needle.
            rel = _bound_relation(df, dim).with_columns(expr.alias("__t"))
            with pytest.raises(BackendCapabilityError) as error:
                rel.to_dict()
            assert error.value.backend == "narwhals"
            return
        result = (
            _bound_relation(df, dim)
            .with_columns(expr.alias("__t"))
            .sort("row_id")
            .to_dict()
        )
        expected = [1, -1, 0, 0, 0, -1, 0]
        if strategy == MatchStrategy.SET_EXCLUSION:
            expected = [-value for value in expected]
        assert result["__t"] == expected


