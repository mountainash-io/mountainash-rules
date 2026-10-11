"""Lower retained predicate-1 metadata without a host reasoner or row search.

Projections are necessary restrictions, never proofs of joint satisfiability.
Construction must keep the full predicate when composing projected pieces.
"""

from __future__ import annotations

import typing as t
from dataclasses import dataclass, field
from datetime import datetime, timezone

import mountainash.expressions as ma
from mountainash import MountainashDtype
from mountainash.expressions import BaseExpressionAPI

from mountainash_rules.core.codec import validate_envelope
from mountainash_rules.core.constants import DataType
from mountainash_rules.core.contracts import DomainDefinition, DomainField
from mountainash_rules.core.numeric import decimal_operand, exact_literal, literal_dtype
from mountainash_rules.core.scalar import decode_scalar
from mountainash_rules.engines.accumulate.errors import (
    DomainError,
    UnsupportedDomainError,
)


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
