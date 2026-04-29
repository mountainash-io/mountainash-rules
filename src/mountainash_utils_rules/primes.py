"""Prime table and utilities for accumulator combination identity."""

from __future__ import annotations

_INT64_MAX = (2**63) - 1


def _sieve(limit: int) -> list[int]:
    """Sieve of Eratosthenes up to `limit`."""
    is_prime = [True] * (limit + 1)
    is_prime[0] = is_prime[1] = False
    for i in range(2, int(limit**0.5) + 1):
        if is_prime[i]:
            for j in range(i * i, limit + 1, i):
                is_prime[j] = False
    return [i for i, v in enumerate(is_prime) if v]


PRIME_TABLE: list[int] = _sieve(3572)


def get_prime(index: int) -> int:
    """Return the prime at the given 0-based index."""
    if index < 0:
        raise IndexError(f"Prime index must be non-negative, got {index}")
    if index >= len(PRIME_TABLE):
        raise IndexError(
            f"Prime index {index} exceeds table size {len(PRIME_TABLE)}. "
            f"Partition has too many rules."
        )
    return PRIME_TABLE[index]


def checked_multiply(a: int, b: int) -> int:
    """Multiply two ints, raising OverflowError if the result exceeds int64."""
    result = a * b
    if result > _INT64_MAX:
        raise OverflowError(
            f"Prime product {a} * {b} = {result} exceeds int64 max ({_INT64_MAX}). "
            f"Partition has too many mutually compatible rules for int64 representation."
        )
    return result
