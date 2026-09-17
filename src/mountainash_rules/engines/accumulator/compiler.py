"""AccumulatorCompiler: compiles coalesce/compatible expressions for the accumulator engine."""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from types import MappingProxyType
import typing as t
import uuid

from mountainash_rules.core.codec import (
    _bounded_json_size,
    _content_id_bytes,
    canonical_bytes,
    validate_envelope,
)
from mountainash_rules.core.contracts import (
    AnalysisInput,
    ContextContract,
    DomainDefinition,
    OperationBudget,
    Scope,
    SemanticVersions,
    ValidationPolicy,
    _freeze,
)
from mountainash_rules.core.constants import (
    DataType,
    DimensionRole,
    not_set_sentinel_for,
)
from mountainash_rules.core.scalar import encode_scalar, normalize_scalar
from mountainash_rules.engines.accumulator.aggregate import (
    Aggregate,
    exact_fold,
    validate_aggregates,
)

if t.TYPE_CHECKING:
    from mountainash_rules.core.dimension import DimensionsMetadata
    from mountainash_rules.core.language import RegexOptions
    from mountainash_rules.core.predicates import PredicateGraph
    from mountainash_rules.core.normalization import Fragment, Overlay
    from mountainash_rules.core.validation import AnalysisGeometry, CanonicalMaterial
import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI

from mountainash_rules.core.constants import (
    MatchStrategy,
    unknown_sentinel_for,
)
from mountainash_rules.core.dimension import Dimension
from mountainash_rules.core.set_wildcard import (
    set_wildcard_predicate,
    canonicalize_set_expr,
    sentinel_list_expr,
)


def _reserve_canonical(
    budget: OperationBudget,
    value: t.Any,
    *,
    phase: str,
    counter: str = "max_input_bytes",
    retain: bool = True,
) -> int:
    """Reserve canonical input/output and owned representation before materializing it."""
    size = _bounded_json_size(value, budget, phase=phase, counter=counter)
    budget.reserve(counter, size, phase=phase, units="maximum canonical JSON bytes")
    if retain:
        budget.reserve(
            "max_live_bytes", size + 128, phase=phase, units="immutable bytes"
        )
    return size


def _canonical_id(
    budget: OperationBudget,
    kind: str,
    payload: t.Mapping[str, t.Any],
    *,
    phase: str,
    counter: str = "max_input_bytes",
) -> str:
    """Hash a preflighted canonical record while its temporary buffer is reserved."""
    size = _reserve_canonical(
        budget, payload, phase=phase, counter=counter, retain=False
    )
    buffer_size = size * 6 + 1024
    budget.reserve(
        "max_live_bytes",
        buffer_size,
        phase=phase,
        units="canonical digest buffer bytes",
    )
    try:
        return _content_id_bytes(kind, canonical_bytes(payload))
    finally:
        budget.release("max_live_bytes", buffer_size)


