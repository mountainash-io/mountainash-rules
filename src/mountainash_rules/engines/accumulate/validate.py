"""Independent checks over captured accumulator artifacts."""

from __future__ import annotations

import itertools
import typing as t
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID, uuid4

import mountainash.expressions as ma
from mountainash import MountainashDtype, materialization_scope
from mountainash.relations import Relation, concat, relation

from mountainash_rules.core.aggregate import Aggregate, AggregateOp
from mountainash_rules.core.constants import DataType, DimensionRole, MatchStrategy
from mountainash_rules.core.contracts import DomainDefinition
from mountainash_rules.core.dimension import DimensionsMetadata
from mountainash_rules.core.numeric import (
    decimal_operand, decimal_subtract_exact, exact_literal,
    literal_dtype,
)
from mountainash_rules.core.scalar import decode_scalar
from mountainash_rules.engines.accumulate.domain import (
    FieldRestriction,
    Interval,
    ValueConstraint,
    feasible_assignments,
    filter_feasible_pairs,
)
from mountainash_rules.engines.accumulate.errors import UnsupportedDomainError
from mountainash_rules.engines.accumulate.lattice import Lattice
from mountainash_rules.engines.accumulate.sources import (
    SOURCE_CHECK_NAMES,
    CheckResult,
    DependencyReference,
    ReportBinding,
    SourceReport,
    capture_sources,
)


def _capture_derived(owner: t.Any, *queries: Relation) -> tuple[Relation, ...]:
    """Stage queries over immutable inputs, including independent connections."""
    return tuple(owner.snapshot(relation(query.collect())) for query in queries)


@dataclass(frozen=True, slots=True)
class ArtifactReport:
    """Immutable factual results bound to the artifact and source owners checked."""

    report_id: UUID
    binding: ReportBinding
    checks: tuple[CheckResult, ...]
    findings: Relation
    subjects: Relation
    witnesses: t.Mapping[str, Relation]

    def __post_init__(self) -> None:
        object.__setattr__(self, "checks", tuple(self.checks))
        object.__setattr__(self, "witnesses", MappingProxyType(dict(self.witnesses)))


@dataclass(frozen=True, slots=True)
class Approval:
    """An explicit report-local approval of warning findings."""

    report_id: UUID
    finding_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "finding_ids", tuple(self.finding_ids))


def _witness_id(rows: Relation, name: str, alias: str) -> t.Any:
    return (
        ma.col(name).cast(MountainashDtype.I64)
        if name in rows.columns
        else ma.lit(None, dtype=MountainashDtype.I64)
    ).alias(alias)


def _empty_diagnostics(seed: Relation) -> Relation:
    return seed.select(
        ma.col("partition").cast(str).alias("partition"),
        ma.col("scope").cast(str).alias("scope"),
        ma.lit("", dtype=MountainashDtype.STRING).alias("kind"),
        ma.lit("error", dtype=MountainashDtype.STRING).alias("severity"),
        ma.lit(None, dtype=MountainashDtype.STRING).alias("field"),
        ma.lit(None, dtype=MountainashDtype.STRING).alias("source_uuid"),
        ma.lit(None, dtype=MountainashDtype.I64).alias("witness_cell_id"),
        ma.lit(None, dtype=MountainashDtype.I64).alias("witness_box_id"),
        ma.lit(None, dtype=MountainashDtype.I64).alias("witness_contributor_set_id"),
        ma.lit(None, dtype=MountainashDtype.I64).alias("witness_atom_id"),
    ).filter(ma.lit(False))


def _diagnostic_rows(rows: Relation, kind: str, *, severity: str = "error",
                     field: str | None = None, source: str | None = None) -> Relation:
    source_expr = (
        ma.col(source).cast(str) if source is not None and source in rows.columns
        else ma.lit(None, dtype=MountainashDtype.STRING)
    )
    selected = rows.select(
        ma.col("partition").cast(str).alias("partition"),
        ma.col("scope").cast(str).alias("scope"),
        _witness_id(rows, "cell_id", "witness_cell_id"),
        _witness_id(rows, "box_id", "witness_box_id"),
        _witness_id(rows, "contributor_set_id", "witness_contributor_set_id"),
        _witness_id(rows, "__atom_id", "witness_atom_id"),
        source_expr.alias("source_uuid"),
    )
    return selected.with_columns(
        ma.lit(kind, dtype=MountainashDtype.STRING).alias("kind"),
        ma.lit(severity, dtype=MountainashDtype.STRING).alias("severity"),
        ma.lit(field, dtype=MountainashDtype.STRING).alias("field"),
    ).select(
        "partition", "scope", "kind", "severity", "field", "source_uuid",
        "witness_cell_id", "witness_box_id", "witness_contributor_set_id",
        "witness_atom_id",
    )


def _duplicates(relation_: Relation, keys: t.Sequence[str]) -> Relation:
    return relation_.group_by(*keys).agg(
        ma.count_records().alias("__count")
    ).filter(ma.col("__count").gt(1))
def _context_timezone(metadata: DimensionsMetadata, dimension: t.Any) -> str | None:
    domains = ([metadata.domain] if metadata.domain is not None else []) + list(
        metadata.context_domains
    )
    names = {dimension.resolved_context_field, dimension.dimension_name}
    return next(
        (
            domain_field.timezone
            for domain in domains
            for domain_field in domain.fields
            if domain_field.name in names
        ),
        None,
    )
def _native_dimension_dtype(dimension: t.Any) -> t.Any:
    if dimension.data_type is DataType.DECIMAL:
        if dimension.numeric_dtype is None:
            raise UnsupportedDomainError("Decimal geometry lacks its declared numeric dtype")
        return dimension.numeric_dtype
    return {
        DataType.STR: MountainashDtype.STRING,
        DataType.BOOL: MountainashDtype.BOOL,
        DataType.FLOAT: MountainashDtype.FP64,
        DataType.INT: MountainashDtype.I64,
        DataType.DATE: MountainashDtype.DATE,
        DataType.DATETIME: MountainashDtype.TIMESTAMP,
    }[dimension.data_type]

def _unused_dimension_value(
    metadata: DimensionsMetadata,
    dimension: t.Any,
) -> t.Any:
    if (
        dimension.data_type is DataType.DATETIME
        and _context_timezone(metadata, dimension) == "utc"
    ):
        return ma.lit(datetime(1970, 1, 1, tzinfo=timezone.utc))
    return ma.lit(None, dtype=_native_dimension_dtype(dimension))


def _domain_points(
    metadata: DimensionsMetadata,
    dimension: t.Any,
) -> tuple[t.Any, ...]:
    """Decode authored scalar boundaries for fields the geometry partitions."""
    domains = ([metadata.domain] if metadata.domain is not None else []) + list(
        metadata.context_domains
    )
    fields = {dimension.resolved_context_field, dimension.dimension_name}
    declarations = {item.name: item for domain in domains for item in domain.fields}
    pending = [domain.predicate_id for domain in domains]
    if metadata.policy is not None:
        pending.extend(
            item.region_predicate_id for item in metadata.policy.coverage_requirements
        )
    visited: set[str] = set()
    points = []
    while pending:
        identifier = pending.pop()
        if identifier in visited:
            continue
        visited.add(identifier)
        envelope = metadata.domain_nodes.get(identifier)
        if envelope is None or not isinstance(envelope.get("node"), t.Mapping):
            continue
        node = envelope["node"]
        operation = node.get("op")
        field = node.get("field")
        payloads: tuple[t.Mapping[str, t.Any], ...] = ()
        if operation == "eq":
            payloads = (node["value"],)
        elif operation == "in":
            payloads = tuple(node["values"])
        elif operation == "interval":
            payloads = tuple(
                value for value in (node["lower"], node["upper"]) if value is not None
            )
        if field in fields:
            for payload in payloads:
                native = decode_scalar(dict(payload), allow_reserved=True)
                if native is None:
                    continue
                expression = exact_literal(native)
                if dimension.data_type in (DataType.INT, DataType.FLOAT):
                    expression = expression.cast(_native_dimension_dtype(dimension))
                if dimension.data_type is DataType.DECIMAL:
                    declaration = declarations.get(field)
                    numeric_dtype = (
                        dimension.numeric_dtype
                        or (None if declaration is None else declaration.numeric_dtype)
                    )
                    rounding = (
                        dimension.rounding
                        or (None if declaration is None else declaration.rounding)
                    )
                    if numeric_dtype is None or rounding is None:
                        raise UnsupportedDomainError(
                            f"Decimal domain field {field!r} lacks exact numeric metadata"
                        )
                    expression = decimal_operand(
                        expression, literal_dtype(native), numeric_dtype, rounding,
                    ).value
                points.append(expression)
        if operation in {"and", "or"}:
            pending.extend(node["args"])
        elif operation == "not":
            pending.append(node["arg"])
    return tuple(points)



def _expr_fold(rows: Relation, aggregates: t.Sequence[Aggregate],
               keys: t.Sequence[str]) -> Relation:
    expressions = []
    for index, aggregate in enumerate(aggregates, 1):
        value = ma.col(f"a{index:04d}")
        if aggregate.operation is AggregateOp.SUM:
            folded = value.sum()
        elif aggregate.operation is AggregateOp.MIN:
            folded = value.min()
        elif aggregate.operation is AggregateOp.MAX:
            folded = value.max()
        elif aggregate.operation is AggregateOp.PRODUCT:
            folded = (
                value.cast(MountainashDtype.FP64).product()
                if aggregate.data_type is DataType.DECIMAL else value.product()
            )
        else:  # pragma: no cover - metadata model validates operations
            raise ValueError(f"Unsupported aggregate operation {aggregate.operation!r}")
        if aggregate.output_numeric_dtype is not None:
            assert aggregate.output_rounding is not None
            folded = folded.cast(
                aggregate.output_numeric_dtype, rounding=aggregate.output_rounding,
            )
        expressions.append(folded.alias(f"v{index:04d}"))
    return rows.group_by(*keys).agg(*expressions)


def _category_axis(
    lattice: Lattice,
    sources: t.Any,
    dimension: t.Any,
    all_keys: Relation,
    index: int,
    owner: t.Any,
) -> tuple[Relation, Relation]:
    """Create finite category points plus a symbolic cofinite remainder."""
    keys = ("partition", "scope")
    alias_dimensions = [
        item for item in sources.metadata.dimensions
        if item.resolved_context_field == dimension.resolved_context_field
    ]
    value_relations = []
    include_boolean = False
    for alias in alias_dimensions:
        state = sources.states[alias.dimension_name]
        include_boolean = include_boolean or alias.data_type is DataType.BOOL
        if alias.match_strategy is MatchStrategy.RANGE:
            value_relations.extend([
                state.filter(~ma.col("lower_unbounded")).select(
                    *keys, ma.col("lower").alias("__value"),
                ),
                state.filter(~ma.col("upper_unbounded")).select(
                    *keys, ma.col("upper").alias("__value"),
                ),
            ])
        else:
            alias_values = state.filter(~ma.col("is_wildcard")).select(
                *keys, ma.col("value").alias("__value"),
            )
            if alias.match_strategy in (
                MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION,
            ):
                alias_values = alias_values.explode("__value")
            value_relations.append(alias_values)
        bounds = lattice.cell_ordered_bounds.get(alias.resolved_context_field)
        if bounds is not None:
            value_relations.extend([
                bounds.filter(ma.col("lower_state").eq("bounded")).select(
                    *keys, ma.col("lower_value").alias("__value"),
                ),
                bounds.filter(ma.col("upper_state").eq("bounded")).select(
                    *keys, ma.col("upper_value").alias("__value"),
                ),
            ])
        stored = lattice.cell_category_values.get(alias.resolved_context_field)
        if stored is not None:
            value_relations.append(stored.select(
                *keys, ma.col("value").alias("__value"),
            ))
        if alias.wildcard_values:
            value_relations.append(all_keys.with_columns(
                ma.lit(alias.wildcard_values).alias("__value"),
            ).explode("__value").select(*keys, "__value"))
        value_relations.extend(
            all_keys.select(*keys, point.alias("__value"))
            for point in _domain_points(sources.metadata, alias)
        )
    values = concat(value_relations) if value_relations else all_keys.select(
        *keys, ma.lit(None, dtype=MountainashDtype.STRING).alias("__value"),
    ).filter(ma.lit(False))
    if include_boolean:
        values = concat([
            values,
            all_keys.with_columns(ma.lit(False).alias("__value")).select(*keys, "__value"),
            all_keys.with_columns(ma.lit(True).alias("__value")).select(*keys, "__value"),
        ])
    values = values.filter(~ma.col("__value").is_null()).unique(
        subset=[*keys, "__value"],
    )
    point_axis = values.with_columns(
        ma.lit(True).alias(f"__a{index}_has_value"),
    ).select(
        *keys, ma.col("__value").alias(f"__a{index}_value"),
        f"__a{index}_has_value",
    )
    residual_axis = all_keys.select(
        *keys, _unused_dimension_value(sources.metadata, dimension).alias(f"__a{index}_value"),
        ma.lit(False).alias(f"__a{index}_has_value"),
    )
    axis = concat([point_axis, residual_axis]).with_row_index(name=f"__axis_id_{index}")
    axis = _capture_derived(owner, axis)[0]
    excluded = axis.filter(~ma.col(f"__a{index}_has_value")).select(
        *keys, f"__axis_id_{index}",
    ).join(
        values, on=list(keys), how="inner",
    ).select(
        ma.col(f"__axis_id_{index}").alias("__axis_id"),
        ma.col("__value").alias("value"),
    )
    return axis, _capture_derived(owner, excluded)[0]


