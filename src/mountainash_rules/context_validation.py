"""Context validation: a caller-run capability, separate from table construction and matching.

Contexts reach :meth:`DecisionTable.match` as the engine's input; ``match`` evaluates what it is
given and performs no validation.  Callers who want their contexts checked run this capability
first.  It has three levels, all aligned to the dimension metadata:

1. **The caller's own pydantic model** validates each instance: types, required fields and
   business constraints.  It knows nothing about rules, and this library supplies neither a
   generated model nor a base class.
2. :func:`check_context_schema` compares a pydantic class's annotations, a Relation's schema
   (read with ``collect_schema(allow_execution=False)``) or a schema mapping with the declared
   context fields, without reading any data.  It raises one ``ValueError`` listing every
   mismatch: field type, DATETIME unit and timezone, representability of the wildcard marker
   and of the truthy values, and a missing ``request_id``.  Two conditions only warn, through
   :func:`warnings.warn`: a declared field absent from the schema (that dimension is unknown at
   evaluation) and a DECIMAL scale finer than declared (the engine reduces the value at context
   binding using the declared ``rounding``).
3. :func:`validate_contexts` checks a batch of values relationally: numbers are finite, truthy
   values are mapped, reserved markers are used correctly, list values have no null elements,
   and ``request_id`` is non-null and unique.  It raises one ``ValueError`` listing every
   invalid value and otherwise returns ``contexts`` exactly as passed.  A context null is
   unknown and is not rejected.

Context protocol
----------------
A context is identified by ``request_id`` and carries one attribute (or column) named after each
context field it supplies; there is no base class.  ``match(contexts, scopes=None)`` takes either

* a list of protocol objects (pydantic models): ``request_id`` comes from the attribute of that
  name, and a field the object leaves unset (absent from ``model_fields_set``) is missing and
  therefore unknown, which differs from an explicit ``None``; or
* a conforming Relation: a ``request_id`` column plus a column per supplied field.  A column
  that is absent is not supplied (unknown); a null cell is an explicit null.

Lists are converted with :func:`~mountainash_rules.decision_table.apply.contexts_to_relation`, the
converter ``match`` uses, so validation sees what matching will see.  Null semantics follow the
explicit don't-care principle: on a value dimension a context null is unknown unless the field's
truthy mapping maps it, on a PRESENCE dimension an explicit null is the known fact "is null",
and null elements inside list values are invalid.

``unmatched_context_ids`` stays a post-match method of ``BatchRuleResult``; DECIMAL reduction and
DATETIME truncation happen at engine context binding, not here.
"""

from __future__ import annotations

import datetime as dt
import types
import typing as t
import warnings
from dataclasses import dataclass
from decimal import Decimal
from functools import reduce

import mountainash.expressions as ma
from mountainash import DecimalDtype, MountainashDtype
from mountainash.core.dtypes import ListDtype, TimestampDtype
from mountainash.relations import Relation
from pydantic import AwareDatetime, BaseModel, NaiveDatetime, TypeAdapter

from mountainash_rules.core.dimension import ContextField, DimensionsMetadata
from mountainash_rules.core.numeric import exact_literal
from mountainash_rules.core.type_families import type_family
from mountainash_rules.decision_table.apply import REQUEST_ID, contexts_to_relation

__all__: t.Sequence[str] = ("check_context_schema", "validate_contexts")

_INT_RANGES = {
    MountainashDtype.I8: (-(1 << 7), (1 << 7) - 1),
    MountainashDtype.I16: (-(1 << 15), (1 << 15) - 1),
    MountainashDtype.I32: (-(1 << 31), (1 << 31) - 1),
    MountainashDtype.I64: (-(1 << 63), (1 << 63) - 1),
    MountainashDtype.U8: (0, (1 << 8) - 1),
    MountainashDtype.U16: (0, (1 << 16) - 1),
    MountainashDtype.U32: (0, (1 << 32) - 1),
    MountainashDtype.U64: (0, (1 << 64) - 1),
}
_FLOATS = frozenset({MountainashDtype.FP32, MountainashDtype.FP64})
_TIMESTAMP_UNITS = {0: "s", 3: "ms", 6: "us"}
_UTC_NAMES = frozenset({"UTC", "Z", "+00:00", "ETC/UTC"})


# --------------------------------------------------------------------------------------
# Level 2: schema alignment
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Shape:
    """What a schema says about one context field, whichever source it came from."""

    family: str  # str int float decimal bool date datetime | null any other
    detail: str
    is_list: bool = False
    nullable: bool | None = None  # None: the schema does not say
    int_range: tuple[int, int] | None = None
    precision: int | None = None
    scale: int | None = None
    unit: str | None = None
    timezone: str | None = None  # naive | utc | other | aware (offset unknown)
    adapter: TypeAdapter | None = None  # validates one element/value of a pydantic annotation


