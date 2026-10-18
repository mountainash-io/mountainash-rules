"""Lower retained predicate-1 metadata without a host reasoner or row search.

Projections are necessary restrictions, never proofs of joint satisfiability.
Construction must keep the full predicate when composing projected pieces.
"""

from __future__ import annotations

import typing as t
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import mountainash.expressions as ma
from mountainash import DecimalDtype, MountainashDtype, materialization_scope
from mountainash.expressions import BaseExpressionAPI
from mountainash.relations import Relation, concat, relation

from mountainash_rules.core.codec import validate_envelope
from mountainash_rules.core.constants import DataType
from mountainash_rules.core.contracts import DomainDefinition, DomainField
from mountainash_rules.core.dimension import DimensionsMetadata
from mountainash_rules.core.numeric import (
    decimal_add_exact,
    decimal_operand,
    decimal_subtract_exact,
    exact_literal,
    literal_dtype,
)
from mountainash_rules.core.scalar import decode_scalar
from mountainash_rules.engines.accumulate.errors import (
    DomainError,
    UnsupportedDomainError,
)


def _materialize_owned(owner: t.Any | None, query: Relation) -> Relation:
    if owner is None:
        return query
    return owner.snapshot(relation(query.collect()))

@dataclass(frozen=True)
class Interval:
    """Native endpoints with independent closure and structural unboundedness.

    Unbounded payloads are unused, not sentinel values. No successor/predecessor
    arithmetic is needed to intersect endpoint geometry, including DECIMAL.
    """

    lower: BaseExpressionAPI
    upper: BaseExpressionAPI
    lower_closed: BaseExpressionAPI
    upper_closed: BaseExpressionAPI
    lower_unbounded: BaseExpressionAPI
    upper_unbounded: BaseExpressionAPI

    @property
    def nonempty(self) -> BaseExpressionAPI:
        """Whether the endpoint intersection is nonempty (before discrete cuts)."""
        return (
            self.lower_unbounded
            | self.upper_unbounded
            | (self.lower < self.upper)
            | ((self.lower == self.upper) & self.lower_closed & self.upper_closed)
        ).fill_null(False)

    def contains(self, value: BaseExpressionAPI) -> BaseExpressionAPI:
        """The exact native comparison predicate, including endpoint closure."""
        return (
            self.lower_unbounded
            | (value > self.lower)
            | ((value == self.lower) & self.lower_closed)
        ) & (
            self.upper_unbounded
            | (value < self.upper)
            | ((value == self.upper) & self.upper_closed)
        )



@dataclass(frozen=True)
class ValueConstraint:
    """One native finite or cofinite source restriction on a field."""

    values: BaseExpressionAPI | Relation
    is_list: bool
    allowed: bool
    inactive: BaseExpressionAPI


@dataclass(frozen=True)
class FieldRestriction:
    """Normalized constraints contributed by one source/cell side."""

    data_type: DataType
    intervals: tuple[Interval, ...] = ()
    values: tuple[ValueConstraint, ...] = ()
    numeric_dtype: DecimalDtype | None = None
    timezone: str | None = None
    rounding: str | None = None
def intersect_intervals(left: Interval, right: Interval) -> Interval:
    """Intersect piece rows natively; ties require both endpoints to be closed."""
    choose_left_lower = right.lower_unbounded | (
        ~left.lower_unbounded & (left.lower > right.lower)
    )
    choose_right_lower = left.lower_unbounded | (
        ~right.lower_unbounded & (right.lower > left.lower)
    )
    choose_left_upper = right.upper_unbounded | (
        ~left.upper_unbounded & (left.upper < right.upper)
    )
    choose_right_upper = left.upper_unbounded | (
        ~right.upper_unbounded & (right.upper < left.upper)
    )
    lower_unbounded = left.lower_unbounded & right.lower_unbounded
    upper_unbounded = left.upper_unbounded & right.upper_unbounded
    return Interval(
        lower=ma.when(choose_left_lower).then(left.lower).otherwise(right.lower),
        upper=ma.when(choose_left_upper).then(left.upper).otherwise(right.upper),
        lower_closed=(
            ~lower_unbounded
            & ma.when(choose_left_lower)
            .then(left.lower_closed)
            .when(choose_right_lower)
            .then(right.lower_closed)
            .otherwise(left.lower_closed & right.lower_closed)
        ),
        upper_closed=(
            ~upper_unbounded
            & ma.when(choose_left_upper)
            .then(left.upper_closed)
            .when(choose_right_upper)
            .then(right.upper_closed)
            .otherwise(left.upper_closed & right.upper_closed)
        ),
        lower_unbounded=lower_unbounded,
        upper_unbounded=upper_unbounded,
    )


