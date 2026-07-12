"""Hit-policy selection layer over the filter engine's survivor pipeline."""

from __future__ import annotations

import typing as t
from dataclasses import dataclass

import mountainash.expressions as ma

from mountainash_rules.constants import HitPolicy, MatchStrategy

if t.TYPE_CHECKING:
    from mountainash_rules.dimension import DimensionsMetadata


@dataclass(frozen=True)
class SelectionInfo:
    """What a RuleResult needs to re-apply policies post-hoc."""

    dimension_rule_fields: tuple[str, ...]
    priority_field: str | None
    output_fields: tuple[str, ...]
    truncated: bool
    observability: bool


class HitPolicyViolationError(ValueError):
    """A UNIQUE or ANY assertion failed over the survivor set."""

    def __init__(self, policy: HitPolicy, offending: t.Any, message: str) -> None:
        super().__init__(message)
        self.policy = policy
        self.offending = offending


def selection_info_from_metadata(
    metadata: "DimensionsMetadata | None",
    priority_field: str | None,
    observability: bool,
) -> SelectionInfo:
    fields: list[str] = []
    md_priority: str | None = None
    md_outputs: tuple[str, ...] = ()
    if metadata is not None:
        for d in metadata.dimensions:
            if d.match_strategy == MatchStrategy.RANGE:
                fields.extend([d.range_min_field, d.range_max_field])
            else:
                fields.append(d.resolved_rule_field)
        md_priority = metadata.priority_field
        md_outputs = tuple(metadata.output_fields)
    return SelectionInfo(
        dimension_rule_fields=tuple(fields),
        priority_field=priority_field or md_priority,
        output_fields=md_outputs,
        truncated=False,
        observability=observability,
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
        return [c for c in columns if c in info.output_fields]
    excluded = set(info.dimension_rule_fields) | {"rule_name"}
    if info.priority_field:
        excluded.add(info.priority_field)
    return [
        c for c in columns
        if c not in excluded and not c.startswith("__")
    ]


def check_assertions(rel: t.Any, policy: HitPolicy, info: SelectionInfo) -> None:
    """Raise HitPolicyViolationError for UNIQUE/ANY breaches. Zero survivors pass."""
    if policy == HitPolicy.UNIQUE:
        n = rel.count_rows()
        if n > 1:
            raise HitPolicyViolationError(
                policy, rel.collect(),
                f"hit_policy=unique but {n} rules survived",
            )
    elif policy == HitPolicy.ANY:
        if rel.count_rows() > 1:
            outputs = default_output_fields(rel.columns, info)
            if not outputs:
                raise ValueError(
                    "hit_policy=any requires output_fields when no metadata "
                    "is available to infer them"
                )
            distinct = (
                rel.select(*[ma.col(c) for c in outputs]).unique().count_rows()
            )
            if distinct > 1:
                raise HitPolicyViolationError(
                    policy, rel.collect(),
                    f"hit_policy=any but survivors disagree on outputs {outputs}",
                )


def apply_cardinality(rel: t.Any, policy: HitPolicy) -> t.Any:
    if policy in (HitPolicy.FIRST, HitPolicy.PRIORITY, HitPolicy.ANY):
        return rel.head(1)
    return rel
