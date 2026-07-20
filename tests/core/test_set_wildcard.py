"""Tests for the shared set-wildcard sentinel helpers."""

import polars as pl
import pytest

from mountainash_rules.core.constants import MatchStrategy, DataType
from mountainash_rules.core.dimension import Dimension
from mountainash_rules.core.set_wildcard import (
    sentinel_list_expr,
    canonicalize_set_expr,
    normalize_set_expr,
    set_wildcard_predicate,
    validate_set_columns,
    validate_set_no_null_elements,
)
from mountainash.relations import relation


def _str_dim():
    return Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.STR)


def _float_dim():
    return Dimension(dimension_name="scores", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.FLOAT)


class TestNormalizeAndDetect:
    def test_null_becomes_sentinel_list(self):
        dim = _str_dim()
        df = pl.DataFrame({"region": pl.Series("region", [None, ["AU"]], dtype=pl.List(pl.Utf8))})
        out = df.with_columns(normalize_set_expr(dim, __import__("mountainash").col("region")).alias("n").compile(df, booleanizer=None))
        assert out["n"].to_list() == [["<NA>"], ["AU"]]
        assert out["n"].dtype == pl.List(pl.Utf8)

    def test_concrete_list_sorted_and_deduped(self):
        dim = _str_dim()
        import mountainash as ma
        df = pl.DataFrame({"region": pl.Series("region", [["UK", "NZ", "NZ"]], dtype=pl.List(pl.Utf8))})
        out = df.with_columns(normalize_set_expr(dim, ma.col("region")).alias("n").compile(df, booleanizer=None))
        assert out["n"].to_list() == [["NZ", "UK"]]

    def test_wildcard_predicate_true_on_sentinel_false_on_concrete(self):
        dim = _str_dim()
        import mountainash as ma
        df = pl.DataFrame({"region": pl.Series("region", [["<NA>"], ["AU"], []], dtype=pl.List(pl.Utf8))})
        out = df.with_columns(set_wildcard_predicate(dim, ma.col("region")).alias("w").compile(df, booleanizer=None))
        assert out["w"].to_list() == [True, False, False]

    def test_float_dim_sentinel_list_is_float_typed(self):
        dim = _float_dim()
        import mountainash as ma
        df = pl.DataFrame({"scores": pl.Series("scores", [None], dtype=pl.List(pl.Float64))})
        out = df.with_columns(normalize_set_expr(dim, ma.col("scores")).alias("n").compile(df, booleanizer=None))
        assert out["n"].dtype == pl.List(pl.Float64)
        assert out["n"].to_list() == [[-999999999.0]]

    def test_normalize_idempotent(self):
        dim = _str_dim()
        import mountainash as ma
        df = pl.DataFrame({"region": pl.Series("region", [None, ["UK", "NZ"]], dtype=pl.List(pl.Utf8))})
        once = df.with_columns(normalize_set_expr(dim, ma.col("region")).alias("region").compile(df, booleanizer=None))
        twice = once.with_columns(normalize_set_expr(dim, ma.col("region")).alias("region").compile(once, booleanizer=None))
        assert once["region"].to_list() == twice["region"].to_list()

    def test_normalize_idempotent_float_and_date(self):
        # F9: idempotence must hold across dtypes, not just str.
        import datetime as _dt
        import mountainash as ma
        from mountainash_rules.core.constants import DataType
        float_dim = _float_dim()
        fdf = pl.DataFrame({"scores": pl.Series("scores", [None, [2.5, 1.5]], dtype=pl.List(pl.Float64))})
        f1 = fdf.with_columns(normalize_set_expr(float_dim, ma.col("scores")).alias("scores").compile(fdf, booleanizer=None))
        f2 = f1.with_columns(normalize_set_expr(float_dim, ma.col("scores")).alias("scores").compile(f1, booleanizer=None))
        assert f1["scores"].to_list() == f2["scores"].to_list()
        assert f1["scores"].to_list() == [[-999999999.0], [1.5, 2.5]]

        date_dim = Dimension(dimension_name="days", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.DATE)
        d = [_dt.date(2020, 1, 2), _dt.date(2020, 1, 1)]
        ddf = pl.DataFrame({"days": pl.Series("days", [None, d], dtype=pl.List(pl.Date))})
        d1 = ddf.with_columns(normalize_set_expr(date_dim, ma.col("days")).alias("days").compile(ddf, booleanizer=None))
        d2 = d1.with_columns(normalize_set_expr(date_dim, ma.col("days")).alias("days").compile(d1, booleanizer=None))
        assert d1["days"].to_list() == d2["days"].to_list()
        assert d1["days"].dtype == pl.List(pl.Date)


