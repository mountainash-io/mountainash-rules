"""Tests for prime table and utilities."""

import pytest

from mountainash_rules.engines.accumulator.primes import (
    MAX_RULES_PER_PARTITION,
    PRIME_TABLE,
    LatticeWidthExceededError,
    _first_n_primes,
    get_prime,
    checked_multiply,
)


class TestPrimeTable:
    def test_first_primes_correct(self):
        assert PRIME_TABLE[:10] == [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]

    def test_table_size_equals_cap(self):
        # Invariant: the table is exactly the first MAX_RULES_PER_PARTITION primes.
        assert len(PRIME_TABLE) == MAX_RULES_PER_PARTITION

    def test_500th_prime_is_3571(self):
        assert PRIME_TABLE[499] == 3571

    def test_all_entries_are_prime(self):
        for p in PRIME_TABLE[:100]:
            assert p >= 2
            for d in range(2, int(p**0.5) + 1):
                assert p % d != 0, f"{p} is not prime"


class TestFirstNPrimes:
    def test_empty_for_non_positive(self):
        assert _first_n_primes(0) == []
        assert _first_n_primes(-3) == []

    def test_small_counts(self):
        assert _first_n_primes(1) == [2]
        assert _first_n_primes(5) == [2, 3, 5, 7, 11]

    def test_returns_exact_count(self):
        assert len(_first_n_primes(1000)) == 1000

    def test_boundary_nth_prime_correct(self):
        # 10000th prime is 104729 — exercises the Rosser sieve-sizing bound.
        assert _first_n_primes(10000)[-1] == 104729


class TestGetPrime:
    def test_index_zero_returns_two(self):
        assert get_prime(0) == 2

    def test_index_fourteen_returns_47(self):
        assert get_prime(14) == 47

    def test_large_index_works(self):
        assert get_prime(499) == 3571

    def test_negative_index_raises(self):
        with pytest.raises(IndexError):
            get_prime(-1)

    def test_index_beyond_cap_raises_with_constant_name(self):
        with pytest.raises(IndexError, match="MAX_RULES_PER_PARTITION"):
            get_prime(MAX_RULES_PER_PARTITION)


class TestCheckedMultiply:
    def test_small_product_succeeds(self):
        assert checked_multiply(6, 5) == 30

    def test_product_at_int64_boundary_raises(self):
        large = 2**62
        with pytest.raises(OverflowError, match="int64"):
            checked_multiply(large, 3)

    def test_product_of_first_15_primes_fits_int64(self):
        product = 1
        for i in range(15):
            product = checked_multiply(product, get_prime(i))
        assert product > 0
        assert product < 2**63

    def test_product_of_16_primes_overflows(self):
        product = 1
        for i in range(15):
            product = checked_multiply(product, get_prime(i))
        with pytest.raises(OverflowError):
            checked_multiply(product, get_prime(15))


class TestLatticeWidthExceededError:
    def test_is_an_overflow_error(self):
        assert issubclass(LatticeWidthExceededError, OverflowError)

    def test_carries_message(self):
        err = LatticeWidthExceededError("partition ('AU',) level 15")
        assert "partition" in str(err)
