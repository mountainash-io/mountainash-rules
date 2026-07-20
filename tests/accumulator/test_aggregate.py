"""Tests for the Aggregate model and AggregateOp enum."""

import pytest
from pydantic import ValidationError

from mountainash_rules import Aggregate, AggregateOp


class TestAggregateOp:
    def test_default_is_sum(self):
        assert Aggregate(column_name="margin").operation is AggregateOp.SUM

    def test_string_coerces_to_enum(self):
        assert Aggregate(column_name="margin", operation="min").operation is AggregateOp.MIN

    def test_all_members(self):
        assert {o.value for o in AggregateOp} == {"sum", "min", "max", "product"}

    def test_unknown_operation_rejected_at_construction(self):
        with pytest.raises(ValidationError):
            Aggregate(column_name="margin", operation="median")