class AccumulatorCompiler:
    """Compiles accumulator-specific expressions for dimension pairs.

    Expressions operate on two rule rows: LHS (coalesced combination, co_ prefix)
    and RHS (candidate rule, _rhs suffix from the cross-join).

    Sentinel semantics: a rule-side don't-care bound is UNKNOWN_NUMERIC only
    (sentinel min = -inf, sentinel max = +inf). NOT_SET_NUMERIC is a
    context-side sentinel; the filter engine currently also tolerates it
    rule-side (NUMERIC_SENTINELS contains both), but the accumulator does
    not recognise it — rule tables fed to build() must use UNKNOWN_NUMERIC.
    """

    def compile_compatible(self, dim: Dimension) -> BaseExpressionAPI:
        """Expression that is True when two rules can coexist on this dimension."""
        match dim.match_strategy:
            case MatchStrategy.EXACT:
                return self._compatible_exact(dim)
            case MatchStrategy.RANGE:
                return self._compatible_range(dim)
            case MatchStrategy.GREATER_THAN | MatchStrategy.LESS_THAN:
                return self._compatible_threshold(dim)
            case MatchStrategy.SET_MEMBERSHIP:
                return self._compatible_set_membership(dim)
            case MatchStrategy.SET_EXCLUSION:
                return ma.lit(True)
            case _:
                raise ValueError(
                    f"Strategy {dim.match_strategy.name} not supported by accumulator"
                )

    def compile_coalesce(self, dim: Dimension) -> list[BaseExpressionAPI]:
        """Expression(s) producing the coalesced value from two compatible rules."""
        match dim.match_strategy:
            case MatchStrategy.EXACT:
                return self._coalesce_exact(dim)
            case MatchStrategy.RANGE:
                return self._coalesce_range(dim)
            case MatchStrategy.GREATER_THAN:
                return self._coalesce_threshold(dim, ma.greatest)
            case MatchStrategy.LESS_THAN:
                return self._coalesce_threshold(dim, ma.least)
            case MatchStrategy.SET_MEMBERSHIP:
                return self._coalesce_set(dim, "intersection")
            case MatchStrategy.SET_EXCLUSION:
                return self._coalesce_set(dim, "union")
            case _:
                raise ValueError(
                    f"Strategy {dim.match_strategy.name} not supported by accumulator"
                )

    def compile_coalesce_na_flag(self, dim: Dimension) -> BaseExpressionAPI:
        """Expression for the coalesced NA flag (1 = combination leaves dim unconstrained)."""
        if dim.match_strategy == MatchStrategy.RANGE:
            co_min_s, co_max_s, rhs_min_s, rhs_max_s = self._range_sentinel_checks(dim)
            all_sentinel = (
                co_min_s.__and__(rhs_min_s).__and__(co_max_s).__and__(rhs_max_s)
            )
            return all_sentinel.cast(int).alias(f"co_{dim.dimension_name}_na")
        if dim.match_strategy in (
            MatchStrategy.SET_MEMBERSHIP,
            MatchStrategy.SET_EXCLUSION,
        ):
            co_w, rhs_w = self._set_wild_checks(dim)
            field = dim.resolved_rule_field
            return co_w.__and__(rhs_w).cast(int).alias(f"co_{field}_na")
        co_sentinel, rhs_sentinel = self._sentinel_checks(dim)
        field = dim.resolved_rule_field
        return co_sentinel.__and__(rhs_sentinel).cast(int).alias(f"co_{field}_na")

    def _sentinel_checks(
        self, dim: Dimension
    ) -> tuple[BaseExpressionAPI, BaseExpressionAPI]:
        """Return (co_is_sentinel, rhs_is_sentinel) expressions."""
        field = dim.resolved_rule_field
        sentinel = unknown_sentinel_for(dim.data_type)
        co_is_sentinel = ma.col(f"co_{field}").eq(ma.lit(sentinel))
        rhs_is_sentinel = ma.col(f"{field}_rhs").eq(ma.lit(sentinel))
        return co_is_sentinel, rhs_is_sentinel

    def _compatible_exact(self, dim: Dimension) -> BaseExpressionAPI:
        co_sentinel, rhs_sentinel = self._sentinel_checks(dim)
        field = dim.resolved_rule_field
        values_match = ma.col(f"co_{field}").eq(ma.col(f"{field}_rhs"))
        return co_sentinel.__or__(rhs_sentinel).__or__(values_match)

    def _coalesce_exact(self, dim: Dimension) -> list[BaseExpressionAPI]:
        co_sentinel, rhs_sentinel = self._sentinel_checks(dim)
        field = dim.resolved_rule_field
        co_hard = ma.when(co_sentinel).then(None).otherwise(ma.col(f"co_{field}"))
        rhs_hard = ma.when(rhs_sentinel).then(None).otherwise(ma.col(f"{field}_rhs"))
        return [
            ma.coalesce(co_hard, rhs_hard, ma.col(f"co_{field}")).alias(f"co_{field}")
        ]

    def _range_sentinel_checks(
        self,
        dim: Dimension,
    ) -> tuple[
        BaseExpressionAPI, BaseExpressionAPI, BaseExpressionAPI, BaseExpressionAPI
    ]:
        sentinel = unknown_sentinel_for(dim.data_type)
        co_min_s = ma.col(f"co_{dim.range_min_field}").eq(ma.lit(sentinel))
        co_max_s = ma.col(f"co_{dim.range_max_field}").eq(ma.lit(sentinel))
        rhs_min_s = ma.col(f"{dim.range_min_field}_rhs").eq(ma.lit(sentinel))
        rhs_max_s = ma.col(f"{dim.range_max_field}_rhs").eq(ma.lit(sentinel))
        return co_min_s, co_max_s, rhs_min_s, rhs_max_s

    def _compatible_range(self, dim: Dimension) -> BaseExpressionAPI:
        """True when the two effective intervals overlap.

        A sentinel min is -inf, a sentinel max is +inf, each bound
        independently. Touching endpoints overlap iff both the min and the
        max side are inclusive (covers all four flag combinations).
        """
        co_min_s, co_max_s, rhs_min_s, rhs_max_s = self._range_sentinel_checks(dim)
        co_min = ma.col(f"co_{dim.range_min_field}")
        co_max = ma.col(f"co_{dim.range_max_field}")
        rhs_min = ma.col(f"{dim.range_min_field}_rhs")
        rhs_max = ma.col(f"{dim.range_max_field}_rhs")

        touch_overlaps = dim.range_min_inclusive and dim.range_max_inclusive
        if touch_overlaps:
            low = co_min.le(rhs_max)
            high = co_max.ge(rhs_min)
        else:
            low = co_min.lt(rhs_max)
            high = co_max.gt(rhs_min)

        low_ok = co_min_s.__or__(rhs_max_s).__or__(low)
        high_ok = co_max_s.__or__(rhs_min_s).__or__(high)
        return low_ok.__and__(high_ok)

    def _coalesce_range(self, dim: Dimension) -> list[BaseExpressionAPI]:
        co_min_s, co_max_s, rhs_min_s, rhs_max_s = self._range_sentinel_checks(dim)
        sentinel = unknown_sentinel_for(dim.data_type)

        new_min = (
            ma.when(co_min_s.__and__(rhs_min_s))
            .then(ma.lit(sentinel))
            .when(co_min_s)
            .then(ma.col(f"{dim.range_min_field}_rhs"))
            .when(rhs_min_s)
            .then(ma.col(f"co_{dim.range_min_field}"))
            .otherwise(
                ma.greatest(
                    ma.col(f"co_{dim.range_min_field}"),
                    ma.col(f"{dim.range_min_field}_rhs"),
                )
            )
            .alias(f"co_{dim.range_min_field}")
        )

        new_max = (
            ma.when(co_max_s.__and__(rhs_max_s))
            .then(ma.lit(sentinel))
            .when(co_max_s)
            .then(ma.col(f"{dim.range_max_field}_rhs"))
            .when(rhs_max_s)
            .then(ma.col(f"co_{dim.range_max_field}"))
            .otherwise(
                ma.least(
                    ma.col(f"co_{dim.range_max_field}"),
                    ma.col(f"{dim.range_max_field}_rhs"),
                )
            )
            .alias(f"co_{dim.range_max_field}")
        )

        return [new_min, new_max]

    def _compatible_threshold(self, dim: Dimension) -> BaseExpressionAPI:
        return ma.lit(True)

    def _coalesce_threshold(
        self, dim: Dimension, combine_fn
    ) -> list[BaseExpressionAPI]:
        co_sentinel, rhs_sentinel = self._sentinel_checks(dim)
        field = dim.resolved_rule_field
        sentinel = unknown_sentinel_for(dim.data_type)
        new_val = (
            ma.when(co_sentinel.__and__(rhs_sentinel))
            .then(ma.lit(sentinel))
            .when(co_sentinel)
            .then(ma.col(f"{field}_rhs"))
            .when(rhs_sentinel)
            .then(ma.col(f"co_{field}"))
            .otherwise(combine_fn(ma.col(f"co_{field}"), ma.col(f"{field}_rhs")))
            .alias(f"co_{field}")
        )
        return [new_val]

    def _set_wild_checks(
        self, dim: Dimension
    ) -> tuple[BaseExpressionAPI, BaseExpressionAPI]:
        field = dim.resolved_rule_field
        co_w = set_wildcard_predicate(dim, ma.col(f"co_{field}"))
        rhs_w = set_wildcard_predicate(dim, ma.col(f"{field}_rhs"))
        return co_w, rhs_w

    def _compatible_set_membership(self, dim: Dimension) -> BaseExpressionAPI:
        co_w, rhs_w = self._set_wild_checks(dim)
        field = dim.resolved_rule_field
        intersection_nonempty = (
            ma.col(f"co_{field}")
            .list.set_intersection(ma.col(f"{field}_rhs"))
            .list.len()
            .gt(ma.lit(0))
        )
        return co_w.__or__(rhs_w).__or__(intersection_nonempty)

    def _coalesce_set(self, dim: Dimension, op: str) -> list[BaseExpressionAPI]:
        co_w, rhs_w = self._set_wild_checks(dim)
        field = dim.resolved_rule_field
        co = ma.col(f"co_{field}")
        rhs = ma.col(f"{field}_rhs")
        combined = (
            co.list.set_intersection(rhs)
            if op == "intersection"
            else co.list.set_union(rhs)
        )
        new_val = (
            ma.when(co_w.__and__(rhs_w))
            .then(sentinel_list_expr(dim))
            .when(co_w)
            .then(rhs)
            .when(rhs_w)
            .then(co)
            .otherwise(canonicalize_set_expr(combined))
            .alias(f"co_{field}")
        )
        return [new_val]


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    predicate_id: str
    content_id: str
    routing_values: tuple[t.Mapping[str, t.Any], ...]
    contributions: t.Mapping[str, t.Any]
    origins: tuple[t.Mapping[str, t.Any], ...]