@dataclass(frozen=True)
class FieldProjection:
    """Necessary unary restriction with optional native candidate geometry.

    ``values`` are candidate enumeration expressions, not already-filtered rows:
    conjunction may retain one operand's candidates plus the full unary predicate.
    Consumers must filter them with ``predicate``. ``interval`` likewise never
    replaces this predicate or the full joint domain.
    """

    predicate: BaseExpressionAPI
    interval: Interval | None = None
    values: tuple[BaseExpressionAPI, ...] | None = None


@dataclass(frozen=True)
class LoweredDomain:
    """Full joint predicate and conservative projections over normalized values.

    ``invalid`` carries row-dependent input failures; ``declaration_invalid``
    carries row-independent literal failures, which remain meaningful for empty
    input. Operation owners decide how to report or reject both ledgers before
    using the predicate. An unused numeric conversion payload never denotes valid
    data, no-match, or null-fill.
    """

    predicate: BaseExpressionAPI
    projections: dict[str, FieldProjection] = field(default_factory=dict)
    invalid: dict[str, BaseExpressionAPI] = field(default_factory=dict)
    declaration_invalid: dict[str, BaseExpressionAPI] = field(default_factory=dict)


def _conjoin(left: FieldProjection, right: FieldProjection) -> FieldProjection:
    interval = left.interval if left.interval is not None else right.interval
    if left.interval is not None and right.interval is not None:
        interval = intersect_intervals(left.interval, right.interval)
    return FieldProjection(
        left.predicate & right.predicate,
        interval,
        left.values if left.values is not None else right.values,
    )


