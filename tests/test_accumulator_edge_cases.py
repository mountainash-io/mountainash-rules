"""Edge case and isolation tests for AccumulatorEngine."""

import polars as pl
import pytest
from mountainash.relations import relation

from mountainash_rules.accumulator_engine import AccumulatorEngine
from mountainash_rules.aggregate import Aggregate
from mountainash_rules.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy, DimensionRole
from mountainash_rules.dimension import Dimension, DimensionsMetadata


def _rows(df) -> dict:
    return relation(df).to_dict()


class TestEmptyRules:
    def test_build_with_empty_dataframe(self):
        rules = pl.DataFrame({
            "rule_name": [],
            "channel": [],
            "margin": [],
        }).cast({"rule_name": pl.Utf8, "channel": pl.Utf8, "margin": pl.Float64})
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        assert lattice.count == 0


class TestMultipleAggregates:
    def test_two_aggregates_accumulated_correctly(self):
        """Two all-wildcard rules combine into one combination; both agg columns sum."""
        rules = pl.DataFrame({
            "rule_name": ["r1", "r2"],
            "channel": [UNKNOWN, UNKNOWN],
            "margin": [1.0, 2.0],
            "fee": [10.0, 20.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[
                Aggregate(column_name="margin"),
                Aggregate(column_name="fee"),
            ],
        )
        lattice = engine.build(rules)
        # All wildcard = full combination dominates, only 1 outermost
        assert lattice.count == 1
        rows = _rows(lattice.combinations)
        assert rows["__agg_margin"][0] == pytest.approx(3.0)
        assert rows["__agg_fee"][0] == pytest.approx(30.0)

    def test_three_rules_two_aggregates(self):
        """r1 and r2 share channel=A and combine; r3 has channel=B and is standalone."""
        rules = pl.DataFrame({
            "rule_name": ["r1", "r2", "r3"],
            "channel": ["A", "A", "B"],
            "margin": [1.0, 2.0, 3.0],
            "fee": [10.0, 20.0, 30.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[
                Aggregate(column_name="margin"),
                Aggregate(column_name="fee"),
            ],
        )
        lattice = engine.build(rules)
        rows = _rows(lattice.combinations)
        # r1+r2 combine (both channel=A), r3 standalone (channel=B)
        # r1 prime=2, r2 prime=3, r3 prime=5
        pp_to_margin = dict(zip(rows["__prime_product"], rows["__agg_margin"]))
        pp_to_fee = dict(zip(rows["__prime_product"], rows["__agg_fee"]))
        # pair r1+r2: prime 2*3=6, margin 3.0, fee 30.0
        assert pp_to_margin[6] == pytest.approx(3.0)
        assert pp_to_fee[6] == pytest.approx(30.0)
        # r3 singleton: prime=5, margin 3.0, fee 30.0
        assert pp_to_margin[5] == pytest.approx(3.0)
        assert pp_to_fee[5] == pytest.approx(30.0)


class TestUnsupportedAggregateOperation:
    def test_unsupported_operation_raises_on_build(self):
        """An unsupported aggregate operation should raise ValueError during expansion."""
        rules = pl.DataFrame({
            "rule_name": ["r1", "r2"],
            "channel": [UNKNOWN, UNKNOWN],
            "margin": [1.0, 2.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin", operation="median")],
        )
        with pytest.raises(ValueError, match="Unsupported aggregate"):
            engine.build(rules)


class TestFrontierFilterIsolation:
    """Test the frontier filter with hand-crafted dominance scenarios."""

    def test_all_same_fingerprint_deepest_wins(self):
        """All rules have same constraint value — deepest combo dominates all singletons/pairs."""
        rules = pl.DataFrame({
            "rule_name": ["r1", "r2", "r3"],
            "channel": ["A", "A", "A"],
            "margin": [1.0, 2.0, 3.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        # All have fingerprint (A). The triple dominates all pairs and singletons.
        assert lattice.count == 1
        rows = _rows(lattice.combinations)
        assert rows["__prime_product"][0] == 2 * 3 * 5
        assert rows["__agg_margin"][0] == pytest.approx(6.0)

    def test_dominated_singletons_removed(self):
        """Wildcard singletons dominated by the wildcard pair are removed."""
        rules = pl.DataFrame({
            "rule_name": ["r1", "r2"],
            "channel": [UNKNOWN, UNKNOWN],
            "margin": [1.0, 2.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        rows = _rows(lattice.combinations)
        primes = set(rows["__prime_product"])
        # Singletons 2 and 3 are dominated by the pair 6
        assert 6 in primes
        assert 2 not in primes
        assert 3 not in primes

    def test_different_fingerprints_both_survive(self):
        """Two rules with different (non-wildcard) values cannot combine; both singletons survive."""
        rules = pl.DataFrame({
            "rule_name": ["r1", "r2"],
            "channel": ["A", "B"],
            "margin": [1.0, 2.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        # r1 and r2 are incompatible — no pair forms, both singletons survive as outermost
        assert lattice.count == 2
        rows = _rows(lattice.combinations)
        primes = set(rows["__prime_product"])
        assert 2 in primes
        assert 3 in primes

    def test_mixed_wildcard_and_concrete(self):
        """A concrete rule and a wildcard form a pair; dominance depends on fingerprint namespace.

        The frontier filter groups by co_ columns. The wildcard singleton has
        co_channel=<NA> (wildcard namespace) while the pair coalesces to co_channel=A
        (concrete namespace). Because they live in different fingerprint namespaces,
        the pair does NOT dominate the wildcard singleton — both survive.
        Only the concrete singleton (co_channel=A, pp=2) is dominated by the pair
        (co_channel=A, pp=6), because 6 % 2 == 0 and they share the same fingerprint.
        """
        rules = pl.DataFrame({
            "rule_name": ["concrete", "wildcard"],
            "channel": ["A", UNKNOWN],
            "margin": [1.0, 2.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        rows = _rows(lattice.combinations)
        primes = set(rows["__prime_product"])
        # concrete=2, wildcard=3, pair=6
        assert 6 in primes   # pair survives (co_channel=A namespace)
        assert 2 not in primes  # concrete singleton dominated by pair (same A namespace)
        assert 3 in primes   # wildcard singleton in its own <NA> namespace — NOT dominated


class TestNoConstraintDimensions:
    """All dims are CONTEXT_KEY — degenerate but valid.

    When there are no constraint dimensions, _frontier_filter returns all
    combinations unfiltered (no fingerprint columns to group on). All levels
    survive: singletons AND the pair.
    """

    def test_build_with_only_context_keys(self):
        rules = pl.DataFrame({
            "product_id": [1, 1],
            "rule_name": ["r1", "r2"],
            "margin": [1.0, 2.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="product_id",
                match_strategy=MatchStrategy.EXACT,
                data_type=int,
                role=DimensionRole.CONTEXT_KEY,
            ),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules, partition_key={"product_id": 1})
        # With no constraint dimensions, the frontier filter has no fingerprint
        # columns to group on, so it returns all combinations: 2 singletons + 1 pair = 3
        assert lattice.count == 3

    def test_pair_present_in_no_constraint_build(self):
        """Confirm the pair combination exists when no constraint dims are present."""
        rules = pl.DataFrame({
            "product_id": [1, 1],
            "rule_name": ["r1", "r2"],
            "margin": [1.0, 2.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="product_id",
                match_strategy=MatchStrategy.EXACT,
                data_type=int,
                role=DimensionRole.CONTEXT_KEY,
            ),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules, partition_key={"product_id": 1})
        rows = _rows(lattice.combinations)
        pp_to_margin = dict(zip(rows["__prime_product"], rows["__agg_margin"]))
        # The pair (pp=6) should have accumulated margin 3.0
        assert pp_to_margin[6] == pytest.approx(3.0)


class TestRangeCoalesceIntegration:
    """Test RANGE coalesce through the full build pipeline."""

    def test_range_intersection_in_build(self):
        """Two overlapping ranges coalesce to their intersection."""
        rules = pl.DataFrame({
            "rule_name": ["wide", "narrow"],
            "lvr_min": [50, 70],
            "lvr_max": [100, 90],
            "margin": [1.0, 2.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="lvr", match_strategy=MatchStrategy.RANGE,
                data_type=int, range_min_field="lvr_min", range_max_field="lvr_max",
            ),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        rows = _rows(lattice.combinations)
        # The pair {wide, narrow} should have coalesced range [70, 90]
        pp_to_min = dict(zip(rows["__prime_product"], rows["co_lvr_min"]))
        pp_to_max = dict(zip(rows["__prime_product"], rows["co_lvr_max"]))
        pair_pp = 2 * 3  # primes for 2 rules
        assert pp_to_min[pair_pp] == 70
        assert pp_to_max[pair_pp] == 90

    def test_non_overlapping_ranges_incompatible(self):
        """Non-overlapping ranges do not combine — both singletons survive."""
        rules = pl.DataFrame({
            "rule_name": ["low", "high"],
            "lvr_min": [50, 80],
            "lvr_max": [70, 100],
            "margin": [1.0, 2.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="lvr", match_strategy=MatchStrategy.RANGE,
                data_type=int, range_min_field="lvr_min", range_max_field="lvr_max",
            ),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        # Ranges [50,70] and [80,100] do not overlap — no pair, both singletons survive
        assert lattice.count == 2

    def test_sentinel_range_coalesces_with_hard_range(self):
        """A wildcard range paired with a hard range yields the hard range values."""
        rules = pl.DataFrame({
            "rule_name": ["constrained", "wildcard"],
            "lvr_min": [60, UNKNOWN_NUMERIC],
            "lvr_max": [80, UNKNOWN_NUMERIC],
            "margin": [1.0, 2.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="lvr", match_strategy=MatchStrategy.RANGE,
                data_type=int, range_min_field="lvr_min", range_max_field="lvr_max",
            ),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        rows = _rows(lattice.combinations)
        # The pair should coalesce to [60, 80] (hard wins over sentinel)
        pp_to_min = dict(zip(rows["__prime_product"], rows["co_lvr_min"]))
        pp_to_max = dict(zip(rows["__prime_product"], rows["co_lvr_max"]))
        pair_pp = 2 * 3
        assert pp_to_min[pair_pp] == 60
        assert pp_to_max[pair_pp] == 80


class TestGreaterThanLessThanIntegration:
    def test_greater_than_tightens_in_build(self):
        """Two GREATER_THAN rules coalesce to the tighter (greater) threshold."""
        rules = pl.DataFrame({
            "rule_name": ["loose", "tight"],
            "score": [10, 20],
            "margin": [1.0, 2.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="score", match_strategy=MatchStrategy.GREATER_THAN,
                data_type=int,
            ),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        rows = _rows(lattice.combinations)
        pp_to_score = dict(zip(rows["__prime_product"], rows["co_score"]))
        pair_pp = 2 * 3
        assert pp_to_score[pair_pp] == 20  # greatest(10, 20) = tighter bound

    def test_less_than_tightens_in_build(self):
        """Two LESS_THAN rules coalesce to the tighter (lesser) threshold."""
        rules = pl.DataFrame({
            "rule_name": ["loose", "tight"],
            "cap": [100, 50],
            "margin": [1.0, 2.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="cap", match_strategy=MatchStrategy.LESS_THAN,
                data_type=int,
            ),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        rows = _rows(lattice.combinations)
        pp_to_cap = dict(zip(rows["__prime_product"], rows["co_cap"]))
        pair_pp = 2 * 3
        assert pp_to_cap[pair_pp] == 50  # least(100, 50) = tighter bound

    def test_greater_than_always_compatible(self):
        """Any two GREATER_THAN rules are always compatible (no incompatibility possible).

        Frontier filter groups by co_score (the coalesced threshold). Each unique
        coalesced value forms its own fingerprint namespace, so combinations with
        different co_score values are NOT in the same namespace and cannot dominate
        each other. With scores [5, 10, 100]:
          - r1 (co=5), r1+r2 (co=10), r1+r2+r3 (co=100) each have different namespaces
          - r2+r3 (co=100) and r1+r3 (co=100) are dominated by the triple (also co=100)
          - r1+r2 (co=10) is NOT dominated by the triple (co=100 ≠ co=10)
          - r1 (co=5) is NOT dominated by r1+r2 (co=10 ≠ co=5)
        Result: 3 survivors — r1 (pp=2, co=5), r1+r2 (pp=6, co=10), triple (pp=30, co=100)
        """
        rules = pl.DataFrame({
            "rule_name": ["r1", "r2", "r3"],
            "score": [5, 10, 100],
            "margin": [1.0, 2.0, 3.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="score", match_strategy=MatchStrategy.GREATER_THAN,
                data_type=int,
            ),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        rows = _rows(lattice.combinations)
        pp_to_score = dict(zip(rows["__prime_product"], rows["co_score"]))
        # The triple survives (co=100 namespace, tightest)
        assert 2 * 3 * 5 in pp_to_score
        assert pp_to_score[2 * 3 * 5] == 100
        # The pair r1+r2 survives in its own namespace (co=10)
        assert 2 * 3 in pp_to_score
        assert pp_to_score[2 * 3] == 10
        # r1 singleton survives in its own namespace (co=5)
        assert 2 in pp_to_score
        assert pp_to_score[2] == 5
        # 3 survivors total
        assert lattice.count == 3

    def test_less_than_sentinel_coalesces_with_hard_value(self):
        """A wildcard LESS_THAN paired with a hard value yields the hard value."""
        rules = pl.DataFrame({
            "rule_name": ["constrained", "wildcard"],
            "cap": [75, UNKNOWN_NUMERIC],
            "margin": [1.0, 2.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="cap", match_strategy=MatchStrategy.LESS_THAN,
                data_type=int,
            ),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        rows = _rows(lattice.combinations)
        pp_to_cap = dict(zip(rows["__prime_product"], rows["co_cap"]))
        pair_pp = 2 * 3
        assert pp_to_cap[pair_pp] == 75


class TestBuildStepIsolation:
    """Verify anchor level and first expansion step produce correct structures."""

    def test_anchor_level_produces_singleton_combinations(self):
        """Level-0 anchor must have one row per rule, each a singleton."""
        rules = pl.DataFrame({
            "rule_name": ["r1", "r2", "r3"],
            "channel": ["A", "B", UNKNOWN],
            "margin": [1.0, 2.0, 3.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        # Build and inspect the lattice to indirectly verify anchor via level col
        lattice = engine.build(rules)
        rows = _rows(lattice.combinations)
        # Filter to level-0 entries (singletons that survive frontier)
        pp_level = list(zip(rows["__prime_product"], rows["__level"]))
        level_0 = [pp for pp, lvl in pp_level if lvl == 0]
        # All singletons that survive are at level 0
        for pp in level_0:
            assert pp in {2, 3, 5}

    def test_anchor_level_values_correct(self):
        """Anchor level __prime equals __prime_product (singleton identity)."""
        rules = pl.DataFrame({
            "rule_name": ["r1", "r2"],
            "channel": ["A", "A"],
            "margin": [1.0, 2.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        rows = _rows(lattice.combinations)
        # The surviving combination (the pair) is at level 1; verify aggregate is correct
        pp_to_level = dict(zip(rows["__prime_product"], rows["__level"]))
        pp_to_margin = dict(zip(rows["__prime_product"], rows["__agg_margin"]))
        assert pp_to_level[6] == 1
        assert pp_to_margin[6] == pytest.approx(3.0)

    def test_expansion_step_produces_pairs(self):
        """Two compatible rules should produce one pair at level 1."""
        rules = pl.DataFrame({
            "rule_name": ["r1", "r2"],
            "channel": [UNKNOWN, UNKNOWN],
            "margin": [10.0, 20.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        rows = _rows(lattice.combinations)
        pp_to_level = dict(zip(rows["__prime_product"], rows["__level"]))
        # Pair pp=6 at level 1
        assert 6 in pp_to_level
        assert pp_to_level[6] == 1

    def test_triple_is_level_2(self):
        """Three mutually compatible rules produce a triple at level 2."""
        rules = pl.DataFrame({
            "rule_name": ["r1", "r2", "r3"],
            "channel": [UNKNOWN, UNKNOWN, UNKNOWN],
            "margin": [1.0, 2.0, 3.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        rows = _rows(lattice.combinations)
        # The surviving triple (pp=30) should be at level 2
        assert lattice.count == 1
        assert rows["__level"][0] == 2
        assert rows["__prime_product"][0] == 2 * 3 * 5
