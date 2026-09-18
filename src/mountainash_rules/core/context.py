"""Context value extraction utilities."""

from __future__ import annotations

import math
import typing as t
from itertools import product
from dataclasses import dataclass
from types import MappingProxyType

from pydantic import BaseModel

import mountainash.expressions as ma
from mountainash import (
    CaseFailureBehaviour,
    ValueKind,
    boolean_value,
    text_value,
    value_kind,
)
from mountainash.expressions import BaseExpressionAPI

from mountainash_rules.core.constants import (
    NOT_SET,
    BooleanCoercion,
    DataType,
    not_set_sentinel_for,
)

if t.TYPE_CHECKING:
    from mountainash_rules.core.contracts import ContextContract, Issue, ResolutionProfile
    from mountainash_rules.core.dimension import Dimension, DimensionsMetadata


_BOOLEAN_TRIM = " \t\n\r\f\v"
_BOOLEAN_TRUE = tuple(
    "".join(chars) for chars in product(*[(c, c.upper()) for c in "true"])
) + ("1",)
_BOOLEAN_FALSE = tuple(
    "".join(chars) for chars in product(*[(c, c.upper()) for c in "false"])
) + ("0",)


def _validate_boolean_coercion(policy: BooleanCoercion) -> None:
    """Reject implicit configuration conversions and unknown flag bits."""
    if not isinstance(policy, BooleanCoercion) or int(policy) & ~7:
        raise ValueError(
            "boolean_coercion must be a BooleanCoercion with only known flags"
        )


def _numeric_boolean_source(policy: BooleanCoercion) -> str | None:
    if policy & BooleanCoercion.NUMERIC_TRUTHINESS:
        return "finite_number"
    if policy & BooleanCoercion.BINARY_NUMBERS:
        return "binary_number"
    return None


def _normalize_boolean(
    value: t.Any,
    field: str,
    policy: BooleanCoercion = BooleanCoercion.NONE,
) -> bool | None:
    """Admit candidates from the original domain, never a conversion chain."""
    if value_kind(value) is ValueKind.ABSENT:
        return None
    normalized = boolean_value(value, source="boolean")
    if normalized is not None:
        return normalized
    numeric_source = _numeric_boolean_source(policy)
    if numeric_source is not None:
        normalized = boolean_value(value, source=numeric_source)
        if normalized is not None:
            return normalized
    if policy & BooleanCoercion.BOOLEAN_TEXT:
        text = text_value(value)
        if text is not None:
            token = text.strip(_BOOLEAN_TRIM)
            if token in _BOOLEAN_TRUE:
                return True
            if token in _BOOLEAN_FALSE:
                return False
    raise ValueError(
        f"Invalid Boolean input for context field {field!r} under {policy!r}"
    )


def _boolean_columns(
    rel: t.Any,
    fields: t.Iterable[str],
    policy: BooleanCoercion = BooleanCoercion.NONE,
) -> dict[str, BaseExpressionAPI]:
    """Validate original fields in one aggregate and return nullable bindings."""
    candidates = {}
    available = set(rel.columns)
    numeric_source = _numeric_boolean_source(policy)
    for field in dict.fromkeys(fields):
        if field not in available:
            continue
        original = ma.col(field)
        enabled = [original.boolean_value(source="boolean")]
        if numeric_source is not None:
            enabled.append(original.boolean_value(source=numeric_source))
        if policy & BooleanCoercion.BOOLEAN_TEXT:
            enabled.append(
                original.text_value()
                .str.strip_chars(_BOOLEAN_TRIM)
                .parse_boolean(
                    true_values=_BOOLEAN_TRUE,
                    false_values=_BOOLEAN_FALSE,
                    field_name=field,
                    failure_behavior=CaseFailureBehaviour.NULL,
                )
            )
        candidates[field] = enabled[0] if len(enabled) == 1 else ma.coalesce(*enabled)
    if candidates:
        invalid = (
            rel.group_by()
            .agg(
                *[
                    (
                        ma.col(field).value_kind().ne(ValueKind.ABSENT.value)
                        & candidate.is_null()
                    )
                    .any()
                    .fill_null(False)
                    .alias(field)
                    for field, candidate in candidates.items()
                ]
            )
            .to_dict()
        )
        for field, values in invalid.items():
            if values[0]:
                raise ValueError(
                    f"Invalid Boolean input for context field {field!r} under {policy!r}"
                )
    return candidates


