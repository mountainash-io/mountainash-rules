"""Typed authoring normalization and state-only dimension expressions."""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash import MountainashDtype
from mountainash.core.dtypes import dtype_family
from mountainash.expressions import BaseExpressionAPI

from mountainash_rules.core.constants import MatchStrategy
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.core.numeric import exact_literal, typed_marker_predicate
from mountainash_rules.core.set_wildcard import set_state
from mountainash_rules.core.type_families import type_family


def rule_binding_names(dim: Dimension, endpoint: str | None = None) -> tuple[str, str]:
    name = dim.dimension_name
    if endpoint is not None:
        return f"__rule_{endpoint}_value_{name}", f"__rule_{endpoint}_unbounded_{name}"
    return f"__rule_value_{name}", f"__rule_wildcard_{name}"


def rule_flag_state(
    rel: t.Any, field: str | None, marker: BaseExpressionAPI
) -> tuple[BaseExpressionAPI, BaseExpressionAPI]:
    """Mapped flags are authoritative only when their authoring is consistent."""
    if field is None:
        return marker, ma.lit(False)
    if field not in rel.columns or dtype_family(rel.schema[field]) is not MountainashDtype.BOOL:
        raise ValueError(f"Rule flag {field!r} must be an existing Boolean column")
    flag = ma.col(field)
    return flag.fill_null(False), flag.is_null() | ((~flag).fill_null(False) & marker)


def rule_binding_expressions(
    rel: t.Any,
    dimensions: t.Sequence[Dimension],
) -> list[BaseExpressionAPI]:
    """Native payload and wildcard-state bindings of every rule operand; no admission.

    A PRESENCE operand is the authored ``IS_NULL``/``IS_NOT_NULL`` text whatever the observed
    field's datatype, so it binds as text rather than as a value of the field.
    """
    columns = []
    schema = rel.collect_schema(allow_execution=False)
    for dim in dimensions:
        if dim.match_strategy is MatchStrategy.CONTEXT_REGEX:
            continue
        if dim.match_strategy is MatchStrategy.RANGE:
            operands = [("min", dim.range_min_field), ("max", dim.range_max_field)]
        else:
            operands = [(None, dim.resolved_rule_field)]
        for endpoint, field in operands:
            if field is None or field not in schema:
                raise ValueError(f"Missing rule operand {field!r}")
            raw = ma.col(field)
            source_dtype = schema[field]
            if dim.match_strategy in (MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION):
                wildcard, _, payload = set_state(rel, dim, field)
            else:
                markers = [dim.field.wildcard] if dim.field.wildcard_declared else []
                wildcard = typed_marker_predicate(raw, source_dtype, markers)
                if dim.match_strategy is MatchStrategy.PRESENCE:
                    payload = raw
                else:
                    payload, _ = type_family(dim.data_type).bind_rule(dim.field, raw, source_dtype, wildcard)
                    if dim.match_strategy is MatchStrategy.REGEX:
                        payload = ma.when(wildcard).then("").otherwise(payload)
            value_name, state_name = rule_binding_names(dim, endpoint)
            columns.extend([payload.alias(value_name), wildcard.alias(state_name)])
    return columns


def normalize_rule_relation(rel: t.Any, dimensions: t.Sequence[Dimension]) -> t.Any:
    """Bind the rule operands of ``dimensions``; the rules are scored as given, never admitted."""
    columns = rule_binding_expressions(rel, dimensions)
    return rel.with_columns(*columns) if columns else rel


def truthy_outcome(
    dim: Dimension,
    value: BaseExpressionAPI,
    *,
    context: bool = False,
) -> BaseExpressionAPI:
    """Map declared source values to their ternary truthy outcomes.

    A rule value maps through the field's ``truthy`` table.  A context value additionally maps
    ``None`` when the table declares it, read from the bound ``__ctx_origin_<dimension>`` column.
    """
    outcome = ma.lit(0)
    for mapped_value, mapped_outcome in dim.field.truthy.items():
        if mapped_value is None:
            if not context:
                continue
            matches = ma.col(f"__ctx_origin_{dim.dimension_name}").eq("null")
        else:
            matches = value.eq(exact_literal(mapped_value)).fill_null(False)
            if context:
                # Inactive payloads (notably DECIMAL's safe zero) are not values.
                matches = ma.col(f"__ctx_present_{dim.dimension_name}") & matches
        outcome = ma.when(matches).then(mapped_outcome).otherwise(outcome)
    return outcome


