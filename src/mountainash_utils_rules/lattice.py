import typing as t

from mountainash.relations import relation

from mountainash_utils_rules.aggregate import Aggregate
from mountainash_utils_rules.dimension import DimensionsMetadata


class Lattice:
    def __init__(
        self,
        dataframe: t.Any,
        metadata: DimensionsMetadata,
        aggregates: list[Aggregate],
        partition_key: dict | None,
    ) -> None:
        self._df = dataframe
        self._metadata = metadata
        self._aggregates = aggregates
        self._partition_key = partition_key

    @property
    def combinations(self) -> t.Any:
        return self._df

    @property
    def count(self) -> int:
        return relation(self._df).count_rows()

    @property
    def partition_key(self) -> dict | None:
        return self._partition_key
