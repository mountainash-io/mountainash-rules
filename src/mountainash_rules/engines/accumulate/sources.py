"""Owned authored sources and native state/contribution admission."""

from __future__ import annotations

import typing as t
import uuid
from dataclasses import dataclass, field as dataclass_field
from types import MappingProxyType

import mountainash.expressions as ma
from mountainash import DecimalDtype, MountainashDtype, materialization_scope
from mountainash.relations import Relation, concat

from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.compiler import rule_binding_expressions, rule_binding_names, same_scalar_type
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import DimensionsMetadata
from mountainash_rules.core.numeric import reject_invalid
from mountainash_rules.engines.accumulate.domain import (
    FieldRestriction,
    Interval,
    ValueConstraint,
    filter_feasible_pairs,
    lower_domain,
)
from mountainash_rules.engines.accumulate.errors import UnsupportedDomainError


SOURCE_CHECK_NAMES = (
    "source_identity", "duplicate_identity", "wildcard_consistency",
    "predicate_admission", "unsupported_strategies",
    "source_contradictions", "source_overlaps", "contribution_admission",
)


def _native_timezone(dtype: t.Any) -> str | None:
    """Return a temporal dtype's timezone, including a list item dtype."""
    item_dtype = getattr(dtype, "inner", None)
    if item_dtype is not None:
        dtype = item_dtype
    return getattr(dtype, "time_zone", None)


def _utc_expression(expression: t.Any, source_timezone: str | None):
    """Normalize aware source values only when their native zone is non-UTC."""
    if source_timezone is None or source_timezone == "UTC":
        return expression
    return expression.dt.to_timezone("UTC")


def _source_rule_bindings(
    rel: Relation, dimensions: t.Sequence[t.Any],
) -> tuple[Relation, dict[str, t.Any]]:
    """Apply shared admission bindings, then normalize accumulator timestamps."""
    bindings, invalid = rule_binding_expressions(rel, dimensions)
    normalized = rel.with_columns(*bindings) if bindings else rel
    temporal = [
        dimension for dimension in dimensions
        if dimension.data_type is DataType.DATETIME
        and dimension.match_strategy is not MatchStrategy.CONTEXT_REGEX
    ]
    if not temporal:
        return normalized, invalid
    native_schema = rel.filter(ma.lit(False)).to_polars().schema
    updates = []
    for dimension in temporal:
        if dimension.match_strategy is MatchStrategy.RANGE:
            operands = [
                (dimension.range_min_field, rule_binding_names(dimension, "min")[0]),
                (dimension.range_max_field, rule_binding_names(dimension, "max")[0]),
            ]
        else:
            operands = [(dimension.resolved_rule_field, rule_binding_names(dimension)[0])]
        zones = [_native_timezone(native_schema[field]) for field, _ in operands]
        awareness = {zone is not None for zone in zones}
        if len(awareness) > 1:
            raise ValueError(
                f"Datetime operands for {dimension.dimension_name!r} mix naive and aware values"
            )
        if dimension.match_strategy in (
            MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION,
        ):
            field, binding = operands[0]
            zone = zones[0]
            if zone is not None and zone != "UTC":
                updates.append(
                    ma.col(binding).list.agg(
                        ma.col(field).dt.to_timezone("UTC")
                    ).alias(binding)
                )
        else:
            updates.extend(
                _utc_expression(ma.col(binding), zone).alias(binding)
                for (_field, binding), zone in zip(operands, zones)
            )
    if updates:
        normalized = normalized.with_columns(*updates)
    return normalized, invalid

@dataclass(frozen=True, slots=True)
class DependencyReference:
    """An owned checked relation, or metadata for an unavailable historical one."""

    relation: Relation | None
    keys: tuple[str, ...]
    schema: tuple[tuple[str, t.Any], ...]
    version_id: uuid.UUID
    capture_id: uuid.UUID = dataclass_field(default_factory=uuid.uuid4)
    table_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "keys", tuple(self.keys))
        object.__setattr__(self, "schema", tuple(tuple(item) for item in self.schema))


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Factual result, or ``not_run`` when a check's prerequisite is unavailable."""

    name: str
    dependency_groups: tuple[str, ...]
    outcome: str
    finding_count: int
    emitted_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "dependency_groups", tuple(self.dependency_groups))