@dataclass(frozen=True, slots=True)
class PreparedSources:
    ruleset_id: str
    graph: PredicateGraph
    domain: DomainDefinition
    domain_digest: str
    metadata: t.Mapping[str, t.Any]
    routing: t.Mapping[str, t.Any]
    aggregate_record: t.Mapping[str, t.Any]
    aggregates: tuple[Aggregate, ...]
    sources: tuple[SourceRecord, ...]
    source_bundle_id: str
    source_labels: t.Mapping[str, str]
    semantic_versions: SemanticVersions
    source_id_field: str


@dataclass(frozen=True, slots=True)
class AnalyzedCell:
    cell_id: str
    predicate_id: str
    contributors: tuple[str, ...]
    contributor_set_id: str
    outputs: t.Mapping[str, t.Any]
    _lease: t.Any = dataclass_field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ExactAnalysis:
    prepared: PreparedSources
    sources: tuple[SourceRecord, ...]
    partition_identity: t.Mapping[str, t.Any]
    domain: DomainDefinition
    domain_digest: str
    overlay: Overlay
    cells: tuple[AnalyzedCell, ...]
    artifact_id: str
    source_bundle_id: str


def _dimension_record(
    dimension: Dimension, *, routing: bool = False
) -> dict[str, t.Any]:
    ranged = dimension.match_strategy is MatchStrategy.RANGE and not routing
    return {
        "dimension_name": dimension.dimension_name,
        "context_field": dimension.resolved_context_field,
        "rule_field": dimension.resolved_rule_field,
        "match_strategy": MatchStrategy.EXACT_KEY.value
        if routing
        else dimension.match_strategy.value,
        "data_type": dimension.data_type.value,
        "role": dimension.role.value,
        "range_min_field": dimension.range_min_field if ranged else None,
        "range_max_field": dimension.range_max_field if ranged else None,
        "range_min_inclusive": dimension.range_min_inclusive if ranged else None,
        "range_max_inclusive": dimension.range_max_inclusive if ranged else None,
        "regex_pattern": dimension.regex_pattern
        if dimension.match_strategy is MatchStrategy.CONTEXT_REGEX
        else None,
    }