def _validate_context_ids(rel: t.Any, context_id_field: str) -> None:
    """Reject missing, null or duplicate caller IDs before conversion/chunking.

    Aggregate row, non-null and distinct counts together; never collect the
    full input merely to validate identity. A single null is invalid even
    when uniqueness would otherwise allow it.
    """
    if not isinstance(context_id_field, str) or not context_id_field:
        raise ValueError("context_id_field must be a nonempty string or None")
    if context_id_field not in rel.columns:
        raise ValueError(f"context_id_field {context_id_field!r} not found in contexts")
    stats = (
        rel.group_by()
        .agg(
            ma.count_records().alias("__total"),
            ma.col(context_id_field).count().alias("__non_null"),
            ma.col(context_id_field).n_unique().alias("__distinct"),
        )
        .to_dict()
    )
    total = stats["__total"][0]
    non_null = stats["__non_null"][0]
    distinct = stats["__distinct"][0]
    if non_null != total:
        raise ValueError(
            f"context_id_field {context_id_field!r} must be non-null "
            f"({total - non_null} null value(s) found among {total} rows)"
        )
    if distinct != total:
        raise ValueError(
            f"context_id_field {context_id_field!r} must be globally unique "
            f"({total} rows, {distinct} distinct value(s))"
        )


def _absent_context_value(data_type: DataType | None) -> t.Any:
    """Choose the declared type's missing value; bool has no in-band sentinel."""
    if data_type is DataType.BOOL:
        return None
    return not_set_sentinel_for(data_type) if data_type is not None else NOT_SET


def _nullable_bool(expr: BaseExpressionAPI) -> BaseExpressionAPI:
    """Preserve absence when a backend's Boolean cast maps null to False."""
    return ma.when(expr.is_null()).then(None).otherwise(expr.cast(bool))


def _context_literal(value: t.Any, data_type: DataType | None) -> BaseExpressionAPI:
    """Bind Boolean absence with a type even when no concrete values exist."""
    literal = ma.lit(value)
    return (
        _nullable_bool(literal)
        if value is None and data_type is DataType.BOOL
        else literal
    )


def extract_context_values(
    context: BaseModel | dict,
    dimension_names: list[str],
    metadata: DimensionsMetadata | None = None,
    *,
    boolean_coercion: BooleanCoercion = BooleanCoercion.NONE,
) -> dict[str, t.Any]:
    """Extract context values for the given dimension names.

    Args:
        context: A Pydantic model or dict containing context values.
        dimension_names: The dimension names to extract values for.
        metadata: Optional dimension metadata. When provided, values are read
            from each dimension's resolved_context_field and missing values
            use its typed NOT_SET sentinel (Boolean null for bool). Without
            metadata, values are read by dimension name and use string NOT_SET.

    Returns:
        Dict mapping dimension name to its value or sentinel.
    """
    if isinstance(context, BaseModel):
        raw = context.model_dump()
    elif isinstance(context, dict):
        raw = context
    else:
        raise TypeError(
            f"Context must be a BaseModel or dict, got {type(context).__name__}"
        )

    result: dict[str, t.Any] = {}
    for name in dimension_names:
        dim = metadata.get_dimension(name) if metadata is not None else None
        field = dim.resolved_context_field if dim is not None else name
        value = raw.get(field)
        if dim is not None and dim.data_type is DataType.BOOL:
            result[name] = _normalize_boolean(value, field, boolean_coercion)
        elif value is None:
            result[name] = _absent_context_value(
                dim.data_type if dim is not None else None
            )
        else:
            result[name] = value
    return result


@dataclass(frozen=True, slots=True)
class ClassifiedContext:
    """Supplied facts remain separate from observations retained by a profile.

    This boundary classifies availability and types only. Joint-domain, guard
    and routing admission must still run before effective facts are reasoned on.
    """

    provided_values: t.Mapping[str, t.Any]
    effective_values: t.Mapping[str, t.Any]
    observations: t.Mapping[str, str]
    issues: tuple[Issue, ...]