def _range_atoms(
    lattice: Lattice, sources: t.Any, owner: t.Any,
) -> tuple[Relation, dict[str, FieldRestriction]]:
    """Build complete native ordered boundary classes without normalizing cells."""
    metadata = lattice.metadata
    keys = ("partition", "scope")
    all_keys = concat([
        sources.identities.select(*keys),
        lattice.cells.select(*keys),
    ], distinct=True)
    all_keys = _capture_derived(owner, all_keys)[0]
    atoms = all_keys
    restrictions: dict[str, FieldRestriction] = {}
    categorical: list[tuple[int, t.Any, Relation]] = []
    ordered_axes: dict[str, tuple[Relation, int, dict[str, str]]] = {}
    categorical_axes: dict[str, int] = {}
    for index, dimension in enumerate(metadata.dimensions):
        field = dimension.resolved_context_field
        if dimension.match_strategy not in (
            MatchStrategy.RANGE, MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN,
        ):
            if field in categorical_axes:
                previous_index = categorical_axes[field]
                atoms = atoms.with_columns(
                    ma.col(f"__axis_id_{previous_index}").alias(f"__axis_id_{index}"),
                    ma.col(f"__a{previous_index}_value").alias(f"__a{index}_value"),
                    ma.col(f"__a{previous_index}_has_value").alias(
                        f"__a{index}_has_value",
                    ),
                )
                continue
            axis, excluded = _category_axis(
                lattice, sources, dimension, all_keys, index, owner,
            )
            categorical_axes[field] = index
            atoms = atoms.join(axis, on=list(keys), how="inner")
            categorical.append((index, dimension, excluded))
            continue
        alias_dimensions = [
            item for item in metadata.dimensions
            if item.resolved_context_field == field
        ]
        source_points = []
        geometry_points = []
        for alias in alias_dimensions:
            alias_state = sources.states[alias.dimension_name]
            if alias.match_strategy is MatchStrategy.RANGE:
                source_points.extend([
                    alias_state.filter(~ma.col("lower_unbounded")).select(
                        *keys, ma.col("lower").alias("__point"),
                    ),
                    alias_state.filter(~ma.col("upper_unbounded")).select(
                        *keys, ma.col("upper").alias("__point"),
                    ),
                ])
            else:
                values = alias_state.filter(~ma.col("is_wildcard")).select(
                    *keys, ma.col("value").alias("__point"),
                )
                if alias.match_strategy in (
                    MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION,
                ):
                    values = values.explode("__point")
                source_points.append(values)
            alias_bounds = lattice.cell_ordered_bounds.get(alias.resolved_context_field)
            if alias_bounds is not None:
                geometry_points.extend([
                    alias_bounds.filter(ma.col("lower_state").eq("bounded")).select(
                        *keys, ma.col("lower_value").alias("__point"),
                    ),
                    alias_bounds.filter(ma.col("upper_state").eq("bounded")).select(
                        *keys, ma.col("upper_value").alias("__point"),
                    ),
                ])
            category_values = lattice.cell_category_values.get(alias.resolved_context_field)
            if category_values is not None:
                geometry_points.append(category_values.select(
                    *keys, ma.col("value").alias("__point"),
                ))
            geometry_points.extend(
                all_keys.select(*keys, point.alias("__point"))
                for point in _domain_points(metadata, alias)
            )
        points = concat([
            *source_points, *geometry_points,
        ], distinct=True)
        points = _capture_derived(owner, points.select(*keys, "__point").unique(subset=[*keys, "__point"]))[0]
        left = points.select(*keys, ma.col("__point").alias("__lower"))
        right = points.select(*keys, ma.col("__point").alias("__upper"))
        pairs = left.join(right, how="cross").filter(
            *[ma.col(key).eq(ma.col(f"{key}_right")) for key in keys],
            ma.col("__lower").lt(ma.col("__upper")),
        ).select(*keys, "__lower", "__upper")
        middle = points.select(*keys, ma.col("__point").alias("__middle"))
        blocked = pairs.join(middle, how="cross").filter(
            *[ma.col(key).eq(ma.col(f"{key}_right")) for key in keys],
            ma.col("__lower").lt(ma.col("__middle")),
            ma.col("__middle").lt(ma.col("__upper")),
        ).select(*keys, "__lower", "__upper").unique(
            subset=[*keys, "__lower", "__upper"],
        )
        bands = pairs.join(
            blocked, on=[*keys, "__lower", "__upper"], how="anti",
        ).with_columns(
            ma.lit(False).alias("__lower_inclusive"),
            ma.lit(False).alias("__upper_inclusive"),
            ma.lit(False).alias("__lower_unbounded"),
            ma.lit(False).alias("__upper_unbounded"),
        )
        singleton = points.select(
            *keys,
            ma.col("__point").alias("__lower"),
            ma.col("__point").alias("__upper"),
            ma.lit(True).alias("__lower_inclusive"),
            ma.lit(True).alias("__upper_inclusive"),
            ma.lit(False).alias("__lower_unbounded"),
            ma.lit(False).alias("__upper_unbounded"),
        )
        extrema = points.group_by(*keys).agg(
            ma.col("__point").min().alias("__minimum"),
            ma.col("__point").max().alias("__maximum"),
        )
        unused = _unused_dimension_value(metadata, dimension)
        low_tail = extrema.select(
            *keys, unused.alias("__lower"),
            ma.col("__minimum").alias("__upper"),
            ma.lit(False).alias("__lower_inclusive"),
            ma.lit(False).alias("__upper_inclusive"),
            ma.lit(True).alias("__lower_unbounded"),
            ma.lit(False).alias("__upper_unbounded"),
        )
        high_tail = extrema.select(
            *keys, ma.col("__maximum").alias("__lower"),
            unused.alias("__upper"),
            ma.lit(False).alias("__lower_inclusive"),
            ma.lit(False).alias("__upper_inclusive"),
            ma.lit(False).alias("__lower_unbounded"),
            ma.lit(True).alias("__upper_unbounded"),
        )
        no_points = all_keys.join(
            points.select(*keys).unique(subset=list(keys)), on=list(keys), how="anti",
        ).select(
            *keys, unused.alias("__lower"),
            unused.alias("__upper"),
            ma.lit(False).alias("__lower_inclusive"),
            ma.lit(False).alias("__upper_inclusive"),
            ma.lit(True).alias("__lower_unbounded"),
            ma.lit(True).alias("__upper_unbounded"),
        )
        axis_names = {
            name: f"__a{index}_{name.removeprefix('__')}"
            for name in (
                "__lower", "__upper", "__lower_inclusive", "__upper_inclusive",
                "__lower_unbounded", "__upper_unbounded",
            )
        }
        if field in ordered_axes:
            shared_axis, shared_index, shared_names = ordered_axes[field]
            axis = shared_axis.select(
                *keys,
                ma.col(f"__axis_id_{shared_index}").alias(f"__axis_id_{index}"),
                *[
                    ma.col(shared_names[name]).alias(axis_names[name])
                    for name in axis_names
                ],
            )
        else:
            axis = concat([bands, singleton, low_tail, high_tail, no_points])
            axis = axis.with_row_index(name=f"__axis_id_{index}").select(
                *keys, f"__axis_id_{index}",
                *[ma.col(old).alias(new) for old, new in axis_names.items()],
            )
            axis = _capture_derived(owner, axis)[0]
            ordered_axes[field] = (axis, index, axis_names)
        atoms = atoms.join(axis, on=list(keys), how="inner")
        restriction = Interval(
            ma.col(axis_names["__lower"]), ma.col(axis_names["__upper"]),
            ma.col(axis_names["__lower_inclusive"]),
            ma.col(axis_names["__upper_inclusive"]),
            ma.col(axis_names["__lower_unbounded"]),
            ma.col(axis_names["__upper_unbounded"]),
        )
        previous = restrictions.get(field)
        intervals = (() if previous is None else previous.intervals) + (restriction,)
        restrictions[field] = FieldRestriction(
            data_type=dimension.data_type,
            intervals=intervals,
            numeric_dtype=dimension.numeric_dtype,
            timezone=_context_timezone(metadata, dimension),
            rounding=dimension.rounding,
        )
    atoms = _capture_derived(owner, atoms.with_row_index(name="__atom_id"))[0]
    for index, dimension, excluded in categorical:
        axis_key = f"__axis_id_{index}"
        has_value = ma.col(f"__a{index}_has_value")
        exclusions = atoms.select(
            "__atom_id", axis_key,
        ).join(
            excluded, left_on=axis_key, right_on="__axis_id", how="inner",
        ).select("__atom_id", ma.col("value"))
        value_constraints = (
            ValueConstraint(
                ma.col(f"__a{index}_value"), False, True, ~has_value,
            ),
            ValueConstraint(exclusions, False, False, has_value),
        )
        field = dimension.resolved_context_field
        previous = restrictions.get(field)
        restrictions[field] = FieldRestriction(
            data_type=dimension.data_type,
            intervals=() if previous is None else previous.intervals,
            values=(() if previous is None else previous.values) + value_constraints,
            numeric_dtype=dimension.numeric_dtype,
            timezone=_context_timezone(metadata, dimension),
            rounding=dimension.rounding,
        )
    return atoms, restrictions

def _pair_atom_restrictions(
    pair: Relation,
    restrictions: dict[str, FieldRestriction],
) -> dict[str, FieldRestriction]:
    """Rekey atom-local finite/cofinite constraints to unique candidate pairs."""
    mapped: dict[str, FieldRestriction] = {}
    for field, restriction in restrictions.items():
        values = []
        for constraint in restriction.values:
            if isinstance(constraint.values, Relation):
                members = pair.select("__pair_id", "__atom_id").join(
                    constraint.values,
                    left_on="__atom_id", right_on="__atom_id", how="inner",
                ).select("__pair_id", "value")
                values.append(ValueConstraint(
                    members, constraint.is_list, constraint.allowed,
                    constraint.inactive,
                ))
            else:
                values.append(constraint)
        mapped[field] = FieldRestriction(
            restriction.data_type, restriction.intervals, tuple(values),
            restriction.numeric_dtype, restriction.timezone, restriction.rounding,
        )
    return mapped