def _timezone_kind(aware: bool, name: t.Any) -> str:
    if not aware:
        return "naive"
    if name is None:
        return "aware"  # the backend reports no offset
    return "utc" if str(name).upper() in _UTC_NAMES else "other"


def _shape_from_dtype(dtype: t.Any) -> _Shape:
    is_list = isinstance(dtype, ListDtype)
    if is_list:
        dtype = dtype.element
    if isinstance(dtype, DecimalDtype):
        return _Shape("decimal", repr(dtype), is_list, precision=dtype.precision, scale=dtype.scale)
    if isinstance(dtype, TimestampDtype):
        return _Shape(
            "datetime",
            repr(dtype),
            is_list,
            unit=_TIMESTAMP_UNITS.get(dtype.precision, f"precision {dtype.precision}"),
            timezone=_timezone_kind(dtype.timezone_aware, dtype.timezone),
        )
    if dtype in _INT_RANGES:
        return _Shape("int", str(dtype.value), is_list, int_range=_INT_RANGES[dtype])
    simple = {
        MountainashDtype.STRING: "str",
        MountainashDtype.BOOL: "bool",
        MountainashDtype.DATE: "date",
    }
    if dtype in simple:
        return _Shape(simple[dtype], str(dtype.value), is_list)
    if dtype in _FLOATS:
        return _Shape("float", str(dtype.value), is_list)
    if "null" in str(getattr(dtype, "value", dtype)).lower():
        return _Shape("null", "null", is_list)
    return _Shape("other", repr(dtype), is_list)


def _literal_family(values: tuple[t.Any, ...]) -> str:
    kinds = {
        "bool" if isinstance(v, bool) else "int" if isinstance(v, int) else "str" if isinstance(v, str) else "other"
        for v in values
    }
    return kinds.pop() if len(kinds) == 1 else "other"


def _shape_from_annotation(annotation: t.Any, metadata: t.Sequence[t.Any] = ()) -> _Shape:
    """Describe a pydantic field annotation; ``metadata`` carries its ``Field`` constraints."""
    detail = getattr(annotation, "__name__", None) or str(annotation)
    if annotation is t.Any:
        return _Shape("any", "Any", nullable=True)
    origin = t.get_origin(annotation)
    if origin is t.Annotated:
        inner, *extra = t.get_args(annotation)
        return _shape_from_annotation(inner, [*metadata, *extra])
    if origin in (t.Union, types.UnionType):
        arguments = t.get_args(annotation)
        members = [a for a in arguments if a is not type(None)]
        nullable = len(members) != len(arguments)
        if len(members) == 1:
            inner = _shape_from_annotation(members[0], metadata)
            return _Shape(**{**inner.__dict__, "nullable": nullable or inner.nullable})
        return _Shape("other", str(annotation), nullable=nullable)
    if origin in (list, set, frozenset, tuple, t.Sequence, t.Iterable) or annotation in (list, set, frozenset):
        arguments = t.get_args(annotation)
        element = _shape_from_annotation(arguments[0]) if arguments else _Shape("any", "Any", nullable=True)
        return _Shape(**{**element.__dict__, "is_list": True, "nullable": False, "detail": f"list[{element.detail}]"})
    adapter = TypeAdapter(t.Annotated[(annotation, *metadata)] if metadata else annotation)
    if origin is t.Literal:
        return _Shape(_literal_family(t.get_args(annotation)), str(annotation), nullable=False, adapter=adapter)
    common = {"nullable": False, "adapter": adapter}
    if annotation is AwareDatetime:
        return _Shape("datetime", detail, timezone="aware", **common)
    if annotation is NaiveDatetime:
        return _Shape("datetime", detail, timezone="naive", **common)
    if not isinstance(annotation, type):
        return _Shape("other", str(annotation), **common)
    if issubclass(annotation, bool):
        return _Shape("bool", detail, **common)
    if issubclass(annotation, int):
        return _Shape("int", detail, **common)
    if issubclass(annotation, float):
        return _Shape("float", detail, **common)
    if issubclass(annotation, str):
        return _Shape("str", detail, **common)
    if issubclass(annotation, Decimal):
        places = next((p.decimal_places for p in metadata if getattr(p, "decimal_places", None) is not None), None)
        return _Shape("decimal", detail, scale=places, **common)
    if issubclass(annotation, dt.datetime):
        return _Shape("datetime", detail, **common)
    if issubclass(annotation, dt.date):
        return _Shape("date", detail, **common)
    return _Shape("other", detail, **common)


