"""Lazy stored retrieval and a single typed result materialisation boundary."""
from __future__ import annotations

import typing as t
from dataclasses import dataclass

import mountainash.expressions as ma
from mountainash import MountainashDtype
from mountainash.relations import Relation, concat, relation

from mountainash_rules.engines.accumulate.admission import (
    admit_partition, bind_request_controls, project_contexts,
    project_physical_fields, select_scopes,
)
from mountainash_rules.engines.accumulate.domain import feasible_assignments_expression
from mountainash_rules.engines.accumulate.errors import RequestValidationError
from mountainash_rules.engines.accumulate.result import QueryResult

if t.TYPE_CHECKING:
    from mountainash_rules.engines.accumulate.lattice import Lattice


Schema = t.Mapping[str, t.Any]
View = tuple[Relation, Schema]


def _raw_kind(value: t.Any) -> tuple:
    """Group transport branches without coercing unlike raw values together."""
    if isinstance(value, (list, tuple)):
        return type(value), tuple(sorted({_raw_kind(item) for item in value}, key=repr))
    return type(value), str(getattr(value, "tzinfo", ""))


def _records(records: t.Sequence[t.Mapping[str, t.Any]], metadata):
    if isinstance(records, (str, bytes)) or not isinstance(records, t.Sequence):
        raise RequestValidationError("contexts must be a Relation or sequence of request mappings")
    occupied = {dim.resolved_context_field for dim in metadata.dimensions}
    occupied.add(metadata.partition_field)
    for domain in (*metadata.context_domains, *((metadata.domain,) if metadata.domain else ())):
        occupied.update(field.name for field in domain.fields)
    for contract in metadata.context_contracts:
        occupied.update(field.name for field in contract.fields)
    for record in records:
        if not isinstance(record, t.Mapping) or not isinstance(record.get("context"), t.Mapping):
            raise RequestValidationError("each request requires a context mapping")
        occupied.update(record["context"])
    id_column, order_column = "__record_id", "__record_order"
    while id_column in occupied:
        id_column = "_" + id_column
    while order_column in occupied:
        order_column = "_" + order_column
    groups: dict[tuple, list[dict]] = {}
    controls, masks, identifiers = [], [], []
    for index, record in enumerate(records):
        identifier = record.get("request_id")
        if type(identifier) not in (str, int):
            raise RequestValidationError("request_id must be an integer or string")
        identifiers.append(identifier)
        context = dict(record["context"])
        key = tuple((name, _raw_kind(value)) for name, value in sorted(context.items()))
        groups.setdefault(key, []).append({**context, id_column: identifier, order_column: index})
        if "contract_id" in record or "profile_id" in record:
            if not all(name in record for name in ("contract_id", "profile_id")):
                raise RequestValidationError("contract_id and profile_id must be supplied together")
            controls.append({name: record[name] for name in ("request_id", "contract_id", "profile_id")})
        dimensions = record.get("masks", ())
        if not isinstance(dimensions, (list, tuple)) or any(not isinstance(name, str) for name in dimensions):
            raise RequestValidationError("masks must be a sequence of dimension names")
        masks.extend({"request_id": identifier, "dimension_name": name} for name in dimensions)
    if len({type(identifier) for identifier in identifiers}) > 1:
        raise RequestValidationError("request_id must have one type per batch")
    if len(set(identifiers)) != len(identifiers):
        raise RequestValidationError("request_id must be unique")
    branches = [relation(rows) for rows in groups.values()]
    if not branches:
        branches = [relation({id_column: [""], order_column: [0]}).filter(ma.lit(False))]
    return (branches, relation(controls) if controls else None,
            relation(masks) if masks else None, id_column, order_column)


def _combine(left: Relation | None, right: Relation | None, columns: tuple[str, ...],
             errors: list[tuple[Relation, ValueError]]) -> tuple[Relation | None, tuple[Schema, ...] | None]:
    if left is None:
        return right, None
    if right is None:
        return left, None
    projected, schemas = [], []
    # Explicit native channels retain their binding; inline records are local.
    for rows in (right, left):
        if not isinstance(rows, Relation):
            raise RequestValidationError("request channel must be a Mountainash Relation")
        schema = rows.collect_schema(allow_execution=False)
        if not set(columns).issubset(schema):
            raise RequestValidationError(f"request channel requires {', '.join(columns)}")
        invalid = {name for name in columns[1:] if schema[name] is not MountainashDtype.STRING}
        if invalid:
            errors.append((rows, RequestValidationError("Invalid request control or mask field type")))
        # Invalid originals still fail atomically. Inert typed payloads only
        # prevent incompatible comparisons/unions; empty channels remain empty.
        projected.append(rows.select(*[
            ma.lit(None, dtype=MountainashDtype.STRING).alias(name) if name in invalid else ma.col(name)
            for name in columns
        ]))
        schemas.append({name: MountainashDtype.STRING if name in invalid else schema[name] for name in columns})
    return concat(projected), tuple(schemas)