def _source_atom_matches(
    atoms: Relation,
    atom_restrictions: dict[str, FieldRestriction],
    sources: t.Any,
    metadata: DimensionsMetadata,
    owner: t.Any,
) -> Relation:
    keys = ("partition", "scope")
    pair = atoms.join(sources.identities, on=list(keys), how="inner").with_row_index(
        name="__pair_id",
    )
    pair = _capture_derived(owner, pair)[0]
    left: dict[str, FieldRestriction] = {}
    right = dict(atom_restrictions)
    for index, dimension in enumerate(metadata.dimensions):
        field = dimension.resolved_context_field
        state = sources.states[dimension.dimension_name]
        if dimension.match_strategy is MatchStrategy.RANGE:
            names = {
                name: f"__s{index}_{name}"
                for name in ("lower", "upper", "lower_unbounded", "upper_unbounded")
            }
            state_columns = [
                ma.col(name).alias(alias) for name, alias in names.items()
            ]
        else:
            names = {
                "value": f"__s{index}_value",
                "is_wildcard": f"__s{index}_inactive",
            }
            state_columns = [
                ma.col("value").alias(names["value"]),
                ma.col("is_wildcard").alias(names["is_wildcard"]),
            ]
        pair = pair.join(
            state.select(*keys, "source_uuid", *state_columns),
            on=[*keys, "source_uuid"], how="inner",
        )
        old = left.get(field)
        intervals = () if old is None else old.intervals
        values = () if old is None else old.values
        if dimension.match_strategy is MatchStrategy.RANGE:
            intervals += (Interval(
                ma.col(names["lower"]), ma.col(names["upper"]),
                ma.lit(dimension.range_min_inclusive),
                ma.lit(dimension.range_max_inclusive),
                ma.col(names["lower_unbounded"]), ma.col(names["upper_unbounded"]),
            ),)
        elif dimension.match_strategy in (MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN):
            bound = ma.col(names["value"])
            inactive = ma.col(names["is_wildcard"])
            intervals += (Interval(
                bound, bound,
                ma.lit(False) if dimension.match_strategy is MatchStrategy.GREATER_THAN else ma.lit(True),
                ma.lit(True) if dimension.match_strategy is MatchStrategy.GREATER_THAN else ma.lit(False),
                inactive if dimension.match_strategy is MatchStrategy.GREATER_THAN else ma.lit(True),
                ma.lit(True) if dimension.match_strategy is MatchStrategy.GREATER_THAN else inactive,
            ),)
        else:
            strategy = dimension.match_strategy
            values += (ValueConstraint(
                ma.col(names["value"]),
                strategy in (MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION),
                strategy not in (MatchStrategy.NOT_EQUAL, MatchStrategy.SET_EXCLUSION),
                ma.col(names["is_wildcard"]),
            ),)
        left[field] = FieldRestriction(
            dimension.data_type, intervals, values, dimension.numeric_dtype,
            timezone=_context_timezone(metadata, dimension),
            rounding=dimension.rounding,
        )
    right = _pair_atom_restrictions(pair, right)
    return filter_feasible_pairs(
        pair, metadata, left, right, pair_key="__pair_id",
    ).select(*keys, "__atom_id", "source_uuid")


def _cell_atom_matches(
    lattice: Lattice,
    atoms: Relation,
    atom_restrictions: dict[str, FieldRestriction],
    metadata: DimensionsMetadata,
    owner: t.Any,
    *,
    check_domain: bool = True,
) -> Relation:
    keys = ("partition", "scope")
    boxes = lattice.cells.select(*keys, "cell_id", "contributor_set_id").join(
        lattice.cell_boxes, on=[*keys, "cell_id"], how="inner",
    )
    pair = atoms.join(boxes, on=list(keys), how="inner").with_row_index(
        name="__pair_id",
    )
    pair = _capture_derived(owner, pair)[0]
    left: dict[str, FieldRestriction] = {}
    categorical: list[tuple[int, t.Any, str]] = []
    seen_fields: set[str] = set()
    for index, dimension in enumerate(metadata.dimensions):
        field = dimension.resolved_context_field
        if field in seen_fields:
            continue
        seen_fields.add(field)
        bounds = lattice.cell_ordered_bounds.get(field)
        if bounds is None:
            set_relation = lattice.cell_category_sets.get(field)
            if set_relation is None:
                raise UnsupportedDomainError(
                    f"Missing cell category geometry for {dimension.dimension_name!r}"
                )
            kind_name = f"__c{index}_membership_kind"
            pair = pair.join(
                set_relation.select(
                    *keys, "cell_id", "box_id",
                    ma.col("membership_kind").alias(kind_name),
                ), on=[*keys, "cell_id", "box_id"], how="inner",
            )
            categorical.append((index, dimension, kind_name))
            continue
        names = {
            name: f"__c{index}_{name}"
            for name in (
                "lower", "upper", "lower_inclusive", "upper_inclusive",
                "lower_unbounded", "upper_unbounded",
            )
        }
        pair = pair.join(
            bounds.select(
                *keys, "cell_id", "box_id",
                ma.col("lower_value").alias(names["lower"]),
                ma.col("upper_value").alias(names["upper"]),
                ma.col("lower_inclusive").alias(names["lower_inclusive"]),
                ma.col("upper_inclusive").alias(names["upper_inclusive"]),
                ma.col("lower_state").eq("unbounded").alias(names["lower_unbounded"]),
                ma.col("upper_state").eq("unbounded").alias(names["upper_unbounded"]),
            ), on=[*keys, "cell_id", "box_id"], how="inner",
        )
        interval = Interval(
            ma.col(names["lower"]), ma.col(names["upper"]),
            ma.col(names["lower_inclusive"]), ma.col(names["upper_inclusive"]),
            ma.col(names["lower_unbounded"]), ma.col(names["upper_unbounded"]),
        )
        old = left.get(field)
        left[field] = FieldRestriction(
            data_type=dimension.data_type,
            intervals=(() if old is None else old.intervals) + (interval,),
            values=() if old is None else old.values,
            numeric_dtype=dimension.numeric_dtype,
            timezone=_context_timezone(metadata, dimension),
            rounding=dimension.rounding,
        )
    candidate_metadata = (
        metadata if check_domain
        else metadata.model_copy(update={"domain": None, "context_domains": [], "domain_nodes": {}})
    )
    results = []
    for kinds in itertools.product(("finite", "cofinite"), repeat=len(categorical)):
        conditions = [
            ma.col(kind_name).eq(kind)
            for (_, _, kind_name), kind in zip(categorical, kinds)
        ]
        candidate_pair = pair.filter(*conditions) if conditions else pair
        restrictions = dict(left)
        for (_, dimension, _), kind in zip(categorical, kinds):
            field = dimension.resolved_context_field
            members = candidate_pair.select(
                "__pair_id", "partition", "scope", "cell_id", "box_id",
            ).join(
                lattice.cell_category_values[field],
                on=["partition", "scope", "cell_id", "box_id"], how="inner",
            ).select("__pair_id", ma.col("value"))
            previous = restrictions.get(field)
            restrictions[field] = FieldRestriction(
                data_type=dimension.data_type,
                intervals=() if previous is None else previous.intervals,
                values=(() if previous is None else previous.values) + (
                    ValueConstraint(
                        members, False, kind == "finite", ma.lit(False),
                    ),
                ),
                numeric_dtype=dimension.numeric_dtype,
                timezone=_context_timezone(metadata, dimension),
                rounding=dimension.rounding,
            )
        atom_values = _pair_atom_restrictions(candidate_pair, atom_restrictions)
        results.append(filter_feasible_pairs(
            candidate_pair, candidate_metadata, restrictions, atom_values,
            pair_key="__pair_id",
        ).select(*keys, "__atom_id", "cell_id", "box_id", "contributor_set_id"))
    return concat(results, distinct=True)






def _range_geometry_findings(
    lattice: Lattice,
    sources: t.Any,
    atoms: Relation,
    atom_restrictions: dict[str, FieldRestriction],
    metadata: DimensionsMetadata,
    owner: t.Any,
) -> tuple[Relation, Relation, Relation, Relation, Relation, Relation, Relation, Relation, Relation, Relation]:
    keys = ("partition", "scope")
    atom_axis_columns = []
    for index, dimension in enumerate(metadata.dimensions):
        atom_axis_columns.append(f"__axis_id_{index}")
        if dimension.match_strategy in (
            MatchStrategy.RANGE, MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN,
        ):
            atom_axis_columns.extend(
                f"__a{index}_{name}" for name in (
                    "lower", "upper", "lower_inclusive", "upper_inclusive",
                    "lower_unbounded", "upper_unbounded",
                )
            )
        else:
            atom_axis_columns.extend((f"__a{index}_value", f"__a{index}_has_value"))
    keyed_atoms = atoms.with_columns(
        ma.col("__atom_id").cast(MountainashDtype.I64).alias("__geometry_atom_id"),
    )
    valid_candidates = filter_feasible_pairs(
        keyed_atoms, metadata, {}, atom_restrictions, pair_key="__atom_id",
    )
    valid_atoms = _capture_derived(owner, valid_candidates.select(
        *keys,
        ma.col("__geometry_atom_id").cast(MountainashDtype.I64).alias("__atom_id"),
        *atom_axis_columns,
    ))[0]
    source_hits = _capture_derived(owner, _source_atom_matches(
        valid_atoms, atom_restrictions, sources, metadata, owner,
    ))[0]
    cell_hits = _capture_derived(owner, _cell_atom_matches(
        lattice, valid_atoms, atom_restrictions, metadata, owner,
    ).unique(subset=[
        "partition", "scope", "__atom_id", "cell_id", "box_id", "contributor_set_id",
    ]))[0]
    all_atoms = keyed_atoms.select(
        *keys,
        ma.col("__geometry_atom_id").cast(MountainashDtype.I64).alias("__atom_id"),
        *atom_axis_columns,
    )
    cell_hits_no_domain = _capture_derived(owner, _cell_atom_matches(
        lattice, all_atoms, atom_restrictions, metadata, owner, check_domain=False,
    ).unique(subset=[
        "partition", "scope", "__atom_id", "cell_id", "box_id", "contributor_set_id",
    ]))[0]
    outside_domain = cell_hits_no_domain.join(
        cell_hits.select("partition", "scope", "__atom_id", "cell_id", "box_id"),
        on=["partition", "scope", "__atom_id", "cell_id", "box_id"], how="anti",
    )
    uncovered = source_hits.join(
        cell_hits.select("partition", "scope", "__atom_id").unique(
            subset=["partition", "scope", "__atom_id"],
        ), on=["partition", "scope", "__atom_id"], how="anti",
    )
    cell_atom_counts = cell_hits.select(
        "partition", "scope", "__atom_id", "cell_id",
    ).unique(subset=["partition", "scope", "__atom_id", "cell_id"]).group_by(
        "partition", "scope", "__atom_id",
    ).agg(ma.count_records().alias("__cell_count")).filter(
        ma.col("__cell_count").gt(1)
    )
    boxes_per_cell = cell_hits.select(
        "partition", "scope", "__atom_id", "cell_id", "box_id",
    ).unique(subset=["partition", "scope", "__atom_id", "cell_id", "box_id"]).group_by(
        "partition", "scope", "__atom_id", "cell_id",
    ).agg(ma.count_records().alias("__box_count")).filter(
        ma.col("__box_count").gt(1)
    )
    actual_members = cell_hits.join(
        lattice.contributors,
        on=["partition", "scope", "contributor_set_id"],
        how="inner",
    ).select(
        "partition", "scope", "__atom_id", "cell_id", "source_uuid",
    ).unique(subset=["partition", "scope", "__atom_id", "cell_id", "source_uuid"])
    unowned = cell_hits.select(
        "partition", "scope", "__atom_id", "cell_id",
    ).unique(subset=["partition", "scope", "__atom_id", "cell_id"]).join(
        source_hits.select("partition", "scope", "__atom_id").unique(
            subset=["partition", "scope", "__atom_id"],
        ), on=["partition", "scope", "__atom_id"], how="anti",
    )
    expected_members = cell_hits.select(
        "partition", "scope", "__atom_id", "cell_id",
    ).unique(subset=["partition", "scope", "__atom_id", "cell_id"]).join(
        source_hits,
        on=["partition", "scope", "__atom_id"],
        how="inner",
    ).select("partition", "scope", "__atom_id", "cell_id", "source_uuid")
    missing_members = expected_members.join(
        actual_members,
        on=["partition", "scope", "__atom_id", "cell_id", "source_uuid"],
        how="anti",
    )
    extra_members = actual_members.join(
        expected_members,
        on=["partition", "scope", "__atom_id", "cell_id", "source_uuid"],
        how="anti",
    )
    nonempty_cells = lattice.cells.select("partition", "scope", "cell_id")
    represented_cells = cell_hits.select(
        "partition", "scope", "cell_id",
    ).unique(subset=["partition", "scope", "cell_id"])
    empty_cells = nonempty_cells.join(
        represented_cells, on=["partition", "scope", "cell_id"], how="anti",
    )
    return (
        uncovered, unowned, cell_atom_counts, missing_members, extra_members,
        outside_domain, empty_cells, boxes_per_cell, valid_atoms, cell_hits,
    )


