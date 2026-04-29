"""Tests for prime table and utilities."""

import pytest

from mountainash_utils_rules.primes import (
    PRIME_TABLE,
    get_prime,
    checked_multiply,
)


class TestPrimeTable:
    def test_first_primes_correct(self):
        assert PRIME_TABLE[:10] == [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]

    def test_table_has_at_least_500_entries(self):
        assert len(PRIME_TABLE) >= 500

    def test_500th_prime_is_3571(self):
        assert PRIME_TABLE[499] == 3571

    def test_all_entries_are_prime(self):
        for p in PRIME_TABLE[:100]:
            assert p >= 2
            for d in range(2, int(p**0.5) + 1):
                assert p % d != 0, f"{p} is not prime"


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