@dataclass(frozen=True)
class _Terminal:
    rows: Relation
    columns: t.Mapping[str, t.Mapping[str, str]]
    errors: t.Mapping[str, ValueError]

    def fetch(self) -> dict[str, Relation]:
        local = relation(self.rows.to_polars())
        if self.errors:
            failures = local.filter(ma.col("__view").is_in(list(self.errors))).select("__view").to_dicts()
            if failures:
                error = self.errors[failures[0]["__view"]]
                raise type(error)(*error.args)
        return {
            name: local.filter(ma.col("__view").eq(name)).select(*[
                ma.col(slot).alias(column) for column, slot in mapping.items()
            ]) for name, mapping in self.columns.items()
        }


def _terminal(views: t.Mapping[str, View], errors: t.Sequence[tuple[Relation, ValueError]]) -> _Terminal:
    """Pad each view from declared types, without inspecting joined schemas."""
    tables = dict(views)
    messages = {}
    for index, (rows, error) in enumerate(errors):
        name = f"error:{index}"
        tables[name] = (rows.limit(1).select(ma.col(rows.columns[0]).is_null().alias("failed")),
                        {"failed": MountainashDtype.BOOL})
        messages[name] = error
    slots, types = {}, {}
    for index, (name, (_, schema)) in enumerate(tables.items()):
        slots[name] = {column: f"v{index}_{position}" for position, column in enumerate(schema)}
        types.update({slots[name][column]: dtype for column, dtype in schema.items()})
    projections = []
    for name, (rows, _) in tables.items():
        own = {slot: column for column, slot in slots[name].items()}
        projections.append(rows.select(ma.lit(name).alias("__view"), *[
            (ma.col(own[slot]) if slot in own else ma.lit(None, dtype=dtype)).alias(slot)
            for slot, dtype in types.items()
        ]))
    return _Terminal(concat(projections), {name: slots[name] for name in views}, messages)


@dataclass(frozen=True)
class QueryOperation:
    """Retain lattice and lazy inputs until an explicit, uncached fetch."""
    _terminal: _Terminal
    _lattice: Lattice
    _scopes: tuple[str, ...]

    def fetch(self) -> QueryResult:
        views = self._terminal.fetch()
        evidence = {name: views.get(name) for name in (
            "contributors", "cell_identity", "cell_identity_boxes", "cell_identity_contributors",
        )}
        for name in ("cell_identity_ordered_bounds", "cell_identity_category_sets", "cell_identity_category_values"):
            evidence[name] = {key[len(name) + 1:]: value for key, value in views.items()
                              if key.startswith(name + ":")} or None
        return QueryResult(
            requests=views["requests"].sort("request_order"), scopes=self._scopes,
            matches={scope: views[f"matches:{scope}"] for scope in self._scopes},
            payload_columns={scope: self._lattice.output_columns[scope] for scope in self._scopes},
            _metadata_json=self._lattice._metadata_json, version_id=self._lattice.version_id,
            **evidence,
        )


def _stored_schema(lattice: Lattice, role: str, *, scope=None, field=None) -> dict:
    for descriptor in lattice.table_descriptors:
        if (descriptor.role, descriptor.scope, descriptor.field) == (role, scope, field):
            return dict(descriptor.schema)
    raise KeyError((role, scope, field))


