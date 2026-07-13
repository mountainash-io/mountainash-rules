"""Correctness tests for accumulator range semantics, NA flags, and overflow."""

import polars as pl
import pytest
from mountainash.relations import relation

from mountainash_rules.engines.accumulator.engine import AccumulatorEngine
from mountainash_rules.core.constants import UNKNOWN_NUMERIC, MatchStrategy
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata

S = UNKNOWN_NUMERIC


def _range_metadata(min_inc=True, max_inc=True):
    return DimensionsMetadata(dimensions=[
        Dimension(
            dimension_name="x", match_strategy=MatchStrategy.RANGE,
            data_type=int, range_min_field="x_min", range_max_field="x_max",
            range_min_inclusive=min_inc, range_max_inclusive=max_inc,
        ),
    ])


def _build_pair(a, b, min_inc=True, max_inc=True):
    """Build a 2-rule lattice; the pair combined iff __prime_product 6 exists."""
    engine = AccumulatorEngine(dimension_metadata=_range_metadata(min_inc, max_inc))
    rules = pl.DataFrame({
        "rule_name": ["A", "B"],
        "x_min": [a[0], b[0]],
        "x_max": [a[1], b[1]],
    })
    lattice = engine.build(rules)
    rows = relation(lattice.combinations).to_dict()
    return set(rows["__prime_product"]), rows


class TestHalfOpenRangeCompatibility:
    def test_two_lower_bounded_ranges_combine(self):
        # [50, +inf) and [60, +inf) overlap on [60, +inf)
        products, _ = _build_pair((50, S), (60, S))
        assert 6 in products

    def test_lower_bounded_vs_disjoint_upper_bounded_do_not_combine(self):
        # [50, +inf) and (-inf, 40] are disjoint
        products, _ = _build_pair((50, S), (S, 40))
        assert 6 not in products

    def test_two_upper_bounded_ranges_combine_to_tighter_max(self):
        products, rows = _build_pair((S, 10), (S, 20))
        assert 6 in products
        by_product = {p: i for i, p in enumerate(rows["__prime_product"])}
        i = by_product[6]
        assert rows["co_x_min"][i] == S
        assert rows["co_x_max"][i] == 10

    def test_sentinel_min_does_not_mean_whole_dimension_wildcard(self):
        # (-inf, 5] vs [7, 9]: disjoint even though one min is sentinel
        products, _ = _build_pair((S, 5), (7, 9))
        assert 6 not in products


class TestInclusivityFlags:
    def test_touching_inclusive_ranges_combine_to_point(self):
        products, rows = _build_pair((0, 10), (10, 20))
        assert 6 in products
        by_product = {p: i for i, p in enumerate(rows["__prime_product"])}
        i = by_product[6]
        assert rows["co_x_min"][i] == 10
        assert rows["co_x_max"][i] == 10

    def test_touching_exclusive_max_ranges_do_not_combine(self):
        products, _ = _build_pair((0, 10), (10, 20), max_inc=False)
        assert 6 not in products


class TestRangeNaFlags:
    def test_combined_upper_bounded_ranges_not_flagged_na(self):
        # (-inf, 10] + (-inf, 20]: bounded above, so NOT fully don't-care
        products, rows = _build_pair((S, 10), (S, 20))
        by_product = {p: i for i, p in enumerate(rows["__prime_product"])}
        assert rows["co_x_na"][by_product[6]] == 0

    def test_combined_all_sentinel_ranges_flagged_na(self):
        products, rows = _build_pair((S, S), (S, S))
        by_product = {p: i for i, p in enumerate(rows["__prime_product"])}
        assert rows["co_x_na"][by_product[6]] == 1


class TestAnchorNaFlags:
    def test_half_open_singleton_not_flagged_na(self):
        engine = AccumulatorEngine(dimension_metadata=_range_metadata())
        rules = pl.DataFrame({"rule_name": ["A"], "x_min": [S], "x_max": [10]})
        lattice = engine.build(rules)
        rows = relation(lattice.combinations).to_dict()
        assert rows["co_x_na"][0] == 0

    def test_all_sentinel_singleton_flagged_na(self):
        engine = AccumulatorEngine(dimension_metadata=_range_metadata())
        rules = pl.DataFrame({"rule_name": ["A"], "x_min": [S], "x_max": [S]})
        lattice = engine.build(rules)
        rows = relation(lattice.combinations).to_dict()
        assert rows["co_x_na"][0] == 1


import mountainash_rules.engines.accumulator.engine as acc_mod
from mountainash_rules.engines.accumulator.primes import LatticeWidthExceededError