def _atom_face_edges(atoms: Relation, metadata: DimensionsMetadata) -> Relation:
    """Return full-joint, face-adjacent ordered atom pairs."""
    keys = ("partition", "scope")
    left_exprs = [
        ma.col(key).alias(key) for key in keys
    ] + [ma.col("__atom_id").alias("__left_atom")]
    right_exprs = [
        ma.col(key).alias(f"{key}__right") for key in keys
    ] + [ma.col("__atom_id").alias("__right_atom")]
    endpoint_names = (
        "lower", "upper", "lower_inclusive", "upper_inclusive",
        "lower_unbounded", "upper_unbounded",
    )
    for index, dimension in enumerate(metadata.dimensions):
        if dimension.match_strategy not in (
            MatchStrategy.RANGE, MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN,
        ):
            left_exprs.append(ma.col(f"__axis_id_{index}").alias(f"__laxis{index}"))
            right_exprs.append(ma.col(f"__axis_id_{index}").alias(f"__raxis{index}"))
            continue
        for name in endpoint_names:
            left_exprs.append(ma.col(f"__a{index}_{name}").alias(f"__l{index}_{name}"))
            right_exprs.append(ma.col(f"__a{index}_{name}").alias(f"__r{index}_{name}"))
        left_exprs.append(ma.col(f"__axis_id_{index}").alias(f"__laxis{index}"))
        right_exprs.append(ma.col(f"__axis_id_{index}").alias(f"__raxis{index}"))
    left = atoms.select(*left_exprs)
    right = atoms.select(*right_exprs)
    cross = left.join(right, how="cross").filter(
        *[ma.col(key).eq(ma.col(f"{key}__right")) for key in keys],
        ma.col("__left_atom").lt(ma.col("__right_atom")),
    )
    edges = []
    seen_fields: set[str] = set()
    for touching_index, dimension in enumerate(metadata.dimensions):
        if dimension.match_strategy not in (
            MatchStrategy.RANGE, MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN,
        ):
            continue
        touching_field = dimension.resolved_context_field
        if touching_field in seen_fields:
            continue
        seen_fields.add(touching_field)
        right_lower = ma.col(f"__r{touching_index}_lower")
        left_lower = ma.col(f"__l{touching_index}_lower")
        right_upper = ma.col(f"__r{touching_index}_upper")
        left_upper = ma.col(f"__l{touching_index}_upper")
        left_closed = ma.col(f"__l{touching_index}_upper_inclusive")
        right_lower_closed = ma.col(f"__r{touching_index}_lower_inclusive")
        right_closed = ma.col(f"__r{touching_index}_upper_inclusive")
        left_lower_closed = ma.col(f"__l{touching_index}_lower_inclusive")
        direct_lr = left_upper.eq(right_lower) & (left_closed | right_lower_closed)
        direct_rl = left_lower.eq(right_upper) & (right_closed | left_lower_closed)
        successor_lr = ma.lit(False)
        successor_rl = ma.lit(False)
        if dimension.data_type is DataType.INT:
            maximum = (1 << 63) - 1
            successor_lr = (
                ma.col(f"__l{touching_index}_lower").eq(left_upper)
                & left_upper.lt(maximum)
                & left_upper.add(1).eq(right_lower)
            )
            successor_rl = (
                ma.col(f"__r{touching_index}_lower").eq(right_upper)
                & right_upper.lt(maximum)
                & right_upper.add(1).eq(left_lower)
            )
        elif dimension.data_type is DataType.DECIMAL:
            assert dimension.numeric_dtype is not None
            quantum = Decimal((0, (1,), -dimension.numeric_dtype.scale))
            extent = Decimal((
                0, (1,), dimension.numeric_dtype.precision
                - dimension.numeric_dtype.scale,
            ))
            decimal_maximum = decimal_subtract_exact(extent, quantum)
            successor_lr = (
                left_upper.lt(ma.lit(decimal_maximum))
                & left_upper.add(ma.lit(quantum)).eq(right_lower)
            )
            successor_rl = (
                right_upper.lt(ma.lit(decimal_maximum))
                & right_upper.add(ma.lit(quantum)).eq(left_lower)
            )
        elif dimension.data_type is DataType.DATE:
            successor_lr = (
                left_upper.lt(ma.lit(date.max))
                & left_upper.dt.add_days(1).eq(right_lower)
            )
            successor_rl = (
                right_upper.lt(ma.lit(date.max))
                & right_upper.dt.add_days(1).eq(left_lower)
            )
        elif dimension.data_type is DataType.DATETIME:
            upper_bound = (
                datetime.max.replace(tzinfo=timezone.utc)
                if _context_timezone(metadata, dimension) == "utc"
                else datetime.max
            )
            successor_lr = (
                left_upper.lt(ma.lit(upper_bound))
                & left_upper.dt.add_microseconds(1).eq(right_lower)
            )
            successor_rl = (
                right_upper.lt(ma.lit(upper_bound))
                & right_upper.dt.add_microseconds(1).eq(left_lower)
            )
        else:
            continue
        touching = direct_lr | direct_rl | successor_lr | successor_rl
        same_other_axes = [
            ma.col(f"__laxis{index}").eq(ma.col(f"__raxis{index}"))
            for index, other in enumerate(metadata.dimensions)
            if other.resolved_context_field != touching_field
        ]
        candidates = cross.filter(touching, *same_other_axes)
        restrictions: dict[str, FieldRestriction] = {}
        for index, other in enumerate(metadata.dimensions):
            if other.match_strategy not in (
                MatchStrategy.RANGE, MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN,
            ):
                continue
            if other.resolved_context_field in restrictions:
                continue
            if other.resolved_context_field == touching_field:
                lower = ma.when(successor_lr).then(left_upper).when(
                    successor_rl,
                ).then(right_upper).when(direct_lr).then(left_upper).otherwise(
                    right_upper,
                )
                upper = ma.when(successor_lr).then(right_lower).when(
                    successor_rl,
                ).then(left_lower).when(direct_lr).then(left_upper).otherwise(
                    right_upper,
                )
                interval = Interval(
                    lower, upper, ma.lit(True), ma.lit(True), ma.lit(False), ma.lit(False),
                )
            else:
                prefix_left = f"__l{index}_"
                interval = Interval(
                    ma.col(f"{prefix_left}lower"),
                    ma.col(f"{prefix_left}upper"),
                    ma.col(f"{prefix_left}lower_inclusive"),
                    ma.col(f"{prefix_left}upper_inclusive"),
                    ma.col(f"{prefix_left}lower_unbounded"),
                    ma.col(f"{prefix_left}upper_unbounded"),
                )
            restrictions[other.resolved_context_field] = FieldRestriction(
                data_type=other.data_type,
                intervals=(interval,),
                numeric_dtype=other.numeric_dtype,
                timezone=_context_timezone(metadata, other),
                rounding=other.rounding,
            )
        feasible = filter_feasible_pairs(candidates, metadata, {}, restrictions)
        edges.append(feasible.select(
            *keys, "__left_atom", "__right_atom",
        ))
    if edges:
        return concat(edges, distinct=True)
    return atoms.filter(ma.lit(False)).select(
        *keys, ma.col("__atom_id").alias("__left_atom"),
        ma.col("__atom_id").alias("__right_atom"),
    )


def _canonical_findings(
    lattice: Lattice,
    atoms: Relation,
    cell_hits: Relation,
    metadata: DimensionsMetadata,
    owner: t.Any,
) -> Relation:
    edges = _capture_derived(owner, _atom_face_edges(atoms, metadata))[0]
    categorical_indices = [
        index for index, dimension in enumerate(metadata.dimensions)
        if dimension.match_strategy not in (MatchStrategy.RANGE, MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN)
    ]
    if categorical_indices:
        ordered_indices = [
            index for index, dimension in enumerate(metadata.dimensions)
            if dimension.match_strategy is MatchStrategy.RANGE
        ]
        hit_axes = cell_hits.join(
            atoms.select(
                "partition", "scope", "__atom_id",
                *[f"__axis_id_{index}" for index in ordered_indices],
            ), on=["partition", "scope", "__atom_id"], how="inner",
        )
        left = hit_axes.select(
            "partition", "scope", "cell_id", "box_id",
            ma.col("__atom_id").alias("__left_atom"),
            *[ma.col(f"__axis_id_{index}").alias(f"__left_axis{index}") for index in ordered_indices],
        )
        right = hit_axes.select(
            ma.col("partition").alias("__right_partition"),
            ma.col("scope").alias("__right_scope"),
            ma.col("cell_id").alias("__right_cell"),
            ma.col("box_id").alias("__right_box"),
            ma.col("__atom_id").alias("__right_atom"),
            *[ma.col(f"__axis_id_{index}").alias(f"__right_axis{index}") for index in ordered_indices],
        )
        co_box = left.join(right, how="cross").filter(
            ma.col("partition").eq(ma.col("__right_partition"))
            & ma.col("scope").eq(ma.col("__right_scope"))
            & ma.col("cell_id").eq(ma.col("__right_cell"))
            & ma.col("box_id").eq(ma.col("__right_box"))
            & ma.col("__left_atom").lt(ma.col("__right_atom")),
            *[
                ma.col(f"__left_axis{index}").eq(ma.col(f"__right_axis{index}"))
                for index in ordered_indices
            ],
        ).select(
            "partition", "scope", "cell_id", "__left_atom", "__right_atom",
        )
    if categorical_indices:
        co_box = _capture_derived(owner, co_box)[0]
    edges = _capture_derived(owner, edges)[0]
    nodes = _capture_derived(owner, cell_hits.select(
        "partition", "scope", "cell_id", "__atom_id",
    ).unique(subset=["partition", "scope", "cell_id", "__atom_id"]))[0]
    left_nodes = nodes.select(
        "partition", "scope", "cell_id", ma.col("__atom_id").alias("__left_atom"),
    )
    right_nodes = nodes.select(
        "partition", "scope", "cell_id", ma.col("__atom_id").alias("__right_atom"),
    )
    cell_edges = edges.join(
        left_nodes, on=["partition", "scope", "__left_atom"], how="inner",
    ).select("partition", "scope", "cell_id", "__left_atom", "__right_atom").join(
        right_nodes, on=["partition", "scope", "cell_id", "__right_atom"], how="inner",
    ).select("partition", "scope", "cell_id", "__left_atom", "__right_atom")
    if categorical_indices:
        cell_edges = concat([
            cell_edges,
            co_box.select(
                "partition", "scope", "cell_id", "__left_atom", "__right_atom",
            ),
        ], distinct=True)
    forward = cell_edges.select(
        "partition", "scope", "cell_id",
        ma.col("__left_atom").alias("__node"),
        ma.col("__right_atom").alias("__neighbor"),
    )
    reverse = cell_edges.select(
        "partition", "scope", "cell_id",
        ma.col("__right_atom").alias("__node"),
        ma.col("__left_atom").alias("__neighbor"),
    )
    neighbors = concat([forward, reverse], distinct=True)
    neighbors = _capture_derived(owner, neighbors)[0]
    labels = nodes.select(
        "partition", "scope", "cell_id",
        ma.col("__atom_id").alias("__node"),
        ma.col("__atom_id").alias("__label"),
    )
    labels = _capture_derived(owner, labels)[0]
    while True:
        lookup = labels.select(
            "partition", "scope", "cell_id",
            ma.col("__node").alias("__neighbor"), "__label",
        )
        propagated = neighbors.join(
            lookup, on=["partition", "scope", "cell_id", "__neighbor"], how="inner",
        ).select("partition", "scope", "cell_id", "__node", "__label")
        revised = concat([labels, propagated]).group_by(
            "partition", "scope", "cell_id", "__node",
        ).agg(ma.col("__label").min().alias("__label"))
        revised = _capture_derived(owner, revised)[0]
        changed = revised.join(
            labels, on=["partition", "scope", "cell_id", "__node"],
            how="inner", suffix="__old",
        ).filter(ma.col("__label").ne(ma.col("__label__old"))).count_rows()
        labels = revised
        if not changed:
            break
    components = labels.select(
        "partition", "scope", "cell_id", "__label",
    ).unique(subset=["partition", "scope", "cell_id", "__label"]).group_by(
        "partition", "scope", "cell_id",
    ).agg(ma.count_records().alias("__components")).filter(
        ma.col("__components").gt(1)
    )
    diagnostics = []
    diagnostics.append(_diagnostic_rows(components, "disconnected_compound_cell"))

    left_cells = cell_hits.select(
        "partition", "scope", ma.col("cell_id").alias("__left_cell"),
        ma.col("__atom_id").alias("__left_atom"),
    ).unique(subset=["partition", "scope", "__left_cell", "__left_atom"])
    right_cells = cell_hits.select(
        "partition", "scope", ma.col("cell_id").alias("__right_cell"),
        ma.col("__atom_id").alias("__right_atom"),
    ).unique(subset=["partition", "scope", "__right_cell", "__right_atom"])
    adjacent_cells = edges.join(
        left_cells, on=["partition", "scope", "__left_atom"], how="inner",
    ).join(
        right_cells, on=["partition", "scope", "__right_atom"], how="inner",
    ).filter(ma.col("__left_cell").ne(ma.col("__right_cell"))).select(
        "partition", "scope",
        ma.when(ma.col("__left_cell").lt(ma.col("__right_cell")))
        .then(ma.col("__left_cell")).otherwise(ma.col("__right_cell")).alias("__left_cell"),
        ma.when(ma.col("__left_cell").lt(ma.col("__right_cell")))
        .then(ma.col("__right_cell")).otherwise(ma.col("__left_cell")).alias("__right_cell"),
    ).unique(subset=["partition", "scope", "__left_cell", "__right_cell"])
    membership = lattice.cells.join(
        lattice.contributors, on=["partition", "scope", "contributor_set_id"], how="inner",
    ).select(
        "partition", "scope", ma.col("cell_id").alias("__member_cell"),
        ma.col("source_uuid").alias("__member_source"),
    ).unique(subset=["partition", "scope", "__member_cell", "__member_source"])
    left_members = membership.select(
        "partition", "scope", ma.col("__member_cell").alias("__left_cell"),
        ma.col("__member_source").alias("__source"),
    )
    right_members = membership.select(
        "partition", "scope", ma.col("__member_cell").alias("__right_cell"),
        ma.col("__member_source").alias("__source"),
    )
    left_only = adjacent_cells.join(
        left_members, on=["partition", "scope", "__left_cell"], how="inner",
    ).join(
        right_members,
        left_on=["partition", "scope", "__right_cell", "__source"],
        right_on=["partition", "scope", "__right_cell", "__source"],
        how="anti",
    )
    right_only = adjacent_cells.join(
        right_members, on=["partition", "scope", "__right_cell"], how="inner",
    ).join(
        left_members,
        left_on=["partition", "scope", "__left_cell", "__source"],
        right_on=["partition", "scope", "__left_cell", "__source"],
        how="anti",
    )
    unequal = concat([
        left_only.select("partition", "scope", "__left_cell", "__right_cell"),
        right_only.select("partition", "scope", "__left_cell", "__right_cell"),
    ], distinct=True)
    split = adjacent_cells.join(
        unequal, on=["partition", "scope", "__left_cell", "__right_cell"], how="anti",
    )
    diagnostics.append(_diagnostic_rows(split, "split_connected_component"))
    return concat(diagnostics) if diagnostics else _empty_diagnostics(lattice.cells)

