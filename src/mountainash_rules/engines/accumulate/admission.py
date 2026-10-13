"""Request identity and original-state projection before source joins."""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash import MountainashDtype
from mountainash.relations import Relation

from mountainash_rules.core.constants import BooleanCoercion
from mountainash_rules.core.context import _validate_context_ids, context_binding_expressions
from mountainash_rules.core.dimension import DimensionsMetadata
from mountainash_rules.engines.accumulate.errors import RequestValidationError


def select_scopes(metadata: DimensionsMetadata, scopes: t.Sequence[str] | None) -> tuple[str, ...]:
    """Validate once; preserve declaration/caller order without repeated scopes."""
    declared = tuple(metadata.outcome_scopes) or ("default",)
    if scopes is None:
        return declared
    if not isinstance(scopes, (list, tuple)) or any(not isinstance(name, str) for name in scopes):
        raise RequestValidationError("scopes must be a sequence of declared scope names")
    if any(name not in declared for name in scopes):
        raise RequestValidationError("Unknown outcome scope")
    return tuple(dict.fromkeys(scopes))


def project_contexts(contexts: Relation, metadata: DimensionsMetadata) -> Relation:
    """Keep supplied-state and invalidity independent of rule survival.

    All expressions bind against original context columns. Only transport keys
    and reserved working columns survive projection, preventing authored rule
    column names from changing context meaning after a join.
    """
    if not isinstance(contexts, Relation):
        raise RequestValidationError("contexts must be a Mountainash Relation")
    try:
        _validate_context_ids(contexts, "request_id")
    except ValueError as exc:
        raise RequestValidationError(str(exc)) from exc
    columns = [ma.col("request_id")]
    invalid = ma.lit(False)
    for dim in metadata.dimensions:
        field = dim.resolved_context_field
        supplied = field in contexts.columns
        raw = ma.col(field) if supplied else ma.lit(None)
        bindings, failure = context_binding_expressions(
            dim, raw, contexts.schema.get(field), ma.lit(supplied), BooleanCoercion.NONE,
            strict_concrete=True,
        )
        columns.extend(bindings)
        invalid = invalid | failure
    partition_field = metadata.partition_field
    if partition_field is None:
        partition = ma.lit("default")
        partition_present = ma.lit(True)
    elif partition_field in contexts.columns:
        partition = ma.col(partition_field)
        partition_present = partition.is_not_null()
    else:
        partition = ma.lit(None, dtype=MountainashDtype.STRING)
        partition_present = ma.lit(False)
    columns.extend([
        partition.alias("partition"), partition_present.alias("__partition_present"),
        invalid.fill_null(True).alias("__invalid_value"),
    ])
    return contexts.select(*columns).with_row_index(name="request_order")


def admit_partition(working: Relation, identities: Relation, metadata: DimensionsMetadata) -> Relation:
    """Exact routing is request admission, even for an empty selected scope set."""
    if metadata.partition_field is None:
        known = ma.lit(True)
    else:
        partitions = identities.select("partition").unique().with_columns(ma.lit(True).alias("__known_partition"))
        working = working.join(partitions, on="partition", how="left")
        known = ma.col("__known_partition").fill_null(False)
    return working.with_columns(
        ma.when(~ma.col("__partition_present") | ~known).then("invalid_partition")
        .when(ma.col("__invalid_value")).then("invalid_value")
        .otherwise(ma.lit(None, dtype=MountainashDtype.STRING)).alias("__admission_reason")
    )


