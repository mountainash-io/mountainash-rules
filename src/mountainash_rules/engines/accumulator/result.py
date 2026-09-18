"""Exact single- and batch-context accumulator outcome wrappers."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
import typing as t

import mountainash.expressions as ma
from mountainash.relations import Relation, concat, relation

from mountainash_rules.core.codec import _bounded_json_size, _canonical
from mountainash_rules.core.contracts import (
    InvalidContextError,
    OperationBudget,
    OutcomeRecord,
    UnresolvedContextError,
)
from mountainash_rules.core.scalar import decode_scalar
from mountainash_rules.engines.accumulator.lattice import Lattice

from mountainash_rules.engines.accumulator.tables import (
    native_column_bytes,
    relation_host_bytes,
    relation_native_bytes,
    storage_type,
)


_CELL_IDENTITIES = ("cell_id", "predicate_id", "contributor_set_id")
_OUTCOME_FIELDS = (
    "status",
    "reason",
    "binding_id",
    "contract_id",
    "profile_id",
    "values",
    "cell_id",
    "contributor_ids",
    "may_have_no_match",
    "observations",
    "issues",
)


def _outcome_json(record: OutcomeRecord, field: str, budget: OperationBudget) -> str:
    """Canonical nested state is scalar UTF-8, never a backend object column."""
    value = getattr(record, field)
    size = _bounded_json_size(
        value, budget, phase="batch.json", counter="max_output_bytes"
    )
    budget.reserve(
        "max_output_bytes", size, phase="batch.json", units="nested outcome JSON"
    )
    budget.reserve(
        "max_live_bytes", 4 * size, phase="batch.json", units="nested JSON buffers"
    )
    return _canonical(value)


class AccumulatorResult:
    """One normalized exact outcome and its non-promoted candidate inspection."""

    def __init__(
        self,
        outcome: OutcomeRecord,
        *,
        lattice: Lattice | None,
        candidate_ids: tuple[str, ...] | None,
        output_fields: tuple[str, ...],
    ) -> None:
        if not isinstance(outcome, OutcomeRecord):
            raise ValueError("outcome must be OutcomeRecord")
        if lattice is not None and not isinstance(lattice, Lattice):
            raise ValueError("lattice must be Lattice or None")
        if not isinstance(output_fields, tuple) or any(
            type(field) is not str for field in output_fields
        ):
            raise ValueError("output_fields must be a tuple of strings")
        if len(set(output_fields)) != len(output_fields):
            raise ValueError("output_fields must be unique")
        if candidate_ids is not None:
            if (
                not isinstance(candidate_ids, tuple)
                or any(type(identifier) is not str for identifier in candidate_ids)
                or len(set(candidate_ids)) != len(candidate_ids)
            ):
                raise ValueError("candidate_ids must be a unique tuple of cell IDs")
            if lattice is None:
                raise ValueError("candidate analysis requires its selected lattice")
            lattice._require_exact()
            declared_outputs = {
                aggregate.output_name for aggregate in lattice.aggregates
            }
            if not set(output_fields).issubset(declared_outputs):
                raise ValueError(
                    "output_fields are not declared by the selected lattice"
                )
        if outcome.contributor_ids is not None and lattice is None:
            raise ValueError("definite contributors require their selected lattice")

        self._outcome = outcome
        self._lattice = lattice
        self._candidate_ids = candidate_ids
        self._output_fields = output_fields

    @property
    def outcome(self) -> OutcomeRecord:
        """The canonical, typed record retained by this outcome."""
        return self._outcome

    @property
    def status(self) -> str:
        return self._outcome.status

    @property
    def reason(self) -> str:
        return self._outcome.reason

    @property
    def binding_id(self) -> str | None:
        return self._outcome.binding_id

    @property
    def contract_id(self) -> str | None:
        return self._outcome.contract_id

    @property
    def profile_id(self) -> str | None:
        return self._outcome.profile_id

    @property
    def values(self) -> Mapping[str, t.Any] | None:
        """Decision values decoded from scalar-1 envelopes, in profile order."""
        encoded = self._outcome.values
        if encoded is None:
            return None
        ordered = (
            self._output_fields
            if set(encoded) == set(self._output_fields)
            else tuple(encoded)
        )
        return MappingProxyType(
            {field: decode_scalar(dict(encoded[field])) for field in ordered}
        )

    @property
    def cell_id(self) -> str | None:
        return self._outcome.cell_id

    @property
    def contributor_ids(self) -> tuple[str, ...] | None:
        return self._outcome.contributor_ids

    @property
    def may_have_no_match(self) -> bool | None:
        return self._outcome.may_have_no_match

    @property
    def observations(self) -> Mapping[str, t.Any] | None:
        return self._outcome.observations

    @property
    def issues(self) -> tuple[t.Any, ...]:
        return self._outcome.issues

    @property
    def output_fields(self) -> tuple[str, ...]:
        """The requested logical outputs used for candidate projection."""
        return self._output_fields

    def _candidate_cells(self) -> t.Any | None:
        if self._candidate_ids is None:
            return None
        assert self._lattice is not None
        state = self._lattice._require_exact()
        return state.cells.filter(ma.col("cell_id").is_in(self._candidate_ids)).select(
            *_CELL_IDENTITIES,
            *[ma.col(field) for field in self._output_fields],
        )

    @property
    def candidate_cells(self) -> t.Any | None:
        """Lazy exact possible cells, or ``None`` when analysis did not run."""
        return self._candidate_cells()

    @property
    def candidate_contributors(self) -> t.Any | None:
        """Lazy unique possible ``(cell_id, source_id)`` contributor edges."""
        candidates = self._candidate_cells()
        if candidates is None:
            return None
        assert self._lattice is not None
        state = self._lattice._require_exact()
        return (
            candidates.select("cell_id", "contributor_set_id")
            .join(state.contributors, on="contributor_set_id")
            .select("cell_id", "source_id")
            .unique()
        )

    @property
    def lineage(self) -> t.Any | None:
        """Definite requested-output lineage; candidates never become definite."""
        if self._outcome.contributor_ids is None:
            return None
        assert self._lattice is not None
        return self._lattice._lineage(
            self._outcome.contributor_ids, output_fields=self._output_fields
        )

    def candidate_lineage(self, cell_id: str) -> t.Any:
        """Return all declared lineage for one exact candidate cell."""
        if self._candidate_ids is None or cell_id not in self._candidate_ids:
            raise KeyError(cell_id)
        assert self._lattice is not None
        return self._lattice.lineage(cell_id)

    def raise_for_status(self) -> "AccumulatorResult":
        """Return this result, or re-raise its request-scoped typed error."""
        if self._outcome.status == "invalid_context":
            error = InvalidContextError(self._outcome)
            error.result = self
            raise error
        if self._outcome.status == "rejected":
            error = UnresolvedContextError(self._outcome)
            error.result = self
            raise error
        return self


class AccumulatorBatchResult:
    """Ordered exact outcomes with per-context inspection that never reroutes."""

    def __init__(
        self,
        results: tuple[AccumulatorResult, ...],
        context_ids: t.Any,
        *,
        context_id_field: str,
        output_fields: tuple[str, ...],
        template_lattice: Lattice,
        budget: OperationBudget,
    ) -> None:
        if not isinstance(results, tuple) or any(
            not isinstance(result, AccumulatorResult) for result in results
        ):
            raise ValueError("results must be a tuple of AccumulatorResult")
        if type(context_id_field) is not str or not context_id_field:
            raise ValueError("context_id_field must be a nonempty string")
        if not isinstance(output_fields, tuple) or any(
            type(field) is not str for field in output_fields
        ):
            raise ValueError("output_fields must be a tuple of strings")
        if not isinstance(template_lattice, Lattice):
            raise ValueError("template_lattice must be Lattice")
        if not isinstance(budget, OperationBudget):
            raise ValueError("budget must be OperationBudget")

        context_relation = (
            context_ids if isinstance(context_ids, Relation) else relation(context_ids)
        )
        if tuple(context_relation.columns) != ("__context_id",):
            raise ValueError("context_ids must contain exactly __context_id")
        count = context_relation.count_rows()
        budget.reserve(
            "max_work",
            count,
            phase="apply.batch.records",
            units="context record materialization",
        )
        budget.reserve(
            "max_live_bytes",
            count * 256,
            phase="apply.batch.records",
            units="context ID and record references",
        )
        # Only the already-admitted ID projection crosses the host output
        # boundary; request columns retain their original carriers for admission.
        context_id_bytes, self._context_id_kind = native_column_bytes(
            context_relation,
            "__context_id",
            budget=budget,
            phase="apply.batch.context-ids",
        )
        budget.reserve(
            "max_output_bytes",
            context_id_bytes,
            phase="apply.batch.context-ids",
            units="returned native context IDs",
        )
        budget.reserve(
            "max_live_bytes",
            context_id_bytes * 3,
            phase="apply.batch.context-ids",
            units="native context ID transfer buffers",
        )
        self._context_ids = relation(context_relation.to_polars())
        identifiers = tuple(row["__context_id"] for row in self._context_ids.to_dicts())
        if len(identifiers) != len(results):
            raise ValueError("results must align one-for-one with context_ids")
        try:
            records = dict(
                zip(identifiers, (item.outcome for item in results), strict=True)
            )
        except TypeError as exc:
            raise ValueError("context IDs must be hashable") from exc
        if len(records) != len(identifiers):
            raise ValueError("context IDs must be globally unique")

        self._results = results
        self._context_id_field = context_id_field
        self._output_fields = output_fields
        self._template_lattice = template_lattice
        self._budget = budget
        self._records = MappingProxyType(records)
        self._by_context_id = MappingProxyType(
            dict(zip(identifiers, results, strict=True))
        )
        self._outcomes = self._materialize_outcomes(identifiers)
        self._candidate_cells, self._candidate_contributors = (
            self._materialize_candidates(identifiers)
        )

    def _materialize_outcomes(self, identifiers: tuple[t.Any, ...]) -> t.Any:
        size = len(identifiers)
        self._budget.reserve(
            "max_live_bytes",
            size * 512,
            phase="apply.batch.outcomes",
            units="normalized outcome rows",
        )
        from mountainash_rules.engines.accumulator.tables import make_relation

        rows = []
        for identifier, result in zip(identifiers, self._results, strict=True):
            values = {
                field: _outcome_json(result.outcome, field, self._budget)
                if field in {"values", "contributor_ids", "observations", "issues"}
                else getattr(result.outcome, field)
                for field in _OUTCOME_FIELDS
            }
            rows.append(values)
        schema = [
            (
                field,
                "bool" if field == "may_have_no_match" else "utf8",
                field in {"binding_id", "cell_id", "may_have_no_match"},
            )
            for field in _OUTCOME_FIELDS
        ]
        schema.append(("__outcome_position", "int64", False))
        for position, row in enumerate(rows):
            row["__outcome_position"] = position
        payload = make_relation(rows, schema, budget=self._budget)
        outcome_columns = [
            ("__context_id", self._context_id_kind, False),
            *[column for column in schema if column[0] != "__outcome_position"],
        ]
        outcome_output_bytes = relation_native_bytes(
            (
                {
                    "__context_id": identifier,
                    **{
                        name: row[name]
                        for name, _kind, _nullable in schema
                        if name != "__outcome_position"
                    },
                }
                for identifier, row in zip(identifiers, rows, strict=True)
            ),
            outcome_columns,
        )
        outcome_host_bytes = relation_host_bytes(
            (
                {
                    "__context_id": identifier,
                    **{
                        name: row[name]
                        for name, _kind, _nullable in schema
                        if name != "__outcome_position"
                    },
                }
                for identifier, row in zip(identifiers, rows, strict=True)
            ),
            outcome_columns,
        )
        self._budget.reserve(
            "max_output_bytes",
            outcome_output_bytes,
            phase="apply.batch.outcomes",
            units="returned normalized outcome rows",
        )
        self._budget.reserve(
            "max_live_bytes",
            outcome_host_bytes + 3 * outcome_output_bytes,
            phase="apply.batch.outcomes",
            units="outcome host and native projection buffers",
        )
        self._budget.reserve(
            "max_work",
            len(rows),
            phase="batch.outcome-join",
            units="ordered output rows",
        )
        numbered = self._context_ids.with_row_index(name="__outcome_position")
        joined = (
            numbered.join(payload, on="__outcome_position")
            .sort("__outcome_position")
            .drop("__outcome_position")
        )
        return relation(joined.collect())

    def _empty_candidate_cells(self) -> t.Any:
        state = self._template_lattice._require_exact()
        cells = state.cells.filter(ma.lit(False)).select(
            *_CELL_IDENTITIES, *[ma.col(field) for field in self._output_fields]
        )
        return (
            self._context_ids.slice(0, 0)
            .cross_join(cells)
            .select(
                "__context_id",
                *_CELL_IDENTITIES,
                *[ma.col(field) for field in self._output_fields],
            )
        )

    def _empty_candidate_contributors(self) -> t.Any:
        state = self._template_lattice._require_exact()
        cells = state.cells.filter(ma.lit(False)).select(
            "cell_id", "contributor_set_id"
        )
        edges = cells.join(state.contributors, on="contributor_set_id").select(
            "cell_id", "source_id"
        )
        return (
            self._context_ids.slice(0, 0)
            .cross_join(edges)
            .select("__context_id", "cell_id", "source_id")
        )

    def _candidate_schemas(
        self,
    ) -> tuple[list[tuple[str, str, bool]], list[tuple[str, str, bool]]]:
        outputs = {
            aggregate.output_name: aggregate
            for aggregate in self._template_lattice.aggregates
        }
        cell_columns = [
            ("__context_id", self._context_id_kind, False),
            ("cell_id", "utf8", False),
            ("predicate_id", "utf8", False),
            ("contributor_set_id", "utf8", False),
        ] + [
            (
                field,
                storage_type(outputs[field].data_type, outputs[field].timezone),
                False,
            )
            for field in self._output_fields
        ]
        return cell_columns, [
            ("__context_id", self._context_id_kind, False),
            ("cell_id", "utf8", False),
            ("source_id", "utf8", False),
        ]

    def _candidate_rows(self, identifiers, selected):
        for position, _result, cells in selected:
            for cell in cells:
                yield {
                    "__context_id": identifiers[position],
                    "cell_id": cell.cell_id,
                    "predicate_id": cell.predicate_id,
                    "contributor_set_id": cell.contributor_set_id,
                    **{field: cell.outputs[field] for field in self._output_fields},
                }

    def _candidate_contributor_rows(self, identifiers, selected):
        for position, _result, cells in selected:
            for cell in cells:
                for source_id in cell.contributors:
                    yield {
                        "__context_id": identifiers[position],
                        "cell_id": cell.cell_id,
                        "source_id": source_id,
                    }

    def _materialize_candidates(
        self, identifiers: tuple[t.Any, ...]
    ) -> tuple[t.Any | None, t.Any | None]:
        analyzed = [
            (position, result)
            for position, result in enumerate(self._results)
            if result._candidate_ids is not None
        ]
        if not analyzed:
            if not self._results:
                return (
                    self._empty_candidate_cells(),
                    self._empty_candidate_contributors(),
                )
            return None, None

        candidate_count = sum(
            len(result._candidate_ids or ()) for _, result in analyzed
        )
        self._budget.reserve(
            "max_live_bytes",
            128 * len(analyzed) + 192 * candidate_count,
            phase="apply.batch.candidates",
            units="candidate selection references",
        )
        selected = []
        for position, result in analyzed:
            candidate_ids = frozenset(result._candidate_ids or ())
            cells = tuple(
                cell
                for cell in result._lattice._require_exact().analysis.cells
                if cell.cell_id in candidate_ids
            )
            selected.append((position, result, cells))
        candidate_count = sum(len(cells) for _position, _result, cells in selected)
        edge_count = sum(
            len(cell.contributors)
            for _position, _result, cells in selected
            for cell in cells
        )
        cell_columns, contributor_columns = self._candidate_schemas()
        cell_output_bytes = relation_native_bytes(
            self._candidate_rows(identifiers, selected), cell_columns
        )
        contributor_output_bytes = relation_native_bytes(
            self._candidate_contributor_rows(identifiers, selected), contributor_columns
        )
        candidate_output_bytes = cell_output_bytes + contributor_output_bytes
        candidate_host_bytes = relation_host_bytes(
            self._candidate_rows(identifiers, selected), cell_columns
        ) + relation_host_bytes(
            self._candidate_contributor_rows(identifiers, selected),
            contributor_columns,
        )
        self._budget.reserve(
            "max_work",
            candidate_count + edge_count,
            phase="apply.batch.candidates",
            units="candidate rows and contributor edges",
        )
        self._budget.reserve(
            "max_contributor_edges",
            edge_count,
            phase="apply.batch.candidates",
            units="candidate contributor edges",
        )
        self._budget.reserve(
            "max_output_bytes",
            candidate_output_bytes,
            phase="apply.batch.candidates",
            units="returned candidate cells and contributor edges",
        )
        self._budget.reserve(
            "max_live_bytes",
            candidate_host_bytes + 3 * candidate_output_bytes,
            phase="apply.batch.candidates",
            units="candidate host and native materialization buffers",
        )
        cell_frames = []
        contributor_frames = []
        for position, result in analyzed:
            identity = self._context_ids.slice(position, 1)
            cells = result.candidate_cells
            contributors = result.candidate_contributors
            assert cells is not None and contributors is not None
            cell_frames.append(
                identity.cross_join(cells).select(
                    "__context_id",
                    *_CELL_IDENTITIES,
                    *[ma.col(field) for field in self._output_fields],
                )
            )
            contributor_frames.append(
                identity.cross_join(contributors).select(
                    "__context_id", "cell_id", "source_id"
                )
            )
        cells = (
            concat(cell_frames).collect()
            if cell_frames
            else self._empty_candidate_cells().collect()
        )
        contributors = (
            concat(contributor_frames).collect()
            if contributor_frames
            else self._empty_candidate_contributors().collect()
        )
        return relation(cells), relation(contributors)

    @property
    def context_ids(self) -> t.Any:
        return self._context_ids

    @property
    def context_id_field(self) -> str:
        return self._context_id_field

    @property
    def output_fields(self) -> tuple[str, ...]:
        return self._output_fields

    @property
    def records(self) -> Mapping[t.Any, OutcomeRecord]:
        return self._records

    @property
    def outcomes(self) -> t.Any:
        return self._outcomes

    @property
    def candidate_cells(self) -> t.Any | None:
        return self._candidate_cells

    @property
    def candidate_contributors(self) -> t.Any | None:
        return self._candidate_contributors

    def for_context(self, context_id: t.Any) -> AccumulatorResult:
        try:
            result = self._by_context_id[context_id]
        except (KeyError, TypeError) as exc:
            raise KeyError(context_id) from exc
        return result.raise_for_status()