def _fold_findings(lattice: Lattice, sources: t.Any) -> Relation:
    diagnostics = []
    keys = ["partition", "scope", "contributor_set_id"]
    metadata = lattice.metadata
    scopes = metadata.outcome_scopes or {"default": metadata.aggregates}
    for scope, aggregates in scopes.items():
        memberships = lattice.contributors.filter(ma.col("scope").eq(scope))
        payload = sources.payloads[scope]
        missing_payload = memberships.join(
            payload.select("partition", "scope", "source_uuid"),
            on=["partition", "scope", "source_uuid"], how="anti",
        )
        diagnostics.append(_diagnostic_rows(
            missing_payload, "missing_contributor_payload", source="source_uuid",
        ))
        distinct_memberships = memberships.unique(
            subset=["partition", "scope", "contributor_set_id", "source_uuid"],
        )
        joined = distinct_memberships.join(
            payload, on=["partition", "scope", "source_uuid"], how="inner",
        ).select(
            *keys,
            *[ma.col(f"a{index:04d}") for index in range(1, len(aggregates) + 1)],
        )
        expected = _expr_fold(joined, aggregates, keys)
        actual = lattice.outputs[scope]
        actual_keys = actual.select(*keys)
        missing = expected.join(actual_keys, on=keys, how="anti")
        extra = actual_keys.join(expected.select(*keys), on=keys, how="anti")
        diagnostics.append(_diagnostic_rows(missing, "missing_output"))
        diagnostics.append(_diagnostic_rows(extra, "extra_output"))
        actual_fields = [f"v{index:04d}" for index in range(1, len(aggregates) + 1)]
        absent = [name for name in actual_fields if name not in actual.columns]
        if absent:
            diagnostics.append(_diagnostic_rows(expected, "missing_output_column"))
        else:
            compared = expected.join(
                actual, on=keys, how="inner", suffix="__actual",
            )
            differs = ma.lit(False)
            for name in actual_fields:
                differs = differs | ~ma.col(name).eq(
                    ma.col(f"{name}__actual")
                ).fill_null(False)
            mismatch = compared.filter(differs).select(*keys)
            diagnostics.append(_diagnostic_rows(mismatch, "fold_mismatch"))
        duplicate_memberships = _duplicates(
            memberships, ["partition", "scope", "contributor_set_id", "source_uuid"],
        )
        diagnostics.append(_diagnostic_rows(
            duplicate_memberships, "duplicate_membership", source="source_uuid",
        ))
        duplicate_outputs = _duplicates(actual, keys)
        diagnostics.append(_diagnostic_rows(duplicate_outputs, "duplicate_output"))
        nulls = actual.filter(ma.lit(False))
        for name in actual_fields:
            nulls = concat([nulls, actual.filter(ma.col(name).is_null())])
        diagnostics.append(_diagnostic_rows(nulls, "null_output"))
    return concat(diagnostics) if diagnostics else _empty_diagnostics(lattice.cells)


def _table_findings(lattice: Lattice) -> Relation:
    diagnostics = []
    descriptors = lattice.table_descriptors
    descriptor_ids = [descriptor.table_id for descriptor in descriptors]
    if len(descriptor_ids) != len(set(descriptor_ids)) or set(descriptor_ids) != set(lattice.tables):
        raise ValueError("Lattice table descriptors do not identify every captured table exactly once")
    for descriptor in descriptors:
        table = lattice.tables[descriptor.table_id]
        if tuple(table.schema.items()) != descriptor.schema:
            raise ValueError(f"Captured table {descriptor.table_id!r} disagrees with its declared schema")
        if not descriptor.key or any(name not in table.columns for name in descriptor.key):
            raise ValueError(f"Captured table {descriptor.table_id!r} has an invalid declared key")
        duplicate = _duplicates(table, descriptor.key)
        if descriptor.role == "source_rules":
            metadata = lattice.metadata
            duplicate = duplicate.with_columns(
                (ma.col(metadata.partition_field) if metadata.partition_field else ma.lit("default")).alias("partition"),
                (ma.col(metadata.outcome_scope_field) if metadata.outcome_scope_field else ma.lit("default")).alias("scope"),
            )
        diagnostics.append(_diagnostic_rows(duplicate, "duplicate_table_key"))
    return concat(diagnostics) if diagnostics else _empty_diagnostics(lattice.cells)


def _identity_findings(lattice: Lattice, sources: t.Any) -> Relation:
    diagnostics = []
    cells = lattice.cells
    memberships = lattice.contributors
    source_ids = sources.identities.select("partition", "scope", "source_uuid")
    cell_ids = cells.select("partition", "scope", "cell_id", "contributor_set_id")
    bad_cells = cells.join(
        source_ids.select("partition", "scope").unique(subset=["partition", "scope"]),
        on=["partition", "scope"], how="anti",
    )
    diagnostics.append(_diagnostic_rows(bad_cells, "unowned_cell"))
    bad_members = memberships.join(source_ids, on=["partition", "scope", "source_uuid"], how="anti")
    diagnostics.append(_diagnostic_rows(bad_members, "unqualified_contributor", source="source_uuid"))
    bad_sets = memberships.select("partition", "scope", "contributor_set_id").join(
        cell_ids.select("partition", "scope", "contributor_set_id").unique(
            subset=["partition", "scope", "contributor_set_id"],
        ), on=["partition", "scope", "contributor_set_id"], how="anti",
    )
    diagnostics.append(_diagnostic_rows(bad_sets, "unreferenced_contributor_set"))
    missing_members = cell_ids.select("partition", "scope", "contributor_set_id").join(
        memberships.select("partition", "scope", "contributor_set_id").unique(
            subset=["partition", "scope", "contributor_set_id"],
        ), on=["partition", "scope", "contributor_set_id"], how="anti",
    )
    diagnostics.append(_diagnostic_rows(missing_members, "empty_cell_membership"))
    for relation_, key, kind in (
        (lattice.cell_boxes, "cell_id", "orphan_cell_box"),
        (lattice.contributors, "contributor_set_id", "orphan_membership"),
    ):
        orphans = relation_.join(
            cells.select("partition", "scope", key),
            on=["partition", "scope", key], how="anti",
        )
        diagnostics.append(_diagnostic_rows(orphans, kind))
    set_ids = memberships.select(
        "partition", "scope", ma.col("contributor_set_id").alias("__left_set"),
    ).unique(subset=["partition", "scope", "__left_set"])
    right_ids = memberships.select(
        "partition", "scope", ma.col("contributor_set_id").alias("__right_set"),
    ).unique(subset=["partition", "scope", "__right_set"])
    set_pairs = set_ids.join(right_ids, on=["partition", "scope"], how="inner").filter(
        ma.col("__left_set").lt(ma.col("__right_set"))
    )
    left_edges = memberships.select(
        "partition", "scope", ma.col("contributor_set_id").alias("__left_set"),
        ma.col("source_uuid").alias("__left_source"),
    )
    right_edges = memberships.select(
        "partition", "scope", ma.col("contributor_set_id").alias("__right_set"),
        ma.col("source_uuid").alias("__right_source"),
    )
    left_only = set_pairs.join(
        left_edges, on=["partition", "scope", "__left_set"], how="inner",
    ).join(
        right_edges,
        left_on=["partition", "scope", "__right_set", "__left_source"],
        right_on=["partition", "scope", "__right_set", "__right_source"],
        how="anti",
    )
    right_only = set_pairs.join(
        right_edges, on=["partition", "scope", "__right_set"], how="inner",
    ).join(
        left_edges,
        left_on=["partition", "scope", "__left_set", "__right_source"],
        right_on=["partition", "scope", "__left_set", "__left_source"],
        how="anti",
    )
    unequal = concat([
        left_only.select("partition", "scope", "__left_set", "__right_set"),
        right_only.select("partition", "scope", "__left_set", "__right_set"),
    ], distinct=True)
    equal_sets = set_pairs.join(
        unequal, on=["partition", "scope", "__left_set", "__right_set"], how="anti",
    )
    diagnostics.append(_diagnostic_rows(equal_sets, "equal_membership_distinct_set_id"))
    cell_set_ids = cell_ids.select(
        "partition", "scope", "contributor_set_id",
    ).unique(subset=["partition", "scope", "contributor_set_id"])
    for output in lattice.outputs.values():
        orphan_outputs = output.select(
            "partition", "scope", "contributor_set_id",
        ).join(cell_set_ids, on=["partition", "scope", "contributor_set_id"], how="anti")
        diagnostics.append(_diagnostic_rows(orphan_outputs, "orphan_output"))
    return concat(diagnostics) if diagnostics else _empty_diagnostics(cells)


ARTIFACT_CHECK_NAMES = (
    "table_integrity", "identity_references", "source_union",
    "disjointness", "membership", "canonical_geometry", "folds",
    "profile_guarantees",
)


def _table_reference(lattice: Lattice, descriptor: t.Any) -> DependencyReference:
    return DependencyReference(
        lattice.tables[descriptor.table_id], descriptor.key, descriptor.schema,
        lattice.version_id, descriptor.capture_id, descriptor.table_id,
    )


def _owned_reference(relation_: Relation, keys: t.Sequence[str],
                     version_id: UUID) -> DependencyReference:
    return DependencyReference(
        relation_, tuple(keys), tuple(relation_.schema.items()), version_id,
    )