def bind_request_controls(
    working: Relation,
    metadata: DimensionsMetadata,
    request_control: Relation | None,
    masks: Relation | None,
) -> Relation:
    """Admit separate control channels without interpreting context columns as IDs."""
    def reject_rows(rows: Relation, message: str) -> None:
        if rows.limit(1).count_rows():
            raise RequestValidationError(message)

    request_ids = working.select("request_id")
    if request_control is None:
        working = working.with_columns(
            ma.lit(None, dtype=MountainashDtype.STRING).alias("contract_id"),
            ma.lit(None, dtype=MountainashDtype.STRING).alias("profile_id"),
        )
    else:
        required = {"request_id", "contract_id", "profile_id"}
        if not isinstance(request_control, Relation) or not required.issubset(request_control.columns):
            raise RequestValidationError("request_control requires request_id, contract_id and profile_id")
        try:
            _validate_context_ids(request_control, "request_id")
        except ValueError as exc:
            raise RequestValidationError(str(exc)) from exc
        reject_rows(
            request_control.join(request_ids, on="request_id", how="anti"),
            "request_control refers to an unknown request",
        )
        allowed = ma.lit(False)
        if all(request_control.schema[name] is MountainashDtype.STRING for name in ("contract_id", "profile_id")):
            for contract in metadata.context_contracts:
                for profile in contract.profiles:
                    allowed = allowed | (
                        ma.col("contract_id").eq(contract.contract_id)
                        & ma.col("profile_id").eq(profile.profile_id)
                    )
        reject_rows(
            request_control.filter(~allowed.fill_null(False)),
            "Unknown contract/profile selection",
        )
        controls = request_control.select(
            "request_id",
            ma.col("contract_id").cast(str).alias("contract_id"),
            ma.col("profile_id").cast(str).alias("profile_id"),
        )
        working = working.join(controls, on="request_id", how="left")

    if masks is None:
        columns = [ma.lit(False).alias(f"__mask_{dim.dimension_name}") for dim in metadata.dimensions]
        return working.with_columns(*columns) if columns else working
    if not isinstance(masks, Relation) or not {"request_id", "dimension_name"}.issubset(masks.columns):
        raise RequestValidationError("masks requires request_id and dimension_name")
    reject_rows(
        masks.filter(ma.col("request_id").is_null() | ma.col("dimension_name").is_null()),
        "Mask keys must be nonnull",
    )
    counts = masks.group_by("request_id", "dimension_name").agg(ma.count_records().alias("__mask_count"))
    reject_rows(counts.filter(ma.col("__mask_count").gt(1)), "Duplicate request/dimension mask")
    reject_rows(
        masks.join(request_ids, on="request_id", how="anti"),
        "Mask refers to an unknown request",
    )
    names = [dim.dimension_name for dim in metadata.dimensions]
    known_dimension = (
        ma.col("dimension_name").is_in(names)
        if masks.schema["dimension_name"] is MountainashDtype.STRING else ma.lit(False)
    )
    reject_rows(masks.filter(~known_dimension.fill_null(False)), "Unknown masked dimension")
    selected = masks.join(
        working.select("request_id", "contract_id", "profile_id"), on="request_id",
    )
    permitted = ma.lit(False)
    for contract in metadata.context_contracts:
        for profile in contract.profiles:
            if profile.allow_dont_care:
                permitted = permitted | (
                    ma.col("contract_id").eq(contract.contract_id)
                    & ma.col("profile_id").eq(profile.profile_id)
                    & ma.col("dimension_name").is_in(list(profile.allow_dont_care))
                )
    reject_rows(selected.filter(~permitted.fill_null(False)), "Mask is not permitted by the selected profile")
    for dim in metadata.dimensions:
        name = f"__mask_{dim.dimension_name}"
        selected_mask = masks.filter(ma.col("dimension_name").eq(dim.dimension_name)).select(
            "request_id", ma.lit(True).alias(name),
        )
        working = working.join(selected_mask, on="request_id", how="left").with_columns(
            ma.col(name).fill_null(False).alias(name),
        )
    return working


