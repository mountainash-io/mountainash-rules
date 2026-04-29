"""AccumulatorResult: extends RuleResult with accumulator-specific accessors."""

from __future__ import annotations

import typing as t

from mountainash.relations import relation

from mountainash_utils_rules.aggregate import Aggregate
from mountainash_utils_rules.lattice import Lattice
from mountainash_utils_rules.result import RuleResult


class AccumulatorResult(RuleResult):
    """Wraps accumulator apply-phase output with convenience accessors."""

    def __init__(
        self,
        dataframe: t.Any,
        active_dimensions: list[str],
        aggregates: list[Aggregate],
        lattice: Lattice,
    ) -> None:
        super().__init__(dataframe=dataframe, active_dimensions=active_dimensions)
        self._aggregates = aggregates
        self._lattice = lattice

    @property
    def best_combination(self) -> t.Any:
        """The most specific matching outermost ruleset."""
        return self.best_match

    def accumulated(self, aggregate_name: str) -> t.Any:
        """Return the accumulated value column for matching combinations."""
        col_name = f"__agg_{aggregate_name}"
        return relation(self._df).select(col_name).collect()

    @property
    def provenance(self) -> t.Any:
        """Prime products of matching combinations, for traceability."""
        return relation(self._df).select("__prime_product").collect()

    @property
    def depths(self) -> t.Any:
        """Combination depths (number of contributing rules) for matches."""
        return relation(self._df).select("__level").collect()