def _artifact_witnesses(
    lattice: Lattice,
    sources: t.Any,
    findings: Relation,
    atoms: Relation,
    cell_hits: Relation,
    check_names: t.Sequence[str],
) -> dict[str, Relation]:
    """Attach finding IDs to typed region, geometry and result children."""
    keys = ("partition", "scope")
    axis_fields = tuple(
        name for name in atoms.columns
        if name != "__atom_id"
        and (name.startswith("__a") or name.startswith("__axis_id_"))
    )
    atom_map = atoms.select(
        *keys,
        ma.col("__atom_id").cast(MountainashDtype.I64).alias("witness_atom_id"),
        *axis_fields,
    )
    cell_map = lattice.cell_boxes.select(
        *keys,
        ma.col("cell_id").cast(MountainashDtype.I64).alias("witness_cell_id"),
        ma.col("box_id").cast(MountainashDtype.I64).alias("__stored_box_id"),
    )
    result: dict[str, Relation] = {}
    for name in check_names:
        base = findings.filter(ma.col("check").eq(name))
        result[name] = base.join(
            atom_map, on=[*keys, "witness_atom_id"], how="left",
        ).select(
            "finding_id", "check", "kind", *keys, "field", "source_uuid",
            "witness_cell_id", "witness_box_id", "witness_contributor_set_id",
            "witness_atom_id", *axis_fields,
        )
        hit_cells = cell_hits.select(
            *keys,
            ma.col("__atom_id").cast(MountainashDtype.I64).alias("witness_atom_id"),
            ma.col("cell_id").cast(MountainashDtype.I64).alias("__hit_cell_id"),
            ma.col("box_id").cast(MountainashDtype.I64).alias("__hit_box_id"),
            ma.col("contributor_set_id").cast(MountainashDtype.I64).alias(
                "__hit_contributor_set_id",
            ),
        )
        result[f"{name}:atom_cells"] = base.filter(
            ~ma.col("witness_atom_id").is_null()
        ).join(
            hit_cells, on=[*keys, "witness_atom_id"], how="inner",
        ).select(
            "finding_id", *keys, "witness_atom_id",
            ma.col("__hit_cell_id").alias("cell_id"),
            ma.col("__hit_box_id").alias("box_id"),
            ma.col("__hit_contributor_set_id").alias("contributor_set_id"),
        ).unique(subset=[
            "finding_id", *keys, "witness_atom_id", "cell_id", "box_id",
            "contributor_set_id",
        ])
        cell_findings = base.filter(~ma.col("witness_cell_id").is_null())
        cell_boxes = cell_findings.join(
            cell_map, on=[*keys, "witness_cell_id"], how="inner",
        ).filter(
            ma.col("witness_box_id").is_null()
            | ma.col("witness_box_id").eq(ma.col("__stored_box_id"))
        ).select(
            "finding_id", *keys,
            ma.col("witness_cell_id").alias("cell_id"),
            ma.col("__stored_box_id").alias("box_id"),
        )
        result[f"{name}:cell_boxes"] = cell_boxes
        for dimension in lattice.metadata.dimensions:
            field = dimension.dimension_name
            if field in lattice.cell_ordered_bounds:
                result[f"{name}:ordered:{field}"] = cell_boxes.join(
                    lattice.cell_ordered_bounds[field],
                    on=[*keys, "cell_id", "box_id"], how="inner",
                ).select(
                    "finding_id", *keys, "cell_id", "box_id",
                    "lower_state", "lower_value", "lower_inclusive",
                    "upper_state", "upper_value", "upper_inclusive",
                )
            if field in lattice.cell_category_sets:
                result[f"{name}:category_sets:{field}"] = cell_boxes.join(
                    lattice.cell_category_sets[field],
                    on=[*keys, "cell_id", "box_id"], how="inner",
                ).select(
                    "finding_id", *keys, "cell_id", "box_id",
                    "membership_kind", "residual_class_included",
                )
                result[f"{name}:category_values:{field}"] = cell_boxes.join(
                    lattice.cell_category_values[field],
                    on=[*keys, "cell_id", "box_id"], how="inner",
                ).select("finding_id", *keys, "cell_id", "box_id", "value")
        set_findings = base.filter(
            ~ma.col("witness_contributor_set_id").is_null()
        )
        result[f"{name}:memberships"] = set_findings.join(
            lattice.contributors.select(
                *keys,
                ma.col("contributor_set_id").cast(MountainashDtype.I64).alias(
                    "witness_contributor_set_id",
                ),
                "source_uuid",
            ), on=[*keys, "witness_contributor_set_id"], how="inner",
        ).select(
            "finding_id", *keys, "witness_contributor_set_id", "source_uuid",
        )
        for scope, output in lattice.outputs.items():
            output_values = [column for column in output.columns if column.startswith("v")]
            output_child = set_findings.filter(ma.col("scope").eq(scope)).join(
                output.select(
                    *keys,
                    ma.col("contributor_set_id").cast(MountainashDtype.I64).alias(
                        "witness_contributor_set_id",
                    ),
                    *[ma.col(column).alias(f"stored_{column}") for column in output_values],
                ), on=[*keys, "witness_contributor_set_id"], how="inner",
            ).select(
                "finding_id", *keys, "witness_contributor_set_id",
                *[f"stored_{column}" for column in output_values],
            )
            result[f"{name}:outputs:{scope}"] = output_child
        for scope, payload in sources.payloads.items():
            scoped = base.filter(
                ma.col("scope").eq(scope) & ~ma.col("source_uuid").is_null()
            )
            result[f"{name}:payloads:{scope}"] = scoped.join(
                payload, on=["partition", "scope", "source_uuid"], how="inner",
            ).select(
                "finding_id", *keys, "source_uuid",
                *[column for column in payload.columns if column not in keys + ("source_uuid",)],
            )
    return result


def _owned_report(
    lattice: Lattice,
    sources: t.Any,
    findings_by_check: tuple[tuple[str, tuple[str, ...], Relation], ...],
    owner: t.Any,
    atoms: Relation,
    cell_hits: Relation,
) -> ArtifactReport:
    raw_findings = []
    checks = []
    witnesses: dict[str, Relation] = {}
    for name, groups, findings in findings_by_check:
        retained = owner.detach(_capture_derived(owner, findings)[0])
        count = retained.count_rows()
        checks.append(CheckResult(
            name=name, dependency_groups=groups,
            outcome="findings" if count else "passed",
            finding_count=count, emitted_count=count,
        ))
        raw_findings.append(retained.with_columns(
            ma.lit(name, dtype=MountainashDtype.STRING).alias("check"),
        ))
    combined_with_subject = concat(raw_findings).with_row_index(name="finding_id").with_columns(
        ma.col("finding_id").cast(int).alias("finding_id"),
    )
    witnesses = _artifact_witnesses(
        lattice, sources, combined_with_subject, atoms, cell_hits,
        tuple(name for name, _, _ in findings_by_check),
    )
    subjects = combined_with_subject.filter(
        ~ma.col("source_uuid").is_null()
    ).select("finding_id", "partition", "scope", "source_uuid")
    combined = combined_with_subject.select(
        "finding_id", "check", "severity", "kind", "partition", "scope", "field",
    )
    descriptors = lattice.table_descriptors
    source_table_refs = tuple(
        _table_reference(lattice, descriptor) for descriptor in descriptors
        if descriptor.role in {"source_rules", "source_state"}
    )
    source_input_refs = (
        _owned_reference(sources.rules, _source_rule_key(lattice.metadata, sources.rules),
                         lattice.version_id),
        *(
            _owned_reference(
                relation_, ("partition", "scope", "source_uuid"), lattice.version_id,
            ) for relation_ in sources.states.values()
        ),
    )
    payload_table_refs = tuple(
        _table_reference(lattice, descriptor) for descriptor in descriptors
        if descriptor.role == "source_payloads"
    )
    payload_input_refs = tuple(
        _owned_reference(relation_, ("partition", "scope", "source_uuid"), lattice.version_id)
        for relation_ in sources.payloads.values()
    )
    report_dependencies = {
        "source_structure": source_table_refs + source_input_refs,
        "source_payloads": payload_table_refs + payload_input_refs,
        "cell_geometry": tuple(
            _table_reference(lattice, descriptor) for descriptor in descriptors
            if descriptor.role in {
                "cells", "cell_boxes", "cell_ordered_bounds",
                "cell_category_sets", "cell_category_values",
            }
        ),
        "memberships": tuple(
            _table_reference(lattice, descriptor) for descriptor in descriptors
            if descriptor.role == "contributors"
        ),
        "outputs": tuple(
            _table_reference(lattice, descriptor) for descriptor in descriptors
            if descriptor.role == "outputs"
        ),
    }
    binding = ReportBinding(
        semantic_version="accumulator-artifact-1",
        _metadata_json=lattice._metadata_json,
        dependencies=report_dependencies,
    )
    owned = _capture_derived(owner, combined, subjects, *witnesses.values())
    report_tables = [owner.detach(item) for item in owned]
    combined_owned, subjects_owned = report_tables[:2]
    witnesses_owned = dict(zip(witnesses, report_tables[2:]))
    return ArtifactReport(
        report_id=uuid4(), binding=binding, checks=tuple(checks),
        findings=combined_owned, subjects=subjects_owned,
        witnesses=witnesses_owned,
    )



def _filter_domain_atoms(
    atoms: Relation,
    atom_restrictions: dict[str, FieldRestriction],
    metadata: DimensionsMetadata,
    domains: t.Sequence[DomainDefinition],
    owner: t.Any,
) -> Relation:
    assignments, _, _ = feasible_assignments(
        atoms, metadata, {}, atom_restrictions,
        pair_key="__atom_id", domains=domains,
    )
    feasible = assignments.select("__atom_id").unique(subset=["__atom_id"])
    return _capture_derived(owner, atoms.join(feasible, on="__atom_id", how="inner").select(*atoms.columns))[0]


def _scope_partition_atoms(
    atoms: Relation,
    metadata: DimensionsMetadata,
    scope: t.Any,
) -> Relation:
    if not scope.partition_refs:
        return atoms
    key_dimensions = {
        dimension.dimension_name: (index, dimension)
        for index, dimension in enumerate(metadata.dimensions)
        if dimension.role is DimensionRole.CONTEXT_KEY
    }
    candidates = []
    for reference in scope.partition_refs:
        candidate = atoms
        for entry in reference["key_values"]:
            name = entry["dimension_name"]
            if name not in key_dimensions:
                raise UnsupportedDomainError(
                    f"Cannot project validation partition key {name!r} onto native atoms"
                )
            index, dimension = key_dimensions[name]
            match = entry["match"]
            if match["kind"] == "wildcard":
                continue
            value = decode_scalar(dict(match["value"]), allow_reserved=True)
            literal = exact_literal(value)
            if dimension.data_type in (DataType.INT, DataType.FLOAT):
                literal = literal.cast(_native_dimension_dtype(dimension))
            candidate = candidate.filter(
                ma.col(f"__a{index}_has_value")
                & ma.col(f"__a{index}_value").eq(literal)
            )
        candidates.append(candidate)
    return concat(candidates, distinct=True)


def _equal_values(left: t.Any, right: t.Any) -> t.Any:
    return left.eq(right).fill_null(False) | (left.is_null() & right.is_null())