def lower_domain(
    domain: DomainDefinition | None,
    nodes: t.Mapping[str, t.Mapping[str, t.Any]],
    *,
    values: t.Mapping[str, BaseExpressionAPI] | None = None,
    source_dtypes: t.Mapping[str, t.Any] | None = None,
) -> LoweredDomain:
    """Lower the declared root and retained predicate-1 envelopes to expressions.

    ``values`` maps resolved physical context fields, not dimension aliases; its
    default is ``ma.col(field)``. If ``source_dtypes`` is omitted, callers supply
    already-normalized concrete values. With source dtypes, decimal columns use
    the shared original-type admission and conversion. Marker/missing/mask state
    belongs to the caller's shared context normalization, never to domain literals.

    Only authored metadata is traversed on the host. No data relation is executed
    during lowering, and no per-field projection claims joint feasibility.
    """
    if domain is None:
        return LoweredDomain(ma.lit(True))

    declarations = {declaration.name: declaration for declaration in domain.fields}
    normalized: dict[str, BaseExpressionAPI] = {}
    invalid: dict[str, BaseExpressionAPI] = {}
    declaration_invalid: dict[str, BaseExpressionAPI] = {}
    memo: dict[str, LoweredDomain] = {}
    visiting: set[str] = set()

    def add_invalid(name: str, predicate: BaseExpressionAPI) -> None:
        invalid[name] = invalid[name] | predicate if name in invalid else predicate

    def declaration(name: str) -> DomainField:
        if name not in declarations:
            raise DomainError(f"Undeclared domain field {name!r}")
        return declarations[name]

    def value(name: str) -> BaseExpressionAPI:
        if name in normalized:
            return normalized[name]
        declared = declaration(name)
        expression = ma.col(name) if values is None else values.get(name, ma.col(name))
        if source_dtypes is not None and declared.data_type is DataType.DECIMAL:
            if name not in source_dtypes:
                raise DomainError(
                    f"Missing original source dtype for domain field {name!r}"
                )
            assert declared.numeric_dtype is not None and declared.rounding is not None
            operand = decimal_operand(
                expression,
                source_dtypes[name],
                declared.numeric_dtype,
                declared.rounding,
            )
            expression = operand.value
            add_invalid(name, operand.invalid)
        normalized[name] = expression
        return expression

    def literal(name: str, payload: t.Mapping[str, t.Any]) -> BaseExpressionAPI:
        declared = declaration(name)
        try:
            native = decode_scalar(dict(payload), allow_reserved=True)
            expression = exact_literal(native)
            if declared.data_type is DataType.DECIMAL:
                assert (
                    declared.numeric_dtype is not None and declared.rounding is not None
                )
                operand = decimal_operand(
                    expression,
                    literal_dtype(native),
                    declared.numeric_dtype,
                    declared.rounding,
                )
                declaration_invalid[name] = (
                    declaration_invalid[name] | operand.invalid
                    if name in declaration_invalid
                    else operand.invalid
                )
                return operand.value
            if (
                payload["type"] != declared.data_type.value
                or payload.get("timezone") != declared.timezone
            ):
                raise DomainError(f"Incompatible scalar for domain field {name!r}")
            return expression
        except ValueError as exc:
            raise DomainError(f"Invalid domain scalar for {name!r}: {exc}") from exc

    def absent_endpoint(name: str) -> BaseExpressionAPI:
        declared = declaration(name)
        if declared.data_type is DataType.DATETIME and declared.timezone == "utc":
            # TIMESTAMP alone denotes a naive null. Bind an aware unused payload
            # so native intersection branches retain UTC; unboundedness, not the
            # epoch value, determines the endpoint's meaning.
            return exact_literal(datetime(1970, 1, 1, tzinfo=timezone.utc))
        dtype = declared.numeric_dtype or {
            DataType.INT: MountainashDtype.I64,
            DataType.FLOAT: MountainashDtype.FP64,
            DataType.DATE: MountainashDtype.DATE,
            DataType.DATETIME: MountainashDtype.TIMESTAMP,
        }.get(declared.data_type)
        if dtype is None:
            raise DomainError(f"Interval requires ordered domain field {name!r}")
        return ma.lit(None, dtype=dtype)

    def lower(identifier: str) -> LoweredDomain:
        if identifier in memo:
            return memo[identifier]
        if identifier in visiting:
            raise DomainError(f"Cyclic domain predicate reference {identifier!r}")
        payload = nodes.get(identifier)
        if payload is None:
            raise DomainError(f"Unresolved domain predicate {identifier!r}")
        raw_node = payload.get("node")
        if isinstance(raw_node, t.Mapping) and raw_node.get("op") == "language":
            raise UnsupportedDomainError("Unsupported domain predicate: language")
        try:
            validate_envelope({"id": identifier, "payload": dict(payload)}, "predicate")
        except ValueError as exc:
            raise DomainError(
                f"Invalid domain predicate {identifier!r}: {exc}"
            ) from exc
        node = t.cast(t.Mapping[str, t.Any], raw_node)
        visiting.add(identifier)
        op = node["op"]
        projections: dict[str, FieldProjection] = {}
        if op in {"true", "false"}:
            predicate = ma.lit(op == "true")
        elif op in {"and", "or"}:
            children = [lower(child) for child in node["args"]]
            predicate = ma.lit(op == "and")
            for child in children:
                predicate = (
                    predicate & child.predicate
                    if op == "and"
                    else predicate | child.predicate
                )
            if op == "and":
                for child in children:
                    for name, projection in child.projections.items():
                        projections[name] = (
                            _conjoin(projections[name], projection)
                            if name in projections
                            else projection
                        )
            else:
                # Only a union of same-field finite enumerations is projected.
                # Other OR shapes retain their correlations solely in predicate.
                first = children[0].projections
                if len(first) == 1:
                    name = next(iter(first))
                    if all(
                        set(child.projections) == {name}
                        and child.projections[name].values is not None
                        for child in children
                    ):
                        candidates = tuple(
                            candidate
                            for child in children
                            for candidate in t.cast(
                                tuple[BaseExpressionAPI, ...],
                                child.projections[name].values,
                            )
                        )
                        unary = ma.lit(False)
                        for child in children:
                            unary = unary | child.projections[name].predicate
                        projections[name] = FieldProjection(unary, values=candidates)
        elif op == "not":
            predicate = ~lower(node["arg"]).predicate
        elif op in {"eq", "in"}:
            name = node["field"]
            expression = value(name)
            encoded = [node["value"]] if op == "eq" else node["values"]
            candidates = tuple(literal(name, item) for item in encoded)
            predicate = ma.lit(False)
            for candidate in candidates:
                predicate = predicate | (expression == candidate)
            projections[name] = FieldProjection(predicate, values=candidates)
        elif op == "interval":
            name = node["field"]
            declared = declaration(name)
            if declared.data_type is DataType.FLOAT:
                raise UnsupportedDomainError("FLOAT intervals are unsupported")
            if not (declared.data_type.is_numeric or declared.data_type.is_temporal):
                raise DomainError(f"Interval requires ordered domain field {name!r}")
            geometry = Interval(
                lower=absent_endpoint(name)
                if node["lower"] is None
                else literal(name, node["lower"]),
                upper=absent_endpoint(name)
                if node["upper"] is None
                else literal(name, node["upper"]),
                lower_closed=ma.lit(node["lower_closed"]),
                upper_closed=ma.lit(node["upper_closed"]),
                lower_unbounded=ma.lit(node["lower"] is None),
                upper_unbounded=ma.lit(node["upper"] is None),
            )
            predicate = geometry.contains(value(name))
            projections[name] = FieldProjection(predicate, interval=geometry)
        elif op == "compare":
            left, right = declaration(node["left"]), declaration(node["right"])
            if left.data_type is not right.data_type or left.timezone != right.timezone:
                raise DomainError(
                    "Compared domain fields require compatible types and timezones"
                )
            relation = node["relation"]
            if left.data_type is DataType.FLOAT and relation not in {"eq", "ne"}:
                raise UnsupportedDomainError("Ordered FLOAT comparisons are unsupported")
            if relation not in {"eq", "ne"} and not (
                left.data_type.is_numeric or left.data_type.is_temporal
            ):
                raise DomainError(
                    "Ordered field comparison requires ordered domain fields"
                )
            lhs, rhs = value(left.name), value(right.name)
            predicate = getattr(lhs, relation)(rhs)
        else:
            raise UnsupportedDomainError(f"Unsupported domain predicate: {op}")
        result = LoweredDomain(predicate, projections)
        visiting.remove(identifier)
        memo[identifier] = result
        return result

    result = lower(domain.predicate_id)
    return LoweredDomain(
        result.predicate.fill_null(False),
        result.projections,
        invalid,
        declaration_invalid,
    )


