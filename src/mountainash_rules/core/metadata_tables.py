"""Canonical flat relational serialization for ruleset metadata."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from mountainash.relations import relation

from mountainash_rules.core.constants import DataType, MatchStrategy


def _text(value, data_type):
    if value is None:
        return None
    if data_type is DataType.DATE:
        return value.isoformat()
    if data_type is DataType.DATETIME:
        return value.isoformat()
    if data_type is DataType.DECIMAL:
        return format(value, "f")
    if data_type is DataType.BOOL:
        return "true" if value else "false"
    return str(value)


def _parse(value, data_type):
    if value is None:
        return None
    if data_type is DataType.DATE:
        return dt.date.fromisoformat(value)
    if data_type is DataType.DATETIME:
        return dt.datetime.fromisoformat(value)
    if data_type is DataType.DECIMAL:
        return Decimal(value)
    if data_type is DataType.BOOL:
        if value not in ("true", "false"):
            raise ValueError(f"Invalid canonical BOOL marker {value!r}")
        return value == "true"
    if data_type is DataType.INT:
        return int(value)
    if data_type is DataType.FLOAT:
        return float(value)
    return value


def _rows(table):
    return table.to_dicts() if hasattr(table, "to_dicts") else table.to_dict(orient="records")


def metadata_to_tables(metadata, relation_factory=relation):
    fields = [{"name": f.name, "data_type": f.data_type.value, "precision": f.precision, "scale": f.scale, "rounding": f.rounding, "datetime_unit": f.datetime_unit, "timezone": f.timezone, "wildcard": _text(f.wildcard, f.data_type), "wildcard_declared": f.wildcard_declared} for f in metadata.fields]
    dimensions = [{"dimension_name": d.dimension_name, "context_field": d.context_field, "rule_field": d.rule_field, "match_strategy": d.match_strategy.value, "range_min_field": d.range_min_field, "range_max_field": d.range_max_field, "regex_pattern": d.regex_pattern} for d in metadata.dimensions]
    outputs = [{"scope": o.scope, "output_name": o.output_name, "contribution_column": o.contribution_column, "operation": o.operation.value, "data_type": o.data_type.value, "precision": o.precision, "scale": o.scale, "rounding": o.rounding, "timezone": o.timezone} for o in metadata.outputs]
    truthy = [{"context_field": f.name, "value": _text(value, f.data_type), "outcome": outcome} for f in metadata.fields for value, outcome in f.truthy.items()]
    return {"ruleset": relation_factory([{"uuid_column": metadata.uuid_column, "scope_column": metadata.scope_column}]), "fields": relation_factory(fields), "dimensions": relation_factory(dimensions), "outputs": relation_factory(outputs), "truthy": relation_factory(truthy)}


def metadata_from_tables(tables, metadata_type):
    from mountainash_rules.core.aggregate import Aggregate
    from mountainash_rules.core.dimension import ContextField, Dimension

    ruleset, fields_rows = _rows(tables["ruleset"]), _rows(tables["fields"])
    truthy_rows = _rows(tables["truthy"])
    by_field = {r["name"]: r for r in fields_rows}
    truthy = {}
    for row in truthy_rows:
        dtype = DataType(by_field[row["context_field"]]["data_type"])
        truthy.setdefault(row["context_field"], {})[_parse(row["value"], dtype)] = row["outcome"]
    fields = []
    for row in fields_rows:
        dtype = DataType(row["data_type"])
        fields.append(ContextField(name=row["name"], data_type=dtype, precision=row["precision"], scale=row["scale"], rounding=row["rounding"], datetime_unit=row["datetime_unit"], timezone=row["timezone"], wildcard=_parse(row["wildcard"], dtype), wildcard_declared=row["wildcard_declared"], truthy=truthy.get(row["name"], {})))
    dimensions = [Dimension(dimension_name=r["dimension_name"], context_field=r["context_field"], rule_field=r["rule_field"], match_strategy=MatchStrategy(r["match_strategy"]), range_min_field=r["range_min_field"], range_max_field=r["range_max_field"], regex_pattern=r["regex_pattern"]) for r in _rows(tables["dimensions"])]
    outputs = [Aggregate(**{**r, "data_type": DataType(r["data_type"])}) for r in _rows(tables["outputs"])]
    ruleset_row = ruleset[0]
    return metadata_type(fields=tuple(fields), dimensions=tuple(dimensions), outputs=tuple(outputs), uuid_column=ruleset_row["uuid_column"], scope_column=ruleset_row["scope_column"])