class DimensionCompiler:
    """Compile normalized state/payload bindings, independent of authoring form."""

    def compile_dimensions(
        self, metadata: DimensionsMetadata
    ) -> dict[str, BaseExpressionAPI]:
        return {
            dim.dimension_name: self.compile_dimension(dim)
            for dim in metadata.dimensions
        }

    @staticmethod
    def _ternary(
        dim: Dimension, matched: BaseExpressionAPI, endpoint: str | None = None
    ) -> BaseExpressionAPI:
        _, state_name = rule_binding_names(dim, endpoint)
        missing = ~ma.col(f"__ctx_present_{dim.dimension_name}")
        return (
            ma.when(ma.col(state_name) | missing)
            .then(0)
            .when(matched)
            .then(1)
            .otherwise(-1)
        )

    @staticmethod
    def _outcome_ternary(
        rule: BaseExpressionAPI, context: BaseExpressionAPI
    ) -> BaseExpressionAPI:
        """Compare required and observed ternary outcomes."""
        return (
            ma.when(rule.eq(ma.lit(0)) | context.eq(ma.lit(0)))
            .then(0)
            .when(rule.eq(context))
            .then(1)
            .otherwise(-1)
        )

    def compile_dimension(self, dim: Dimension) -> BaseExpressionAPI:
        context_name = f"__ctx_value_{dim.dimension_name}"
        context = ma.col(context_name)
        if dim.match_strategy is MatchStrategy.PRESENCE:
            # Suppliedness and the original nullness, never the value-marker classification: an
            # explicit null is a known null even when null is the field's declared wildcard.
            rule, wildcard = rule_binding_names(dim)
            name = dim.dimension_name
            return (
                ma.when(ma.col(wildcard) | ~ma.col(f"__ctx_supplied_{name}"))
                .then(0)
                .when(ma.col(f"__ctx_null_{name}"))
                .then(ma.when(ma.col(rule).eq("IS_NULL")).then(1).otherwise(-1))
                .otherwise(ma.when(ma.col(rule).eq("IS_NOT_NULL")).then(1).otherwise(-1))
            )
        if dim.match_strategy is MatchStrategy.CONTEXT_REGEX:
            return (
                ma.when(~ma.col(f"__ctx_present_{dim.dimension_name}"))
                .then(0)
                .when(context.str.regex_contains(dim.regex_pattern))
                .then(1)
                .otherwise(-1)
            )
        if dim.match_strategy is MatchStrategy.RANGE:
            lower = ma.col(rule_binding_names(dim, "min")[0])
            upper = ma.col(rule_binding_names(dim, "max")[0])
            lo = self._ternary(dim, lower.le(context), "min")
            hi = self._ternary(dim, upper.ge(context), "max")
            return ma.when(lo.le(hi)).then(lo).otherwise(hi)
        value_name, state_name = rule_binding_names(dim)
        rule = ma.col(value_name)
        if dim.match_strategy is MatchStrategy.OUTCOME:
            # Wildcard state survives an inactive payload becoming null during binding.
            return (
                ma.when(ma.col(state_name) | ~ma.col(f"__ctx_present_{dim.dimension_name}"))
                .then(0)
                .otherwise(self._outcome_ternary(rule, context))
            )
        if dim.match_strategy is MatchStrategy.TRUTHY:
            rule_outcome = ma.when(ma.col(state_name)).then(0).otherwise(
                truthy_outcome(dim, rule)
            )
            return self._outcome_ternary(
                rule_outcome, truthy_outcome(dim, context, context=True)
            )
        match dim.match_strategy:
            case MatchStrategy.EXACT:
                matched = rule.eq(context)
            case MatchStrategy.NOT_EQUAL:
                matched = rule.ne(context)
            case MatchStrategy.MIN:
                matched = context.ge(rule)
            case MatchStrategy.MAX:
                matched = context.le(rule)
            case MatchStrategy.PREFIX:
                matched = context.str.starts_with(rule)
            case MatchStrategy.SUFFIX:
                matched = context.str.ends_with(rule)
            case MatchStrategy.CONTAINS:
                matched = context.str.contains(rule)
            case MatchStrategy.REGEX:
                matched = context.str.regex_contains(rule)
            case MatchStrategy.SET_MEMBERSHIP:
                matched = rule.list.contains(context)
            case MatchStrategy.SET_EXCLUSION:
                matched = ~rule.list.contains(context)
            case _:
                raise ValueError(f"Unknown match strategy: {dim.match_strategy}")
        return self._ternary(dim, matched)
