"""Shared helpers for the DecisionTable application tests (Plan 4)."""

from __future__ import annotations

import functools
import typing as t
from dataclasses import dataclass

import polars as pl
from pydantic import BaseModel, create_model

from mountainash_rules.decision_table.build import build_decision_table
from mountainash_rules.decision_table.table import DecisionTable
from mountainash_rules.engines.filter.engine import ExpressionRulesEngine

from tests.decision_table.construction_support import rules_relation, validated_rules
from tests.decision_table.oracles.finite_completion import FiniteCompletionOracle, contexts_over


def context_model(**fields: type) -> type[BaseModel]:
    """A protocol model: ``request_id`` plus the named fields, each optional and unset by default."""
    definitions = {name: (kind | None, None) for name, kind in fields.items()}
    return create_model("Context", request_id=(t.Any, ...), **definitions)


def build(backend_name: str, data: dict, metadata) -> DecisionTable:
    return build_decision_table(validated_rules(backend_name, data, metadata))


@functools.cache
def build_once(backend_name: str, rules: t.Callable[[], tuple[dict, t.Any]]) -> DecisionTable:
    """``build`` of ``rules()`` (a ``(data, metadata)`` pair), built once per session and backend.

    For tests that only read the table; one that spies on, mutates or tests the build builds its own.
    """
    data, metadata = rules()
    return build(backend_name, data, metadata)


def context_relation(backend_name: str, data: dict):
    """A context Relation on the requested backend."""
    return rules_relation(backend_name, data, name="contexts")


def sources_by_entry(table: DecisionTable) -> dict[tuple, frozenset]:
    """``{(scope, entry_sources_id): source uuids}`` from the table's entry_sources."""
    grouped: dict[tuple, set] = {}
    for row in table.entry_sources.to_dicts():
        grouped.setdefault((row["scope"], row["entry_sources_id"]), set()).add(row["source_uuid"])
    return {key: frozenset(value) for key, value in grouped.items()}


def fetch(table: DecisionTable, contexts, scopes=None) -> list[dict]:
    """Every match row of ``table.match(contexts, scopes).fetch()``."""
    return table.match(contexts, scopes).fetch().matches.to_dicts()


def matched_sources(table: DecisionTable, contexts, scopes=None) -> dict[t.Any, set[tuple]]:
    """``{request_id: {(scope, sources)}}`` of every matching entry; unmatched requests are absent."""
    sources = sources_by_entry(table)
    found: dict[t.Any, set[tuple]] = {}
    for row in fetch(table, contexts, scopes):
        found.setdefault(row["request_id"], set()).add(
            (row["scope"], sources[(row["scope"], row["entry_sources_id"])])
        )
    return found


def sources_of(table: DecisionTable, rows: t.Iterable[dict]) -> set[frozenset]:
    sources = sources_by_entry(table)
    return {sources[(row["scope"], row["entry_sources_id"])] for row in rows}


@dataclass
class EngineSpy:
    """Records the engine's construction and ``evaluate_batch`` calls."""

    constructed: list[dict]
    batches: list[dict]


def spy_on_engine(monkeypatch) -> EngineSpy:
    """Wrap ``ExpressionRulesEngine`` so every construction and ``evaluate_batch`` call is recorded."""
    spy = EngineSpy([], [])
    original_init = ExpressionRulesEngine.__init__
    original_batch = ExpressionRulesEngine.evaluate_batch

    def init(self, rules, dimension_metadata=None, *args, **kwargs):
        spy.constructed.append({"rules": rules, "metadata": dimension_metadata})
        original_init(self, rules, dimension_metadata, *args, **kwargs)

    def evaluate_batch(self, contexts, **kwargs):
        spy.batches.append({"contexts": contexts, **kwargs})
        return original_batch(self, contexts, **kwargs)

    monkeypatch.setattr(ExpressionRulesEngine, "__init__", init)
    monkeypatch.setattr(ExpressionRulesEngine, "evaluate_batch", evaluate_batch)
    return spy


# -- the finite-completion oracle at the application boundary ---------------------------------


def request_contexts(model: type[BaseModel], contexts: t.Sequence[dict]) -> list[BaseModel]:
    """Protocol objects for ``contexts``; a key a context lacks stays unset, ``None`` is explicit."""
    return [model(request_id=index, **context) for index, context in enumerate(contexts)]


def relation_batches(backend_name: str, contexts: t.Sequence[dict], dtypes: t.Mapping[str, t.Any]):
    """``[(Relation, [indices])]``: contexts sharing a key set become one Relation.

    A Relation cannot leave a field unset in some rows only, so contexts are grouped by the
    fields they set; a field a group omits is an absent column, an explicit ``None`` a null cell.
    ``dtypes`` types each field's column (a column of nulls has no type to infer).
    """
    groups: dict[tuple, list[int]] = {}
    for index, context in enumerate(contexts):
        groups.setdefault(tuple(sorted(context)), []).append(index)
    batches = []
    for keys, indices in groups.items():
        data = {
            "request_id": indices,
            **{key: pl.Series([contexts[i][key] for i in indices], dtype=dtypes[key]) for key in keys},
        }
        batches.append((context_relation(backend_name, pl.DataFrame(data)), indices))
    return batches


def check_application(
    table: DecisionTable,
    oracle: FiniteCompletionOracle,
    contexts: t.Sequence[dict],
    found_rows: t.Sequence[dict],
) -> None:
    """Assert match rows ``found_rows`` of ``contexts[request_id]`` agree with the oracle, per scope.

    The oracle judges by the authored rules (survivors) and by the same entries read through the
    engine with its own computation of the context outcomes; the application must return exactly
    those entries, each with its specificity and the output of its own sources (alternatives are
    never summed), and every reachable completion's source set must be found.
    """
    by_request: dict[int, list[dict]] = {}
    for row in found_rows:
        by_request.setdefault(row["request_id"], []).append(row)
    sources = sources_by_entry(table)
    for index, context in enumerate(contexts):
        rows = by_request.get(index, [])
        for scope in oracle.scopes:
            where = f"scope {scope!r}, context {context!r}"
            expected = oracle.table_matches(table, scope, context)
            got = [row for row in rows if row["scope"] == scope]
            assert sorted((row["entry_id"], row["specificity"]) for row in got) == sorted(
                (match.entry_id, match.specificity) for match in expected
            ), where
            names = [name for name, _ in expected[0].outputs] if expected else []
            for row in got:
                entry_sources = sources[(scope, row["entry_sources_id"])]
                folded = oracle.fold(scope, entry_sources)
                for name in names:
                    assert _same(row[name], folded[name]), f"{name} of entry {row['entry_id']}; {where}"
            covered = frozenset().union(*(sources[(scope, row["entry_sources_id"])] for row in got))
            assert covered == oracle.survivors(scope, context), where
            if oracle.exact_grid is not None and all(
                key not in context or context[key] in values for key, values in oracle.exact_grid.items()
            ):
                found = {sources[(scope, row["entry_sources_id"])] for row in got}
                for needed in oracle.reachable(scope, context):
                    assert needed in found, f"no entry has sources {sorted(needed, key=repr)}; {where}"


def _same(left, right) -> bool:
    from decimal import Decimal

    if isinstance(left, Decimal) or isinstance(right, Decimal):
        return left is not None and right is not None and Decimal(left) == Decimal(right)
    return left == right


def all_contexts(oracle: FiniteCompletionOracle) -> list[dict]:
    """Every context of the oracle's grid: each field omitted or set to a grid value."""
    return contexts_over(oracle.grid)