class TestCanonicalize:
    def test_sort_and_dedupe(self):
        import mountainash as ma
        df = pl.DataFrame({"c": pl.Series("c", [["UK", "NZ", "UK"]], dtype=pl.List(pl.Utf8))})
        out = df.with_columns(canonicalize_set_expr(ma.col("c")).alias("c2").compile(df, booleanizer=None))
        assert out["c2"].to_list() == [["NZ", "UK"]]


class TestValidateSetColumns:
    def test_embedded_sentinel_rejected(self):
        dim = _str_dim()
        rules = pl.DataFrame({"region": pl.Series("region", [["AU", "<NA>"]], dtype=pl.List(pl.Utf8))})
        with pytest.raises(ValueError, match="sentinel"):
            validate_set_columns(relation(rules), [dim])

    def test_embedded_sentinel_passes_null_element_check(self):
        # validate_set_columns is reservation-only; whole-list null + concrete OK.
        dim = _str_dim()
        rules = pl.DataFrame({"region": pl.Series("region", [None, ["AU"], ["<NA>"]], dtype=pl.List(pl.Utf8))})
        validate_set_columns(relation(rules), [dim])  # no raise

    def test_null_element_rejected_by_dedicated_check(self):
        dim = _str_dim()
        rules = pl.DataFrame({"region": pl.Series("region", [["AU", None]], dtype=pl.List(pl.Utf8))})
        with pytest.raises(ValueError, match="null element"):
            validate_set_no_null_elements(relation(rules), [dim])

    def test_null_element_check_allows_whole_list_null(self):
        dim = _str_dim()
        rules = pl.DataFrame({"region": pl.Series("region", [None, ["AU"]], dtype=pl.List(pl.Utf8))})
        validate_set_no_null_elements(relation(rules), [dim])  # no raise

    def test_no_set_dims_is_noop(self):
        validate_set_columns(relation(pl.DataFrame({"x": [1]})), [])
        validate_set_no_null_elements(relation(pl.DataFrame({"x": [1]})), [])

    def test_reservation_check_ignores_null_elements(self):
        # The split is directional: validate_set_columns is reservation-only and
        # must NOT reject an element-null list (that is the other validator's job).
        dim = _str_dim()
        rules = pl.DataFrame({"region": pl.Series("region", [["AU", None]], dtype=pl.List(pl.Utf8))})
        validate_set_columns(relation(rules), [dim])  # no raise

    def test_null_element_check_ignores_embedded_sentinel(self):
        # ...and validate_set_no_null_elements must NOT reject an embedded-sentinel
        # list (that is the reservation check's job). Together with the two tests
        # above, this pins each validator to exactly its own violation class.
        dim = _str_dim()
        rules = pl.DataFrame({"region": pl.Series("region", [["AU", "<NA>"]], dtype=pl.List(pl.Utf8))})
        validate_set_no_null_elements(relation(rules), [dim])  # no raise


class TestSentinelListExpr:
    def test_str_sentinel_list(self):
        dim = _str_dim()
        df = pl.DataFrame({"x": [0]})
        out = df.with_columns(sentinel_list_expr(dim).alias("s").compile(df, booleanizer=None))
        assert out["s"].to_list() == [["<NA>"]]
        assert out["s"].dtype == pl.List(pl.Utf8)

    def test_float_sentinel_list_is_float_typed(self):
        dim = _float_dim()
        df = pl.DataFrame({"x": [0]})
        out = df.with_columns(sentinel_list_expr(dim).alias("s").compile(df, booleanizer=None))
        assert out["s"].to_list() == [[-999999999.0]]
        assert out["s"].dtype == pl.List(pl.Float64)
