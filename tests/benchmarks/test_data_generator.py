"""
Test data generation utilities for benchmarking the rules engine.
Creates realistic test datasets with various sizes and complexity patterns.
"""

import random
import string
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import polars as pl
from pydantic import BaseModel

from mountainash_utils_rules.constants import RuleConstants, MatchStrategy
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata

@dataclass
class BenchmarkConfig:
    """Configuration for benchmark test data generation"""
    rule_count: int = 1000
    dimension_count: int = 5
    exact_match_ratio: float = 0.4
    range_match_ratio: float = 0.3
    regex_match_ratio: float = 0.3
    unknown_value_ratio: float = 0.1
    
    # Value distribution parameters
    exact_value_cardinality: int = 10  # Number of distinct values for exact match
    range_min: int = 0
    range_max: int = 1000
    regex_complexity: str = 'medium'  # 'simple', 'medium', 'complex'

class TestDataGenerator:
    """Generate realistic test data for rules engine benchmarks"""
    
    def __init__(self, config: BenchmarkConfig = None):
        self.config = config or BenchmarkConfig()
        self.random = random.Random(42)  # Fixed seed for reproducible benchmarks
        
        # Pre-generate common values for consistency
        self.exact_values = self._generate_exact_values()
        self.regex_patterns = self._generate_regex_patterns()
    
    def _generate_exact_values(self) -> List[str]:
        """Generate pool of exact match values"""
        values = []
        
        # Add common business-like values
        categories = ['A', 'B', 'C', 'D', 'E']
        regions = ['US', 'EU', 'ASIA', 'LATAM', 'EMEA']
        types = ['PREMIUM', 'STANDARD', 'BASIC', 'ENTERPRISE']
        
        all_values = categories + regions + types
        
        # Extend to desired cardinality
        while len(all_values) < self.config.exact_value_cardinality:
            all_values.append(f"VAL_{len(all_values)}")
        
        return all_values[:self.config.exact_value_cardinality]
    
    def _generate_regex_patterns(self) -> List[str]:
        """Generate realistic regex patterns based on complexity"""
        patterns = {
            'simple': [
                r'A.*', r'B.*', r'C.*',
                r'.*_US', r'.*_EU', r'.*_ASIA',
                r'PROD_.*', r'TEST_.*', r'DEV_.*'
            ],
            'medium': [
                r'^[A-Z]{2,4}_\d+$',
                r'USER_[0-9]{4,6}',
                r'(PREMIUM|STANDARD)_.*',
                r'[A-Z]{3}_\d{2,4}_[A-Z]{2}',
                r'^\d{4}-\d{2}-\d{2}T.*'
            ],
            'complex': [
                r'^(?:PREMIUM|STANDARD|BASIC)_[A-Z]{2,4}_\d{4,8}$',
                r'^[A-Z]{2,3}_\d{4}_(?:US|EU|ASIA)_[A-Z]{2,4}$',
                r'(?i)^(prod|test|dev)_[a-z0-9]{8,16}_\d{2,4}$',
                r'^[A-Z][a-z]{2,10}_\d{4}_[A-Z]{2}_(?:HIGH|MED|LOW)$',
                r'^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d$'
            ]
        }
        
        return patterns.get(self.config.regex_complexity, patterns['medium'])
    
    def generate_rules_dataframe(self, rule_count: Optional[int] = None) -> pl.DataFrame:
        """Generate a rules DataFrame with specified characteristics"""
        count = rule_count or self.config.rule_count
        
        # Base rule data
        data = {
            'rule_name': [f'rule_{i:06d}' for i in range(count)]
        }
        
        # Generate dimensions based on ratios
        dimensions_per_type = self._calculate_dimensions_per_type()
        
        dim_idx = 1
        
        # Generate exact match dimensions
        for _ in range(dimensions_per_type['exact']):
            dim_name = f'DIM_{dim_idx}'
            data[dim_name] = self._generate_exact_dimension_values(count)
            dim_idx += 1
        
        # Generate range match dimensions
        for _ in range(dimensions_per_type['range']):
            dim_name = f'DIM_{dim_idx}'
            data[f'{dim_name}_MIN'] = self._generate_range_min_values(count)
            data[f'{dim_name}_MAX'] = self._generate_range_max_values(count, data[f'{dim_name}_MIN'])
            dim_idx += 1
        
        # Generate regex match dimensions
        for _ in range(dimensions_per_type['regex']):
            dim_name = f'DIM_{dim_idx}'
            data[dim_name] = self._generate_regex_dimension_values(count)
            dim_idx += 1
        
        return pl.DataFrame(data)
    
    def _calculate_dimensions_per_type(self) -> Dict[str, int]:
        """Calculate number of dimensions per match type based on ratios"""
        total_dims = self.config.dimension_count
        
        exact_dims = max(1, int(total_dims * self.config.exact_match_ratio))
        range_dims = max(1, int(total_dims * self.config.range_match_ratio))
        regex_dims = total_dims - exact_dims - range_dims
        
        # Ensure we have at least one of each type for comprehensive testing
        if regex_dims < 1:
            if exact_dims > 1:
                exact_dims -= 1
                regex_dims += 1
            elif range_dims > 1:
                range_dims -= 1
                regex_dims += 1
        
        return {
            'exact': exact_dims,
            'range': range_dims, 
            'regex': regex_dims
        }
    
    def _generate_exact_dimension_values(self, count: int) -> List[str]:
        """Generate exact match values with unknown ratio"""
        values = []
        unknown_count = int(count * self.config.unknown_value_ratio)
        
        for i in range(count):
            if i < unknown_count:
                values.append(RuleConstants.UNKNOWN)
            else:
                values.append(self.random.choice(self.exact_values))
        
        self.random.shuffle(values)
        return values
    
    def _generate_range_min_values(self, count: int) -> List[int]:
        """Generate range minimum values"""
        values = []
        unknown_count = int(count * self.config.unknown_value_ratio)
        
        for i in range(count):
            if i < unknown_count:
                values.append(RuleConstants.UNKNOWN_NUMERIC)
            else:
                # Generate min values in lower portion of range
                min_val = self.random.randint(
                    self.config.range_min,
                    self.config.range_min + (self.config.range_max - self.config.range_min) // 2
                )
                values.append(min_val)
        
        self.random.shuffle(values)
        return values
    
    def _generate_range_max_values(self, count: int, min_values: List[int]) -> List[int]:
        """Generate range maximum values that are >= corresponding min values"""
        values = []
        
        for min_val in min_values:
            if min_val == RuleConstants.UNKNOWN_NUMERIC:
                values.append(RuleConstants.UNKNOWN_NUMERIC)
            else:
                # Generate max value >= min value
                max_val = self.random.randint(
                    min_val + 1,
                    self.config.range_max
                )
                values.append(max_val)
        
        return values
    
    def _generate_regex_dimension_values(self, count: int) -> List[str]:
        """Generate regex pattern values"""
        values = []
        unknown_count = int(count * self.config.unknown_value_ratio)
        patterns = self.regex_patterns
        
        for i in range(count):
            if i < unknown_count:
                values.append(RuleConstants.UNKNOWN)
            else:
                values.append(self.random.choice(patterns))
        
        self.random.shuffle(values)
        return values
    
    def generate_dimension_metadata(self) -> DimensionsMetadata:
        """Generate dimension metadata corresponding to the rules DataFrame"""
        dimensions = []
        dimensions_per_type = self._calculate_dimensions_per_type()
        
        dim_idx = 1
        
        # Add exact match dimensions
        for _ in range(dimensions_per_type['exact']):
            dimensions.append(Dimension(
                dimension_name=f'DIM_{dim_idx}',
                match_strategy=MatchStrategy.EXACT,
                data_type=str
            ))
            dim_idx += 1
        
        # Add range match dimensions
        for _ in range(dimensions_per_type['range']):
            dim_name = f'DIM_{dim_idx}'
            dimensions.append(Dimension(
                dimension_name=dim_name,
                match_strategy=MatchStrategy.RANGE,
                data_type=int,
                range_min_field=f'{dim_name}_MIN',
                range_max_field=f'{dim_name}_MAX'
            ))
            dim_idx += 1
        
        # Add regex match dimensions
        for _ in range(dimensions_per_type['regex']):
            dimensions.append(Dimension(
                dimension_name=f'DIM_{dim_idx}',
                match_strategy=MatchStrategy.REGEX,
                data_type=str
            ))
            dim_idx += 1
        
        return DimensionsMetadata(dimensions=dimensions)
    
    def generate_test_context(self, selectivity: str = 'medium') -> BaseModel:
        """Generate test context with different selectivity patterns"""
        dimensions_per_type = self._calculate_dimensions_per_type()
        context_data = {}
        
        # Generate context values based on selectivity
        selectivity_params = {
            'high': 0.9,    # Most rules will match (low selectivity in filtering)
            'medium': 0.5,  # Moderate matching
            'low': 0.1      # Few rules will match (high selectivity in filtering)
        }
        
        match_probability = selectivity_params.get(selectivity, 0.5)
        
        dim_idx = 1
        
        # Exact match context values
        for _ in range(dimensions_per_type['exact']):
            if self.random.random() < match_probability:
                context_data[f'DIM_{dim_idx}'] = self.random.choice(self.exact_values)
            else:
                # Generate value unlikely to match
                context_data[f'DIM_{dim_idx}'] = f'NONMATCH_{self.random.randint(1000, 9999)}'
            dim_idx += 1
        
        # Range match context values
        for _ in range(dimensions_per_type['range']):
            if self.random.random() < match_probability:
                # Generate value likely to fall in ranges
                context_data[f'DIM_{dim_idx}'] = self.random.randint(
                    self.config.range_min + 100,
                    self.config.range_max - 100
                )
            else:
                # Generate value unlikely to match
                context_data[f'DIM_{dim_idx}'] = self.config.range_max + self.random.randint(1, 1000)
            dim_idx += 1
        
        # Regex match context values
        for _ in range(dimensions_per_type['regex']):
            if self.random.random() < match_probability:
                # Generate value that should match common patterns
                context_data[f'DIM_{dim_idx}'] = self._generate_matching_string()
            else:
                # Generate value unlikely to match patterns
                context_data[f'DIM_{dim_idx}'] = f'nomatch_{self.random.randint(1000, 9999)}'
            dim_idx += 1
        
        # Create dynamic context class
        class TestContext(BaseModel):
            pass
        
        # Add fields dynamically
        for field_name, value in context_data.items():
            setattr(TestContext, field_name, type(value))
        
        return TestContext(**context_data)
    
    def _generate_matching_string(self) -> str:
        """Generate string likely to match regex patterns"""
        patterns = [
            lambda: f"A_{self.random.randint(100, 999)}",
            lambda: f"USER_{self.random.randint(1000, 9999)}",
            lambda: f"PREMIUM_{self.random.choice(['US', 'EU', 'ASIA'])}",
            lambda: f"PROD_{self.random.randint(1000, 9999)}",
            lambda: ''.join(self.random.choices(string.ascii_uppercase, k=3)) + f"_{self.random.randint(100, 999)}"
        ]
        
        return self.random.choice(patterns)()