def _source_label(value: t.Any, name: str) -> str:
    if (
        type(value) is not str
        or not value
        or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    ):
        raise ValueError(f"{name} must be a nonempty Unicode-scalar string")
    return value


def _source_columns(dimension: Dimension) -> tuple[str, ...]:
    if dimension.match_strategy is MatchStrategy.CONTEXT_REGEX:
        return ()
    if dimension.match_strategy is MatchStrategy.RANGE:
        return t.cast(
            tuple[str, ...], (dimension.range_min_field, dimension.range_max_field)
        )
    return (dimension.resolved_rule_field,)


def _origin_value(value: t.Any, dimension: Dimension, graph: PredicateGraph) -> t.Any:
    declaration = graph.fields[dimension.resolved_context_field]
    if isinstance(value, (list, tuple)):
        return [
            encode_scalar(
                item,
                declaration.data_type,
                timezone=declaration.timezone,
                allow_reserved=True,
                allow_null=True,
            )
            for item in value
        ]
    return encode_scalar(
        value,
        declaration.data_type,
        timezone=declaration.timezone,
        allow_reserved=True,
        allow_null=True,
    )


def prepare_sources(
    rows: t.Iterable[t.Mapping[str, t.Any]],
    *,
    graph: PredicateGraph,
    domain: DomainDefinition,
    metadata: DimensionsMetadata,
    aggregates: t.Iterable[Aggregate],
    ruleset_id: str,
    source_id_field: str,
    source_label_field: str | None,
    routing: t.Mapping[str, t.Any],
    regex_options: RegexOptions | None,
) -> PreparedSources:
    """Validate authored scalar mappings before clipping or partitioning.

    A caller must preserve original physical scalar precision when extracting
    rows. Backend materialization/serving adapters are not this pure kernel.
    """
    _source_label(ruleset_id, "ruleset_id")
    _source_label(source_id_field, "source_id_field")
    if source_label_field is not None:
        _source_label(source_label_field, "source_label_field")
    if tuple(domain.fields) != tuple(
        graph.fields[name] for name in sorted(graph.fields)
    ):
        raise ValueError("Compilation domain and predicate graph fields disagree")
    if domain.predicate_id not in graph.nodes:
        raise ValueError("Compilation domain predicate is unavailable")
    dimensions = tuple(sorted(metadata.dimensions, key=lambda dim: dim.dimension_name))
    if len({dim.dimension_name for dim in dimensions}) != len(dimensions):
        raise ValueError("Duplicate dimension names")
    for dimension in dimensions:
        field = graph.fields.get(dimension.resolved_context_field)
        if field is None or field.data_type is not dimension.data_type:
            raise ValueError(
                "Dimension disagrees with authoritative physical field type"
            )
    uses_regex = any(
        dim.match_strategy in (MatchStrategy.REGEX, MatchStrategy.CONTEXT_REGEX)
        for dim in dimensions
    )
    if uses_regex != (regex_options is not None):
        raise ValueError(
            "Regex semantics must be explicit exactly when regex dimensions exist"
        )
    declarations = validate_aggregates(aggregates)
    aggregate_payload = {
        "schema_version": 1,
        "declarations": [
            item.native_record()
            for item in sorted(
                declarations, key=lambda item: t.cast(str, item.output_name)
            )
        ],
    }
    aggregate_record = {
        "id": _canonical_id(
            graph.budget, "aggregates", aggregate_payload, phase="source.aggregates"
        ),
        "payload": aggregate_payload,
    }
    metadata_payload = {
        "schema_version": 1,
        "dimensions": [_dimension_record(dimension) for dimension in dimensions],
        "regex_semantics": regex_options.to_dict()
        if regex_options is not None
        else None,
        "normalization_semantics": "normalization-2",
    }
    metadata_record = {
        "id": _canonical_id(
            graph.budget, "metadata", metadata_payload, phase="source.metadata"
        ),
        "payload": metadata_payload,
    }
    routing_record = validate_envelope(dict(routing), "routing")
    key_dimensions = tuple(
        dim for dim in dimensions if dim.role is DimensionRole.CONTEXT_KEY
    )
    if routing_record["payload"]["key_dimensions"] != [
        _dimension_record(dim, routing=True) for dim in key_dimensions
    ]:
        raise ValueError("Routing declarations disagree with context-key dimensions")
    for dimension in key_dimensions:
        if dimension.match_strategy not in (
            MatchStrategy.EXACT,
            MatchStrategy.EXACT_KEY,
        ):
            raise ValueError("Context-key routing requires exact matching")
    guards = tuple(
        dim for dim in dimensions if dim.match_strategy is MatchStrategy.CONTEXT_REGEX
    )
    domain_predicate = graph.and_(
        domain.predicate_id,
        *(
            graph.lower_dimension(dim, {}, regex_options=regex_options)
            for dim in guards
        ),
    )
    domain = domain.model_copy(update={"predicate_id": domain_predicate})
    domain_digest = _canonical_id(
        graph.budget,
        "domain",
        domain.model_dump(mode="json", exclude_none=True),
        phase="source.domain",
    )
    input_columns = {source_id_field}
    input_columns.update(
        column for dim in dimensions for column in _source_columns(dim)
    )
    input_columns.update(aggregate.column_name for aggregate in declarations)
    aggregate_sources = {item.column_name: item for item in declarations}
    sources = []
    seen = set()
    labels = {}
    for row in rows:
        if not isinstance(row, t.Mapping):
            raise ValueError(
                "Source rows must be mappings of preserved physical scalars"
            )
        missing = input_columns - row.keys()
        if missing:
            raise ValueError(f"Missing required source columns: {sorted(missing)}")
        graph.budget.reserve("max_regions", 1, phase="source.registry", units="sources")
        identifier = normalize_scalar(
            row[source_id_field], DataType.STR, allow_reserved=True
        )
        try:
            valid_uuid = str(uuid.UUID(identifier)) == identifier
        except (ValueError, TypeError, AttributeError):
            valid_uuid = False
        if not valid_uuid or identifier in seen:
            raise ValueError(
                "Source UUIDs must be canonical and unique across the ruleset"
            )
        label = (
            normalize_scalar(row[source_label_field], DataType.STR, allow_reserved=True)
            if source_label_field is not None
            and row.get(source_label_field) is not None
            else None
        )
        contributions = {
            name: normalize_scalar(
                row[name],
                t.cast(DataType, aggregate.data_type),
                timezone=aggregate.timezone,
                allow_reserved=True,
            )
            for name, aggregate in aggregate_sources.items()
        }
        terms = []
        origins = []
        keys = []
        for dimension in dimensions:
            if dimension.match_strategy is MatchStrategy.CONTEXT_REGEX:
                continue
            lowered = graph.lower_dimension(dimension, row, regex_options=regex_options)
            origins.append(
                {
                    "source_id": identifier,
                    "dimension_name": dimension.dimension_name,
                    "predicate_id": lowered,
                    "authored_values": {
                        column: _origin_value(row[column], dimension, graph)
                        for column in _source_columns(dimension)
                    },
                }
            )
            if dimension.role is DimensionRole.CONTEXT_KEY:
                field = graph.fields[dimension.resolved_context_field]
                value = normalize_scalar(
                    row[dimension.resolved_rule_field],
                    field.data_type,
                    timezone=field.timezone,
                    allow_reserved=True,
                    allow_null=field.data_type is DataType.BOOL,
                )
                marker = (
                    value.replace(tzinfo=None)
                    if field.data_type is DataType.DATETIME
                    else value
                )
                wildcard = (
                    value is None
                    if field.data_type is DataType.BOOL
                    else marker == unknown_sentinel_for(field.data_type)
                )
                if (
                    field.data_type is not DataType.BOOL
                    and marker == not_set_sentinel_for(field.data_type)
                ):
                    raise ValueError("NOT_SET is invalid source routing intent")
                match = (
                    {"kind": "wildcard"}
                    if wildcard
                    else {
                        "kind": "value",
                        "value": encode_scalar(
                            value, field.data_type, timezone=field.timezone
                        ),
                    }
                )
                keys.append(
                    {"dimension_name": dimension.dimension_name, "match": match}
                )
            else:
                terms.append(lowered)
        if keys not in routing_record["payload"]["partition_keys"]:
            raise ValueError("Source routing key is outside the approved registry")
        predicate = graph.and_(*terms)
        source_payload = {
            "schema_version": 1,
            "ruleset_id": ruleset_id,
            "source_id": identifier,
            "predicate_id": predicate,
            "routing_values": keys,
            "contributions": {
                name: encode_scalar(
                    value,
                    t.cast(DataType, aggregate_sources[name].data_type),
                    timezone=aggregate_sources[name].timezone,
                    allow_reserved=True,
                )
                for name, value in contributions.items()
            },
        }
        retained_source = {
            "source": source_payload,
            "origins": origins,
            "label": label,
        }
        source_size = _reserve_canonical(
            graph.budget,
            retained_source,
            phase="source.registry",
            retain=False,
        )
        graph.budget.reserve(
            "max_live_bytes",
            source_size * 8 + 1024,
            phase="source.registry",
            units="retained source bytes",
        )
        seen.add(identifier)
        if label is not None:
            labels[identifier] = label
        sources.append(
            SourceRecord(
                identifier,
                predicate,
                _canonical_id(
                    graph.budget,
                    "source-content",
                    source_payload,
                    phase="source.digest",
                ),
                _freeze(keys),
                MappingProxyType(contributions),
                _freeze(origins),
            )
        )
    sources.sort(key=lambda source: source.source_id)
    bundle = {
        "schema_version": 1,
        "ruleset_id": ruleset_id,
        "sources": [
            {"source_id": source.source_id, "content_id": source.content_id}
            for source in sources
        ],
    }
    versions = SemanticVersions(
        scalar="scalar-1",
        predicate="predicate-1",
        language="language-1",
        numeric="numeric-1",
        canonical="canonical-json-1",
        normalization="normalization-2",
    )
    return PreparedSources(
        ruleset_id,
        graph,
        domain,
        domain_digest,
        _freeze(metadata_record),
        _freeze(routing_record),
        _freeze(aggregate_record),
        declarations,
        tuple(sources),
        _canonical_id(graph.budget, "source-bundle", bundle, phase="source.bundle"),
        MappingProxyType(labels),
        versions,
        source_id_field,
    )