def project_physical_fields(
    contexts: Relation,
    working: Relation,
    metadata: DimensionsMetadata,
    declarations: t.Mapping[str, t.Any],
    *,
    contract: t.Any = None,
    profile: t.Any = None,
):
    """Preserve original facts separately from the profile's physical observations."""
    from datetime import datetime, timezone

    from mountainash_rules.core.compiler import scalar_operand
    from mountainash_rules.core.constants import DataType, DimensionRole
    from mountainash_rules.core.context import context_absence_expression
    from mountainash_rules.core.numeric import exact_literal
    from mountainash_rules.engines.accumulate.domain import FieldRestriction, ValueConstraint

    native_schema = (
        contexts.filter(ma.lit(False)).to_polars().schema
        if any(field.data_type is DataType.DATETIME for field in declarations.values()) else {}
    )

    def temporal_payload(field, declaration, payload, present):
        if declaration.data_type is not DataType.DATETIME:
            return payload, ma.lit(False)
        expected_aware = declaration.timezone == "utc"
        actual_zone = getattr(native_schema.get(field), "time_zone", None)
        if contexts.schema.get(field) is not MountainashDtype.TIMESTAMP or (
            (actual_zone is not None) != expected_aware
        ):
            # This payload is inactive. Keep its native timezone compatible with
            # domain expressions without interpreting a missing value as epoch.
            unused = datetime(1970, 1, 1, tzinfo=timezone.utc if expected_aware else None)
            return exact_literal(unused), present
        if expected_aware and actual_zone != "UTC":
            payload = payload.dt.to_timezone("UTC")
        return payload, ma.lit(False)

    original, effective = {}, {}
    columns = []
    additional = []
    invalid = ma.col("__invalid_value")
    required = ma.lit(True)
    forbidden_marker = ma.lit(False)
    for index, (field, declaration) in enumerate(declarations.items()):
        aliases = [dim for dim in metadata.dimensions if dim.resolved_context_field == field]
        value_name = f"__physical_value_{index}"
        known_name = f"__physical_known_{index}"
        effective_name = f"__physical_effective_{index}"
        if aliases:
            values = [ma.col(f"__ctx_value_{dim.dimension_name}") for dim in aliases]
            value = values[0] if len(values) == 1 else ma.coalesce(*values)
            known = ma.lit(False)
            observed = ma.lit(False)
            for dim in aliases:
                present = ma.col(f"__ctx_present_{dim.dimension_name}")
                known = known | present
                selected = profile is None or dim.role is DimensionRole.CONTEXT_KEY or dim.dimension_name in profile.dimensions
                if selected:
                    observed = observed | (present & ~ma.col(f"__mask_{dim.dimension_name}"))
                    if profile is not None and dim.dimension_name not in profile.allow_dont_care:
                        forbidden_marker = forbidden_marker | ma.col(f"__ctx_origin_{dim.dimension_name}").eq("dont_care")
                if contract is not None and dim.role is DimensionRole.CONTEXT_KEY:
                    required = required & present
            value, temporal_invalid = temporal_payload(field, declaration, value, known)
            invalid = invalid | temporal_invalid
            known = known & ~temporal_invalid
            observed = observed & ~temporal_invalid
            columns.append(value.alias(value_name))
        else:
            supplied = field in contexts.columns
            raw = ma.col(field) if supplied else ma.lit(None)
            dtype = contexts.schema.get(field)
            absent = context_absence_expression(raw, dtype, declaration.data_type)
            payload, bad = scalar_operand(declaration, raw, dtype, ~ma.lit(supplied) | absent)
            payload, temporal_invalid = temporal_payload(
                field, declaration, payload, ma.lit(supplied) & ~absent & ~bad,
            )
            bad = bad | temporal_invalid
            additional.extend([
                payload.alias(value_name),
                (ma.lit(supplied) & ~absent & ~bad).alias(known_name),
                bad.alias(f"__physical_invalid_{index}"),
            ])
            known = ma.col(known_name)
            observed = known if profile is None else ma.lit(False)
            invalid = invalid | ma.col(f"__physical_invalid_{index}")
        columns.extend([known.alias(known_name), observed.alias(effective_name)])
        if contract is not None and any(item.name == field and item.required for item in contract.fields):
            required = required & known
        options = dict(
            data_type=declaration.data_type, numeric_dtype=declaration.numeric_dtype,
            rounding=declaration.rounding, timezone=declaration.timezone,
        )
        original[field] = FieldRestriction(**options, values=(
            ValueConstraint(ma.col(value_name), False, True, ~ma.col(known_name)),
        ))
        effective[field] = FieldRestriction(**options, values=(
            ValueConstraint(ma.col(value_name), False, True, ~ma.col(effective_name)),
        ))
    if additional:
        working = working.join(contexts.select("request_id", *additional), on="request_id")
    if columns:
        working = working.with_columns(*columns)
    working = working.with_columns(
        ma.when(ma.col("__admission_reason").is_not_null()).then(ma.col("__admission_reason"))
        .when(invalid.fill_null(True)).then("invalid_value")
        .when(~required.fill_null(False)).then("missing_required")
        .when(forbidden_marker.fill_null(False)).then("dont_care_not_permitted")
        .otherwise(ma.lit(None, dtype=MountainashDtype.STRING)).alias("__admission_reason"),
    )
    return working, original, effective