def filter_feasible_pairs(
    pair_relation: Relation,
    metadata: DimensionsMetadata,
    left: t.Mapping[str, FieldRestriction],
    right: t.Mapping[str, FieldRestriction],
    *,
    pair_key: str | None = None,
) -> Relation:
    """Keep input pairs with at least one feasible native assignment."""
    if metadata.domain is None and not left and not right:
        return pair_relation
    original_columns = pair_relation.columns
    pair_id = pair_key
    if pair_id is None:
        pair_id = "__feasibility_pair"
        while pair_id in pair_relation.columns:
            pair_id = f"_{pair_id}"
    with materialization_scope() as owner:
        assignments, _, _, indexed = _feasible_assignments_owned(
            pair_relation, metadata, left, right, pair_key=pair_key,
            boundaries=None, domains=None, owner=owner,
        )
        feasible = assignments.select(pair_id).unique(subset=[pair_id])
        result = indexed.join(feasible, on=pair_id).select(*original_columns)
        return owner.detach(_materialize_owned(owner, result))


def feasible_assignments(
    pair_relation: Relation,
    metadata: DimensionsMetadata,
    left: t.Mapping[str, FieldRestriction],
    right: t.Mapping[str, FieldRestriction],
    *,
    pair_key: str | None = None,
    boundaries: t.Mapping[str, Relation] | None = None,
    domains: t.Sequence[DomainDefinition] | None = None,
) -> tuple[Relation, dict[str, str], dict[str, str]]:
    """Relation-valued constraints and boundaries remain native keyed relations."""
    selected_domains = (
        tuple(domains)
        if domains is not None
        else ((metadata.domain,) if metadata.domain is not None else ())
    )
    if not selected_domains and not left and not right and not boundaries:
        return pair_relation, {}, {}
    with materialization_scope() as owner:
        assignments, names, residuals, _ = _feasible_assignments_owned(
            pair_relation, metadata, left, right, pair_key=pair_key,
            boundaries=boundaries, domains=domains, owner=owner,
        )
        return owner.detach(assignments), names, residuals


def feasible_assignments_expression(
    pair_relation: Relation,
    metadata: DimensionsMetadata,
    left: t.Mapping[str, FieldRestriction],
    right: t.Mapping[str, FieldRestriction],
    *,
    pair_key: str | None = None,
    boundaries: t.Mapping[str, Relation] | None = None,
    domains: t.Sequence[DomainDefinition] | None = None,
) -> tuple[Relation, dict[str, str], dict[str, str]]:
    """Build feasible assignments lazily for composition into a serving query."""
    assignments, names, residuals, _ = _feasible_assignments_owned(
        pair_relation, metadata, left, right, pair_key=pair_key,
        boundaries=boundaries, domains=domains, owner=None,
    )
    return assignments, names, residuals


