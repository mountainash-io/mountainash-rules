"""Hit-policy selection layer over the filter engine's survivor pipeline."""

from __future__ import annotations

import typing as t
from dataclasses import dataclass

import mountainash.expressions as ma

from mountainash_rules.core.constants import HitPolicy, MatchStrategy

if t.TYPE_CHECKING:
    from mountainash_rules.core.dimension import DimensionsMetadata


@dataclass(frozen=True)
class SelectionInfo:
    """Policy provenance; truncated means possibly incomplete for re-selection."""

    dimension_rule_fields: tuple[str, ...]
    priority_field: str | None
    output_fields: tuple[str, ...]
    truncated: bool
    observability: bool
    metadata_backed: bool = False


class HitPolicyViolationError(ValueError):
    """A UNIQUE or ANY assertion failed over the survivor set."""

    def __init__(self, policy: HitPolicy, offending: t.Any, message: str) -> None:
        super().__init__(message)
        self.policy = policy
        self.offending = offending


def normalize_policy(policy: HitPolicy | str) -> HitPolicy:
    """Reject unknown policies instead of falling through to COLLECT."""
    try:
        return HitPolicy(policy)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid hit policy: {policy!r}") from None


def validate_priority_field(priority_field: str | None) -> None:
    if priority_field is not None and (
        not isinstance(priority_field, str) or not priority_field
    ):
        raise ValueError("priority_field must be a nonempty string")


def validate_output_fields(
    fields: t.Sequence[str], columns: list[str]
) -> tuple[str, ...]:
    """Validate a declared schema without silently dropping misspelled fields."""
    if any(not isinstance(field, str) or not field for field in fields):
        raise ValueError("output_fields must contain nonempty field names")
    if len(set(fields)) != len(fields):
        raise ValueError("output_fields must not contain duplicate names")
    missing = [field for field in fields if field not in columns]
    if missing:
        raise ValueError(f"output_fields not found in rules: {missing}")
    return tuple(fields)


def selection_info_from_metadata(
    metadata: "DimensionsMetadata | None",
    priority_field: str | None,
    observability: bool,
    *,
    output_fields: tuple[str, ...] = (),
) -> SelectionInfo:
    fields: list[str] = []
    md_priority: str | None = None
    md_outputs: tuple[str, ...] = ()
    if metadata is not None:
        for d in metadata.dimensions:
            if d.match_strategy == MatchStrategy.RANGE:
                fields.extend(
                    [
                        t.cast(str, d.range_min_field),
                        t.cast(str, d.range_max_field),
                    ]
                )
            elif d.match_strategy != MatchStrategy.CONTEXT_REGEX:
                fields.append(d.resolved_rule_field)
        md_priority = metadata.priority_field
        md_outputs = tuple(metadata.output_fields)
    effective_priority = priority_field if priority_field is not None else md_priority
    validate_priority_field(effective_priority)
    return SelectionInfo(
        dimension_rule_fields=tuple(fields),
        priority_field=effective_priority,
        output_fields=md_outputs if metadata is not None else output_fields,
        truncated=False,
        observability=observability,
        metadata_backed=metadata is not None,
    )


def ordering_keys(
    policy: HitPolicy, priority_field: str | None
) -> list[tuple[str, bool]]:
    """[(column, descending), ...]; always ends with __rule_index ascending."""
    if policy in (HitPolicy.FIRST, HitPolicy.RULE_ORDER):
        return [("__rule_index", False)]
    if policy == HitPolicy.PRIORITY:
        if not priority_field:
            raise ValueError("hit_policy=priority requires priority_field")
        return [
            (priority_field, True),
            ("__specificity", True),
            ("__rule_index", False),
        ]
    # COLLECT, UNIQUE, ANY share the deterministic default ordering
    return [("__specificity", True), ("__rule_index", False)]


def default_output_fields(columns: list[str], info: SelectionInfo) -> list[str]:
    if info.output_fields:
        return list(validate_output_fields(info.output_fields, columns))
    if not info.metadata_backed:
        raise ValueError(
            "hit_policy=any requires explicit output_fields without metadata"
        )
    excluded = set(info.dimension_rule_fields) | {"rule_name"}
    if info.priority_field:
        excluded.add(info.priority_field)
    return [c for c in columns if c not in excluded and not c.startswith("__")]


def check_policy_config(
    columns: list[str], policy: HitPolicy, info: SelectionInfo
) -> None:
    """Configuration errors precede scoring, even for empty candidate sets."""
    if policy == HitPolicy.PRIORITY:
        if info.priority_field is None or info.priority_field not in columns:
            raise ValueError("hit_policy=priority requires an existing priority_field")
    elif policy == HitPolicy.ANY:
        default_output_fields(columns, info)


def check_priority(rel: t.Any, policy: HitPolicy, info: SelectionInfo) -> None:
    """Only null priorities on survivors are invalid."""
    if policy == HitPolicy.PRIORITY:
        if rel.filter(ma.col(info.priority_field).is_null()).count_rows() > 0:
            raise ValueError(
                "hit_policy=priority requires non-null surviving priorities"
            )


def selection_is_truncated(
    policy: HitPolicy,
    top_n: int | None = None,
    min_specificity: int | None = None,
) -> bool:
    """Conservative loss detection without before/after materialization."""
    return (
        top_n is not None
        or min_specificity is not None
        or policy in (HitPolicy.FIRST, HitPolicy.PRIORITY, HitPolicy.ANY)
    )


def check_assertions(rel: t.Any, policy: HitPolicy, info: SelectionInfo) -> None:
    """Raise HitPolicyViolationError for UNIQUE/ANY breaches. Zero survivors pass."""
    if policy == HitPolicy.UNIQUE:
        n = rel.count_rows()
        if n > 1:
            raise HitPolicyViolationError(
                policy,
                rel.collect(),
                f"hit_policy=unique but {n} rules survived",
            )
    elif policy == HitPolicy.ANY:
        outputs = default_output_fields(rel.columns, info)
        if rel.count_rows() > 1:
            if not outputs:
                raise ValueError(
                    "hit_policy=any requires nonempty output_fields "
                    "when more than one rule survives"
                )
            distinct = rel.select(*[ma.col(c) for c in outputs]).unique().count_rows()
            if distinct > 1:
                raise HitPolicyViolationError(
                    policy,
                    rel.collect(),
                    f"hit_policy=any but survivors disagree on outputs {outputs}",
                )


def apply_cardinality(rel: t.Any, policy: HitPolicy) -> t.Any:
    if policy in (HitPolicy.FIRST, HitPolicy.PRIORITY, HitPolicy.ANY):
        return rel.head(1)
    return rel


def first_per_context(rel: t.Any) -> t.Any:
    """Select the minimum retained rank without assuming rank one survives."""
    first = rel.group_by("__context_id").agg(ma.col("__rank").min().alias("__rank"))
    return rel.join(first, on=["__context_id", "__rank"], how="inner")