class BenchmarkTestCases:
    """Pre-defined test cases for consistent benchmarking"""
    
    @staticmethod
    def get_scalability_test_configs() -> List[BenchmarkConfig]:
        """Get configurations for scalability testing"""
        return [
            BenchmarkConfig(rule_count=100, dimension_count=5),
            BenchmarkConfig(rule_count=500, dimension_count=5),
            BenchmarkConfig(rule_count=1000, dimension_count=5),
            BenchmarkConfig(rule_count=5000, dimension_count=5),
            BenchmarkConfig(rule_count=10000, dimension_count=5),
            BenchmarkConfig(rule_count=25000, dimension_count=5),
        ]
    
    @staticmethod
    def get_dimension_complexity_configs() -> List[BenchmarkConfig]:
        """Get configurations for dimension complexity testing"""
        return [
            BenchmarkConfig(rule_count=5000, dimension_count=1),
            BenchmarkConfig(rule_count=5000, dimension_count=3), 
            BenchmarkConfig(rule_count=5000, dimension_count=5),
            BenchmarkConfig(rule_count=5000, dimension_count=10),
            BenchmarkConfig(rule_count=5000, dimension_count=15),
        ]
    
    @staticmethod
    def get_backend_comparison_config() -> BenchmarkConfig:
        """Get standard configuration for backend comparison"""
        return BenchmarkConfig(
            rule_count=10000,
            dimension_count=5,
            exact_match_ratio=0.4,
            range_match_ratio=0.3,
            regex_match_ratio=0.3,
            unknown_value_ratio=0.1
        )