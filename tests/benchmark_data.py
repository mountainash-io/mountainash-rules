"""Synthetic benchmark data generator for mountainash-utils-rules performance tests.

Provides deterministic (seeded RNG) rule sets and context models for use in
benchmarks and integration tests.
"""

from __future__ import annotations

import random
import typing as t

from pydantic import BaseModel, create_model

from mountainash_rules.core.constants import (
    UNKNOWN,
    UNKNOWN_NUMERIC,
    MatchStrategy,
)
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engines.filter.engine import ExpressionRulesEngine

# ---------------------------------------------------------------------------
# Strategy mix defaults
# ---------------------------------------------------------------------------

_DEFAULT_STRATEGY_MIX: dict[MatchStrategy, float] = {
    MatchStrategy.EXACT: 0.35,
    MatchStrategy.RANGE: 0.25,
    MatchStrategy.CONTEXT_REGEX: 0.10,
    MatchStrategy.PREFIX: 0.10,
    MatchStrategy.GREATER_THAN: 0.05,
    MatchStrategy.LESS_THAN: 0.05,
    MatchStrategy.SET_MEMBERSHIP: 0.05,
    MatchStrategy.CONTAINS: 0.05,
}

# Safe REGEX patterns — each produces valid matches like "AB_12345"
_REGEX_PATTERNS: list[str] = [
    r"^[A-Z]{2}_\d+",
    r"^[A-Z]{3}_\d{4}",
    r"^\d{3}_[A-Z]+",
    r"^[A-Z]+\d{2,4}$",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def assign_strategies(
    dim_count: int,
    strategy_mix: dict[MatchStrategy, float] | None = None,
) -> list[MatchStrategy]:
    """Assign match strategies to *dim_count* dimensions.

    Strategies are distributed proportionally using the supplied (or default)
    mix, filling remainder slots with EXACT so the result list always has
    exactly *dim_count* entries.  The output is deterministic given the inputs.

    Args:
        dim_count: Number of dimensions to assign strategies for.
        strategy_mix: Optional override mapping MatchStrategy → weight.

    Returns:
        List of MatchStrategy values, length == dim_count.
    """
    mix = strategy_mix if strategy_mix is not None else _DEFAULT_STRATEGY_MIX

    # Normalise weights
    total = sum(mix.values())
    normalised = {s: w / total for s, w in mix.items()}

    strategies: list[MatchStrategy] = []
    for strategy, weight in normalised.items():
        count = round(dim_count * weight)
        strategies.extend([strategy] * count)

    # Truncate or pad with EXACT to reach exactly dim_count
    if len(strategies) > dim_count:
        strategies = strategies[:dim_count]
    while len(strategies) < dim_count:
        strategies.append(MatchStrategy.EXACT)

    return strategies


def generate_rules(
    rule_count: int,
    dim_count: int,
    strategy_mix: dict[MatchStrategy, float] | None = None,
    unknown_density: float = 0.15,
    seed: int = 42,
) -> tuple[dict[str, list], DimensionsMetadata]:
    """Generate a synthetic rule set.

    Args:
        rule_count: Number of rules to generate.
        dim_count: Number of dimensions per rule.
        strategy_mix: Optional strategy weight override.
        unknown_density: Fraction of rule cells to fill with sentinel unknowns.
        seed: RNG seed for determinism.

    Returns:
        Tuple of (column_dict, DimensionsMetadata). column_dict always contains
        the "rule_name" column plus whatever dimension columns are required by
        the chosen strategies.
    """
    rng = random.Random(seed)
    strategies = assign_strategies(dim_count, strategy_mix)

    columns: dict[str, list] = {
        "rule_name": [f"rule_{i:04d}" for i in range(rule_count)]
    }
    dimensions: list[Dimension] = []

    for k, strategy in enumerate(strategies):
        dim_name = f"DIM_{k}"

        if strategy == MatchStrategy.RANGE:
            mins, maxs = _gen_range_column(rng, rule_count, unknown_density)
            columns[f"{dim_name}_MIN"] = mins
            columns[f"{dim_name}_MAX"] = maxs
            dimensions.append(
                Dimension(
                    dimension_name=dim_name,
                    match_strategy=MatchStrategy.RANGE,
                    data_type=int,
                    range_min_field=f"{dim_name}_MIN",
                    range_max_field=f"{dim_name}_MAX",
                )
            )

        elif strategy in (MatchStrategy.EXACT, MatchStrategy.NOT_EQUAL):
            pool_size = max(5, rule_count // 3)
            pool = [f"VAL_{i:04d}" for i in range(pool_size)]
            values = _gen_string_column(rng, rule_count, pool, unknown_density)
            columns[dim_name] = values
            dimensions.append(
                Dimension(
                    dimension_name=dim_name,
                    match_strategy=strategy,
                    data_type=str,
                )
            )

        elif strategy == MatchStrategy.GREATER_THAN:
            values = _gen_numeric_column(rng, rule_count, unknown_density)
            columns[dim_name] = values
            dimensions.append(
                Dimension(
                    dimension_name=dim_name,
                    match_strategy=MatchStrategy.GREATER_THAN,
                    data_type=int,
                )
            )

        elif strategy == MatchStrategy.LESS_THAN:
            values = _gen_numeric_column(rng, rule_count, unknown_density)
            columns[dim_name] = values
            dimensions.append(
                Dimension(
                    dimension_name=dim_name,
                    match_strategy=MatchStrategy.LESS_THAN,
                    data_type=int,
                )
            )

        elif strategy == MatchStrategy.PREFIX:
            shared_prefixes = [f"PFX_{i:03d}" for i in range(max(3, rule_count // 5))]
            values: list[t.Any] = []
            for _ in range(rule_count):
                if rng.random() < unknown_density:
                    values.append(UNKNOWN)
                else:
                    pfx = rng.choice(shared_prefixes)
                    suffix_num = rng.randint(0, 999)
                    values.append(f"{pfx}_{suffix_num:03d}")
            columns[dim_name] = values
            dimensions.append(
                Dimension(
                    dimension_name=dim_name,
                    match_strategy=MatchStrategy.PREFIX,
                    data_type=str,
                )
            )

        elif strategy == MatchStrategy.SUFFIX:
            shared_suffixes = [f"SFX_{i:03d}" for i in range(max(3, rule_count // 5))]
            values = []
            for _ in range(rule_count):
                if rng.random() < unknown_density:
                    values.append(UNKNOWN)
                else:
                    sfx = rng.choice(shared_suffixes)
                    prefix_num = rng.randint(0, 999)
                    values.append(f"{prefix_num:03d}_{sfx}")
            columns[dim_name] = values
            dimensions.append(
                Dimension(
                    dimension_name=dim_name,
                    match_strategy=MatchStrategy.SUFFIX,
                    data_type=str,
                )
            )

        elif strategy == MatchStrategy.CONTAINS:
            shared_subs = [f"SUB{i:02d}" for i in range(max(3, rule_count // 5))]
            values = []
            for _ in range(rule_count):
                if rng.random() < unknown_density:
                    values.append(UNKNOWN)
                else:
                    sub = rng.choice(shared_subs)
                    pre = rng.choice("abcdefghijklmnopqrstuvwxyz")
                    post = rng.choice("abcdefghijklmnopqrstuvwxyz")
                    values.append(f"{pre}{sub}{post}")
            columns[dim_name] = values
            dimensions.append(
                Dimension(
                    dimension_name=dim_name,
                    match_strategy=MatchStrategy.CONTAINS,
                    data_type=str,
                )
            )

        elif strategy == MatchStrategy.CONTEXT_REGEX:
            # Pick one pattern for this dimension and store per-rule values that
            # the pattern can be validated against in the rule column.  The
            # Dimension validator requires a non-empty regex_pattern literal.
            pattern = rng.choice(_REGEX_PATTERNS)
            # Store the pattern as every rule's column value (used by compiler
            # to match against the context value).
            values = []
            for _ in range(rule_count):
                if rng.random() < unknown_density:
                    values.append(UNKNOWN)
                else:
                    values.append(pattern)
            columns[dim_name] = values
            dimensions.append(
                Dimension(
                    dimension_name=dim_name,
                    match_strategy=MatchStrategy.CONTEXT_REGEX,
                    data_type=str,
                    regex_pattern=pattern,
                )
            )

        elif strategy in (MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION):
            pool_size = max(8, rule_count // 4)
            pool = [f"ITEM_{i:03d}" for i in range(pool_size)]
            values = []
            for _ in range(rule_count):
                if rng.random() < unknown_density:
                    values.append(None)
                else:
                    n = rng.randint(2, 5)
                    values.append(rng.sample(pool, min(n, len(pool))))
            columns[dim_name] = values
            dimensions.append(
                Dimension(
                    dimension_name=dim_name,
                    match_strategy=strategy,
                    data_type=str,
                )
            )

        else:
            # Fallback: treat as EXACT with string values
            pool = [f"VAL_{i:04d}" for i in range(max(5, rule_count // 3))]
            values = _gen_string_column(rng, rule_count, pool, unknown_density)
            columns[dim_name] = values
            dimensions.append(
                Dimension(
                    dimension_name=dim_name,
                    match_strategy=strategy,
                    data_type=str,
                )
            )

    metadata = DimensionsMetadata(dimensions=dimensions)
    return columns, metadata


def generate_context(
    metadata: DimensionsMetadata,
    rules_dict: dict[str, list],
    hit_rate: float = 0.15,
    seed: int = 42,
) -> BaseModel:
    """Generate a synthetic context that will hit approximately *hit_rate* of rules.

    Uses ``pydantic.create_model`` to build a dynamic BaseModel with fields
    matching the dimension names.

    Args:
        metadata: DimensionsMetadata describing the dimensions.
        rules_dict: The column dictionary from ``generate_rules``.
        hit_rate: Fraction of rules the context should approximately match.
        seed: RNG seed for determinism.

    Returns:
        A Pydantic BaseModel instance with one field per dimension.
    """
    rng = random.Random(seed)
    field_defs: dict[str, tuple[type, t.Any]] = {}
    field_values: dict[str, t.Any] = {}

    for dim in metadata.dimensions:
        name = dim.dimension_name
        strategy = dim.match_strategy

        if strategy == MatchStrategy.RANGE:
            min_col = dim.range_min_field or f"{name}_MIN"
            max_col = dim.range_max_field or f"{name}_MAX"
            valid_mins = [
                v for v in rules_dict.get(min_col, []) if v != UNKNOWN_NUMERIC
            ]
            valid_maxs = [
                v for v in rules_dict.get(max_col, []) if v != UNKNOWN_NUMERIC
            ]
            if valid_mins and valid_maxs:
                idx = rng.randrange(len(valid_mins))
                lo = valid_mins[idx]
                hi = valid_maxs[idx] if idx < len(valid_maxs) else lo + 50
                value = rng.randint(lo, max(lo, hi))
            else:
                value = rng.randint(0, 1000)
            field_defs[name] = (int, ...)
            field_values[name] = value

        elif strategy in (MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN):
            thresholds = [
                v for v in rules_dict.get(name, []) if v != UNKNOWN_NUMERIC
            ]
            if thresholds:
                median = sorted(thresholds)[len(thresholds) // 2]
                # For GT context should be > threshold; for LT context should be < threshold
                if strategy == MatchStrategy.GREATER_THAN:
                    value = median + rng.randint(1, 50)
                else:
                    value = median - rng.randint(1, 50)
            else:
                value = 500
            field_defs[name] = (int, ...)
            field_values[name] = value

        elif strategy == MatchStrategy.CONTEXT_REGEX:
            # Generate a value that matches the pattern; use a safe template
            # matching patterns like "^[A-Z]{2}_\d+" or "^[A-Z]{3}_\d{4}"
            letters2 = "".join(rng.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=2))
            digits = "".join(rng.choices("0123456789", k=5))
            value = f"{letters2}_{digits}"
            field_defs[name] = (str, ...)
            field_values[name] = value

        elif strategy == MatchStrategy.PREFIX:
            raw_values = [v for v in rules_dict.get(name, []) if v != UNKNOWN]
            if raw_values:
                sample = rng.choice(raw_values)
                # Context value that starts with the rule value (starts-with match)
                value = sample + "_ctx"
            else:
                value = "PFX_001_ctx"
            field_defs[name] = (str, ...)
            field_values[name] = value

        elif strategy == MatchStrategy.SUFFIX:
            raw_values = [v for v in rules_dict.get(name, []) if v != UNKNOWN]
            if raw_values:
                sample = rng.choice(raw_values)
                # Context value that ends with the rule value (ends-with match)
                value = "ctx_" + sample
            else:
                value = "ctx_SFX_001"
            field_defs[name] = (str, ...)
            field_values[name] = value

        elif strategy == MatchStrategy.CONTAINS:
            raw_values = [v for v in rules_dict.get(name, []) if v != UNKNOWN]
            if raw_values:
                sample = rng.choice(raw_values)
                value = "x" + sample + "y"
            else:
                value = "xSUB01y"
            field_defs[name] = (str, ...)
            field_values[name] = value

        elif strategy in (MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION):
            raw_lists = [v for v in rules_dict.get(name, []) if v is not None]
            if raw_lists:
                chosen_list = rng.choice(raw_lists)
                if chosen_list:
                    value = rng.choice(chosen_list)
                else:
                    value = "ITEM_000"
            else:
                value = "ITEM_000"
            field_defs[name] = (str, ...)
            field_values[name] = value

        else:
            # EXACT / NOT_EQUAL and fallbacks
            raw_values = [v for v in rules_dict.get(name, []) if v != UNKNOWN]
            if raw_values:
                value = rng.choice(raw_values)
            else:
                value = "VAL_0000"
            field_defs[name] = (str, ...)
            field_values[name] = value

    DynamicContext = create_model("DynamicContext", **{k: v for k, v in field_defs.items()})  # type: ignore[call-overload]
    return DynamicContext(**field_values)


def build_engine(
    rules_dict: dict[str, list],
    metadata: DimensionsMetadata,
    backend_name: str,
) -> ExpressionRulesEngine:
    """Construct an ExpressionRulesEngine from a rules dict and metadata.

    Args:
        rules_dict: Column dictionary produced by ``generate_rules``.
        metadata: DimensionsMetadata produced by ``generate_rules``.
        backend_name: One of the 7 backend strings accepted by conftest.build_backend_df.

    Returns:
        A ready-to-use ExpressionRulesEngine.
    """
    # Import build_backend_df from conftest, resolving path at runtime so this
    # module can be imported from anywhere.
    from tests.conftest import build_backend_df  # noqa: PLC0415

    df = build_backend_df(backend_name, rules_dict, table_name="rules")
    return ExpressionRulesEngine(rules=df, dimension_metadata=metadata)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _gen_range_column(
    rng: random.Random,
    n: int,
    unknown_density: float,
) -> tuple[list[int], list[int]]:
    mins: list[int] = []
    maxs: list[int] = []
    for _ in range(n):
        if rng.random() < unknown_density:
            mins.append(UNKNOWN_NUMERIC)
            maxs.append(UNKNOWN_NUMERIC)
        else:
            lo = rng.randint(0, 890)
            hi = lo + rng.randint(10, 100)
            mins.append(lo)
            maxs.append(hi)
    return mins, maxs


def _gen_string_column(
    rng: random.Random,
    n: int,
    pool: list[str],
    unknown_density: float,
) -> list[str]:
    result: list[str] = []
    for _ in range(n):
        if rng.random() < unknown_density:
            result.append(UNKNOWN)
        else:
            result.append(rng.choice(pool))
    return result


def _gen_numeric_column(
    rng: random.Random,
    n: int,
    unknown_density: float,
) -> list[int]:
    result: list[int] = []
    for _ in range(n):
        if rng.random() < unknown_density:
            result.append(UNKNOWN_NUMERIC)
        else:
            result.append(rng.randint(100, 900))
    return result