def analyze_sources(
    prepared: PreparedSources,
    *,
    key_values: t.Sequence[t.Mapping[str, t.Any]],
    order: t.Sequence[str] | None = None,
    fragments: t.Iterable[Fragment] | None = None,
) -> ExactAnalysis:
    """Return proved immutable analysis, never a serving lattice or approval."""
    from mountainash_rules.core.normalization import covered_overlay, finalize_regions
    from mountainash_rules.core.reasoner import Reasoner
    from mountainash_rules.core.scalar import decode_scalar

    graph = prepared.graph
    if _freeze(key_values) not in prepared.routing["payload"]["partition_keys"]:
        raise ValueError("Unknown semantic partition key")
    _reserve_canonical(
        graph.budget, key_values, phase="analysis.partition", retain=False
    )
    graph.budget.reserve(
        "max_live_bytes",
        256 + 128 * len(prepared.sources),
        phase="analysis.sources",
        units="retained source references",
    )
    keys = _freeze(key_values)
    sources = tuple(
        source for source in prepared.sources if source.routing_values == keys
    )
    partition = _freeze(
        {"routing_id": prepared.routing["id"], "key_values": key_values}
    )
    key_declarations = {
        dim["dimension_name"]: dim
        for dim in prepared.routing["payload"]["key_dimensions"]
    }
    key_terms = []
    for key in key_values:
        if key["match"]["kind"] == "value":
            field = key_declarations[key["dimension_name"]]["context_field"]
            key_terms.append(
                graph.eq(field, decode_scalar(dict(key["match"]["value"])))
            )
    domain_predicate = graph.and_(prepared.domain.predicate_id, *key_terms)
    domain = prepared.domain.model_copy(update={"predicate_id": domain_predicate})
    domain_digest = _canonical_id(
        graph.budget,
        "domain",
        domain.model_dump(mode="json", exclude_none=True),
        phase="analysis.domain",
    )
    reasoner = Reasoner(graph)
    graph.budget.reserve(
        "max_live_bytes",
        256 + 128 * len(sources),
        phase="analysis.source-map",
        units="retained source predicate map bytes",
    )
    source_predicates = {source.source_id: source.predicate_id for source in sources}
    _reserve_canonical(
        graph.budget,
        {
            "sources": [
                {"source_id": source.source_id, "predicate_id": source.predicate_id}
                for source in sources
            ]
        },
        phase="analysis.source-map",
    )
    overlay = covered_overlay(
        reasoner, domain_predicate, source_predicates, order=order
    )
    regions = finalize_regions(reasoner, overlay, fragments=fragments)
    _reserve_canonical(
        graph.budget,
        {
            "sources": [
                {"source_id": source.source_id, "content_id": source.content_id}
                for source in sources
            ]
        },
        phase="analysis.registry",
    )
    graph.budget.reserve(
        "max_live_bytes",
        256 + 128 * len(sources),
        phase="analysis.registry",
        units="retained source registry bytes",
    )
    registry = {source.source_id: source for source in sources}
    graph.budget.reserve(
        "max_live_bytes",
        512 + 768 * len(regions),
        phase="analysis.tables",
        units="retained analysis table bytes",
    )
    memberships = {}
    cells = []
    for region in regions:
        cached = memberships.get(region.contributors)
        if cached is None:
            graph.budget.reserve(
                "max_live_bytes",
                256 + 128 * len(region.contributors),
                phase="analysis.membership",
                units="retained contributor membership bytes",
            )
            graph.budget.reserve(
                "max_work",
                len(region.contributors),
                phase="analysis.membership",
                units="contributor ordering steps",
            )
            members = tuple(sorted(region.contributors))
            graph.budget.reserve(
                "max_live_bytes",
                256 + 256 * len(prepared.aggregates),
                phase="analysis.folded",
                units="retained aggregate outputs",
            )
            outputs = MappingProxyType(
                {
                    t.cast(str, aggregate.output_name): exact_fold(
                        aggregate,
                        (
                            registry[source].contributions[aggregate.column_name]
                            for source in members
                        ),
                        budget=graph.budget,
                    )
                    for aggregate in prepared.aggregates
                }
            )
            membership_id = _canonical_id(
                graph.budget,
                "contributor-set",
                {"schema_version": 1, "source_ids": list(members)},
                phase="analysis.membership-id",
            )
            cached = members, membership_id, outputs
            memberships[region.contributors] = cached
        members, membership_id, outputs = cached
        cell_payload = {
            "cell_schema": 1,
            "ruleset_id": prepared.ruleset_id,
            "partition_identity": partition,
            "predicate_id": region.predicate_id,
            "contributor_set_id": membership_id,
        }
        cell_id = _canonical_id(
            graph.budget, "cell", cell_payload, phase="analysis.cell-id"
        )
        graph.budget.reserve(
            "max_live_bytes",
            512,
            phase="analysis.cells",
            units="retained cell record bytes",
        )
        cells.append(
            AnalyzedCell(
                cell_id,
                region.predicate_id,
                members,
                membership_id,
                outputs,
                region._lease,
            )
        )
    cells.sort(key=lambda cell: cell.cell_id)
    bundle_id = _canonical_id(
        graph.budget,
        "source-bundle",
        {
            "schema_version": 1,
            "ruleset_id": prepared.ruleset_id,
            "sources": [
                {"source_id": source.source_id, "content_id": source.content_id}
                for source in sources
            ],
        },
        phase="analysis.bundle",
    )
    artifact_payload = {
        "schema_version": 1,
        "ruleset_id": prepared.ruleset_id,
        "partition_identity": partition,
        "metadata_id": prepared.metadata["id"],
        "aggregates_id": prepared.aggregate_record["id"],
        "compilation_domain_id": domain_digest,
        "source_bundle_id": bundle_id,
        "cells": [
            {
                "cell_id": cell.cell_id,
                "outputs": {
                    t.cast(str, aggregate.output_name): encode_scalar(
                        cell.outputs[t.cast(str, aggregate.output_name)],
                        t.cast(DataType, aggregate.data_type),
                        timezone=aggregate.timezone,
                        allow_reserved=True,
                    )
                    for aggregate in prepared.aggregates
                },
            }
            for cell in cells
        ],
        "semantic_versions": prepared.semantic_versions.model_dump(mode="json"),
    }
    artifact_id = _canonical_id(
        graph.budget,
        "artifact",
        artifact_payload,
        phase="analysis.output",
        counter="max_output_bytes",
    )
    return ExactAnalysis(
        prepared,
        sources,
        partition,
        domain,
        domain_digest,
        overlay,
        tuple(cells),
        artifact_id,
        bundle_id,
    )


