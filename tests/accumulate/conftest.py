"""Bounded assertions for replacement accumulator contracts."""

import pytest


@pytest.fixture
def records():
    """Execute an existing relation; callers explicitly select ordering."""
    return lambda relation: relation.to_dicts()