def _profile_findings(
    lattice: Lattice,
    sources: t.Any,
    atoms: Relation,
    atom_restrictions: dict[str, FieldRestriction],
    cell_hits: Relation,
    metadata: DimensionsMetadata,
    owner: t.Any,
) -> Relation:
    diagnostics = []
    available = {
        name
        for scope, aggregates in (metadata.outcome_scopes or {"default": metadata.aggregates}).items()
        for aggregate in aggregates
        for name in (aggregate.resolved_output_name, f"{scope}.{aggregate.resolved_output_name}")
    }
    domains_by_id = {
        domain.domain_id: domain
        for domain in ([metadata.domain] if metadata.domain is not None else [])
        + list(metadata.context_domains)
    }
    global_domains = () if metadata.domain is None else (metadata.domain,)
    declared_scopes = metadata.outcome_scopes or {"default": metadata.aggregates}

    for requirement in (
        () if metadata.policy is None else metadata.policy.coverage_requirements
    ):
        domain_ids = tuple(dict.fromkeys((
            *requirement.scope.domain_refs,
            requirement.domain_ref,
        )))
        try:
            domains = tuple(domains_by_id[name] for name in domain_ids)
        except KeyError as exc:
            raise UnsupportedDomainError(
                f"Unresolved validation domain {exc.args[0]!r}"
            ) from exc
        all_domains = tuple({
            domain.domain_id: domain for domain in (*global_domains, *domains)
        }.values())
        fields_by_name = {
            field.name: field for domain in all_domains for field in domain.fields
        }
        region_domain = DomainDefinition(
            schema_version=1,
            domain_id=f"validation-region:{requirement.requirement_id}",
            fields=tuple(sorted(fields_by_name.values(), key=lambda field: field.name)),
            predicate_id=requirement.region_predicate_id,
        )
        coverage_atoms = _filter_domain_atoms(
            atoms, atom_restrictions, metadata,
            (*all_domains, region_domain), owner,
        )
        coverage_atoms = _scope_partition_atoms(
            coverage_atoms, metadata, requirement.scope,
        )
        coverage_atoms = _capture_derived(owner, coverage_atoms)[0]
        covered = _source_atom_matches(
            coverage_atoms, atom_restrictions, sources, metadata, owner,
        ).select("partition", "scope", "__atom_id").unique(
            subset=["partition", "scope", "__atom_id"],
        )
        gaps = coverage_atoms.join(
            covered, on=["partition", "scope", "__atom_id"], how="anti",
        )
        diagnostics.append(_diagnostic_rows(
            gaps, "coverage_gap", severity=requirement.severity,
        ))

    for contract in metadata.context_contracts:
        provider = domains_by_id.get(contract.domain_ref)
        if provider is None:
            raise UnsupportedDomainError(
                f"Unresolved provider domain {contract.domain_ref!r}"
            )
        provider_domains = tuple({
            domain.domain_id: domain for domain in (*global_domains, provider)
        }.values())
        provider_atoms = _filter_domain_atoms(
            atoms, atom_restrictions, metadata, provider_domains, owner,
        )
        context_fields_by_name = {field.name: field for field in contract.fields}
        for profile in contract.profiles:
            missing = set(profile.output_fields) - available
            for output_field in sorted(missing):
                diagnostics.append(_diagnostic_rows(
                    lattice.cells, "missing_profile_output", field=output_field,
                ))
            if profile.promise != "definite_outcome":
                continue

            observed_indices: list[int] = []
            for index, dimension in enumerate(metadata.dimensions):
                context_field = context_fields_by_name.get(dimension.resolved_context_field)
                if (
                    dimension.dimension_name in profile.dimensions
                    and dimension.dimension_name not in profile.allow_dont_care
                    and context_field is not None
                    and context_field.required
                ):
                    observed_indices.append(index)

            left_columns = [
                ma.col(key).alias(key) for key in ("partition", "scope")
            ] + [
                ma.col("__atom_id").alias("__left_atom"),
                *[
                    ma.col(f"__axis_id_{index}").alias(f"__left_axis_{index}")
                    for index in observed_indices
                ],
            ]
            right_columns = [
                ma.col(key).alias(f"{key}__right")
                for key in ("partition", "scope")
            ] + [
                ma.col("__atom_id").alias("__right_atom"),
                *[
                    ma.col(f"__axis_id_{index}").alias(f"__right_axis_{index}")
                    for index in observed_indices
                ],
            ]
            left = provider_atoms.select(*left_columns)
            right = provider_atoms.select(*right_columns)
            pairs = left.join(right, how="cross").filter(
                ma.col("partition").eq(ma.col("partition__right")),
                ma.col("scope").eq(ma.col("scope__right")),
                ma.col("__left_atom").lt(ma.col("__right_atom")),
                *[
                    ma.col(f"__left_axis_{index}").eq(
                        ma.col(f"__right_axis_{index}")
                    )
                    for index in observed_indices
                ],
            ).select(
                "partition", "scope", "__left_atom", "__right_atom",
            ).with_row_index(name="__pair_id")
            pairs = _capture_derived(owner, pairs)[0]
            if observed_indices:
                assignments, assignment_names, _ = feasible_assignments(
                    provider_atoms, metadata, {}, atom_restrictions,
                    pair_key="__atom_id", domains=provider_domains,
                )
                observed_fields = tuple(dict.fromkeys(
                    metadata.dimensions[index].resolved_context_field
                    for index in observed_indices
                ))
                left_assignments = assignments.select(
                    ma.col("__atom_id").alias("__left_atom"),
                    *[
                        ma.col(assignment_names[field]).alias(f"__left_value_{index}")
                        for index, field in enumerate(observed_fields)
                    ],
                )
                right_assignments = assignments.select(
                    ma.col("__atom_id").alias("__right_atom"),
                    *[
                        ma.col(assignment_names[field]).alias(f"__right_value_{index}")
                        for index, field in enumerate(observed_fields)
                    ],
                )
                pairs = pairs.join(
                    left_assignments, on="__left_atom", how="inner",
                ).join(
                    right_assignments, on="__right_atom", how="inner",
                ).filter(*[
                    _equal_values(
                        ma.col(f"__left_value_{index}"),
                        ma.col(f"__right_value_{index}"),
                    )
                    for index in range(len(observed_fields))
                ]).select(*pairs.columns)
                pairs = _capture_derived(owner, pairs)[0]

            for scope, aggregates in declared_scopes.items():
                requested = [
                    aggregate for aggregate in aggregates
                    if aggregate.resolved_output_name in profile.output_fields
                    or f"{scope}.{aggregate.resolved_output_name}" in profile.output_fields
                ]
                if not requested:
                    continue
                output = lattice.outputs.get(scope)
                if output is None:
                    continue
                column_map = lattice.output_columns.get(scope, {})
                selected: list[tuple[Aggregate, str]] = []
                for aggregate in requested:
                    column = column_map.get(aggregate.resolved_output_name)
                    if column is not None and column in output.columns:
                        selected.append((aggregate, column))
                if not selected:
                    continue

                scope_atoms = provider_atoms.filter(ma.col("scope").eq(scope))
                cells = cell_hits.filter(ma.col("scope").eq(scope)).join(
                    scope_atoms.select("partition", "scope", "__atom_id"),
                    on=["partition", "scope", "__atom_id"], how="inner",
                ).select(
                    "partition", "scope", "__atom_id", "cell_id",
                    "contributor_set_id",
                ).unique(subset=[
                    "partition", "scope", "__atom_id", "cell_id",
                    "contributor_set_id",
                ])
                outcomes = scope_atoms.select(
                    "partition", "scope", "__atom_id",
                ).join(
                    cells,
                    on=["partition", "scope", "__atom_id"],
                    how="left",
                ).with_columns(
                    ma.col("contributor_set_id").cast(MountainashDtype.I64).alias(
                        "__output_set_id",
                    ),
                )
                output_values = output.select(
                    "partition", "scope",
                    ma.col("contributor_set_id").cast(MountainashDtype.I64).alias(
                        "__output_set_id",
                    ),
                    *[
                        ma.col(column).alias(f"__output_{index}")
                        for index, (_, column) in enumerate(selected)
                    ],
                )
                outcomes = outcomes.join(
                    output_values,
                    on=["partition", "scope", "__output_set_id"],
                    how="left",
                )
                left_outcomes = outcomes.select(
                    "partition", "scope",
                    ma.col("__atom_id").alias("__left_atom"),
                    ma.col("cell_id").alias("__left_cell_id"),
                    ma.col("contributor_set_id").alias("__left_set_id"),
                    *[
                        ma.col(f"__output_{index}").alias(f"__left_output_{index}")
                        for index in range(len(selected))
                    ],
                )
                right_outcomes = outcomes.select(
                    "partition", "scope",
                    ma.col("__atom_id").alias("__right_atom"),
                    ma.col("cell_id").alias("__right_cell_id"),
                    ma.col("contributor_set_id").alias("__right_set_id"),
                    *[
                        ma.col(f"__output_{index}").alias(f"__right_output_{index}")
                        for index in range(len(selected))
                    ],
                )
                compared = pairs.filter(ma.col("scope").eq(scope)).join(
                    left_outcomes, on=["partition", "scope", "__left_atom"],
                    how="inner",
                ).join(
                    right_outcomes, on=["partition", "scope", "__right_atom"],
                    how="inner",
                )
                different = ma.lit(False)
                for index in range(len(selected)):
                    different = different | ~_equal_values(
                        ma.col(f"__left_output_{index}"),
                        ma.col(f"__right_output_{index}"),
                    )
                if profile.provenance == "cell":
                    different = different | ~_equal_values(
                        ma.col("__left_cell_id"), ma.col("__right_cell_id"),
                    )
                bad = compared.filter(different)

                if profile.provenance in {"contributors", "cell"}:
                    members = lattice.contributors.select(
                        "partition", "scope",
                        ma.col("contributor_set_id").cast(
                            MountainashDtype.I64,
                        ).alias("__member_set_id"),
                        "source_uuid",
                    )
                    member_keys = (
                        "__pair_id", "__left_set_id", "__right_set_id",
                    )
                    left_members = compared.select(
                        "partition", "scope", *member_keys,
                    ).join(
                        members,
                        left_on=["partition", "scope", "__left_set_id"],
                        right_on=["partition", "scope", "__member_set_id"],
                        how="inner",
                    ).select(*member_keys, "source_uuid").unique(
                        subset=[*member_keys, "source_uuid"],
                    )
                    right_members = compared.select(
                        "partition", "scope", *member_keys,
                    ).join(
                        members,
                        left_on=["partition", "scope", "__right_set_id"],
                        right_on=["partition", "scope", "__member_set_id"],
                        how="inner",
                    ).select(*member_keys, "source_uuid").unique(
                        subset=[*member_keys, "source_uuid"],
                    )
                    membership_diff = concat([
                        left_members.join(
                            right_members,
                            on=[*member_keys, "source_uuid"],
                            how="anti",
                        ).select(*member_keys),
                        right_members.join(
                            left_members,
                            on=[*member_keys, "source_uuid"],
                            how="anti",
                        ).select(*member_keys),
                    ], distinct=True)
                    bad = concat([
                        bad.select(*compared.columns),
                        compared.join(
                            membership_diff,
                            on=list(member_keys),
                            how="inner",
                        ).select(*compared.columns),
                    ], distinct=True)

                counterexamples = bad.select(
                    "partition", "scope",
                    ma.col("__left_atom").alias("__atom_id"),
                    ma.when(ma.col("__left_cell_id").is_null())
                    .then(ma.col("__right_cell_id"))
                    .otherwise(ma.col("__left_cell_id")).alias("cell_id"),
                    ma.when(ma.col("__left_set_id").is_null())
                    .then(ma.col("__right_set_id"))
                    .otherwise(ma.col("__left_set_id")).alias("contributor_set_id"),
                ).unique(subset=[
                    "partition", "scope", "__atom_id", "cell_id",
                    "contributor_set_id",
                ])
                diagnostics.append(_diagnostic_rows(
                    counterexamples, "profile_counterexample",
                ))

    return concat(diagnostics) if diagnostics else _empty_diagnostics(lattice.cells)


def validate(lattice: Lattice, rules: Relation) -> ArtifactReport:
    """Independently validate actual stored tables against the supplied sources."""
    if not isinstance(lattice, Lattice):
        raise TypeError("lattice must be a captured Lattice")
    if not isinstance(rules, Relation):
        raise TypeError("rules must be a Mountainash Relation")
    metadata = lattice.metadata
    source_bundle = capture_sources(rules, metadata)
    with materialization_scope() as owner:
        table_findings = _table_findings(lattice)
        identity_findings = _identity_findings(lattice, source_bundle)
        fold_findings = _fold_findings(lattice, source_bundle)
        atoms, atom_restrictions = _range_atoms(lattice, source_bundle, owner)
        (
            uncovered, unowned, overlaps, missing_members, extra_members,
            outside_domain, empty_cells, duplicate_boxes, valid_atoms, cell_hits,
        ) = _range_geometry_findings(
            lattice, source_bundle, atoms, atom_restrictions, metadata, owner,
        )
        source_union = concat([
            _diagnostic_rows(uncovered, "source_union_gap", source="source_uuid"),
            _diagnostic_rows(unowned, "unowned_cell_region"),
            _diagnostic_rows(outside_domain, "out_of_domain_cell"),
        ])
        disjointness = _diagnostic_rows(overlaps, "overlapping_cells")
        membership = concat([
            _diagnostic_rows(missing_members, "missing_membership", source="source_uuid"),
            _diagnostic_rows(extra_members, "extra_membership", source="source_uuid"),
            _diagnostic_rows(empty_cells, "empty_cell"),
        ])
        canonical = concat([
            _diagnostic_rows(duplicate_boxes, "overlapping_compound_boxes"),
            _canonical_findings(lattice, valid_atoms, cell_hits, metadata, owner),
        ])
        profile = _profile_findings(
            lattice, source_bundle, atoms, atom_restrictions, cell_hits, metadata, owner,
        )
        findings_by_check = (
            ("table_integrity", ("cell_geometry", "memberships", "outputs"), table_findings),
            ("identity_references", ("source_structure", "cell_geometry", "memberships", "outputs"),
             identity_findings),
            ("source_union", ("source_structure", "cell_geometry"), source_union),
            ("disjointness", ("cell_geometry",), disjointness),
            ("membership", ("source_structure", "cell_geometry", "memberships"), membership),
            ("canonical_geometry", ("cell_geometry", "memberships"), canonical),
            ("folds", ("source_payloads", "memberships", "outputs"), fold_findings),
            ("profile_guarantees", ("source_structure", "cell_geometry", "memberships", "outputs"),
             profile),
        )
        return _owned_report(
            lattice, source_bundle, findings_by_check, owner, atoms, cell_hits,
        )