def prepare_analysis_input(
    prepared: PreparedSources,
    *,
    compilation_domain_ref: str,
    domains: t.Mapping[str, DomainDefinition],
    contracts: t.Sequence[ContextContract],
    validation_policy: ValidationPolicy,
    analysis: ExactAnalysis | None = None,
) -> tuple[AnalysisInput, CanonicalMaterial]:
    """Bind current canonical input and policy, independently of any report."""
    from mountainash_rules.core.reasoner import Reasoner
    from mountainash_rules.core.validation import CanonicalMaterial

    if not isinstance(validation_policy, ValidationPolicy):
        raise TypeError("validation_policy must be ValidationPolicy")
    if domains.get(compilation_domain_ref) != prepared.domain:
        raise ValueError("Compilation domain does not match prepared source analysis")
    if analysis is not None:
        prior = analysis.prepared
        if (
            prior.source_bundle_id,
            prior.metadata,
            prior.aggregate_record,
            prior.routing,
            prior.domain_digest,
            prior.semantic_versions,
        ) != (
            prepared.source_bundle_id,
            prepared.metadata,
            prepared.aggregate_record,
            prepared.routing,
            prepared.domain_digest,
            prepared.semantic_versions,
        ):
            raise ValueError(
                "Artifact analysis does not match current canonical sources"
            )
    graph = prepared.graph
    reasoner = Reasoner(graph)
    graph.budget.reserve(
        "max_work",
        len(domains) + len(contracts),
        phase="analysis.input",
        units="declarations",
    )
    domain_ids = {}
    for label, domain in sorted(domains.items()):
        if label != domain.domain_id or tuple(domain.fields) != tuple(
            graph.fields.values()
        ):
            raise ValueError("Domain registry label or physical declarations disagree")
        if domain.predicate_id not in graph.nodes:
            raise ValueError("Domain predicate is unavailable")
        if not reasoner.is_empty(
            graph.and_(domain.predicate_id, graph.not_(prepared.domain.predicate_id))
        ):
            raise ValueError("Provider domain exceeds the global compilation domain")
        domain_payload = domain.model_dump(mode="json", exclude_none=True)
        domain_ids[label] = _canonical_id(
            graph.budget,
            "domain",
            domain_payload,
            phase="analysis-input.domain",
        )
    contract_records = []
    profile_refs = []
    seen = set()
    for contract in sorted(contracts, key=lambda item: item.contract_id):
        if contract.contract_id in seen or contract.domain_ref not in domain_ids:
            raise ValueError("Duplicate contract or unavailable contract domain")
        seen.add(contract.contract_id)
        for field in contract.fields:
            physical = graph.fields.get(field.name)
            if physical is None or (physical.data_type, physical.timezone) != (
                field.data_type,
                field.timezone,
            ):
                raise ValueError(
                    "Contract physical field declaration disagrees with graph"
                )
        contract_payload = {
            "schema_version": 1,
            "contract": contract.model_dump(mode="json", exclude_none=True),
        }
        digest = _canonical_id(
            graph.budget, "contract", contract_payload, phase="analysis-input.contract"
        )
        _reserve_canonical(
            graph.budget,
            {"contract_id": contract.contract_id, "contract_digest": digest},
            phase="analysis-input.contract-record",
        )
        contract_records.append(
            {"contract_id": contract.contract_id, "contract_digest": digest}
        )
        for profile in contract.profiles:
            _reserve_canonical(
                graph.budget,
                {"contract_id": contract.contract_id, "profile_id": profile.profile_id},
                phase="analysis-input.profile-ref",
            )
            profile_refs.append(
                {"contract_id": contract.contract_id, "profile_id": profile.profile_id}
            )
    payload = {
        "schema_version": 1,
        "ruleset_id": prepared.ruleset_id,
        "source_id_field": prepared.source_id_field,
        "source_bundle_digest": prepared.source_bundle_id,
        "compilation_domain_ref": compilation_domain_ref,
        "domain_digests": domain_ids,
        "metadata_digest": prepared.metadata["id"],
        "aggregate_digest": prepared.aggregate_record["id"],
        "routing_digest": prepared.routing["id"],
        "contracts": contract_records,
        "validation_policy": validation_policy.model_dump(mode="json"),
        "semantic_versions": prepared.semantic_versions.model_dump(mode="json"),
    }
    record = AnalysisInput(
        id=_canonical_id(
            graph.budget,
            "analysis-input",
            payload,
            phase="analysis-input.output",
            counter="max_output_bytes",
        ),
        **payload,
    )
    selected_keys = (
        (analysis.partition_identity["key_values"],)
        if analysis is not None
        else prepared.routing["payload"]["partition_keys"]
    )
    partitions = [
        {
            "routing_id": prepared.routing["id"],
            "key_values": [
                {
                    "dimension_name": key["dimension_name"],
                    "match": {"kind": "wildcard"}
                    if key["match"]["kind"] == "wildcard"
                    else {
                        "kind": "value",
                        "value": dict(key["match"]["value"]),
                    },
                }
                for key in keys
            ],
        }
        for keys in selected_keys
    ]
    scope = Scope(
        partition_refs=sorted(partitions, key=canonical_bytes),
        domain_refs=tuple(domain_ids),
        profile_refs=sorted(profile_refs, key=canonical_bytes),
    )
    _reserve_canonical(
        graph.budget,
        {
            "analysis_input_id": record.id,
            "source_bundle_id": prepared.source_bundle_id,
            "metadata_id": prepared.metadata["id"],
            "aggregates_id": prepared.aggregate_record["id"],
            "routing_id": prepared.routing["id"],
            "domain_ids": domain_ids,
            "artifact_id": analysis.artifact_id if analysis is not None else None,
        },
        phase="analysis-input.material",
        counter="max_output_bytes",
    )
    material = CanonicalMaterial(
        analysis_input_id=record.id,
        source_bundle_id=prepared.source_bundle_id,
        metadata_id=prepared.metadata["id"],
        aggregates_id=prepared.aggregate_record["id"],
        routing_id=prepared.routing["id"],
        domain_ids=domain_ids,
        semantic_versions=prepared.semantic_versions,
        artifact_id=analysis.artifact_id if analysis is not None else None,
        selected_scope=scope,
    )
    return record, material


def analysis_geometry(
    analysis: ExactAnalysis, *, provider_domains: t.Mapping[str, DomainDefinition]
) -> AnalysisGeometry:
    """Expose the completed build geometry to the same neutral proof kernel."""
    from mountainash_rules.core.validation import (
        AnalysisGeometry,
        StructuralCell,
        StructuralSource,
    )

    return AnalysisGeometry(
        graph=analysis.prepared.graph,
        compilation_domain=analysis.domain,
        global_compilation_domain=analysis.prepared.domain,
        provider_domains=provider_domains,
        routing=analysis.prepared.routing["payload"],
        partition_identity=analysis.partition_identity,
        sources=tuple(
            StructuralSource(source.source_id, source.predicate_id)
            for source in analysis.sources
        ),
        cells=tuple(
            StructuralCell(
                cell.cell_id, cell.predicate_id, cell.contributors, cell.outputs
            )
            for cell in analysis.cells
        ),
    )