def _feasible_assignments_owned(
    pair_relation: Relation,
    metadata: DimensionsMetadata,
    left: t.Mapping[str, FieldRestriction],
    right: t.Mapping[str, FieldRestriction],
    *,
    pair_key: str | None,
    boundaries: t.Mapping[str, Relation] | None,
    domains: t.Sequence[DomainDefinition] | None,
    owner: t.Any,
) -> tuple[Relation, dict[str, str], dict[str, str], Relation]:
    """Relation-valued constraints and boundaries remain native keyed relations."""
    input_columns = pair_relation.columns
    domains = (
        tuple(domains)
        if domains is not None
        else ((metadata.domain,) if metadata.domain is not None else ())
    )
    boundaries = boundaries or {}
    if not domains and not left and not right and not boundaries:
        return pair_relation, {}, {}, pair_relation
    needs_external_pair_key = bool(boundaries) or any(
        isinstance(constraint.values, Relation)
        for mapping in (left, right)
        for restriction in mapping.values()
        for constraint in restriction.values
    )
    if needs_external_pair_key and pair_key is None:
        raise DomainError("Relation-backed values and boundaries require a unique pair_key")
    if needs_external_pair_key and any(
        isinstance(constraint.values, Relation)
        and not {pair_key, "value"}.issubset(constraint.values.columns)
        for mapping in (left, right)
        for restriction in mapping.values()
        for constraint in restriction.values
    ):
        raise DomainError("Relation-backed values require canonical pair_key/value columns")
    if any(
        not {pair_key, "value"}.issubset(values.columns)
        for values in boundaries.values()
    ):
        raise DomainError("Boundary values require canonical pair_key/value columns")
    pair_id = pair_key
    if pair_id is None:
        pair_id = "__feasibility_pair"
        while pair_id in pair_relation.columns:
            pair_id = f"_{pair_id}"
        indexed = pair_relation.with_row_index(name=pair_id)
    else:
        indexed = pair_relation
    indexed = _materialize_owned(owner, indexed)

    declarations: dict[str, DomainField] = {}
    for domain in domains:
        declarations.update({item.name: item for item in domain.fields})
    descriptors: dict[str, list[FieldRestriction]] = {}
    for mapping in (left, right):
        for name, restriction in mapping.items():
            descriptors.setdefault(name, []).append(restriction)

    def type_key(item: DomainField) -> tuple[t.Any, ...]:
        return item.data_type, item.numeric_dtype, item.timezone, item.rounding

    def canonical_candidate(item: DomainField, expression: BaseExpressionAPI) -> BaseExpressionAPI:
        if item.data_type is DataType.DECIMAL:
            if item.numeric_dtype is None or item.rounding is None:
                raise DomainError(f"Decimal field {item.name!r} lacks numeric metadata")
            return expression.cast(item.numeric_dtype, rounding=item.rounding)
        if item.data_type is DataType.DATETIME:
            # Python datetime metadata has microsecond precision; retain timezone.
            return expression.dt.add_microseconds(0)
        dtype = {
            DataType.STR: MountainashDtype.STRING,
            DataType.BOOL: MountainashDtype.BOOL,
            DataType.INT: MountainashDtype.I64,
            DataType.FLOAT: MountainashDtype.FP64,
            DataType.DATE: MountainashDtype.DATE,
        }[item.data_type]
        return expression.cast(dtype)

    for name, restrictions in descriptors.items():
        first = restrictions[0]
        descriptor_type = (
            first.data_type, first.numeric_dtype, first.timezone, first.rounding,
        )
        if any(
            (item.data_type, item.numeric_dtype, item.timezone, item.rounding)
            != descriptor_type
            for item in restrictions[1:]
        ):
            raise DomainError(f"Incompatible source restrictions for field {name!r}")
        if name in declarations:
            if type_key(declarations[name]) != descriptor_type:
                raise DomainError(f"Source restriction conflicts with domain field {name!r}")
            continue
        declarations[name] = DomainField(
            name=name, data_type=first.data_type, numeric_dtype=first.numeric_dtype,
            timezone=first.timezone, rounding=first.rounding,
        )

    def default_values(item: DomainField) -> tuple[t.Any, ...]:
        match item.data_type:
            case DataType.STR:
                return ("",)
            case DataType.BOOL:
                return (False, True)
            case DataType.INT:
                return (0,)
            case DataType.FLOAT:
                return (0.0,)
            case DataType.DECIMAL:
                return (Decimal(0),)
            case DataType.DATE:
                return (date(2000, 1, 1),)
            case DataType.DATETIME:
                return (
                    datetime(2000, 1, 1, tzinfo=timezone.utc)
                    if item.timezone == "utc"
                    else datetime(2000, 1, 1),
                )
        raise DomainError(f"Unsupported domain field type {item.data_type!r}")

    items_by_type = {type_key(item): item for item in declarations.values()}
    anchors: dict[tuple[t.Any, ...], list[Relation]] = {}

    def add_anchor(key: tuple[t.Any, ...], relation: Relation) -> None:
        candidate = canonical_candidate(items_by_type[key], ma.col("__candidate"))
        anchors.setdefault(key, []).append(
            relation.with_columns(candidate.alias("__candidate"))
        )


    for name, boundary_relation in boundaries.items():
        if name not in declarations:
            raise DomainError(f"Boundary field {name!r} has no declared interpretation")
        add_anchor(type_key(declarations[name]), boundary_relation.select(
            ma.col(pair_key).alias(pair_id),
            ma.col("value").alias("__candidate"),
        ))
    def literal_expression(item: DomainField, payload: t.Mapping[str, t.Any]) -> BaseExpressionAPI:
        native = decode_scalar(dict(payload), allow_reserved=True)
        expression = exact_literal(native)
        if item.data_type is DataType.DECIMAL:
            if item.numeric_dtype is None or item.rounding is None:
                raise DomainError(f"Decimal domain field {item.name!r} lacks numeric metadata")
            operand = decimal_operand(
                expression, literal_dtype(native), item.numeric_dtype, item.rounding,
            )
            expression = operand.value
        return canonical_candidate(item, expression)

    for name, item in declarations.items():
        key = type_key(item)
        for native in default_values(item):
            add_anchor(key, indexed.select(
                pair_id, exact_literal(native).alias("__candidate"),
            ))
        for restriction in descriptors.get(name, ()):
            for constraint in restriction.values:
                if isinstance(constraint.values, Relation):
                    members = constraint.values.select(
                        ma.col(pair_key).alias(pair_id),
                        ma.col("value").alias("__candidate"),
                    )
                    add_anchor(key, members)
                else:
                    active = indexed.filter(~constraint.inactive.fill_null(False))
                    if constraint.is_list:
                        values = active.select(
                            pair_id, constraint.values.alias("__candidate"),
                        ).explode("__candidate")
                    else:
                        values = active.select(
                            pair_id, constraint.values.alias("__candidate"),
                        )
                    add_anchor(key, values)
            for interval in restriction.intervals:
                add_anchor(key, indexed.filter(
                    ~interval.lower_unbounded.fill_null(False)
                ).select(pair_id, interval.lower.alias("__candidate")))
                add_anchor(key, indexed.filter(
                    ~interval.upper_unbounded.fill_null(False)
                ).select(pair_id, interval.upper.alias("__candidate")))

    def collect_literals(domain: DomainDefinition) -> None:
        visited: set[str] = set()

        def visit(identifier: str) -> None:
            if identifier in visited:
                return
            visited.add(identifier)
            payload = metadata.domain_nodes.get(identifier)
            if payload is None or not isinstance(payload.get("node"), t.Mapping):
                raise DomainError(f"Unresolved domain predicate {identifier!r}")
            node = t.cast(t.Mapping[str, t.Any], payload["node"])
            op = node["op"]
            names: tuple[str, ...] = ()
            scalars: tuple[t.Mapping[str, t.Any], ...] = ()
            if op == "eq":
                names, scalars = (node["field"],), (node["value"],)
            elif op == "in":
                names = (node["field"],)
                scalars = tuple(node["values"])
            elif op == "interval":
                names = (node["field"],)
                scalars = tuple(
                    value for value in (node["lower"], node["upper"]) if value is not None
                )
            for name in names:
                item = declarations[name]
                key = type_key(item)
                for scalar in scalars:
                    value = literal_expression(item, scalar)
                    add_anchor(key, indexed.select(
                        pair_id, value.alias("__candidate"),
                    ))
            if op in {"and", "or"}:
                for child in node["args"]:
                    visit(child)
            elif op == "not":
                visit(node["arg"])

        visit(domain.predicate_id)

    for domain in domains:
        lower_domain(domain, metadata.domain_nodes)
        collect_literals(domain)

    candidate_relations: dict[tuple[t.Any, ...], Relation] = {}
    dimensions_by_type: dict[tuple[t.Any, ...], int] = {}
    for item in declarations.values():
        key = type_key(item)
        dimensions_by_type[key] = dimensions_by_type.get(key, 0) + 1

    for key, relations in anchors.items():
        candidates = concat(relations).filter(~ma.col("__candidate").is_null()).unique(
            subset=[pair_id, "__candidate"],
        )
        candidates = _materialize_owned(owner, candidates)
        data_type, numeric_dtype, _timezone, _rounding = key
        distance = dimensions_by_type[key]
        if data_type in (DataType.INT, DataType.DECIMAL, DataType.DATE, DataType.DATETIME):
            shifted: list[Relation] = []
            for offset in range(1, distance + 1):
                value = ma.col("__candidate")
                item = items_by_type[key]
                if data_type is DataType.INT:
                    upper = canonical_candidate(
                        item, ma.lit((1 << 63) - 1 - offset),
                    )
                    lower = canonical_candidate(
                        item, ma.lit(-(1 << 63) + offset),
                    )
                    after = candidates.filter(value.le(upper)).select(
                        pair_id, value.add(ma.lit(offset)).alias("__candidate"),
                    )
                    before = candidates.filter(value.ge(lower)).select(
                        pair_id, value.sub(ma.lit(offset)).alias("__candidate"),
                    )
                elif data_type is DataType.DECIMAL:
                    if numeric_dtype is None:
                        raise DomainError("Decimal ordered field lacks numeric_dtype")
                    scale = numeric_dtype.scale
                    quantum = Decimal((0, (1,), -scale))
                    delta = Decimal(
                        (0, tuple(int(digit) for digit in str(offset)), -scale)
                    )
                    extent = Decimal(
                        (0, (1,), numeric_dtype.precision - scale)
                    )
                    max_value = decimal_subtract_exact(extent, quantum)
                    min_value = decimal_add_exact(
                        Decimal((1, (1,), numeric_dtype.precision - scale)),
                        quantum,
                    )
                    after_delta = canonical_candidate(item, exact_literal(delta))
                    before_delta = canonical_candidate(item, exact_literal(delta))
                    max_bound = canonical_candidate(
                        item, exact_literal(decimal_subtract_exact(max_value, delta))
                    )
                    min_bound = canonical_candidate(
                        item, exact_literal(decimal_add_exact(min_value, delta))
                    )
                    after = candidates.filter(value.le(max_bound)).select(
                        pair_id, value.add(after_delta).alias("__candidate"),
                    )
                    before = candidates.filter(value.ge(min_bound)).select(
                        pair_id, value.sub(before_delta).alias("__candidate"),
                    )
                elif data_type is DataType.DATE:
                    after_bound = canonical_candidate(
                        item, exact_literal(date.max - timedelta(days=offset)),
                    )
                    before_bound = canonical_candidate(
                        item, exact_literal(date.min + timedelta(days=offset)),
                    )
                    after = candidates.filter(value.le(after_bound)).select(
                        pair_id, value.dt.add_days(offset).alias("__candidate"),
                    )
                    before = candidates.filter(value.ge(before_bound)).select(
                        pair_id, value.dt.add_days(-offset).alias("__candidate"),
                    )
                else:
                    shift = timedelta(microseconds=offset)
                    maximum = datetime.max.replace(tzinfo=timezone.utc) if _timezone == "utc" else datetime.max
                    minimum = datetime.min.replace(tzinfo=timezone.utc) if _timezone == "utc" else datetime.min
                    after_bound = canonical_candidate(item, exact_literal(maximum - shift))
                    before_bound = canonical_candidate(item, exact_literal(minimum + shift))
                    after = candidates.filter(value.le(after_bound)).select(
                        pair_id, value.dt.add_microseconds(offset).alias("__candidate"),
                    )
                    before = candidates.filter(value.ge(before_bound)).select(
                        pair_id, value.dt.add_microseconds(-offset).alias("__candidate"),
                    )
                shifted.extend([
                    after.with_columns(
                        canonical_candidate(item, ma.col("__candidate")).alias("__candidate")
                    ),
                    before.with_columns(
                        canonical_candidate(item, ma.col("__candidate")).alias("__candidate")
                    ),
                ])
            if shifted:
                candidates = concat([candidates, *shifted]).filter(
                    ~ma.col("__candidate").is_null()
                ).unique(subset=[pair_id, "__candidate"])
        if data_type in (DataType.STR, DataType.FLOAT):
            concrete = candidates.select(
                pair_id, ma.col("__candidate"),
                ma.lit(-1, dtype=MountainashDtype.I64).alias("__residual"),
            )
            residuals = indexed.select(
                pair_id, ma.lit(list(range(distance))).alias("__residual"),
            ).explode("__residual").select(
                pair_id,
                ma.lit(
                    None,
                    dtype=(
                        MountainashDtype.STRING
                        if data_type is DataType.STR else MountainashDtype.FP64
                    ),
                ).alias("__candidate"),
                ma.col("__residual").cast(MountainashDtype.I64).alias("__residual"),
            )
            candidates = concat([concrete, residuals])
        else:
            candidates = candidates.select(
                pair_id, ma.col("__candidate"),
                ma.lit(-1, dtype=MountainashDtype.I64).alias("__residual"),
            )
        candidate_relations[key] = _materialize_owned(owner, candidates)

    assignment_names: dict[str, str] = {}
    assignment_residual_names: dict[str, str] = {}
    assignments = indexed
    for name, item in declarations.items():
        key = type_key(item)
        assigned_name = f"__assignment_{name}"
        while assigned_name in assignments.columns:
            assigned_name = f"_{assigned_name}"
        assignment_names[name] = assigned_name
        residual_name = f"__assignment_residual_{name}"
        while residual_name in assignments.columns or residual_name == assigned_name:
            residual_name = f"_{residual_name}"
        assignment_residual_names[name] = residual_name
        candidates = candidate_relations[key].select(
            pair_id,
            ma.col("__candidate").alias(assigned_name),
            ma.col("__residual").alias(residual_name),
        )
        local = indexed.join(candidates, on=pair_id, execute_on="right")
        local_valid = ma.lit(True)
        candidate = ma.col(assigned_name)
        residual = ma.col(residual_name)
        for restriction in descriptors.get(name, ()):
            for interval in restriction.intervals:
                typed_interval = Interval(
                    canonical_candidate(declarations[name], interval.lower),
                    canonical_candidate(declarations[name], interval.upper),
                    interval.lower_closed, interval.upper_closed,
                    interval.lower_unbounded, interval.upper_unbounded,
                )
                local_valid = local_valid & typed_interval.contains(candidate).fill_null(False)
            for constraint in restriction.values:
                inactive = constraint.inactive.fill_null(False)
                if isinstance(constraint.values, Relation):
                    members = constraint.values.select(
                        ma.col(pair_key).alias(pair_id),
                        canonical_candidate(
                            declarations[name], ma.col("value"),
                        ).alias(assigned_name),
                    ).unique(subset=[pair_id, assigned_name])
                    active = local.filter(~inactive)
                    inactive_rows = local.filter(inactive)
                    concrete = active.filter(residual.eq(-1))
                    matching = concrete.join(
                        members,
                        on=[pair_id, assigned_name],
                        how="inner" if constraint.allowed else "anti",
                        execute_on="right",
                    )
                    if constraint.allowed:
                        local = concat([matching, inactive_rows])
                    else:
                        residuals = active.filter(residual.ne(-1))
                        local = concat([matching, residuals, inactive_rows])
                else:
                    match = (
                        constraint.values.list.contains(candidate)
                        if constraint.is_list
                        else candidate.eq(
                            canonical_candidate(declarations[name], constraint.values)
                        )
                    )
                    match = residual.eq(-1) & match
                    local_valid = local_valid & (
                        inactive | (match if constraint.allowed else ~match)
                    ).fill_null(False)
        local = local.filter(local_valid.fill_null(False)).select(
            pair_id, assigned_name, residual_name,
        ).unique(subset=[pair_id, assigned_name, residual_name])
        assignments = _materialize_owned(
            owner, assignments.join(local, on=pair_id, execute_on="right"),
        )

    valid = ma.lit(True)
    def symbolic_domain(domain: DomainDefinition) -> BaseExpressionAPI:
        memo: dict[str, BaseExpressionAPI] = {}
        visiting: set[str] = set()

        def visit(identifier: str) -> BaseExpressionAPI:
            if identifier in memo:
                return memo[identifier]
            if identifier in visiting:
                raise DomainError(f"Cyclic domain predicate reference {identifier!r}")
            visiting.add(identifier)
            payload = metadata.domain_nodes.get(identifier)
            if payload is None or not isinstance(payload.get("node"), t.Mapping):
                raise DomainError(f"Unresolved domain predicate {identifier!r}")
            node = t.cast(t.Mapping[str, t.Any], payload["node"])
            op = node["op"]
            if op in {"true", "false"}:
                result = ma.lit(op == "true")
            elif op in {"and", "or"}:
                result = ma.lit(op == "and")
                for child in node["args"]:
                    predicate = visit(child)
                    result = (
                        result & predicate if op == "and" else result | predicate
                    )
            elif op == "not":
                result = ~visit(node["arg"])
            elif op in {"eq", "in"}:
                name = node["field"]
                candidate = ma.col(assignment_names[name])
                symbolic_value = declarations[name].data_type in (
                    DataType.STR, DataType.FLOAT,
                )
                concrete = (
                    ma.col(assignment_residual_names[name]).eq(-1)
                    if symbolic_value else ma.lit(True)
                )
                encoded = [node["value"]] if op == "eq" else node["values"]
                result = ma.lit(False)
                for scalar in encoded:
                    result = result | (
                        concrete & candidate.eq(literal_expression(declarations[name], scalar))
                    )
            elif op == "compare":
                left_name, right_name = node["left"], node["right"]
                lhs, rhs = ma.col(assignment_names[left_name]), ma.col(
                    assignment_names[right_name]
                )
                relation_name = node["relation"]
                if declarations[left_name].data_type in (
                    DataType.STR, DataType.FLOAT,
                ) and relation_name in {"eq", "ne"}:
                    left_residual = ma.col(assignment_residual_names[left_name])
                    right_residual = ma.col(assignment_residual_names[right_name])
                    left_concrete = left_residual.eq(-1)
                    right_concrete = right_residual.eq(-1)
                    equal = (
                        left_concrete & right_concrete & lhs.eq(rhs)
                    ) | (
                        ~left_concrete & ~right_concrete
                        & left_residual.eq(right_residual)
                    )
                    result = equal if relation_name == "eq" else ~equal
                else:
                    result = getattr(lhs, relation_name)(rhs)
            else:
                leaf = domain.model_copy(update={"predicate_id": identifier})
                value_map = {
                    field.name: ma.col(assignment_names[field.name])
                    for field in domain.fields
                }
                result = lower_domain(
                    leaf, metadata.domain_nodes, values=value_map,
                ).predicate
            memo[identifier] = result
            visiting.remove(identifier)
            return result

        return visit(domain.predicate_id)

    for domain in domains:
        valid = valid & symbolic_domain(domain)
    output_columns = list(input_columns)
    if pair_id not in output_columns:
        output_columns.append(pair_id)
    feasible = assignments.filter(valid.fill_null(False)).select(
        *output_columns, *assignment_names.values(), *assignment_residual_names.values(),
    )
    feasible = _materialize_owned(owner, feasible)
    return feasible, assignment_names, assignment_residual_names, indexed