def _identity_views(lattice: Lattice, refs: Relation, ref_schema: Schema) -> dict[str, View]:
    request_keys = {name: ref_schema[name] for name in ("request_id", "partition", "scope")}
    cell_key = ma.col("cell_id").cast(MountainashDtype.I64).alias("cell_key")
    keys = ["partition", "scope", "cell_id", "contributor_set_id"]
    views = {"cell_identity": (
        refs.join(lattice.cells, on=keys, execute_on="right").select(
            "request_id", "partition", "scope", cell_key, "identity_version",
        ), {**request_keys, "cell_key": MountainashDtype.I64,
            "identity_version": _stored_schema(lattice, "cells")["identity_version"]},
    )}

    def child(table, schema, contributors=False):
        join_keys = ["partition", "scope", "contributor_set_id" if contributors else "cell_id"]
        columns = [name for name in table.columns if name not in join_keys]
        return (refs.join(table, on=join_keys, execute_on="right").select(
            "request_id", "partition", "scope", cell_key, *columns,
        ), {**request_keys, "cell_key": MountainashDtype.I64,
            **{name: schema[name] for name in columns}})

    views["cell_identity_boxes"] = child(lattice.cell_boxes, _stored_schema(lattice, "cell_boxes"))
    views["cell_identity_contributors"] = child(lattice.contributors, _stored_schema(lattice, "contributors"), True)
    for role, tables in (("cell_ordered_bounds", lattice.cell_ordered_bounds),
                         ("cell_category_sets", lattice.cell_category_sets),
                         ("cell_category_values", lattice.cell_category_values)):
        for field, table in tables.items():
            views[f"cell_identity_{role.removeprefix('cell_')}:{field}"] = child(
                table, _stored_schema(lattice, role, field=field),
            )
    return views