def _all_wildcard_rules(n):
    """n mutually compatible rules (every bound sentinel)."""
    return pl.DataFrame({
        "rule_name": [f"R{i}" for i in range(n)],
        "x_min": [S] * n,
        "x_max": [S] * n,
    })


class TestOverflowGuard:
    def test_sixteen_rule_clique_raises(self):
        engine = AccumulatorEngine(dimension_metadata=_range_metadata())
        with pytest.raises(LatticeWidthExceededError) as exc_info:
            engine.build(_all_wildcard_rules(16))
        msg = str(exc_info.value)
        assert "level" in msg
        assert "CONTEXT_KEY" in msg

    def test_unsafe_partition_with_small_clique_builds_with_exact_product(self):
        # 16 rules trips the tier-1 screen (full prime product > int64), but
        # the only real clique is 6 identical-range rules — tier-2 exact
        # verification must pass and the clique's exact product must appear.
        # (A literal 15-wildcard clique is infeasible: its 32k-row lattice
        # makes the frontier dominance join quadratic.)
        import math
        from mountainash_rules.engines.accumulator.primes import get_prime
        engine = AccumulatorEngine(dimension_metadata=_range_metadata())
        rules = pl.DataFrame({
            "rule_name": [f"R{i}" for i in range(16)],
            "x_min": [0] * 6 + [100 + 2 * i for i in range(10)],
            "x_max": [10] * 6 + [101 + 2 * i for i in range(10)],
        })
        lattice = engine.build(rules)
        rows = relation(lattice.combinations).to_dict()
        expected = math.prod(get_prime(i) for i in range(6))  # 2*3*5*7*11*13
        assert expected in set(rows["__prime_product"])

    def test_safe_partition_skips_verification(self, monkeypatch):
        calls = []
        real = acc_mod.checked_multiply
        monkeypatch.setattr(
            acc_mod, "checked_multiply",
            lambda a, b: calls.append((a, b)) or real(a, b),
        )
        engine = AccumulatorEngine(dimension_metadata=_range_metadata())
        engine.build(_all_wildcard_rules(5))  # prod(2..11) = 2310, tier-1 safe
        assert calls == []


import itertools

from mountainash_rules.engines.filter.engine import ExpressionRulesEngine

BOUNDS = [S, 0, 5, 10]
PROBES = [-1, 0, 2, 5, 7, 10, 11]
FLAG_COMBOS = [(True, True), (True, False), (False, True), (False, False)]


def _valid_interval(lo, hi, min_inc=True, max_inc=True):
    # A rule interval must be non-empty as a set: finite lo > hi is always
    # invalid, and a finite point lo == hi is only non-empty when both
    # endpoints are inclusive (e.g. (0, 0] is the empty set).
    if lo == S or hi == S:
        return True
    if lo == hi:
        return min_inc and max_inc
    return lo < hi


def _coalesced_nonempty(a, b, min_inc, max_inc):
    """Python-side oracle: intersection of a and b is non-empty."""
    finite_los = [v for v in (a[0], b[0]) if v != S]
    finite_his = [v for v in (a[1], b[1]) if v != S]
    if not finite_los or not finite_his:
        return True
    lo, hi = max(finite_los), min(finite_his)
    return lo <= hi if (min_inc and max_inc) else lo < hi


class TestThreeWayEquivalence:
    @pytest.mark.parametrize("min_inc,max_inc", FLAG_COMBOS)
    def test_compatible_iff_nonempty_iff_joint_match(self, min_inc, max_inc):
        metadata = _range_metadata(min_inc, max_inc)
        intervals = [
            (lo, hi)
            for lo, hi in itertools.product(BOUNDS, BOUNDS)
            if _valid_interval(lo, hi, min_inc, max_inc)
        ]
        for a, b in itertools.combinations_with_replacement(intervals, 2):
            products, _ = _build_pair(a, b, min_inc, max_inc)
            combined = 6 in products

            nonempty = _coalesced_nonempty(a, b, min_inc, max_inc)

            rules = pl.DataFrame({
                "rule_name": ["A", "B"],
                "x_min": [a[0], b[0]],
                "x_max": [a[1], b[1]],
            })
            filter_engine = ExpressionRulesEngine(
                rules=rules, dimension_metadata=metadata
            )
            joint = any(
                filter_engine.evaluate({"x": v}).count == 2 for v in PROBES
            )

            label = f"A={a} B={b} min_inc={min_inc} max_inc={max_inc}"
            assert combined == nonempty, f"lattice vs interval oracle: {label}"
            assert combined == joint, f"lattice vs filter-engine oracle: {label}"
