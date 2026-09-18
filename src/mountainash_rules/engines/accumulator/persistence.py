"""Semantic native restoration and immutable binding views; transport lives in snapshot_io."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
import typing as t

from mountainash.relations import relation

from mountainash_rules.core.codec import (
    _admit_validation_bundle,
    _bounded_json_size,
    _bundle_payload,
    _make_exact_envelope,
    _materialize_json,
    _source_id,
    decode_json,
    validate_id,
)
from mountainash_rules.core.constants import DataType, DimensionRole, MatchStrategy
from mountainash_rules.core.contracts import (
    ContextContract,
    ContractBinding,
    DomainDefinition,
    SemanticVersions,
    ValidatedBuildInput,
    _label,
)
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.core.predicates import PredicateGraph
from mountainash_rules.core.scalar import decode_scalar, encode_scalar, normalize_scalar
from mountainash_rules.core.validation import (
    _scope_covers,
    validate_build_permission,
    validate_compiled_report_semantics,
    validate_contract_binding,
    validate_source_report_semantics,
)
from mountainash_rules.engines.accumulator.aggregate import Aggregate
from mountainash_rules.engines.accumulator.analysis import (
    BuildPreparation,
    _evidence_domains,
    _evidence_regex_options,
    _prove_compiled,
    _prove_profiles,
)
from mountainash_rules.engines.accumulator.compiler import (
    AnalyzedCell,
    ExactAnalysis,
    PreparedSources,
    SourceRecord,
    _artifact_payload,
    _canonical_id,
    _routing_match,
    _source_columns,
    prepare_analysis_input,
    analysis_geometry,
)
from mountainash_rules.engines.accumulator.layout import (
    ExactLayout,
    ScopeKeyRow,
    ScopeRow,
    SourceMapRow,
    VectorRow,
    WordRow,
    _freeze,
    validate_layout,
)
from mountainash_rules.engines.accumulator.runtime import EvaluationSession
from mountainash_rules.engines.accumulator.state import materialize_state
from mountainash_rules.engines.accumulator.tables import (
    native_storage_type,
    source_rows,
    storage_type,
)

_MANIFEST_FIELDS = {
    "artifact_kind",
    "native_schema_version",
    "semantic_versions",
    "ruleset_id",
    "source_bundle_id",
    "compilation_domain_id",
    "partition",
    "artifact_id",
    "dimensions",
    "aggregates",
    "output_schema",
    "source_schema",
    "context_contracts",
    "files",
    "binding_ids",
    "compiled_layout",
}
_LAYOUT_TYPES = {
    "scopes": ScopeRow,
    "scope_keys": ScopeKeyRow,
    "source_maps": SourceMapRow,
    "vectors": VectorRow,
    "words": WordRow,
}


def _object(value, fields, *, annotations=False):
    if not isinstance(value, t.Mapping):
        raise ValueError("Native record must be an object")
    keys = set(value)
    if not fields <= keys or keys - fields - (
        {"annotations"} if annotations else set()
    ):
        raise ValueError("Native record has missing or unknown fields")


def _metadata(bundle):
    dimensions = [
        Dimension.model_validate(
            {key: value for key, value in item.items() if value is not None}
        )
        for item in bundle.metadata["payload"]["dimensions"]
    ]
    contracts = [
        ContextContract.model_validate(item["payload"]["contract"])
        for item in bundle.context_contracts
    ]
    return DimensionsMetadata(dimensions=dimensions, context_contracts=contracts)


def _value_mapping(logical_name, physical_name, data_type, timezone):
    result = {
        "logical_name": logical_name,
        "physical_name": physical_name,
        "data_type": str(data_type),
        "nullable": False,
    }
    if str(data_type) == "datetime":
        result["timezone"] = timezone
    return result


def _mappings_for(aggregates, routing_record, graph, source_id_field):
    aggregates = sorted(aggregates, key=lambda item: item.output_name)
    outputs = [
        _value_mapping(
            item.output_name, f"__out_{index}", item.data_type, item.timezone
        )
        for index, item in enumerate(aggregates)
    ]
    sources = {item.column_name: item for item in aggregates}
    contributions = [
        _value_mapping(
            name, f"__value_{index}", sources[name].data_type, sources[name].timezone
        )
        for index, name in enumerate(sorted(sources))
    ]
    routing = []
    for index, key in enumerate(routing_record["payload"]["key_dimensions"]):
        field = graph.fields[key["context_field"]]
        entry = {
            "dimension_name": key["dimension_name"],
            "kind_column": f"__key_kind_{index}",
            "value_column": f"__key_value_{index}",
            "data_type": field.data_type.value,
        }
        if field.data_type is DataType.DATETIME:
            entry["timezone"] = field.timezone
        routing.append(entry)
    return outputs, {
        "source_id_field": source_id_field,
        "source_id_column": "__source_id",
        "predicate_id_column": "__predicate_id",
        "contributions": contributions,
        "routing": routing,
    }


def _mappings(prepared):
    return _mappings_for(
        prepared.aggregates,
        prepared.routing,
        prepared.graph,
        prepared.source_id_field,
    )


def _columns(outputs, source_schema, layout):
    cells = [
        ("__cell_id", "utf8", False),
        ("__predicate_id", "utf8", False),
        ("__contributor_set_id", "utf8", False),
    ]
    cells.extend(
        (
            item["physical_name"],
            storage_type(item["data_type"], item.get("timezone")),
            False,
        )
        for item in outputs
    )
    sources = [("__source_id", "utf8", False), ("__predicate_id", "utf8", False)]
    for item in source_schema["routing"]:
        sources.extend(
            [
                (item["kind_column"], "utf8", False),
                (
                    item["value_column"],
                    storage_type(item["data_type"], item.get("timezone")),
                    True,
                ),
            ]
        )
    sources.extend(
        (
            item["physical_name"],
            storage_type(item["data_type"], item.get("timezone")),
            False,
        )
        for item in source_schema["contributions"]
    )
    result = {
        "lattice.parquet": tuple(cells),
        "sources.parquet": tuple(sources),
        "contributors.parquet": (
            ("__contributor_set_id", "utf8", False),
            ("__source_id", "utf8", False),
        ),
    }
    result.update(
        {f"{name}.parquet": schema for name, schema in layout.columns.items()}
    )
    return result


def _source_bundle(ruleset_id, sources, budget):
    return _make_exact_envelope(
        "source-bundle",
        {
            "schema_version": 1,
            "ruleset_id": ruleset_id,
            "sources": [
                {"source_id": source.source_id, "content_id": source.content_id}
                for source in sources
            ],
        },
        budget=budget,
    )


def _native_evidence(lattice, graph, budget):
    """Retain the executable closure in addition to unchanged portable proof records."""
    state = lattice._require_exact()
    analysis = state.analysis
    original = lattice._evidence
    predicates = {item["id"]: item for item in original.predicates["predicates"]}
    languages = {item["id"]: item for item in original.predicates["languages"]}
    roots = {source.predicate_id for source in analysis.prepared.sources}
    roots.update(cell.predicate_id for cell in analysis.cells)
    roots.update(row.predicate_id for row in state.layout.scopes)
    roots.update(row.predicate_id for row in state.layout.scope_keys)
    roots.update(row.predicate_id for row in state.layout.vectors)
    roots.add(analysis.domain.predicate_id)
    pending = list(roots)
    seen = set()
    while pending:
        identifier = pending.pop()
        if identifier in seen:
            continue
        seen.add(identifier)
        budget.reserve(
            "max_work", 1, phase="snapshot.graph", units="retained predicate closure"
        )
        node = graph.nodes[identifier]
        if identifier not in predicates:
            predicates[identifier] = _make_exact_envelope(
                "predicate",
                {"schema_version": 1, "node": _materialize_json(node)},
                budget=budget,
            )
        if node["op"] in {"and", "or"}:
            pending.extend(node["args"])
        elif node["op"] == "not":
            pending.append(node["arg"])
        elif node["op"] == "language" and node["language_id"] not in languages:
            with graph.native_scope():
                raw = graph._native_call(
                    "language_encode",
                    lambda: graph.languages[node["language_id"]].to_json(
                        limits=graph.language_limits
                    ),
                )
                payload = decode_json(raw)
                languages[node["language_id"]] = _make_exact_envelope(
                    "language", payload, budget=budget
                )
    bundles = {item["id"]: item for item in original.predicates["source_bundles"]}
    subset = _source_bundle(analysis.prepared.ruleset_id, analysis.sources, budget)
    if subset["id"] != analysis.source_bundle_id:
        raise ValueError("Native source subset identity is stale")
    bundles[subset["id"]] = subset
    payload = _bundle_payload(original)
    payload["predicates"] = {
        **dict(original.predicates),
        "predicates": tuple(predicates[key] for key in sorted(predicates)),
        "languages": tuple(languages[key] for key in sorted(languages)),
        "source_bundles": tuple(bundles[key] for key in sorted(bundles)),
    }
    return _admit_validation_bundle(payload, budget=budget)


def _legacy_columns(frame, budget):
    """Describe the inspection frame exactly after bounded scalar extraction."""
    rows = source_rows(frame, budget=budget)
    supported = {
        "utf8",
        "bool",
        "int64",
        "float64",
        "date32",
        "datetime_us_naive",
        "datetime_us_utc",
    }
    columns = []
    for name in frame.columns:
        kind = native_storage_type(frame.schema[name])
        if kind not in supported:
            raise ValueError(
                f"Legacy snapshot column {name!r} has unsupported storage type"
            )
        columns.append((name, kind, any(row[name] is None for row in rows)))
    return tuple(columns)


def save(lattice, dir_path, *, budget):
    from mountainash_rules.engines.accumulator import snapshot_io

    if lattice._state is None:
        frame = relation(lattice.combinations)
        manifest = {
            "dimensions": lattice.metadata.model_dump(mode="json"),
            "aggregates": [
                item.model_dump(mode="json", exclude_none=True)
                for item in lattice.aggregates
            ],
            "partition_key": lattice.partition_key,
        }
        return snapshot_io.write_snapshot(
            dir_path,
            manifest=manifest,
            relations={"lattice.parquet": frame},
            columns={"lattice.parquet": _legacy_columns(frame, budget)},
            containers={},
            budget=budget,
        )
    state = lattice._require_exact()
    graph = EvaluationSession(budget).graph(lattice)
    evidence = _native_evidence(lattice, graph, budget)
    analysis = state.analysis
    prepared = analysis.prepared
    outputs, sources = _mappings(prepared)
    manifest = {
        "artifact_kind": "exact_cells",
        "native_schema_version": 1,
        "semantic_versions": prepared.semantic_versions.model_dump(mode="json"),
        "ruleset_id": prepared.ruleset_id,
        "source_bundle_id": analysis.source_bundle_id,
        "compilation_domain_id": prepared.domain_digest,
        "partition": {
            "routing": evidence.routing,
            "key_values": analysis.partition_identity["key_values"],
        },
        "artifact_id": analysis.artifact_id,
        "dimensions": evidence.metadata,
        "aggregates": evidence.aggregates,
        "output_schema": outputs,
        "source_schema": sources,
        "context_contracts": evidence.context_contracts,
        "binding_ids": [binding.id for binding in evidence.validation["bindings"]],
        "compiled_layout": {
            "schema_version": 1,
            "semantics": "scoped-words-1",
            "segmentation_fields": state.layout.segmentation_fields,
            "word_bits": 63,
            "word_storage": "dense",
        },
    }
    return snapshot_io.write_snapshot(
        dir_path,
        manifest=manifest,
        relations=state.native_relations,
        columns=state.columns,
        containers={
            "predicates": evidence.predicates,
            "validation": evidence.validation,
        },
        budget=budget,
    )


def _layout(manifest, rows):
    raw = manifest["compiled_layout"]
    _object(
        raw,
        {
            "schema_version",
            "semantics",
            "segmentation_fields",
            "word_bits",
            "word_storage",
        },
    )
    if (
        type(raw["schema_version"]) is not int
        or raw["schema_version"] != 1
        or raw["semantics"] != "scoped-words-1"
        or type(raw["word_bits"]) is not int
        or raw["word_bits"] != 63
        or raw["word_storage"] != "dense"
    ):
        raise ValueError("Unsupported exact compiled layout")
    fields = raw["segmentation_fields"]
    if (
        not isinstance(fields, list)
        or any(type(field) is not str for field in fields)
        or fields != sorted(set(fields))
    ):
        raise ValueError("segmentation_fields must be sorted unique physical fields")
    restored = {}
    for name, row_type in _LAYOUT_TYPES.items():
        restored[name] = tuple(
            row_type(
                **{attribute: row[native] for native, attribute in row_type._NATIVE}
            )
            for row in rows[f"{name}.parquet"]
        )
    return ExactLayout(**restored, segmentation_fields=tuple(fields))


def _validate_compilation_guards(graph, domain, metadata, evidence):
    """Require the restored compilation domain to imply every global regex guard."""
    guards = tuple(
        dimension
        for dimension in metadata.dimensions
        if dimension.match_strategy is MatchStrategy.CONTEXT_REGEX
    )
    if not guards:
        return
    from mountainash_rules.core.reasoner import Reasoner

    regex_options = _evidence_regex_options(evidence)
    reasoner = Reasoner(graph)
    for dimension in guards:
        guard = graph.lower_dimension(dimension, {}, regex_options=regex_options)
        if not reasoner.is_empty(reasoner.difference(domain.predicate_id, guard)):
            raise ValueError(
                "Native compilation domain extends outside a context-regex guard"
            )


def _restore_sources(
    graph,
    ruleset_id,
    routing,
    source_schema,
    rows,
    metadata,
    evidence,
    budget,
):
    origins = {}
    for origin in evidence.predicates["source_origins"]:
        origins.setdefault(origin["source_id"], {})[origin["dimension_name"]] = origin
    ordinary = tuple(
        dim
        for dim in metadata.dimensions
        if dim.match_strategy is not MatchStrategy.CONTEXT_REGEX
    )
    origin_names = {dim.dimension_name for dim in ordinary}
    source_ids = set()
    sources = []
    regex_options = _evidence_regex_options(evidence)
    for row in rows:
        budget.reserve("max_work", 1, phase="snapshot.sources", units="source records")
        identifier = _source_id(row["__source_id"])
        if identifier in source_ids:
            raise ValueError("Duplicate native source ID")
        source_ids.add(identifier)
        predicate_id = row["__predicate_id"]
        validate_id(predicate_id, "predicate")
        if predicate_id not in graph.nodes:
            raise ValueError("Source predicate is unavailable")
        keys = []
        for mapping in source_schema["routing"]:
            kind, value = row[mapping["kind_column"]], row[mapping["value_column"]]
            if kind == "wildcard" and value is None:
                match = {"kind": "wildcard"}
            elif kind == "value" and value is not None:
                match = {
                    "kind": "value",
                    "value": encode_scalar(
                        value,
                        DataType(mapping["data_type"]),
                        timezone=mapping.get("timezone"),
                    ),
                }
            else:
                raise ValueError("Invalid native routing kind/value pair")
            keys.append({"dimension_name": mapping["dimension_name"], "match": match})
        if _freeze(keys) not in routing["payload"]["partition_keys"]:
            raise ValueError("Source routing key is outside the retained registry")
        routing_values = {key["dimension_name"]: key["match"] for key in keys}
        contributions = {
            mapping["logical_name"]: normalize_scalar(
                row[mapping["physical_name"]],
                DataType(mapping["data_type"]),
                timezone=mapping.get("timezone"),
                allow_reserved=True,
            )
            for mapping in source_schema["contributions"]
        }
        source_origins = origins.get(identifier, {})
        if set(source_origins) != origin_names:
            raise ValueError("Source origins do not cover the declared dimensions")
        terms = []
        for dim in ordinary:
            origin = source_origins[dim.dimension_name]
            values = origin["authored_values"]
            if set(values) != set(_source_columns(dim)):
                raise ValueError("Source origin consumes different source columns")
            authored = {
                name: [decode_scalar(dict(item)) for item in value]
                if isinstance(value, (list, tuple))
                else decode_scalar(dict(value))
                for name, value in values.items()
            }
            lowered = graph.lower_dimension(dim, authored, regex_options=regex_options)
            if lowered != origin["predicate_id"]:
                raise ValueError(
                    "Source origin predicate disagrees with authored semantics"
                )
            if dim.role is DimensionRole.CONTEXT_KEY:
                expected = _routing_match(
                    authored[dim.resolved_rule_field],
                    graph.fields[dim.resolved_context_field],
                )
                if routing_values.get(dim.dimension_name) != expected:
                    raise ValueError(
                        "Source routing values disagree with its context-key origin"
                    )
            else:
                terms.append(lowered)
        if graph.and_(*terms) != predicate_id:
            raise ValueError(
                "Native source predicate disagrees with its dimension origins"
            )
        payload = {
            "schema_version": 1,
            "ruleset_id": ruleset_id,
            "source_id": identifier,
            "predicate_id": predicate_id,
            "routing_values": keys,
            "contributions": {
                mapping["logical_name"]: encode_scalar(
                    contributions[mapping["logical_name"]],
                    DataType(mapping["data_type"]),
                    timezone=mapping.get("timezone"),
                    allow_reserved=True,
                )
                for mapping in source_schema["contributions"]
            },
        }
        sources.append(
            SourceRecord(
                identifier,
                predicate_id,
                _canonical_id(
                    budget, "source-content", payload, phase="snapshot.sources"
                ),
                _freeze(keys),
                MappingProxyType(contributions),
                tuple(source_origins[name] for name in sorted(source_origins)),
            )
        )
    if (
        set(origins) - source_ids
        or set(evidence.predicates["source_labels"]) - source_ids
    ):
        raise ValueError("Source diagnostics refer to missing native sources")
    sources.sort(key=lambda source: source.source_id)
    by_id = {source.source_id: source for source in sources}
    covered = set()
    for bundle in evidence.predicates["source_bundles"]:
        if bundle["payload"]["ruleset_id"] != ruleset_id:
            raise ValueError("Foreign source-bundle ruleset")
        for item in bundle["payload"]["sources"]:
            if (
                item["source_id"] not in by_id
                or by_id[item["source_id"]].content_id != item["content_id"]
            ):
                raise ValueError("Source content disagrees with retained evidence")
            covered.add(item["source_id"])
    if covered != source_ids:
        raise ValueError(
            "Native source rows differ from the referenced source-bundle union"
        )
    return tuple(sources)


def _restore_cells(prepared, partition, outputs, cells, edges, sources, budget):
    source_ids = {source.source_id for source in sources}
    sets = {}
    seen_edges = set()
    for row in edges:
        pair = row["__contributor_set_id"], row["__source_id"]
        if pair in seen_edges or pair[1] not in source_ids:
            raise ValueError("Duplicate or foreign contributor edge")
        seen_edges.add(pair)
        sets.setdefault(pair[0], []).append(pair[1])
    for identifier, members in sets.items():
        members.sort()
        expected = _canonical_id(
            budget,
            "contributor-set",
            {
                "schema_version": 1,
                "source_ids": members,
            },
            phase="snapshot.contributors",
        )
        if identifier != expected:
            raise ValueError(
                "Contributor-set identity disagrees with decoded membership"
            )
    result = []
    cell_ids = set()
    used_sets = set()
    for row in cells:
        identifier = row["__cell_id"]
        predicate_id = row["__predicate_id"]
        set_id = row["__contributor_set_id"]
        if (
            identifier in cell_ids
            or set_id not in sets
            or predicate_id not in prepared.graph.nodes
        ):
            raise ValueError("Duplicate cell or unresolved predicate/contributor set")
        cell_ids.add(identifier)
        used_sets.add(set_id)
        expected = _canonical_id(
            budget,
            "cell",
            {
                "cell_schema": 1,
                "ruleset_id": prepared.ruleset_id,
                "partition_identity": partition,
                "predicate_id": predicate_id,
                "contributor_set_id": set_id,
            },
            phase="snapshot.cells",
        )
        if identifier != expected:
            raise ValueError("Native cell identity disagrees with semantic content")
        values = {
            mapping["logical_name"]: normalize_scalar(
                row[mapping["physical_name"]],
                DataType(mapping["data_type"]),
                timezone=mapping.get("timezone"),
                allow_reserved=True,
            )
            for mapping in outputs
        }
        result.append(
            AnalyzedCell(
                identifier,
                predicate_id,
                tuple(sets[set_id]),
                set_id,
                MappingProxyType(values),
            )
        )
    if used_sets != set(sets):
        raise ValueError("Contributor relation contains unreferenced sets")
    return tuple(sorted(result, key=lambda cell: cell.cell_id))


def _validate_evidence(analysis, metadata, evidence, budget, *, check_geometry=True):
    """Replay canonical input identity and the shared build/binding postconditions."""
    domains = _evidence_domains(evidence)
    contracts = {
        item["payload"]["contract"]["contract_id"]: ContextContract.model_validate(
            item["payload"]["contract"]
        )
        for item in evidence.context_contracts
    }
    reports = {item.id: item for item in evidence.validation["reports"]}
    analyses = {item.id: item for item in evidence.validation["analysis_inputs"]}
    materials = {}
    for item in analyses.values():
        if (
            item.ruleset_id != analysis.prepared.ruleset_id
            or item.source_id_field != analysis.prepared.source_id_field
        ):
            raise ValueError(
                "Analysis input refers to a different native source namespace"
            )
        selected_domains = {name: domains[name] for name in item.domain_digests}
        selected_contracts = tuple(
            contracts[pair["contract_id"]] for pair in item.contracts
        )
        current, material = prepare_analysis_input(
            analysis.prepared,
            compilation_domain_ref=item.compilation_domain_ref,
            domains=selected_domains,
            contracts=selected_contracts,
            validation_policy=item.validation_policy,
            analysis=analysis,
        )
        if current.id != item.id:
            raise ValueError("Native source material disagrees with its analysis input")
        materials[item.id] = (material, selected_domains, selected_contracts)
    for report in reports.values():
        if report.stage == "source":
            validate_source_report_semantics(report)
            continue
        validate_compiled_report_semantics(report, analyses[report.analysis_input_id])
        if report.artifact_id != analysis.artifact_id:
            raise ValueError("Compiled report belongs to a different native artifact")
        if not _scope_covers(
            report.scope, materials[report.analysis_input_id][0].selected_scope
        ):
            raise ValueError("Compiled report scope does not cover the native artifact")
    checked = set()
    bindings = evidence.validation["bindings"]
    obligations = [
        (binding.analysis_input_id, binding.source_report_id, binding.approval_ids)
        for binding in bindings
    ]
    for binding in bindings:
        if binding.artifact_id != analysis.artifact_id:
            raise ValueError("Binding belongs to a different native artifact")
        validate_contract_binding(
            binding, evidence, materials[binding.analysis_input_id][0]
        )
    if not bindings:
        compiled = [report for report in reports.values() if report.stage == "compiled"]
        if not compiled:
            raise ValueError("Native artifact is missing compiled evidence")
        for report in compiled:
            candidates = [
                source
                for source in reports.values()
                if source.stage == "source"
                and source.analysis_input_id == report.analysis_input_id
            ]
            if len(candidates) != 1:
                raise ValueError(
                    "Unbound artifact requires unambiguous retained source evidence"
                )
            source = candidates[0]
            approvals = tuple(
                approval.id
                for approval in evidence.validation["approvals"]
                if approval.report_id == source.id
            )
            obligations.append((source.analysis_input_id, source.id, approvals))
    dimension_fields = {
        dim.dimension_name: dim.resolved_context_field for dim in metadata.dimensions
    }
    guard_fields = tuple(
        dim.resolved_context_field
        for dim in metadata.dimensions
        if dim.match_strategy is MatchStrategy.CONTEXT_REGEX
    )
    for analysis_id, report_id, approval_ids in obligations:
        key = (analysis_id, report_id)
        if key in checked:
            continue
        checked.add(key)
        material, selected_domains, selected_contracts = materials[analysis_id]
        validated = ValidatedBuildInput.model_validate(
            {
                "schema_version": 1,
                "analysis_input_id": analysis_id,
                "source_report_id": report_id,
                "approval_ids": approval_ids,
                "bundle": evidence,
            },
            context={"budget": budget},
        )
        if not bindings:
            _, build_material = prepare_analysis_input(
                analysis.prepared,
                compilation_domain_ref=analyses[analysis_id].compilation_domain_ref,
                domains=selected_domains,
                contracts=selected_contracts,
                validation_policy=analyses[analysis_id].validation_policy,
            )
            validate_build_permission(validated, build_material)
        preparation = BuildPreparation(
            validated,
            analysis.prepared,
            selected_domains,
            material,
            selected_contracts,
            analyses[analysis_id],
            reports[report_id],
            dimension_fields,
            guard_fields,
        )
        if check_geometry:
            _prove_compiled(preparation, analysis, budget)
        else:
            geometry = analysis_geometry(analysis, provider_domains=selected_domains)
            _prove_profiles(preparation, analysis, geometry)


def load(dir_path, *, budget):
    from mountainash_rules.engines.accumulator import snapshot_io
    from mountainash_rules.engines.accumulator.lattice import Lattice

    manifest, relations, containers = snapshot_io.read_snapshot(dir_path, budget=budget)
    if "artifact_kind" not in manifest and "native_schema_version" not in manifest:
        _object(manifest, {"dimensions", "aggregates", "partition_key"})
        return Lattice(
            relations["lattice.parquet"].collect(),
            DimensionsMetadata.model_validate(manifest["dimensions"]),
            [
                Aggregate(column_name=item["column_name"], operation=item["operation"])
                for item in manifest["aggregates"]
            ],
            manifest["partition_key"],
        )
    _object(manifest, _MANIFEST_FIELDS, annotations=True)
    if (
        manifest["artifact_kind"] != "exact_cells"
        or type(manifest["native_schema_version"]) is not int
        or manifest["native_schema_version"] != 1
    ):
        raise ValueError("Unsupported exact native snapshot version")
    versions = SemanticVersions.model_validate(manifest["semantic_versions"])
    ruleset_id = _label(manifest["ruleset_id"], "ruleset_id")
    for name, kind in (
        ("artifact_id", "artifact"),
        ("source_bundle_id", "source-bundle"),
        ("compilation_domain_id", "domain"),
    ):
        validate_id(manifest[name], kind)
    _object(manifest["partition"], {"routing", "key_values"})
    evidence = _admit_validation_bundle(
        {
            "schema_version": 1,
            "metadata": manifest["dimensions"],
            "aggregates": manifest["aggregates"],
            "routing": manifest["partition"]["routing"],
            "context_contracts": manifest["context_contracts"],
            "predicates": containers["predicates"],
            "validation": containers["validation"],
        },
        budget=budget,
    )
    if manifest["binding_ids"] != [
        binding.id for binding in evidence.validation["bindings"]
    ]:
        raise ValueError("Manifest binding IDs disagree with validation evidence")
    metadata = _metadata(evidence)
    domain_envelopes = {item["id"]: item for item in evidence.predicates["domains"]}
    if manifest["compilation_domain_id"] not in domain_envelopes:
        raise ValueError("Native compilation domain is unresolved")
    domain = DomainDefinition.model_validate(
        domain_envelopes[manifest["compilation_domain_id"]]["payload"]
    )
    graph = PredicateGraph(domain.fields, budget=budget)
    graph.decode(evidence.predicates["predicates"], evidence.predicates["languages"])
    _validate_compilation_guards(graph, domain, metadata, evidence)
    aggregates = tuple(
        Aggregate.model_validate(item)
        for item in evidence.aggregates["payload"]["declarations"]
    )
    _object(
        manifest["source_schema"],
        {
            "source_id_field",
            "source_id_column",
            "predicate_id_column",
            "contributions",
            "routing",
        },
    )
    source_id_field = _label(
        manifest["source_schema"]["source_id_field"], "source_id_field"
    )
    outputs, source_schema = _mappings_for(
        aggregates, evidence.routing, graph, source_id_field
    )
    if (
        outputs != manifest["output_schema"]
        or source_schema != manifest["source_schema"]
    ):
        raise ValueError(
            "Native logical/physical mappings disagree with semantic declarations"
        )
    rows = {
        name: source_rows(frame, budget=budget) for name, frame in relations.items()
    }
    layout = _layout(manifest, rows)
    expected_columns = _columns(outputs, source_schema, layout)
    entries = {entry["path"]: entry for entry in manifest["files"]}
    for name, columns in expected_columns.items():
        declared = [
            {"name": field, "storage_type": kind, "nullable": nullable}
            for field, kind, nullable in columns
        ]
        if entries[name]["columns"] != declared:
            raise ValueError(
                "Native physical column declarations disagree with required schema"
            )
    sources = _restore_sources(
        graph,
        ruleset_id,
        evidence.routing,
        source_schema,
        rows["sources.parquet"],
        metadata,
        evidence,
        budget,
    )
    global_bundle = _source_bundle(ruleset_id, sources, budget)
    prepared = PreparedSources(
        ruleset_id,
        graph,
        domain,
        manifest["compilation_domain_id"],
        evidence.metadata,
        evidence.routing,
        evidence.aggregates,
        aggregates,
        sources,
        global_bundle["id"],
        evidence.predicates["source_labels"],
        versions,
        source_id_field,
    )
    key_values = _freeze(manifest["partition"]["key_values"])
    if key_values not in prepared.routing["payload"]["partition_keys"]:
        raise ValueError("Native partition is outside its routing registry")
    partition = _freeze(
        {"routing_id": prepared.routing["id"], "key_values": key_values}
    )
    selected_sources = tuple(
        source for source in sources if source.routing_values == key_values
    )
    if (
        _source_bundle(ruleset_id, selected_sources, budget)["id"]
        != manifest["source_bundle_id"]
    ):
        raise ValueError(
            "Native source subset differs from the selected semantic partition"
        )
    routing_fields = {
        item["dimension_name"]: item["context_field"]
        for item in prepared.routing["payload"]["key_dimensions"]
    }
    partition_predicate = graph.and_(
        domain.predicate_id,
        *(
            graph.eq(
                routing_fields[key["dimension_name"]],
                decode_scalar(dict(key["match"]["value"])),
            )
            for key in key_values
            if key["match"]["kind"] == "value"
        ),
    )
    partition_domain = domain.model_copy(update={"predicate_id": partition_predicate})
    partition_digest = _canonical_id(
        budget,
        "domain",
        partition_domain.model_dump(mode="json", exclude_none=True),
        phase="snapshot.domain",
    )
    cells = _restore_cells(
        prepared,
        partition,
        outputs,
        rows["lattice.parquet"],
        rows["contributors.parquet"],
        selected_sources,
        budget,
    )
    identifier = _canonical_id(
        budget,
        "artifact",
        _artifact_payload(
            prepared,
            partition,
            manifest["source_bundle_id"],
            cells,
        ),
        phase="snapshot.artifact",
    )
    if identifier != manifest["artifact_id"]:
        raise ValueError(
            "Native artifact identity disagrees with restored semantic content"
        )
    analysis = ExactAnalysis(
        prepared,
        selected_sources,
        partition,
        partition_domain,
        partition_digest,
        cells,
        identifier,
        manifest["source_bundle_id"],
    )
    validate_layout(analysis, layout, budget=budget)
    _validate_evidence(analysis, metadata, evidence, budget)
    state = materialize_state(
        analysis, layout, budget=budget, restored_relations=relations
    )
    return Lattice._from_exact(state, metadata, evidence)


def _merge_records(retained, incoming):
    records = {
        item.id if hasattr(item, "id") else item["id"]: item for item in retained
    }
    for item in incoming:
        identifier = item.id if hasattr(item, "id") else item["id"]
        if identifier in records and records[identifier] != item:
            raise ValueError(
                "Conflicting complete evidence records for the same typed ID"
            )
        records[identifier] = item
    return tuple(records[identifier] for identifier in sorted(records))


def with_binding(lattice, binding, *, evidence, budget):
    from mountainash_rules.engines.accumulator.lattice import Lattice

    state = lattice._require_exact()
    if not isinstance(binding, ContractBinding):
        raise TypeError("binding must be a ContractBinding")
    # Rehydrate even a model instance: model_copy/model_construct are not permission.
    admitted = _admit_validation_bundle(_bundle_payload(evidence), budget=budget)
    incoming = admitted.validation["bindings"]
    if len(incoming) != 1 or incoming[0] != binding:
        raise ValueError("Evidence must contain exactly the supplied complete binding")
    if binding.artifact_id != lattice.artifact_id:
        raise ValueError("Binding refers to a different artifact")
    original = lattice._evidence
    for name in ("metadata", "aggregates", "routing"):
        if getattr(admitted, name) != getattr(original, name):
            raise ValueError("Binding evidence changes retained semantic declarations")
    for name in ("source_labels", "source_origins"):
        if admitted.predicates[name] != original.predicates[name]:
            raise ValueError("Binding evidence changes retained source diagnostics")
    sources = {
        source.source_id: source.content_id
        for source in state.analysis.prepared.sources
    }
    for bundle in admitted.predicates["source_bundles"]:
        if bundle["payload"]["ruleset_id"] != state.analysis.prepared.ruleset_id:
            raise ValueError("Binding evidence changes the source namespace")
        if any(
            sources.get(item["source_id"]) != item["content_id"]
            for item in bundle["payload"]["sources"]
        ):
            raise ValueError("Binding evidence adds or changes source content")
    retained_domains = {
        item["payload"]["domain_id"]: item for item in original.predicates["domains"]
    }
    for item in admitted.predicates["domains"]:
        retained = retained_domains.get(item["payload"]["domain_id"])
        if retained is not None and retained != item:
            raise ValueError("Binding evidence redefines a retained domain")
    capacity = _bounded_json_size(
        admitted, budget, phase="binding.merge", counter="max_live_bytes"
    )
    budget.reserve(
        "max_live_bytes",
        capacity * 8,
        phase="binding.merge",
        units="incoming evidence and merged immutable view",
    )
    predicates = dict(original.predicates)
    for name in ("predicates", "languages", "domains", "source_bundles"):
        predicates[name] = _merge_records(
            original.predicates[name], admitted.predicates[name]
        )
    validation = {"schema_version": 1}
    for name in ("analysis_inputs", "findings", "reports", "approvals", "bindings"):
        validation[name] = _merge_records(
            original.validation[name], admitted.validation[name]
        )
    merged = _admit_validation_bundle(
        {
            "schema_version": 1,
            "metadata": original.metadata,
            "aggregates": original.aggregates,
            "routing": original.routing,
            "context_contracts": tuple(
                sorted(
                    _merge_records(
                        original.context_contracts, admitted.context_contracts
                    ),
                    key=lambda item: item["payload"]["contract"]["contract_id"],
                )
            ),
            "predicates": predicates,
            "validation": validation,
        },
        budget=budget,
    )
    graph = EvaluationSession(budget).graph(lattice)
    graph.decode(merged.predicates["predicates"], merged.predicates["languages"])
    analysis = replace(
        state.analysis, prepared=replace(state.analysis.prepared, graph=graph)
    )
    metadata = lattice._metadata.model_copy(
        deep=True,
        update={
            "context_contracts": _metadata(merged).context_contracts,
        },
    )
    _validate_evidence(analysis, metadata, merged, budget, check_geometry=False)
    # No early idempotence: all supplied records and domains have now been checked.
    if any(existing.id == binding.id for existing in lattice.bindings):
        return lattice
    if any(
        existing.contract_id == binding.contract_id for existing in lattice.bindings
    ):
        raise ValueError(
            "A competing binding is already active for this artifact/contract"
        )
    result = Lattice._from_exact(state, metadata, merged)
    result._execution_graph = graph
    return result