def approve(report: SourceReport | ArtifactReport, finding_ids: t.Iterable[int]) -> Approval:
    """Approve existing warning findings, never hard findings or foreign IDs."""
    ids = tuple(finding_ids)
    if len(ids) != len(set(ids)):
        raise ValueError("Finding approvals cannot contain duplicate IDs")
    selected = report.findings.filter(
        ma.col("finding_id").is_in(list(ids))
    )
    if selected.count_rows() != len(ids):
        raise ValueError("Approval names a finding not present exactly once in this report")
    if selected.filter(~ma.col("severity").eq("warning")).count_rows():
        raise ValueError("Only warning findings may be approved")
    return Approval(report_id=report.report_id, finding_ids=ids)




def _report_complete(report: SourceReport | ArtifactReport,
                    expected_names: tuple[str, ...]) -> bool:
    checks = report.checks
    names = tuple(check.name for check in checks)
    if len(names) != len(set(names)) or set(names) != set(expected_names):
        return False
    if any(
        check.outcome not in {"passed", "findings"}
        or check.finding_count < 0
        or check.emitted_count != check.finding_count
        or (check.outcome == "passed" and check.finding_count != 0)
        or (check.outcome == "findings" and check.finding_count == 0)
        for check in checks
    ):
        return False
    return report.findings.count_rows() == sum(check.emitted_count for check in checks)


def _source_rule_key(metadata: DimensionsMetadata, rules: Relation) -> tuple[str, ...]:
    return tuple(
        name for name in (
            metadata.partition_field, metadata.outcome_scope_field, metadata.uuid_field,
        ) if name is not None and name in rules.columns
    )


def _same_relation(left: Relation, right: Relation,
                   keys: tuple[str, ...]) -> bool:
    if tuple(left.schema.items()) != tuple(right.schema.items()):
        return False
    if not keys or any(name not in left.columns for name in keys):
        return False
    if left.count_rows() != right.count_rows():
        return False
    if _duplicates(left, keys).count_rows() or _duplicates(right, keys).count_rows():
        return False
    if right.join(left.select(*keys), on=list(keys), how="anti").count_rows():
        return False
    values = [name for name in left.columns if name not in keys]
    if not values:
        return True
    joined = left.join(right, on=list(keys), how="inner", suffix="__right")
    equal = ma.lit(True)
    for name in values:
        left_value = ma.col(name)
        right_value = ma.col(f"{name}__right")
        same = left_value.eq(right_value) | (
            left_value.is_null() & right_value.is_null()
        )
        equal = equal & same.fill_null(False)
    return joined.filter(~equal).count_rows() == 0


def _same_report_facts(left: t.Any, right: t.Any) -> bool:
    if left is right:
        return True
    if type(left) is not type(right) or left.report_id != right.report_id:
        return False
    if not hasattr(left, "binding") or not hasattr(right, "binding"):
        return left == right
    if (
        left.binding.semantic_version != right.binding.semantic_version
        or left.binding._metadata_json != right.binding._metadata_json
        or left.checks != right.checks
        or not _same_relation(left.findings, right.findings, ("finding_id",))
        or not _same_relation(
            left.subjects, right.subjects,
            ("finding_id", "partition", "scope", "source_uuid"),
        )
    ):
        return False
    if set(left.witnesses) != set(right.witnesses):
        return False
    for name in left.witnesses:
        witness = left.witnesses[name]
        other_witness = right.witnesses[name]
        if not _same_relation(witness, other_witness, tuple(witness.columns)):
            return False
    left_dependencies = left.binding.dependencies
    right_dependencies = right.binding.dependencies
    if set(left_dependencies) != set(right_dependencies):
        return False
    for group, references in left_dependencies.items():
        other_references = right_dependencies[group]
        if len(references) != len(other_references):
            return False
        for reference, other in zip(references, other_references):
            if (
                reference.keys != other.keys
                or reference.schema != other.schema
                or reference.version_id != other.version_id
                or reference.capture_id != other.capture_id
                or reference.table_id != other.table_id
            ):
                return False
            if reference.relation is not other.relation and (
                reference.relation is None
                or other.relation is None
                or not _same_relation(reference.relation, other.relation, reference.keys)
            ):
                return False
    return True


def _merge_evidence(existing: t.Sequence[t.Any], supplied: t.Sequence[t.Any]) -> tuple[t.Any, ...]:
    merged: list[t.Any] = []
    for report in (*existing, *supplied):
        same_id = next(
            (item for item in merged if item.report_id == report.report_id), None,
        )
        if same_id is None:
            merged.append(report)
        elif not _same_report_facts(same_id, report):
            raise ValueError("Conflicting evidence reports share a report ID")
    return tuple(merged)


def _merge_approvals(existing: t.Sequence[Approval],
                     supplied: t.Sequence[Approval]) -> tuple[Approval, ...]:
    merged = list(existing)
    for approval in supplied:
        if approval not in merged:
            merged.append(approval)
    return tuple(merged)


def _current_sources(
    lattice: Lattice,
    group: str,
    report: SourceReport | ArtifactReport,
) -> tuple[Relation, ...]:
    if group == "source_structure":
        if isinstance(report, SourceReport):
            return (lattice.sources.rules,)
        return (lattice.sources.rules, *tuple(lattice.sources.states.values()))
    if group == "source_payloads":
        return tuple(lattice.sources.payloads.values())
    return ()


def _dependency_descriptors(lattice: Lattice, group: str) -> tuple[t.Any, ...]:
    roles = {
        "source_structure": {"source_rules", "source_state"},
        "source_payloads": {"source_payloads"},
        "cell_geometry": {
            "cells", "cell_boxes", "cell_ordered_bounds",
            "cell_category_sets", "cell_category_values",
        },
        "memberships": {"contributors"},
        "outputs": {"outputs"},
    }.get(group, set())
    return tuple(
        descriptor for descriptor in lattice.table_descriptors
        if descriptor.role in roles
    )


def _reference_current(
    lattice: Lattice,
    reference: DependencyReference,
    *,
    descriptors: t.Mapping[str, t.Any],
) -> bool:
    relation_ = reference.relation
    if (
        relation_ is None
        or tuple(relation_.schema.items()) != reference.schema
        or reference.table_id is None
    ):
        return False
    descriptor = descriptors.get(reference.table_id)
    return bool(
        descriptor is not None
        and reference.version_id == lattice.version_id
        and reference.capture_id == descriptor.capture_id
        and reference.keys == descriptor.key
        and reference.schema == descriptor.schema
        and relation_ is lattice.tables[descriptor.table_id]
    )


def _require_current_reports(
    lattice: Lattice,
    source_report: SourceReport,
    artifact_report: ArtifactReport,
) -> tuple[SourceReport, ArtifactReport]:
    if source_report.binding._metadata_json != lattice._metadata_json:
        raise ValueError("Strict publication requires current source metadata")
    if artifact_report.binding._metadata_json != lattice._metadata_json:
        raise ValueError("Strict publication requires current artifact metadata")

    def bind(report: SourceReport | ArtifactReport) -> SourceReport | ArtifactReport:
        dependencies = report.binding.dependencies
        required = (
            {"source_structure", "source_payloads"}
            if isinstance(report, SourceReport)
            else {"source_structure", "source_payloads", "cell_geometry", "memberships", "outputs"}
        )
        if not required.issubset(dependencies):
            raise ValueError("Strict publication cannot establish report applicability")
        rebound: dict[str, tuple[DependencyReference, ...]] = {}
        for group, references in dependencies.items():
            candidates = _dependency_descriptors(lattice, group)
            candidate_by_id = {
                descriptor.table_id: descriptor for descriptor in candidates
            }
            required_table_ids = (
                set(candidate_by_id) if isinstance(report, ArtifactReport) else set()
            )
            covered_table_ids: set[str] = set()
            current_sources = _current_sources(lattice, group, report)
            covered_source_indices: set[int] = set()
            bound_references = []
            for reference in references:
                if reference.table_id is not None:
                    if not _reference_current(
                        lattice, reference, descriptors=candidate_by_id,
                    ):
                        raise ValueError("Strict publication refuses stale or historical evidence")
                    covered_table_ids.add(reference.table_id)
                    matching_sources = [
                        index for index, current in enumerate(current_sources)
                        if _same_relation(reference.relation, current, reference.keys)
                    ]
                    if matching_sources:
                        source_index = next(
                            (
                                index for index in matching_sources
                                if index not in covered_source_indices
                            ),
                            matching_sources[0],
                        )
                        covered_source_indices.add(source_index)
                    bound_references.append(reference)
                    continue
                relation_ = reference.relation
                if relation_ is None or tuple(relation_.schema.items()) != reference.schema:
                    raise ValueError("Strict publication refuses stale or historical evidence")
                matching_sources = [
                    index for index, current in enumerate(current_sources)
                    if _same_relation(relation_, current, reference.keys)
                ]
                if not matching_sources:
                    raise ValueError("Strict publication refuses stale or historical evidence")
                source_index = next(
                    (
                        index for index in matching_sources
                        if index not in covered_source_indices
                    ),
                    matching_sources[0],
                )
                covered_source_indices.add(source_index)
                matching_descriptors = [
                    descriptor for descriptor in candidates
                    if descriptor.key == reference.keys
                    and descriptor.schema == reference.schema
                    and _same_relation(
                        lattice.tables[descriptor.table_id], relation_, reference.keys,
                    )
                ]
                if not matching_descriptors:
                    raise ValueError("Strict publication cannot bind a checked source relation")
                descriptor = next(
                    (
                        item for item in matching_descriptors
                        if item.table_id not in covered_table_ids
                    ),
                    matching_descriptors[0],
                )
                covered_table_ids.add(descriptor.table_id)
                bound_references.append(DependencyReference(
                    lattice.tables[descriptor.table_id], descriptor.key,
                    descriptor.schema, lattice.version_id, descriptor.capture_id,
                    descriptor.table_id,
                ))
            if len(covered_source_indices) != len(current_sources):
                raise ValueError("Strict publication evidence omits current source dependencies")
            if not required_table_ids.issubset(covered_table_ids):
                raise ValueError("Strict publication evidence omits current artifact dependencies")
            rebound[group] = tuple(bound_references)
        return replace(
            report,
            binding=replace(report.binding, dependencies=rebound),
        )

    bound_source = bind(source_report)
    bound_artifact = bind(artifact_report)
    assert isinstance(bound_source, SourceReport)
    assert isinstance(bound_artifact, ArtifactReport)
    return bound_source, bound_artifact


def publish_lattice(
    lattice: Lattice,
    *,
    policy: str = "unchecked",
    source_report: SourceReport | None = None,
    artifact_report: ArtifactReport | None = None,
    approvals: t.Sequence[Approval] = (),
) -> Lattice:
    """Publish the same immutable data version; only explicit strict mode gates."""
    if policy not in {"unchecked", "informative", "strict"}:
        raise ValueError("policy must be 'unchecked', 'informative', or 'strict'")
    approvals = tuple(approvals)
    reports = tuple(report for report in (source_report, artifact_report) if report is not None)
    if policy == "strict":
        if source_report is None or artifact_report is None:
            raise ValueError("Strict publication requires current source and artifact reports")
        if not _report_complete(source_report, SOURCE_CHECK_NAMES):
            raise ValueError("Strict publication requires every complete source check")
        if not _report_complete(artifact_report, ARTIFACT_CHECK_NAMES):
            raise ValueError("Strict publication requires every complete artifact check")
        for report in reports:
            if report.findings.filter(ma.col("severity").eq("error")).count_rows():
                raise ValueError("Strict publication refuses hard findings")
        for approval in approvals:
            if approval.report_id not in {source_report.report_id, artifact_report.report_id}:
                raise ValueError("Approval belongs to a different report")
            report = (
                source_report if approval.report_id == source_report.report_id
                else artifact_report
            )
            approve(report, approval.finding_ids)
        source_warning_count = source_report.findings.filter(
            ma.col("severity").eq("warning"),
        ).count_rows()
        approved_source_ids = {
            finding_id for approval in approvals
            if approval.report_id == source_report.report_id
            for finding_id in approval.finding_ids
        }
        approved_source_warnings = source_report.findings.filter(
            ma.col("severity").eq("warning")
            & ma.col("finding_id").is_in(list(approved_source_ids)),
        ).count_rows()
        if approved_source_warnings != source_warning_count:
            raise ValueError("Strict publication requires approval of every source warning")
        source_report, artifact_report = _require_current_reports(
            lattice, source_report, artifact_report,
        )
        reports = (source_report, artifact_report)
    return replace(
        lattice,
        published=True,
        evidence=_merge_evidence(lattice.evidence, reports),
        approvals=_merge_approvals(lattice.approvals, approvals),
    )