def query(lattice: Lattice, contexts, scopes=None, *, request_control=None, masks=None,
          include_cell_key=False, include_contributors=False) -> QueryOperation:
    from mountainash_rules.engines.accumulate.resolve import _field_declarations, _point_bindings
    from mountainash_rules.engines.accumulate.serving import _matching_cells, stored_boundaries

    metadata = lattice.metadata
    selected = select_scopes(metadata, scopes)
    errors: list[tuple[Relation, ValueError]] = []
    control_schemas = mask_schemas = None
    if isinstance(contexts, Relation):
        branches, id_column, order_column = [contexts], "request_id", None
    else:
        branches, inline_controls, inline_masks, id_column, order_column = _records(contexts, metadata)
        request_control, control_schemas = _combine(
            inline_controls, request_control, ("request_id", "contract_id", "profile_id"), errors,
        )
        masks, mask_schemas = _combine(inline_masks, masks, ("request_id", "dimension_name"), errors)
    # Preserve native bindings and local records alike. Placement is explicit at
    # stored joins; there is no backend-family conversion or construction export.
    cell_schema = _stored_schema(lattice, "cells")
    projected = [project_contexts(
        raw, metadata, errors=errors, id_column=id_column, order_column=order_column,
        partition_dtype=cell_schema["partition"],
    ) for raw in branches]
    request_schema = concat([rows.select("request_id") for rows in projected]).collect_schema(
        allow_execution=False,
    )
    ref_schema = {"request_id": request_schema["request_id"], "partition": cell_schema["partition"],
                  "scope": MountainashDtype.STRING}
    refs_schema = {**ref_schema, **{name: cell_schema[name] for name in ("cell_id", "contributor_set_id")}}
    # Admit each raw key type before combining. The stored key supplies the
    # common partition type; null/wrong-type keys cannot poison valid siblings.
    projected = [admit_partition(rows, lattice.sources.identities, metadata) for rows in projected]
    admitted = concat([
        rows.select("request_id", "partition", "__admission_reason") for rows in projected
    ])
    controlled = bind_request_controls(
        admitted, metadata, request_control, masks, errors=errors,
        request_control_schemas=control_schemas, mask_schemas=mask_schemas,
    )
    controlled = controlled.select(
        "request_id", "contract_id", "profile_id", "__admission_reason",
        *[f"__mask_{dim.dimension_name}" for dim in metadata.dimensions],
    )
    profiles = [(None, None)] + [(contract, profile) for contract in metadata.context_contracts
                               for profile in contract.profiles]
    declarations = metadata.outcome_scopes or {"default": metadata.aggregates}
    requests = []
    matches: dict[str, list[Relation]] = {scope: [] for scope in selected}
    for contract, profile in profiles:
        condition = ma.col("contract_id").is_null() if contract is None else (
            ma.col("contract_id").eq(contract.contract_id) & ma.col("profile_id").eq(profile.profile_id)
        )
        domains = (metadata.domain,) if metadata.domain else ()
        if contract is not None:
            provider = next(domain for domain in (*domains, *metadata.context_domains)
                            if domain.domain_id == contract.domain_ref)
            if provider not in domains:
                domains += (provider,)
        fields = _field_declarations(lattice.sources, domains, contract.fields if contract else ())
        groups = []
        for raw, projected_branch in zip(branches, projected):
            branch = projected_branch.drop("__admission_reason").join(
                controlled, on="request_id", execute_on="right",
            )
            group, original, effective = project_physical_fields(
                raw, branch.filter(condition), metadata, fields,
                contract=contract, profile=profile, id_column=id_column,
            )
            # Normalize each raw schema before combining: original type and
            # timezone facts have already become admission flags. Do not union
            # unlike raw payloads or repeat the domain/matcher per raw schema.
            groups.append(group.select(
                "request_id", "request_order", "partition", "contract_id", "profile_id",
                "__admission_reason",
                *[f"__physical_{kind}_{index}" for index in range(len(fields))
                  for kind in ("value", "known", "effective")],
            ))
        group = concat(groups)
        valid = group.filter(ma.col("__admission_reason").is_null())
        if domains:
            feasible, _, _ = feasible_assignments_expression(
                valid, metadata, original, {},
                pair_key="request_id", domains=domains,
            )
            allowed = feasible.select("request_id").unique().with_columns(ma.lit(True).alias("__in_domain"))
            group = group.join(allowed, on="request_id", how="left", execute_on="right").with_columns(
                ma.when(ma.col("__admission_reason").is_not_null()).then(ma.col("__admission_reason"))
                .when(~ma.col("__in_domain").fill_null(False)).then("outside_domain")
                .otherwise(ma.lit(None, dtype=MountainashDtype.STRING)).alias("__admission_reason"),
            )
        requests.append(group.select(
            "request_id", ma.col("request_order").cast(MountainashDtype.I64).alias("request_order"),
            "contract_id", "profile_id",
            ma.when(ma.col("__admission_reason").is_null()).then("admitted").otherwise("invalid").alias("admission_status"),
            ma.col("__admission_reason").alias("reason_code"),
        ))
        for scope in selected:
            assignments, values, residuals = feasible_assignments_expression(
                valid, metadata, effective, {}, pair_key="request_id", domains=domains,
                boundaries=stored_boundaries(lattice, valid, scope),
            )
            if domains:
                # Original admission and effective feasibility are independent
                # expressions. Gate publication by original admission rather
                # than nesting one complete domain search inside the other.
                assignments = assignments.join(
                    group.filter(ma.col("__admission_reason").is_null()).select("request_id"),
                    on="request_id", execute_on="right",
                )
            points = assignments.with_columns(*_point_bindings(
                metadata, {field: ma.col(name) for field, name in values.items()}, residuals,
            ))
            hits = _matching_cells(lattice, points, scope, ("request_id",))
            output = lattice.outputs[scope]
            names = tuple(f"v{index:04d}" for index in range(1, len(declarations[scope]) + 1))
            if any(name not in output.columns for name in names):
                raise ValueError("selected stored output column is absent")
            keys = ["partition", "scope", "contributor_set_id"]
            errors.append((hits.join(output.select(*keys).unique(), on=keys, how="anti", execute_on="right"),
                           ValueError("stored output is missing for a matched contributor set")))
            matches[scope].append(hits.join(output.select(*keys, *names), on=keys, execute_on="right"))
    views: dict[str, View] = {}
    for scope, rows in matches.items():
        names = tuple(f"v{index:04d}" for index in range(1, len(declarations[scope]) + 1))
        schema = _stored_schema(lattice, "outputs", scope=scope)
        views[f"matches:{scope}"] = (concat(rows).unique(), {**refs_schema, **{name: schema[name] for name in names}})
    views["requests"] = (concat(requests), {
        "request_id": ref_schema["request_id"], "request_order": MountainashDtype.I64,
        **{name: MountainashDtype.STRING for name in ("contract_id", "profile_id", "admission_status", "reason_code")},
    })
    if include_contributors or include_cell_key:
        if selected:
            refs = concat([views[f"matches:{scope}"][0].select(*refs_schema) for scope in selected])
        else:
            refs = projected[0].filter(ma.lit(False)).select("request_id", "partition").join(
                lattice.cells.filter(ma.lit(False)).drop("partition"), how="cross", execute_on="right",
            ).select(*refs_schema)
        if include_contributors:
            views["contributors"] = (refs.join(lattice.contributors,
                on=["partition", "scope", "contributor_set_id"], execute_on="right",
            ).select("request_id", "partition", "scope", "cell_id", "source_uuid").unique(), {
                **{name: refs_schema[name] for name in ("request_id", "partition", "scope", "cell_id")},
                "source_uuid": _stored_schema(lattice, "contributors")["source_uuid"],
            })
        if include_cell_key:
            views.update(_identity_views(lattice, refs, refs_schema))
    return QueryOperation(_terminal(views, errors), lattice, selected)
