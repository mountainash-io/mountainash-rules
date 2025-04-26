import pytest
from mountainash_utils_rules.context import ContextHelper
from mountainash_utils_rules.dimension import Dimension
from mountainash_utils_rules.constants import MatchStrategy
from pydantic import BaseModel

class ValidContext(BaseModel):
    DIM_1: str
    DIM_2: int
    DIM_3: str

class InvalidContext(BaseModel):
    DIM_1: dict
    DIM_2: list
    DIM_3: set

@pytest.fixture
def context_manager():
    return ContextHelper

@pytest.fixture
def sample_dimensions():
    return [
        Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str),
        Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.EXACT, data_type=int),
        Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.EXACT, data_type=str)
    ]

# def test_validate_context_valid(context_manager, sample_dimensions):
#     valid_context = ValidContext(DIM_1="A", DIM_2=1, DIM_3="X")
#     try:
#         context_manager.validate_context(valid_context, sample_dimensions)
#     except Exception as e:
#         pytest.fail(f"Unexpected exception: {e}")

# def test_validate_context_invalid(context_manager, sample_dimensions):
#     invalid_context = InvalidContext(DIM_1={"key": "value"}, DIM_2=[1, 2, 3], DIM_3={1, 2, 3})
#     with pytest.raises(TypeError):
#         context_manager.validate_context(invalid_context, sample_dimensions)

# def test_validate_context_missing_field(context_manager, sample_dimensions):
#     class MissingFieldContext(BaseModel):
#         DIM_1: str
#         DIM_2: int

#     missing_field_context = MissingFieldContext(DIM_1="A", DIM_2=1)
#     try:
#         context_manager.validate_context(missing_field_context, sample_dimensions)
#     except Exception as e:
#         pytest.fail(f"Unexpected exception: {e}")

# def test_validate_context_extra_field(context_manager, sample_dimensions):
#     class ExtraFieldContext(BaseModel):
#         DIM_1: str
#         DIM_2: int
#         DIM_3: str
#         EXTRA: str

#     extra_field_context = ExtraFieldContext(DIM_1="A", DIM_2=1, DIM_3="X", EXTRA="extra")
#     try:
#         context_manager.validate_context(extra_field_context, sample_dimensions)
#     except Exception as e:
#         pytest.fail(f"Unexpected exception: {e}")

# def test_validate_context_non_basemodel(context_manager, sample_dimensions):
#     non_basemodel_context = {"DIM_1": "A", "DIM_2": 1, "DIM_3": "X"}
#     with pytest.raises(ValueError):
#         context_manager.validate_context(non_basemodel_context, sample_dimensions)