def classify_exact_context(
    context: BaseModel | t.Mapping[str, t.Any],
    metadata: DimensionsMetadata,
    contract: ContextContract,
    profile: ResolutionProfile,
    *,
    dont_care: t.Sequence[str] = (),
) -> ClassifiedContext:
    """Classify original values before masking, casting or sentinel filling."""
    from mountainash_rules.core.constants import (
        DimensionRole,
        MatchStrategy,
        unknown_sentinel_for,
    )
    from mountainash_rules.core.contracts import Issue
    from mountainash_rules.core.scalar import normalize_scalar

    if isinstance(context, BaseModel):
        raw = {name: getattr(context, name) for name in context.model_fields_set}
    elif isinstance(context, t.Mapping):
        raw = context
    else:
        raise ValueError("Context must be a mapping or Pydantic model")
    issues = []
    if isinstance(dont_care, (str, bytes)) or not isinstance(dont_care, t.Sequence):
        issues.append(Issue(field="dont_care", dimension=None, code="invalid_type",
                            message="dont_care must be a sequence of dimension names"))
        masks = set()
    elif any(type(name) is not str for name in dont_care) or len(set(dont_care)) != len(dont_care):
        issues.append(Issue(field="dont_care", dimension=None, code="invalid_type",
                            message="dont_care requires unique dimension names"))
        masks = set()
    else:
        masks = set(dont_care)
    provided = {}
    states = {}
    aliases: dict[str, list[Dimension]] = {}
    for dim in metadata.dimensions:
        aliases.setdefault(dim.resolved_context_field, []).append(dim)
    allowed = set(profile.allow_dont_care)
    selected = set(profile.dimensions)

    def issue(field, dimension, code, message):
        issues.append(Issue(field=field, dimension=dimension, code=code, message=message))

    for name in sorted(masks - allowed):
        dim = next((d for d in metadata.dimensions if d.dimension_name == name), None)
        issue(dim.resolved_context_field if dim else None, name, "forbidden_projection", "Mask is not permitted by the profile")
    for field in contract.fields:
        name = field.name
        if name not in raw:
            states[name] = "omitted"
        elif raw[name] is None:
            states[name] = "null"
        elif value_kind(raw[name]) is ValueKind.ABSENT or (
            value_kind(raw[name]) is ValueKind.FLOAT and math.isnan(raw[name])
        ):
            states[name] = "native_missing"
        else:
            try:
                value = normalize_scalar(raw[name], field.data_type, timezone=field.timezone, context=True, allow_reserved=True)
            except ValueError as exc:
                states[name] = "invalid"
                issue(name, None, "invalid_type", str(exc))
                continue
            marker_value = value.replace(tzinfo=None) if field.data_type is DataType.DATETIME else value
            if field.data_type is not DataType.BOOL and marker_value == not_set_sentinel_for(field.data_type):
                states[name] = "not_set"
            elif field.data_type is not DataType.BOOL and marker_value == unknown_sentinel_for(field.data_type):
                states[name] = "dont_care"
                for dim in aliases.get(name, ()):
                    masks.add(dim.dimension_name)
                    if dim.dimension_name not in allowed:
                        issue(name, dim.dimension_name, "forbidden_projection", "UNKNOWN masks every alias and requires permission")
            else:
                states[name] = "concrete"
                provided[name] = value
        if field.required and states[name] != "concrete":
            issue(name, None, "missing_required", "Required field needs a concrete supplied value")
    effective = {}
    for name, value in provided.items():
        for dim in aliases.get(name, ()):
            mandatory = dim.role is DimensionRole.CONTEXT_KEY or dim.match_strategy is MatchStrategy.CONTEXT_REGEX
            if mandatory or (dim.dimension_name in selected and dim.dimension_name not in masks):
                effective[name] = value
                break
    return ClassifiedContext(MappingProxyType(provided), MappingProxyType(effective), MappingProxyType(states), tuple(issues))