def _shapes(schema_or_class: t.Any) -> dict[str, _Shape]:
    """Every named column/field of the schema, read without touching data."""
    if isinstance(schema_or_class, type) and issubclass(schema_or_class, BaseModel):
        return {
            name: _shape_from_annotation(info.annotation, info.metadata)
            for name, info in schema_or_class.model_fields.items()
        }
    if isinstance(schema_or_class, Relation):
        schema_or_class = schema_or_class.collect_schema(allow_execution=False)
    if isinstance(schema_or_class, t.Mapping):
        dtypes = (MountainashDtype, DecimalDtype, TimestampDtype, ListDtype)
        return {
            name: _shape_from_dtype(entry) if isinstance(entry, dtypes) else _shape_from_annotation(entry)
            for name, entry in schema_or_class.items()
        }
    raise TypeError(
        "schema_or_class must be a pydantic model class, a Relation or a schema mapping, "
        f"not {type(schema_or_class).__name__}"
    )


def _value_problem(value: t.Any, field: ContextField, shape: _Shape) -> str | None:
    """Why ``value`` cannot be carried by the field in this schema, or None when it can."""
    if value is None:
        return "null is not allowed by the schema" if shape.nullable is False else None
    family = type_family(field.data_type)
    if not family.accepts_context_value(value):
        return f"{value!r} is not a {field.data_type.value} value"
    if shape.adapter is not None:
        try:
            shape.adapter.validate_python(value, strict=True)
        except ValueError:
            return f"{value!r} is rejected by the schema annotation"
    return family.representability_problem(value, field, shape)


def _check_field(field: ContextField, shape: _Shape, errors: list[str], notices: list[str]) -> None:
    name = field.name
    family = type_family(field.data_type)
    if shape.family not in family.accepted_context_families | {"null", "any"}:
        errors.append(
            f"field {name!r}: schema type {shape.detail} does not match declared {field.data_type.value}"
        )
        return
    schema_errors, schema_notices = family.context_schema_issues(field, shape)
    errors.extend(schema_errors)
    notices.extend(schema_notices)
    if field.wildcard_declared:
        problem = _value_problem(field.wildcard, field, shape)
        if problem:
            errors.append(f"field {name!r}: wildcard marker is not representable: {problem}")
    for mapped in field.truthy:
        problem = _value_problem(mapped, field, shape)
        if problem:
            errors.append(f"field {name!r}: truthy value is not representable: {problem}")


def check_context_schema(
    schema_or_class: type[BaseModel] | Relation | t.Mapping[str, t.Any],
    metadata: DimensionsMetadata,
) -> None:
    """Check that a context schema aligns with the metadata's declared context fields.

    ``schema_or_class`` is a pydantic model class (annotations are read), a Relation (read with
    ``collect_schema(allow_execution=False)``; no row is read and nothing executes) or a mapping
    of column name to a Mountainash dtype or a Python annotation.  Raises one ``ValueError``
    listing every mismatch.  A declared field absent from the schema, and a DECIMAL scale finer
    than declared, only warn (:func:`warnings.warn`).
    """
    shapes = _shapes(schema_or_class)
    errors: list[str] = []
    notices: list[str] = []
    if REQUEST_ID not in shapes:
        errors.append(f"{REQUEST_ID!r} is missing from the context schema")
    for field in metadata.fields:
        shape = shapes.get(field.name)
        if shape is None:
            notices.append(
                f"declared context field {field.name!r} is absent from the context schema; "
                "its dimension is unknown at evaluation"
            )
            continue
        _check_field(field, shape, errors, notices)
    for notice in notices:
        warnings.warn(notice, UserWarning, stacklevel=2)
    if errors:
        raise ValueError("context schema does not align with the metadata:\n- " + "\n- ".join(errors))


# --------------------------------------------------------------------------------------
# Level 3: relational value alignment
# --------------------------------------------------------------------------------------


def _name_ids(ids: list[t.Any]) -> str:
    return ", ".join(repr(i) for i in ids)


def _identity_errors(rel: Relation) -> list[str]:
    totals = rel.group_by().agg(
        ma.count_records().alias("__total"), ma.col(REQUEST_ID).count().alias("__non_null")
    ).to_dicts()[0]
    errors = []
    nulls = totals["__total"] - totals["__non_null"]
    if nulls:
        errors.append(f"{REQUEST_ID!r} must be non-null; {nulls} null value(s) among {totals['__total']} row(s)")
    duplicated = (
        rel.filter(~ma.col(REQUEST_ID).is_null())
        .group_by(REQUEST_ID)
        .agg(ma.count_records().alias("__n"))
        .filter(ma.col("__n").gt(1))
        .to_dicts()
    )
    if duplicated:
        ids = sorted((row[REQUEST_ID] for row in duplicated), key=repr)
        errors.append(f"{REQUEST_ID!r} must be unique; duplicated: {_name_ids(ids)}")
    return errors