@dataclass(frozen=True, slots=True)
class ReportBinding:
    """Owned source dependencies and an isolated effective interpretation."""

    semantic_version: str
    _metadata_json: str
    dependencies: t.Mapping[str, tuple[DependencyReference, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "dependencies",
            MappingProxyType({name: tuple(refs) for name, refs in self.dependencies.items()}),
        )

    @property
    def metadata(self) -> DimensionsMetadata:
        return DimensionsMetadata.model_validate_json(self._metadata_json)


@dataclass(frozen=True, slots=True)
class SourceReport:
    report_id: uuid.UUID
    binding: ReportBinding
    checks: tuple[CheckResult, ...]
    findings: Relation
    subjects: Relation
    witnesses: t.Mapping[str, Relation]

    def __post_init__(self) -> None:
        object.__setattr__(self, "checks", tuple(self.checks))
        object.__setattr__(self, "witnesses", MappingProxyType(dict(self.witnesses)))



def check_sources(rules: Relation, metadata: DimensionsMetadata) -> SourceReport:
    """Run explicit source checks against one owned input snapshot."""
    if not isinstance(rules, Relation):
        raise TypeError("rules must be a Mountainash Relation")
    with materialization_scope() as owner:
        snapshot = owner.snapshot(rules)
        return _check_sources_owned(snapshot, metadata, owner)


def _check_sources_owned(rules: Relation, metadata: DimensionsMetadata, owner: t.Any,) -> SourceReport:
    """Build a source report from the check operation's already-owned snapshot."""
    frozen_metadata = metadata.model_dump_json()
    effective = DimensionsMetadata.model_validate_json(frozen_metadata)
    scopes = effective.outcome_scopes or {"default": effective.aggregates}
    raw = rules
    missing_identity = (
        effective.uuid_field is None or effective.uuid_field not in raw.columns
        or (effective.partition_field is not None and effective.partition_field not in raw.columns)
        or (effective.outcome_scope_field is not None and effective.outcome_scope_field not in raw.columns)
    )
    missing_scope = (
        effective.outcome_scope_field is not None
        and effective.outcome_scope_field not in raw.columns
    )
    not_run: set[str] = set()
    if missing_identity:
        not_run.update(("duplicate_identity", "source_contradictions", "source_overlaps"))
    if missing_scope:
        not_run.add("contribution_admission")
    partition = (
        ma.col(effective.partition_field)
        if effective.partition_field is not None and effective.partition_field in raw.columns
        else ma.lit("default")
    )
    scope_value = (
        ma.col(effective.outcome_scope_field)
        if effective.outcome_scope_field is not None and effective.outcome_scope_field in raw.columns
        else ma.lit("default")
    )
    source_uuid = (
        ma.col(effective.uuid_field)
        if effective.uuid_field is not None and effective.uuid_field in raw.columns
        else ma.lit(None, dtype=MountainashDtype.STRING)
    )
    uuid_dtype = (
        raw.schema[effective.uuid_field]
        if effective.uuid_field is not None and effective.uuid_field in raw.columns
        else MountainashDtype.STRING
    )
    key = raw.select(
        partition.alias("partition"), scope_value.alias("scope"),
        source_uuid.alias("source_uuid"),
    )
    # A schema finding describes the relation, even when it contains no rows.
    schema_key = raw.group_by().agg(ma.count_records().alias("__source_count")).select(
        ma.lit(
            None if effective.partition_field else "default",
            dtype=(
                raw.schema[effective.partition_field]
                if effective.partition_field is not None and effective.partition_field in raw.columns
                else MountainashDtype.STRING
            ),
        ).alias("partition"),
        ma.lit(None if effective.outcome_scope_field else "default", dtype=MountainashDtype.STRING).alias("scope"),
        ma.lit(None, dtype=uuid_dtype).alias("source_uuid"),
    )
    diagnostic_key = schema_key if missing_identity else key
    diagnostics: list[Relation] = []
    check_relations: dict[str, list[Relation]] = {
        name: [] for name in SOURCE_CHECK_NAMES
    }
    boundary_facts: dict[str, Relation] = {}

    def emit(
        source: Relation, kind: t.Any, severity: str, *,
        left_uuid: str = "source_uuid", right_uuid: str | None = None,
        field: str | None = None,
    ) -> Relation:
        left = ma.lit(None, dtype=uuid_dtype) if missing_identity else ma.col(left_uuid)
        right = ma.col(right_uuid) if right_uuid is not None else left
        if missing_identity:
            right = left
        return source.select(
            ma.col("partition"), ma.col("scope"),
            ma.lit(severity).alias("severity"), kind.alias("kind") if hasattr(kind, "alias") else ma.lit(kind).alias("kind"),
            left.alias("__left_uuid"), right.alias("__right_uuid"),
            ma.lit(right_uuid is not None).alias("__has_right"),
            ma.lit(field, dtype=MountainashDtype.STRING).alias("field"),
        )

    invalid_identity = (
        schema_key
        if missing_identity
        else key.filter(
            ma.col("partition").is_null() | ma.col("scope").is_null()
            | ma.col("source_uuid").is_null() | ~ma.col("scope").is_in(list(scopes))
        )
    )
    identity_findings = emit(invalid_identity, "invalid_value", "error")
    diagnostics.append(identity_findings)
    check_relations["source_identity"].append(identity_findings)

    if not missing_identity:
        duplicates = key.group_by(*KEYS).agg(ma.count_records().alias("__count")).filter(
            ma.col("__count").gt(1)
        ).select(*KEYS)
        duplicate_findings = emit(duplicates, "duplicate_identity", "error")
        diagnostics.append(duplicate_findings)
        check_relations["duplicate_identity"].append(duplicate_findings)

    aliases: dict[str, list[t.Any]] = {}
    for dimension in effective.dimensions:
        aliases.setdefault(dimension.resolved_context_field, []).append(dimension)
    for dimensions in aliases.values():
        markers = [tuple((type(value), value) for value in d.wildcard_values) for d in dimensions]
        if any(marker_set != markers[0] for marker_set in markers[1:]):
            alias_findings = emit(diagnostic_key, "inconsistent_wildcard", "error")
            diagnostics.append(alias_findings)
            check_relations["wildcard_consistency"].append(alias_findings)

    if any(dimension.match_strategy not in _SUPPORTED for dimension in effective.dimensions):
        unsupported_findings = emit(diagnostic_key, "unsupported_strategy", "error")
        diagnostics.append(unsupported_findings)
        check_relations["unsupported_strategies"].append(unsupported_findings)

    declared_domains = ([effective.domain] if effective.domain is not None else []) + list(
        effective.context_domains
    )
    for domain in declared_domains:
        try:
            lower_domain(domain, effective.domain_nodes)
        except UnsupportedDomainError:
            unsupported_findings = emit(diagnostic_key, "unsupported_domain", "error")
            diagnostics.append(unsupported_findings)
            check_relations["unsupported_strategies"].append(unsupported_findings)
        except ValueError:
            invalid_domain = emit(diagnostic_key, "invalid_value", "error")
            diagnostics.append(invalid_domain)
            check_relations["predicate_admission"].append(invalid_domain)

    binding_schema_invalid = False
    try:
        normalized, malformed = _source_rule_bindings(raw, effective.dimensions)
    except (ValueError, TypeError):
        normalized, malformed = raw, {}
        binding_schema_invalid = True
    invalid_predicate = ma.lit(False)
    for condition in malformed.values():
        invalid_predicate = invalid_predicate | condition.fill_null(False)
    if malformed or binding_schema_invalid:
        bad_rules = raw.with_columns(
            partition.alias("partition"), scope_value.alias("scope"),
            source_uuid.alias("source_uuid"),
        ).filter(invalid_predicate if malformed else ma.lit(True))
        invalid_findings = emit(bad_rules, "invalid_value", "error")
        diagnostics.append(invalid_findings)
        check_relations["predicate_admission"].append(invalid_findings)
    if not missing_identity:
        analysis_dimensions = (
            []
            if binding_schema_invalid
            or any(dimension.match_strategy not in _SUPPORTED for dimension in effective.dimensions)
            else effective.dimensions
        )
        row_id = "__source_row_id"
        while row_id in normalized.columns:
            row_id = f"_{row_id}"
        indexed = owner.capture(normalized.with_row_index(name=row_id).with_columns(
            partition.alias("partition"), scope_value.alias("scope"),
            source_uuid.alias("source_uuid"),
        ))[0]
        pair_key = "__source_pair_id"
        while pair_key in indexed.columns:
            pair_key = f"_{pair_key}"
    
        def binding_names(side: str) -> list[str]:
            names: list[str] = []
            for dimension in analysis_dimensions:
                if dimension.match_strategy is MatchStrategy.CONTEXT_REGEX:
                    continue
                endpoints = ("min", "max") if dimension.match_strategy is MatchStrategy.RANGE else (None,)
                for endpoint in endpoints:
                    value_name, state_name = rule_binding_names(dimension, endpoint)
                    names.extend((f"__{side}_{value_name}", f"__{side}_{state_name}"))
            return names
    
        def binding_columns(side: str) -> list[t.Any]:
            return [
                ma.col(name.removeprefix(f"__{side}_")).alias(name)
                for name in binding_names(side)
            ]
    
        domain_fields = {
            declaration.name: declaration
            for declaration in (effective.domain.fields if effective.domain is not None else ())
        }
        source_schema = indexed.filter(ma.lit(False)).to_polars().schema
    
        def source_timezone(dimension: t.Any) -> str | None:
            if dimension.data_type is not DataType.DATETIME:
                return None
            if dimension.match_strategy is MatchStrategy.RANGE:
                field = dimension.range_min_field
            else:
                field = dimension.resolved_rule_field
            native = source_schema[field]
            if hasattr(native, "time_zone"):
                return "naive" if native.time_zone is None else "utc"
            inner = getattr(native, "inner", None)
            if inner is not None and hasattr(inner, "time_zone"):
                return "naive" if inner.time_zone is None else "utc"
            raise ValueError(f"Cannot determine source timezone for {field!r}")
    
    
        def make_restrictions(
            side: str, only: t.Any | None = None,
        ) -> dict[str, FieldRestriction]:
            intervals: dict[str, list[Interval]] = {}
            values: dict[str, list[ValueConstraint]] = {}
            descriptors: dict[str, FieldRestriction] = {}
            for dimension in analysis_dimensions:
                if only is not None and dimension is not only:
                    continue
                strategy = dimension.match_strategy
                if strategy is MatchStrategy.CONTEXT_REGEX:
                    continue
                field_name = dimension.resolved_context_field
                domain_field = domain_fields.get(field_name) or domain_fields.get(
                    dimension.dimension_name
                )
                descriptors.setdefault(field_name, FieldRestriction(
                    data_type=dimension.data_type,
                    numeric_dtype=dimension.numeric_dtype or (
                        domain_field.numeric_dtype if domain_field is not None else None
                    ),
                    timezone=(
                        domain_field.timezone if domain_field is not None
                        else source_timezone(dimension)
                    ),
                    rounding=dimension.rounding or (
                        domain_field.rounding if domain_field is not None else None
                    ),
                ))
                if strategy is MatchStrategy.RANGE:
                    low_name, low_state = rule_binding_names(dimension, "min")
                    high_name, high_state = rule_binding_names(dimension, "max")
                    intervals.setdefault(field_name, []).append(Interval(
                        ma.col(f"__{side}_{low_name}"),
                        ma.col(f"__{side}_{high_name}"),
                        ma.lit(dimension.range_min_inclusive),
                        ma.lit(dimension.range_max_inclusive),
                        ma.col(f"__{side}_{low_state}"),
                        ma.col(f"__{side}_{high_state}"),
                    ))
                elif strategy in (MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN):
                    value_name, state_name = rule_binding_names(dimension)
                    bound = ma.col(f"__{side}_{value_name}")
                    inactive = ma.col(f"__{side}_{state_name}")
                    if strategy is MatchStrategy.GREATER_THAN:
                        interval = Interval(
                            bound, bound, ma.lit(False), ma.lit(False), inactive, ma.lit(True),
                        )
                    else:
                        interval = Interval(
                            bound, bound, ma.lit(False), ma.lit(False), ma.lit(True), inactive,
                        )
                    intervals.setdefault(field_name, []).append(interval)
                elif strategy in (
                    MatchStrategy.EXACT, MatchStrategy.EXACT_KEY, MatchStrategy.NOT_EQUAL,
                    MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION,
                ):
                    value_name, state_name = rule_binding_names(dimension)
                    values.setdefault(field_name, []).append(ValueConstraint(
                        values=ma.col(f"__{side}_{value_name}"),
                        is_list=strategy in (
                            MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION,
                        ),
                        allowed=strategy is not MatchStrategy.NOT_EQUAL
                        and strategy is not MatchStrategy.SET_EXCLUSION,
                        inactive=ma.col(f"__{side}_{state_name}"),
                    ))
            return {
                name: FieldRestriction(
                    data_type=descriptor.data_type,
                    intervals=tuple(intervals.get(name, ())),
                    values=tuple(values.get(name, ())),
                    numeric_dtype=descriptor.numeric_dtype,
                    timezone=descriptor.timezone,
                    rounding=descriptor.rounding,
                )
                for name, descriptor in descriptors.items()
            }
    
        def self_pairs() -> Relation:
            columns: list[t.Any] = [
                ma.col("partition"), ma.col("scope"),
                ma.col("source_uuid").alias("__left_uuid"),
                ma.col("source_uuid").alias("__right_uuid"),
                ma.col(row_id).alias("__left_source_row"),
                ma.col(row_id).alias("__right_source_row"),
            ]
            columns.extend(binding_columns("left"))
            columns.extend(binding_columns("right"))
            return owner.capture(indexed.select(*columns).with_row_index(name=pair_key))[0]
    
        def distinct_pairs() -> Relation:
            left = indexed.select(
                ma.col("partition").alias("__left_partition"),
                ma.col("scope").alias("__left_scope"),
                ma.col("source_uuid").alias("__left_uuid"),
                ma.col(row_id).alias("__left_source_row"),
                *binding_columns("left"),
            )
            right = indexed.select(
                ma.col("partition").alias("__right_partition"),
                ma.col("scope").alias("__right_scope"),
                ma.col("source_uuid").alias("__right_uuid"),
                ma.col(row_id).alias("__right_source_row"),
                *binding_columns("right"),
            )
            return owner.capture(left.join(right, how="cross").filter(
                ma.col("__left_partition").eq(ma.col("__right_partition"))
                & ma.col("__left_scope").eq(ma.col("__right_scope"))
                & ma.col("__left_uuid").lt(ma.col("__right_uuid"))
            ).select(
                ma.col("__left_partition").alias("partition"),
                ma.col("__left_scope").alias("scope"),
                "__left_uuid", "__right_uuid", "__left_source_row", "__right_source_row",
                *binding_names("left"),
                *binding_names("right"),
            ).with_row_index(name=pair_key))[0]
    
        left_restrictions = make_restrictions("left")
        right_restrictions = make_restrictions("right")
        self_relation = self_pairs()
        feasible_self = owner.capture(filter_feasible_pairs(
            self_relation, effective, left_restrictions, right_restrictions,
            pair_key=pair_key,
        ))[0]
        feasible_source_ids = feasible_self.select(
            ma.col("__left_source_row").alias(row_id)
        ).unique(subset=[row_id])
        contradictory = indexed.join(feasible_source_ids, on=row_id, how="anti")
        contradiction_findings = emit(contradictory, "contradiction", "error")
        diagnostics.append(contradiction_findings)
        check_relations["source_contradictions"].append(contradiction_findings)
    
        pair_relation = distinct_pairs()
        feasible_pairs = owner.capture(filter_feasible_pairs(
            pair_relation, effective, left_restrictions, right_restrictions,
            pair_key=pair_key,
        ))[0]
        def difference_pairs(
            negated_side: str, positive_restrictions: dict[str, FieldRestriction],
        ) -> Relation:
            """Return source pairs with a witness in one region but not the other."""
            branches: list[Relation] = []
            for dimension in analysis_dimensions:
                strategy = dimension.match_strategy
                field_name = dimension.resolved_context_field
                leaf = make_restrictions(negated_side, dimension).get(field_name)
                if leaf is None:
                    continue
                if strategy is MatchStrategy.RANGE:
                    low_name, low_unbounded = rule_binding_names(dimension, "min")
                    high_name, high_unbounded = rule_binding_names(dimension, "max")
                    low = ma.col(f"__{negated_side}_{low_name}")
                    high = ma.col(f"__{negated_side}_{high_name}")
                    lower_is_unbounded = ma.col(f"__{negated_side}_{low_unbounded}")
                    upper_is_unbounded = ma.col(f"__{negated_side}_{high_unbounded}")
                    complements = (
                        (
                            ~lower_is_unbounded,
                            Interval(
                                low, low, ma.lit(False),
                                ma.lit(not dimension.range_min_inclusive),
                                ma.lit(True), ma.lit(False),
                            ),
                        ),
                        (
                            ~upper_is_unbounded,
                            Interval(
                                high, high,
                                ma.lit(not dimension.range_max_inclusive),
                                ma.lit(False), ma.lit(False), ma.lit(True),
                            ),
                        ),
                    )
                    for active, interval in complements:
                        branch = pair_relation.filter(active.fill_null(False))
                        negated = {field_name: FieldRestriction(
                            data_type=leaf.data_type,
                            intervals=(interval,),
                            numeric_dtype=leaf.numeric_dtype,
                            timezone=leaf.timezone,
                            rounding=leaf.rounding,
                        )}
                        left_side = positive_restrictions if negated_side == "right" else negated
                        right_side = negated if negated_side == "right" else positive_restrictions
                        branches.append(filter_feasible_pairs(
                            branch, effective, left_side, right_side, pair_key=pair_key,
                        ))
                    continue
    
                value_name, state_name = rule_binding_names(dimension)
                inactive = ma.col(f"__{negated_side}_{state_name}")
                branch = pair_relation.filter(~inactive.fill_null(False))
                if strategy in (
                    MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN,
                ):
                    bound = ma.col(f"__{negated_side}_{value_name}")
                    if strategy is MatchStrategy.GREATER_THAN:
                        interval = Interval(
                            bound, bound, ma.lit(False), ma.lit(True),
                            ma.lit(True), ma.lit(False),
                        )
                    else:
                        interval = Interval(
                            bound, bound, ma.lit(True), ma.lit(False),
                            ma.lit(False), ma.lit(True),
                        )
                    negated = {field_name: FieldRestriction(
                        data_type=leaf.data_type,
                        intervals=(interval,),
                        numeric_dtype=leaf.numeric_dtype,
                        timezone=leaf.timezone,
                        rounding=leaf.rounding,
                    )}
                else:
                    constraint = leaf.values[0]
                    negated = {field_name: FieldRestriction(
                        data_type=leaf.data_type,
                        values=(ValueConstraint(
                            values=constraint.values,
                            is_list=constraint.is_list,
                            allowed=not constraint.allowed,
                            inactive=ma.lit(False),
                        ),),
                        numeric_dtype=leaf.numeric_dtype,
                        timezone=leaf.timezone,
                        rounding=leaf.rounding,
                    )}
                left_side = positive_restrictions if negated_side == "right" else negated
                right_side = negated if negated_side == "right" else positive_restrictions
                branches.append(filter_feasible_pairs(
                    branch, effective, left_side, right_side, pair_key=pair_key,
                ))
            return (
                concat(branches).select("__left_source_row", "__right_source_row").unique(
                    subset=["__left_source_row", "__right_source_row"],
                )
                if branches else pair_relation.filter(ma.lit(False)).select(
                    "__left_source_row", "__right_source_row",
                )
            )
    
        if feasible_pairs.head(1).count_rows():
            left_difference = difference_pairs("right", left_restrictions)
            right_difference = difference_pairs("left", right_restrictions)
            differences = concat([left_difference, right_difference]).unique(
                subset=["__left_source_row", "__right_source_row"],
            )
        else:
            differences = feasible_pairs.head(0).select(
                "__left_source_row", "__right_source_row",
            )
        differences = owner.capture(differences)[0]
        feasible_pair_keys = feasible_pairs.join(
            differences,
            on=["__left_source_row", "__right_source_row"],
            how="anti",
        ).select(
            "__left_source_row", "__right_source_row",
            ma.lit(True).alias("__same_source_region"),
        )
        classified_pairs = feasible_pairs.join(
            feasible_pair_keys,
            on=["__left_source_row", "__right_source_row"],
            how="left",
        ).with_columns(ma.col("__same_source_region").fill_null(False))
        classified_pairs = owner.capture(classified_pairs)[0]
        for dimension in analysis_dimensions:
            if dimension.match_strategy is not MatchStrategy.RANGE:
                continue
            low_name, low_unbounded = rule_binding_names(dimension, "min")
            high_name, high_unbounded = rule_binding_names(dimension, "max")
            left = indexed.select(
                ma.col("partition").alias("lp"), ma.col("scope").alias("ls"),
                ma.col("source_uuid").alias("lu"), ma.col(row_id).alias("__left_source_row"),
                ma.col(low_name).alias("llo"), ma.col(high_name).alias("lhi"),
                ma.col(low_unbounded).alias("llw"), ma.col(high_unbounded).alias("lhw"),
            )
            right = indexed.select(
                ma.col("partition").alias("rp"), ma.col("scope").alias("rs"),
                ma.col("source_uuid").alias("ru"), ma.col(row_id).alias("__right_source_row"),
                ma.col(low_name).alias("rlo"), ma.col(high_name).alias("rhi"),
                ma.col(low_unbounded).alias("rlw"), ma.col(high_unbounded).alias("rhw"),
            )
            pairs = left.join(right, how="cross").filter(
                ma.col("lp").eq(ma.col("rp")) & ma.col("ls").eq(ma.col("rs"))
                & ma.col("lu").lt(ma.col("ru"))
            )
            pairs = pairs.join(
                classified_pairs,
                on=["__left_source_row", "__right_source_row"],
            )
            l_before_r = ma.col("llw") | ma.col("rhw") | ma.col("llo").lt(ma.col("rhi"))
            r_before_l = ma.col("rlw") | ma.col("lhw") | ma.col("rlo").lt(ma.col("lhi"))
            interior = l_before_r & r_before_l
            touching = (
                (~ma.col("lhw") & ~ma.col("rlw") & ma.col("lhi").eq(ma.col("rlo")))
                | (~ma.col("rhw") & ~ma.col("llw") & ma.col("rhi").eq(ma.col("llo")))
            )
            boundary = touching & ma.lit(
                dimension.range_min_inclusive and dimension.range_max_inclusive
            )
            overlaps = pairs.filter(interior | boundary)
            contains = (
                (ma.col("llw") | (~ma.col("rlw") & ma.col("llo").le(ma.col("rlo"))))
                & (ma.col("lhw") | (~ma.col("rhw") & ma.col("lhi").ge(ma.col("rhi"))))
            ) | (
                (ma.col("rlw") | (~ma.col("llw") & ma.col("rlo").le(ma.col("llo"))))
                & (ma.col("rhw") | (~ma.col("lhw") & ma.col("rhi").ge(ma.col("lhi"))))
            )
            same_region = ma.col("__same_source_region")
            kind = (
                ma.when(boundary & ~interior).then("singleton_boundary_overlap")
                .when(same_region).then("duplicate_source")
                .when(contains).then("containment_overlap")
                .otherwise("partial_overlap")
            )
            overlap_findings = overlaps.select(
                ma.col("lp").alias("partition"), ma.col("ls").alias("scope"),
                ma.lit("warning").alias("severity"), kind.alias("kind"),
                ma.col("lu").alias("__left_uuid"), ma.col("ru").alias("__right_uuid"),
                ma.lit(True).alias("__has_right"),
                ma.lit(dimension.dimension_name, dtype=MountainashDtype.STRING).alias("field"),
            )
            diagnostics.append(overlap_findings)
            check_relations["source_overlaps"].append(overlap_findings)
            witness = ma.when(ma.col("lhi").eq(ma.col("rlo"))).then(ma.col("lhi")).otherwise(ma.col("rhi"))
            boundary_facts[dimension.dimension_name] = overlaps.filter(boundary & ~interior).select(
                ma.col("lp").alias("partition"), ma.col("ls").alias("scope"),
                ma.col("lu").alias("__left_uuid"), ma.col("ru").alias("__right_uuid"),
                witness.alias("value"),
            )
    
    
        aliases_by_field: dict[str, list[t.Any]] = {}
        for dimension in analysis_dimensions:
            aliases_by_field.setdefault(dimension.resolved_context_field, []).append(dimension)
        for field_name, dimensions in aliases_by_field.items():
            categorical = [
                dimension for dimension in dimensions
                if dimension.match_strategy not in (MatchStrategy.RANGE, MatchStrategy.CONTEXT_REGEX)
            ]
            if not categorical:
                continue
            same_region = ma.col("__same_source_region")
            overlap_findings = classified_pairs.select(
                ma.col("partition"), ma.col("scope"),
                ma.lit("warning").alias("severity"),
                ma.when(same_region).then("duplicate_source").otherwise(
                    "partial_overlap"
                ).alias("kind"),
                ma.col("__left_uuid").alias("__left_uuid"),
                ma.col("__right_uuid").alias("__right_uuid"),
                ma.lit(True).alias("__has_right"),
                ma.lit(field_name, dtype=MountainashDtype.STRING).alias("field"),
            )
            diagnostics.append(overlap_findings)
            check_relations["source_overlaps"].append(overlap_findings)
    for scope_name, aggregates in (() if missing_scope else scopes.items()):
        scoped = raw.filter(scope_value.eq(scope_name))
        for aggregate in aggregates:
            try:
                _, invalid_contribution = _contribution(scoped, aggregate)
            except (ValueError, TypeError):
                invalid_contribution = ma.lit(True)
            invalid_rows = scoped.with_columns(
                partition.alias("partition"), ma.lit(scope_name).alias("scope"),
                source_uuid.alias("source_uuid"),
            ).filter(invalid_contribution.fill_null(True))
            contribution_findings = emit(invalid_rows, "invalid_value", "error")
            diagnostics.append(contribution_findings)
            check_relations["contribution_admission"].append(contribution_findings)
    combined = concat(diagnostics).with_row_index(name="finding_id").with_columns(
        ma.col("finding_id").cast(int).alias("finding_id")
    )
    findings = combined.select("finding_id", "severity", "kind", "partition", "scope", "field")
    left_subjects = combined.filter(~ma.col("__left_uuid").is_null()).select(
        "finding_id", "partition", "scope",
        ma.col("__left_uuid").alias("source_uuid"),
    )
    right_subjects = combined.filter(
        ma.col("__has_right") & ~ma.col("__right_uuid").is_null()
    ).select(
        "finding_id", "partition", "scope",
        ma.col("__right_uuid").alias("source_uuid"),
    )
    subjects = concat([left_subjects, right_subjects])
    checks = tuple(
        CheckResult(
            name, ("source_payloads",) if name == "contribution_admission" else ("source_structure",),
            "not_run" if name in not_run else (
                "findings" if count else "passed"
            ),
            count, count,
        )
        for name, relations in sorted(check_relations.items())
        for count in (sum(rel.count_rows() for rel in relations),)
    )
    witnesses = {
        field: combined.filter(
            ma.col("kind").eq("singleton_boundary_overlap") & ma.col("field").eq(field)
        ).join(facts, on=("partition", "scope", "__left_uuid", "__right_uuid")).select(
            "finding_id", ma.col("value"),
        )
        for field, facts in boundary_facts.items()
    }
    payload_dependencies = []
    for scope_name, aggregates in (() if missing_scope else scopes.items()):
        fields = [item.column_name for item in aggregates]
        if not all(field in raw.columns for field in fields):
            continue
        scoped = raw.filter(scope_value.eq(scope_name))
        payload_expressions = []
        try:
            for index, aggregate in enumerate(aggregates, 1):
                value, _ = _contribution(scoped, aggregate)
                payload_expressions.append(value.alias(f"a{index:04d}"))
        except (ValueError, TypeError):
            # Keep malformed authored payloads as source diagnostics, not errors
            # in report construction.
            continue
        payload_dependencies.append(scoped.select(
            partition.alias("partition"), ma.lit(scope_name).alias("scope"),
            source_uuid.alias("source_uuid"), *payload_expressions,
        ))
    captured = owner.capture(
        raw, *payload_dependencies, findings, subjects, *witnesses.values()
    )
    retained = tuple(owner.detach(item) for item in captured)
    payload_count = len(payload_dependencies)
    report_relations = retained[1 + payload_count:]
    report_id = uuid.uuid4()
    structure_keys = tuple(
        name for name in (
            effective.partition_field, effective.outcome_scope_field, effective.uuid_field,
        ) if name is not None and name in retained[0].schema
    )
    binding = ReportBinding(
        "accumulator-source-1", frozen_metadata,
        {
            "source_structure": (
                DependencyReference(
                    retained[0], structure_keys, tuple(retained[0].schema.items()), report_id,
                ),
            ),
            "source_payloads": tuple(
                DependencyReference(item, KEYS, tuple(item.schema.items()), report_id)
                for item in retained[1:1 + payload_count]
            ),
        },
    )
    return SourceReport(
        report_id, binding, checks, report_relations[0], report_relations[1],
        MappingProxyType(dict(zip(witnesses, report_relations[2:]))),
    )


KEYS = ("partition", "scope", "source_uuid")
_SUPPORTED = frozenset({MatchStrategy.EXACT, MatchStrategy.EXACT_KEY, MatchStrategy.NOT_EQUAL,
    MatchStrategy.RANGE, MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN,
    MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION})
_TYPES = {DataType.INT: MountainashDtype.I64, DataType.STR: MountainashDtype.STRING,
    DataType.BOOL: MountainashDtype.BOOL, DataType.DATE: MountainashDtype.DATE,
    DataType.DATETIME: MountainashDtype.TIMESTAMP}


@dataclass(frozen=True, slots=True)
class SourceBundle:
    """Captured input plus separate native payloads; metadata copies cannot mutate it."""

    rules: Relation
    states: t.Mapping[str, Relation]
    payloads: t.Mapping[str, Relation]
    identities: Relation
    _metadata_json: str

    @property
    def metadata(self) -> DimensionsMetadata:
        return DimensionsMetadata.model_validate_json(self._metadata_json)


def _contribution(rel: Relation, aggregate: Aggregate):
    field = aggregate.column_name
    if field not in rel.schema:
        raise ValueError(f"Missing contribution column {field!r}")
    raw = ma.col(field)
    dtype = rel.schema[field]
    invalid = raw.is_null()
    if aggregate.data_type is DataType.DECIMAL:
        if not isinstance(dtype, DecimalDtype):
            raise ValueError(f"Contribution {field!r} requires native DECIMAL values")
        declared = aggregate.numeric_dtype
        assert declared is not None
        # Admission is exact, not an input rounding policy. Keep native Decimal
        # storage; declared precision remains metadata even when storage is wider.
        text = raw.cast(str)
        integer = text.str.replace(r"^[+-]?0*", "").str.replace(r"\..*$", "")
        invalid = invalid | integer.str.length().gt(declared.precision - declared.scale)
        if dtype.scale > declared.scale:
            token = rf"^[+-]?[0-9]+(\.[0-9]{{0,{declared.scale}}}0*)?$"
            invalid = invalid | ~text.str.regex_contains(token)
    else:
        if not same_scalar_type(dtype, _TYPES[aggregate.data_type]):
            raise ValueError(f"Contribution {field!r} has an incompatible scalar type")
        if aggregate.data_type is DataType.INT:
            if dtype is MountainashDtype.U64:
                invalid = invalid | raw.gt((1 << 63) - 1)
            raw = ma.when(invalid).then(0).otherwise(raw).cast(int)
        elif aggregate.data_type is DataType.DATETIME:
            native_dtype = rel.filter(ma.lit(False)).to_polars().schema[field]
            source_timezone = _native_timezone(native_dtype)
            if (source_timezone is not None) != (aggregate.timezone == "utc"):
                raise ValueError(f"Contribution {field!r} has an incompatible timezone")
            raw = _utc_expression(raw, source_timezone)
    return raw, invalid

def capture_sources(rules: Relation, metadata: DimensionsMetadata) -> SourceBundle:
    """Capture admitted inputs without running optional source assurance."""
    if not isinstance(rules, Relation):
        raise TypeError("rules must be a Mountainash Relation")
    frozen = metadata.model_dump_json()
    metadata = DimensionsMetadata.model_validate_json(frozen)
    scopes = metadata.outcome_scopes or {"default": metadata.aggregates}
    if not all(scopes.values()):
        raise ValueError("Accumulator scopes require contribution declarations")
    for dim in metadata.dimensions:
        if dim.match_strategy not in _SUPPORTED:
            raise UnsupportedDomainError(f"Unsupported accumulator dimension {dim.dimension_name!r}")
    for domain in ([metadata.domain] if metadata.domain is not None else []) + metadata.context_domains:
        lower_domain(domain, metadata.domain_nodes)
    required = [metadata.uuid_field, metadata.partition_field, metadata.outcome_scope_field]
    if any(field is not None and field not in rules.columns for field in required):
        raise ValueError("Missing source identity column")
    with materialization_scope() as scope:
        authored = scope.snapshot(rules)
        partition = ma.col(metadata.partition_field) if metadata.partition_field else ma.lit("default")
        outcome = ma.col(metadata.outcome_scope_field) if metadata.outcome_scope_field else ma.lit("default")
        if metadata.outcome_scope_field and authored.schema[metadata.outcome_scope_field] is not MountainashDtype.STRING:
            raise ValueError("Outcome scope must be a native string column")
        key_exprs = [partition.alias("partition"), outcome.alias("scope"), ma.col(metadata.uuid_field).alias("source_uuid")]
        identities = authored.select(*key_exprs)
        reject_invalid(identities, {
            "identity": ma.col("partition").is_null() | ma.col("scope").is_null() | ma.col("source_uuid").is_null(),
            "scope": ~ma.col("scope").is_in(list(scopes)),
        })
        counts = identities.group_by(*KEYS).agg(ma.count_records().alias("occurrences"))
        reject_invalid(counts, {"duplicate identity": ma.col("occurrences").gt(1)})
        normalized, invalid = _source_rule_bindings(authored, metadata.dimensions)
        reject_invalid(authored, invalid)
        states = {}
        for dim in metadata.dimensions:
            if dim.match_strategy is MatchStrategy.RANGE:
                lo, lo_state = rule_binding_names(dim, "min")
                hi, hi_state = rule_binding_names(dim, "max")
                columns = [ma.col(lo).alias("lower"), ma.col(hi).alias("upper"),
                    ma.col(lo_state).alias("lower_unbounded"), ma.col(hi_state).alias("upper_unbounded")]
            else:
                value, wildcard = rule_binding_names(dim)
                columns = [ma.col(value).alias("value"), ma.col(wildcard).alias("is_wildcard")]
            states[dim.dimension_name] = normalized.select(*key_exprs, *columns)
        payloads = {}
        for name, aggregates in scopes.items():
            selected = authored.filter(outcome.eq(name))
            expressions, failures = [], {}
            for index, aggregate in enumerate(aggregates, 1):
                contribution, failure = _contribution(selected, aggregate)
                expressions.append(contribution.alias(f"a{index:04d}"))
                failures[aggregate.column_name] = failure
            reject_invalid(selected, failures)
            payloads[name] = selected.select(*key_exprs, *expressions)
        owned = [scope.detach(item) for item in scope.capture(authored, identities, *states.values(), *payloads.values())]
    n = len(states)
    return SourceBundle(owned[0], MappingProxyType(dict(zip(states, owned[2:2+n]))),
        MappingProxyType(dict(zip(payloads, owned[2+n:]))), owned[1], frozen)
