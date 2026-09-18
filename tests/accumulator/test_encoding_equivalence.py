"""Prime replacement oracle: preserve comparison namespace and row exclusion."""

import math

import mountainash.expressions as ma
from mountainash.relations import relation

from tests.conftest import build_backend_df


def test_prime_and_bitset_candidate_survivor_equivalence(backend_name):
    sets = [frozenset(i for i in range(4) if mask & (1 << i)) for mask in range(1, 16)]
    sets.append(frozenset(range(4)))  # a disconnected row with equal membership
    primes = (2, 3, 5, 7)
    data = {
        "row": list(range(32)),
        "namespace": ["a"] * 16 + ["b"] * 16,
        "mask": [sum(1 << i for i in members) for members in sets] * 2,
        "prime": [math.prod(primes[i] for i in members) for members in sets] * 2,
    }
    source = relation(build_backend_df(backend_name, data))
    left = source.select(*[ma.col(n).alias("l_" + n) for n in data])
    right = source.select(*[ma.col(n).alias("r_" + n) for n in data])
    pairs = left.cross_join(right).filter(
        ma.col("l_namespace").eq(ma.col("r_namespace"))
        & ma.col("l_row").ne(ma.col("r_row"))
    )
    computed = pairs.select(
        "l_row",
        "r_row",
        (
            (ma.col("r_prime") % ma.col("l_prime")).eq(0)
            & ma.col("l_prime").ne(ma.col("r_prime"))
        ).alias("prime_subset"),
        (
            ma.col("l_mask").bitwise_and(ma.col("r_mask")).eq(ma.col("l_mask"))
            & ma.col("l_mask").ne(ma.col("r_mask"))
        ).alias("word_subset"),
        ma.col("l_mask").bitwise_or(ma.col("r_mask")).alias("union"),
        ma.col("l_mask").bitwise_and(ma.col("r_mask")).alias("intersection"),
    ).to_dicts()
    prime_dominated, word_dominated = set(), set()
    for record in computed:
        i, j = record["l_row"], record["r_row"]
        a, b = sets[i % 16], sets[j % 16]
        assert record["prime_subset"] == record["word_subset"] == (a < b)
        assert record["union"] == sum(1 << k for k in a | b)
        assert record["intersection"] == sum(1 << k for k in a & b)
        if record["prime_subset"]:
            prime_dominated.add(i)
        if record["word_subset"]:
            word_dominated.add(i)
    assert (
        set(data["row"]) - prime_dominated
        == set(data["row"]) - word_dominated
        == {14, 15, 30, 31}
    )