def _value_checks(field: ContextField, shape: _Shape) -> dict[str, t.Any]:
    """``{reason: row predicate}`` for the checks that apply to one supplied column."""
    column = ma.col(field.name)
    checks: dict[str, t.Any] = {}
    typed = shape.family in type_family(field.data_type).accepted_context_families
    marker = field.wildcard if field.wildcard_declared and field.wildcard is not None else None
    if shape.is_list:
        ordered = column.list.sort()
        checks["has a null element in a list value"] = (
            column.list.len().gt(0) & (ordered.list.first().is_null() | ordered.list.last().is_null())
        )
        if marker is not None and typed:
            checks["embeds the reserved wildcard marker among other list elements"] = (
                column.list.contains(exact_literal(marker)).fill_null(False) & column.list.len().gt(1)
            )
        return checks
    if shape.family == "float":
        checks["is not finite"] = ~column.is_null() & ~column.is_finite()
    if field.truthy and typed:
        reserved = [marker] if marker is not None else []
        mapped = reduce(
            lambda left, right: left | right,
            (column.eq(exact_literal(v)).fill_null(False) for v in [*(k for k in field.truthy if k is not None), *reserved]),
            ma.lit(False),
        )
        checks["is not mapped by the field's truthy values"] = ~column.is_null() & ~mapped
        if marker is not None and marker in field.truthy:
            checks["is the reserved wildcard marker and also a mapped truthy value"] = column.eq(
                exact_literal(marker)
            ).fill_null(False)
        if field.wildcard_declared and field.wildcard is None and None in field.truthy:
            checks["is null, the declared wildcard, and also a mapped truthy value"] = column.is_null()
    return checks


def validate_contexts(
    contexts: t.Sequence[t.Any] | Relation, metadata: DimensionsMetadata
) -> t.Sequence[t.Any] | Relation:
    """Validate a batch of contexts relationally and return it as passed.

    ``contexts`` is a Relation or a list of protocol objects (converted with
    :func:`contexts_to_relation`, so an unset field stays distinct from an explicit ``None``).
    Raises one ``ValueError`` listing: a null or duplicated ``request_id``; non-finite numbers;
    values no truthy mapping covers; a reserved wildcard marker used as an ordinary value; and
    null elements inside list values.  A context null is unknown and is not rejected.
    """
    if isinstance(contexts, Relation):
        rel = contexts
    elif isinstance(contexts, (list, tuple)):
        absent = [i for i, obj in enumerate(contexts) if not hasattr(obj, REQUEST_ID)]
        if absent:
            raise ValueError(f"context object(s) at position(s) {absent} have no {REQUEST_ID!r}")
        rel = contexts_to_relation(contexts, [field.name for field in metadata.fields])
    else:
        raise ValueError("contexts must be a Relation or a list of context objects")
    if REQUEST_ID not in rel.columns:
        raise ValueError(f"contexts have no {REQUEST_ID!r} column")
    errors = _identity_errors(rel)

    schema = rel.collect_schema(allow_execution=False)
    findings: list[tuple[ContextField, str]] = []
    flags: dict[str, t.Any] = {}
    for field in metadata.fields:
        if field.name not in schema:
            continue  # not supplied: unknown, nothing to check
        for reason, predicate in _value_checks(field, _shape_from_dtype(schema[field.name])).items():
            flag = f"__invalid_{len(findings)}"
            findings.append((field, reason))
            flags[flag] = predicate.fill_null(False)
    if flags:
        any_invalid = reduce(lambda left, right: left | right, (ma.col(flag) for flag in flags))
        rows = (
            rel.with_columns(*(expr.alias(flag) for flag, expr in flags.items()))
            .filter(any_invalid)
            .select(REQUEST_ID, *flags)
            .to_dicts()
        )
        for flag, (field, reason) in zip(flags, findings):
            ids = [row[REQUEST_ID] for row in rows if row[flag]]
            if ids:
                errors.append(f"field {field.name!r}: {len(ids)} value(s) {reason}; request_id {_name_ids(ids)}")
    if errors:
        raise ValueError("contexts are invalid:\n- " + "\n- ".join(errors))
    return contexts
