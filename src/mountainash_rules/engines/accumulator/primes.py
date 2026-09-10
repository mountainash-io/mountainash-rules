"""Prime table and utilities for accumulator combination identity.

Combinations are identified by the product of their rules' primes. Two
*independent* limits bound this scheme:

* **Combination width — intrinsic, ~15.** No single combination can multiply
  more than 15 primes without exceeding int64: the primorial of the first 15
  primes is ``614_889_782_588_491_410 < 2**63``, but including the 16th prime
  overflows. This bound is inherent to int64 and enforced by
  ``checked_multiply`` / ``LatticeWidthExceededError``. It does **not** depend on
  the table size.
* **Rules per partition — policy, the table size.** Every rule needs a distinct
  prime, so a partition is capped at ``MAX_RULES_PER_PARTITION`` rules. Exceeding
  it raises from ``get_prime``. Remediation: split the partition with a
  CONTEXT_KEY dimension.

The table is simply the first ``MAX_RULES_PER_PARTITION`` primes, computed once
at import by a bounded Sieve of Eratosthenes (microseconds). There is no value in
a precomputed literal or a data file: the sieve is already instant, and a table
"up to 2**64" is impossible (~4.2e17 primes ≈ exabytes) and the wrong dimension —
we index by rule count, never by prime magnitude.
"""

from __future__ import annotations

import math

_INT64_MAX = (2**63) - 1

# Maximum rules in a single partition = the prime-table size. Each rule is
# assigned a distinct prime by position; raise this here if a partition
# legitimately needs more rules (cost is an ~instant wider sieve). The separate,
# intrinsic width-15 bound (see module docstring) is unaffected by this value.
MAX_RULES_PER_PARTITION = 10_000


def _sieve(limit: int) -> list[int]:
    """All primes up to ``limit`` (Sieve of Eratosthenes)."""
    is_prime = [True] * (limit + 1)
    is_prime[0] = is_prime[1] = False
    for i in range(2, int(limit**0.5) + 1):
        if is_prime[i]:
            for j in range(i * i, limit + 1, i):
                is_prime[j] = False
    return [i for i, v in enumerate(is_prime) if v]


def _first_n_primes(n: int) -> list[int]:
    """The first ``n`` primes.

    Sizes the sieve from the prime-counting upper bound (Rosser:
    ``p_n < n(ln n + ln ln n)`` for ``n >= 6``) and widens defensively if the
    bound was ever too tight.
    """
    if n < 1:
        return []
    limit = 15 if n < 6 else int(n * (math.log(n) + math.log(math.log(n)))) + 3
    primes = _sieve(limit)
    while len(primes) < n:  # defensive; the Rosser bound is not exceeded in practice
        limit *= 2
        primes = _sieve(limit)
    return primes[:n]


PRIME_TABLE: list[int] = _first_n_primes(MAX_RULES_PER_PARTITION)


def get_prime(index: int) -> int:
    """Return the prime at the given 0-based index (a rule's position)."""
    if index < 0:
        raise IndexError(f"Prime index must be non-negative, got {index}")
    if index >= len(PRIME_TABLE):
        raise IndexError(
            f"Prime index {index} exceeds the prime table "
            f"(MAX_RULES_PER_PARTITION={MAX_RULES_PER_PARTITION}). "
            f"Partition has too many rules; split it with a CONTEXT_KEY dimension."
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


class LatticeWidthExceededError(OverflowError):
    """A partition contains a compatible rule clique too large for int64 prime products.

    Raised by the accumulator build phase when combining one more rule would
    overflow the int64 ``__prime_product`` combination identity. Remediation:
    split the partition with a CONTEXT_KEY dimension, or reduce the size of
    the mutually compatible rule clique.
    """
