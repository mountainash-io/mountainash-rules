"""Strict predicate-1 graph construction and dimension lowering.

This module is deliberately independent of dataframe backends.  Predicate IDs are
content IDs of the exact wire payload; the graph is only an interning and strict
validation boundary, never a source of sequential identities.
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Callable, Generator, Iterable, Mapping
from itertools import chain
from types import MappingProxyType
import typing as t
import weakref

from mountainash_rules import _native
from mountainash_rules.core.codec import (
    _materialize_json,
    canonical_bytes,
    content_id,
    decode_json,
    validate_id,
)
from mountainash_rules.core.constants import (
    NOT_SET,
    NOT_SET_DATE,
    NOT_SET_DATETIME,
    NOT_SET_NUMERIC,
    UNKNOWN,
    UNKNOWN_DATE,
    UNKNOWN_DATETIME,
    UNKNOWN_NUMERIC,
    DataType,
    MatchStrategy,
)
from mountainash_rules.core.contracts import (
    DomainField,
    ExactResourceError,
    OperationBudget,
)
from mountainash_rules.core.dimension import Dimension
from mountainash_rules.core.language import LanguageLimits, RegexOptions, StringLanguage
from mountainash_rules.core.scalar import (
    decode_scalar,
    encode_scalar,
    normalize_scalar,
    rank,
    rank_bounds,
    unrank,
)


_ORDERED = frozenset({DataType.INT, DataType.FLOAT, DataType.DATE, DataType.DATETIME})


def _freeze(value: t.Any) -> t.Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _is_unknown(value: t.Any, data_type: DataType) -> bool:
    return value == {
        DataType.STR: UNKNOWN,
        DataType.INT: UNKNOWN_NUMERIC,
        DataType.FLOAT: float(UNKNOWN_NUMERIC),
        DataType.DATE: UNKNOWN_DATE,
        DataType.DATETIME: UNKNOWN_DATETIME,
    }.get(data_type, object())


def _is_not_set(value: t.Any, data_type: DataType) -> bool:
    return value == {
        DataType.STR: NOT_SET,
        DataType.INT: NOT_SET_NUMERIC,
        DataType.FLOAT: float(NOT_SET_NUMERIC),
        DataType.DATE: NOT_SET_DATE,
        DataType.DATETIME: NOT_SET_DATETIME,
    }.get(data_type, object())


_NATIVE_STATE_BYTES = 128
_NATIVE_TRANSITION_BYTES = 64
_NATIVE_HANDLE_BYTES = 256
_NATIVE_METADATA_BYTES = 8
_NON_DFA_NATIVE_PHASES = frozenset(
    {
        "language_empty_check",
        "language_class_empty",
        "language_cardinality",
        "language_accepts",
    }
)
_NFA_NATIVE_PHASES = frozenset(
    {"regex_compile", "language_compile", "language_literal"}
)
_OUTPUT_NATIVE_PHASES = frozenset({"language_encode", "language_class_witness"})


def _native_workspace_bytes(limits: LanguageLimits, phase: str) -> int:
    """Return a conservative native/live metadata bound for one operation.

    ``compiler.rs`` stores up to ``max_nfa_states`` instructions, and
    determinization retains an ``Arc<[usize]>`` subset per DFA key.  Every
    subset element and split edge is charged to native work, so their bounded
    products are capped by ``max_work`` rather than multiplying two independent
    user ceilings into an unrepresentable reservation.
    """
    if phase in _OUTPUT_NATIVE_PHASES:
        return limits.max_input_bytes
    if phase in _NON_DFA_NATIVE_PHASES:
        return 0
    dfa = (
        limits.max_states * _NATIVE_STATE_BYTES
        + limits.max_transitions * _NATIVE_TRANSITION_BYTES
        + limits.max_input_bytes * _NATIVE_METADATA_BYTES
        + _NATIVE_HANDLE_BYTES
    )
    if phase not in _NFA_NATIVE_PHASES:
        return dfa
    subset_entries = min(limits.max_work, limits.max_states * limits.max_nfa_states)
    split_edges = min(limits.max_work, limits.max_nfa_states * limits.max_nfa_states)
    return (
        dfa
        + limits.max_nfa_states * _NATIVE_STATE_BYTES
        + subset_entries * 16
        + split_edges * 8
    )


class _ScopedLanguage(StringLanguage):
    """A weak-referenceable private handle wrapper for scope accounting."""

    __slots__ = ("__weakref__",)


class _NativeScope:
    """Own native temporaries, releasing overwritten handles at collection."""

    def __init__(self, budget: OperationBudget) -> None:
        self._budget = budget
        self._holds: list[list[t.Any]] = []
        self._outputs: list[tuple[bytes | str, list[t.Any]]] = []
        self._languages: dict[int, dict[str, t.Any]] = {}

    def reserve(self, counter: str, amount: int, phase: str, units: str) -> list[t.Any]:
        self._budget.reserve(counter, amount, phase=phase, units=units)
        hold: list[t.Any] = [counter, amount]
        self._holds.append(hold)
        return hold

    def release_holds(self, holds: Iterable[list[t.Any]]) -> None:
        for hold in reversed(tuple(holds)):
            if hold in self._holds:
                self._holds.remove(hold)
                self._budget.release(t.cast(str, hold[0]), t.cast(int, hold[1]))

    def own_output(self, value: bytes | str, hold: list[t.Any]) -> None:
        self._outputs.append((value, hold))

    def take_output(self, value: bytes | str) -> list[t.Any]:
        for index in range(len(self._outputs) - 1, -1, -1):
            candidate, hold = self._outputs[index]
            if candidate is value:
                self._outputs.pop(index)
                return hold
        raise ValueError("native output is not scope-owned")

    def release_output(self, value: bytes | str) -> None:
        self.release_holds((self.take_output(value),))

    def own_language(
        self,
        language: _ScopedLanguage,
        holds: list[list[t.Any]],
        raw: bytes,
        raw_hold: list[t.Any],
        identifier: str,
    ) -> None:
        scope_ref = weakref.ref(self)
        language_id = id(language)

        def collected(_: weakref.ReferenceType[_ScopedLanguage]) -> None:
            scope = scope_ref()
            if scope is not None:
                scope._release_language(language_id)

        self._languages[language_id] = {
            "reference": weakref.ref(language, collected),
            "holds": holds,
            "raw": raw,
            "raw_hold": raw_hold,
            "identifier": identifier,
        }

    def language_raw(self, language: StringLanguage) -> bytes:
        try:
            raw = self._languages[id(language)]["raw"]
        except KeyError as exc:
            raise ValueError("native language is not scope-owned") from exc
        if raw is None:
            raise ValueError("native language payload has already been released")
        return t.cast(bytes, raw)

    def release_language_raw(self, language: StringLanguage) -> None:
        record = self._languages.get(id(language))
        if record is not None and record["raw"] is not None:
            record["raw"] = None
            raw_hold = t.cast(list[t.Any], record["raw_hold"])
            record["holds"].remove(raw_hold)
            self.release_holds((raw_hold,))

    def promote_language(self, language: StringLanguage, identifier: str) -> bool:
        record = self._languages.get(id(language))
        if record is None:
            return False
        if record["identifier"] != identifier:
            raise ValueError("registered language ID disagrees with native payload")
        self.release_language_raw(language)
        self._languages.pop(id(language))
        for hold in record["holds"]:
            self._holds.remove(hold)
        return True

    def _release_language(self, language_id: int) -> None:
        record = self._languages.pop(language_id, None)
        if record is not None:
            self.release_holds(record["holds"])

    def release(self) -> None:
        for value, _ in tuple(self._outputs):
            self.release_output(value)
        for language_id in tuple(self._languages):
            self._release_language(language_id)
        self.release_holds(tuple(self._holds))


class PredicateGraph:
    """A bounded, immutable-payload predicate-1 DAG for one physical schema."""

    def __init__(
        self, fields: Iterable[DomainField], *, budget: OperationBudget
    ) -> None:
        if not isinstance(budget, OperationBudget):
            raise TypeError("budget must be OperationBudget")
        by_name: dict[str, DomainField] = {}
        for field in fields:
            if not isinstance(field, DomainField):
                raise TypeError("fields must contain DomainField values")
            if field.name in by_name:
                raise ValueError(f"Duplicate physical field {field.name!r}")
            budget.reserve(
                "max_live_bytes",
                256 + 4 * len(field.name),
                phase="predicate_schema",
                units="retained field bytes",
            )
            by_name[field.name] = field
        self._fields = MappingProxyType(dict(sorted(by_name.items())))
        self._nodes: dict[str, Mapping[str, t.Any]] = {}
        self._nodes_view = MappingProxyType(self._nodes)
        self._languages: dict[str, StringLanguage] = {}
        self.budget = budget
        self._native_scopes: list[_NativeScope] = []
        self.true = self._intern({"op": "true"})
        self.false = self._intern({"op": "false"})

    @property
    def fields(self) -> Mapping[str, DomainField]:
        return self._fields

    @property
    def nodes(self) -> Mapping[str, Mapping[str, t.Any]]:
        """Read-only predicate-ID to immutable operator mapping.

        The ``schema_version``/``node`` payload wrapper remains the hash and
        wire boundary; callers rebuilding syntax consume the operator itself.
        """
        return self._nodes_view

    @property
    def languages(self) -> Mapping[str, StringLanguage]:
        return MappingProxyType(self._languages)

    @property
    def language_limits(self) -> LanguageLimits:
        return self.budget.limits.language

    def _reserve(self, counter: str, amount: int, phase: str, units: str) -> None:
        self.budget.reserve(counter, amount, phase=phase, units=units)

    def _intern(self, node: Mapping[str, t.Any]) -> str:
        from mountainash_rules.core.codec import _bounded_json_size, _content_id_bytes

        bound = 64 + _bounded_json_size(
            node, self.budget, phase="predicate_graph", counter="max_live_bytes"
        )
        workspace = 512 + 8 * bound
        self._reserve(
            "max_live_bytes", workspace, "predicate_graph", "canonical workspace bytes"
        )
        try:
            payload = {"schema_version": 1, "node": dict(node)}
            raw = canonical_bytes(payload)
            identifier = _content_id_bytes("predicate", raw)
            if identifier not in self._nodes:
                self._reserve("max_predicate_nodes", 1, "predicate_graph", "nodes")
                retained = False
                try:
                    self._reserve(
                        "max_live_bytes",
                        workspace,
                        "predicate_graph",
                        "immutable node bytes",
                    )
                    retained = True
                    self._nodes[identifier] = _freeze(payload["node"])
                except BaseException:
                    self.budget.release("max_predicate_nodes", 1)
                    if retained:
                        self.budget.release("max_live_bytes", workspace)
                    raise
            return identifier
        finally:
            self.budget.release("max_live_bytes", workspace)

    def _field(self, name: str) -> DomainField:
        if not isinstance(name, str) or name not in self._fields:
            raise ValueError(f"Unknown field {name!r}")
        return self._fields[name]

    def _scalar(self, field: str, value: t.Any) -> dict[str, t.Any]:
        declaration = self._field(field)
        return encode_scalar(
            value, declaration.data_type, timezone=declaration.timezone
        )

    def _id(self, identifier: str) -> Mapping[str, t.Any]:
        validate_id(identifier, "predicate")
        try:
            return self._nodes[identifier]
        except KeyError as exc:
            raise ValueError(f"Unresolved predicate reference {identifier}") from exc

    def add(self, payload: Mapping[str, t.Any]) -> str:
        """Add one strict node, validating references before simplification."""
        if not isinstance(payload, Mapping):
            raise TypeError("predicate payload must be a mapping")
        if "schema_version" in payload or "node" in payload:
            if (
                set(payload) != {"schema_version", "node"}
                or type(payload["schema_version"]) is not int
                or payload["schema_version"] != 1
                or not isinstance(payload["node"], Mapping)
            ):
                raise ValueError("predicate payload must be predicate-1")
            node = dict(payload["node"])
        else:
            node = dict(payload)
        return self._add_node(node, simplify=True)

    def _add_node(self, node: dict[str, t.Any], *, simplify: bool) -> str:
        op = node.get("op")
        if op in {"true", "false"}:
            if set(node) != {"op"}:
                raise ValueError("constant predicate has unknown fields")
            return self._intern(node)
        if op in {"and", "or"}:
            if (
                set(node) != {"op", "args"}
                or not isinstance(node["args"], (list, tuple))
                or not node["args"]
            ):
                raise ValueError("predicate args must be a non-empty array")
            args = [self._id(item) and item for item in node["args"]]
            if not simplify:
                return self._intern({"op": op, "args": list(args)})
            flattened: list[str] = []
            for item in args:
                child = self._nodes[item]
                flattened.extend(child["args"] if child["op"] == op else (item,))
            identity, annihilator = (
                (self.true, self.false) if op == "and" else (self.false, self.true)
            )
            kept = {item for item in flattened if item != identity}
            if annihilator in kept:
                return annihilator
            for item in tuple(kept):
                child = self._nodes[item]
                if child["op"] == "not" and child["arg"] in kept:
                    return annihilator
            ordered = sorted(
                kept, key=lambda item: canonical_bytes(dict(self._nodes[item]))
            )
            if not ordered:
                return identity
            if len(ordered) == 1:
                return ordered[0]
            return self._intern({"op": op, "args": ordered})
        if op == "not":
            if set(node) != {"op", "arg"}:
                raise ValueError("not predicate has unknown fields")
            arg = node["arg"]
            child = self._id(arg)
            if not simplify:
                return self._intern({"op": "not", "arg": arg})
            if arg == self.true:
                return self.false
            if arg == self.false:
                return self.true
            if child["op"] == "not":
                return child["arg"]
            return self._intern({"op": "not", "arg": arg})
        if op == "eq":
            if set(node) != {"op", "field", "value"}:
                raise ValueError("eq predicate has unknown fields")
            field = node["field"]
            declaration = self._field(field)
            value = decode_scalar(node["value"])
            if node["value"] != encode_scalar(
                value, declaration.data_type, timezone=declaration.timezone
            ):
                raise ValueError("eq value does not agree with field type")
            if declaration.data_type in _ORDERED:
                value_rank = rank(
                    value, declaration.data_type, timezone=declaration.timezone
                )
                return self._interval_ranks(field, value_rank, value_rank)
            return self._intern({"op": "eq", "field": field, "value": node["value"]})
        if op == "in":
            if set(node) != {"op", "field", "values"} or not isinstance(
                node["values"], (list, tuple)
            ):
                raise ValueError("in predicate has invalid fields")
            field = node["field"]
            declaration = self._field(field)
            encoded = [
                encode_scalar(
                    decode_scalar(value),
                    declaration.data_type,
                    timezone=declaration.timezone,
                )
                for value in node["values"]
            ]
            if list(node["values"]) != encoded:
                raise ValueError("in values are not canonical field scalars")
            return self._in_encoded(field, encoded, simplify=simplify)
        if op == "interval":
            required = {"op", "field", "lower", "upper", "lower_closed", "upper_closed"}
            if (
                set(node) != required
                or type(node["lower_closed"]) is not bool
                or type(node["upper_closed"]) is not bool
            ):
                raise ValueError("interval has invalid fields")
            field = node["field"]
            declaration = self._field(field)
            if declaration.data_type not in _ORDERED:
                raise ValueError("interval requires an ordered field")
            lower, upper = node["lower"], node["upper"]
            if (
                lower is None
                and node["lower_closed"]
                or upper is None
                and node["upper_closed"]
            ):
                raise ValueError("unbounded interval side must be open")
            return self._interval_native(
                field,
                lower,
                upper,
                node["lower_closed"],
                node["upper_closed"],
                authored=not simplify,
            )
        if op == "language":
            if set(node) != {"op", "field", "language_id"}:
                raise ValueError("language predicate has unknown fields")
            if self._field(node["field"]).data_type is not DataType.STR:
                raise ValueError("language requires a str field")
            validate_id(node["language_id"], "language")
            if node["language_id"] not in self._languages:
                raise ValueError("Unresolved language reference")
            return self._intern(
                {
                    "op": "language",
                    "field": node["field"],
                    "language_id": node["language_id"],
                }
            )
        if op == "compare":
            if set(node) != {"op", "left", "right", "relation"} or node[
                "relation"
            ] not in {"eq", "ne", "lt", "le", "gt", "ge"}:
                raise ValueError("compare has invalid fields")
            left, right, relation = node["left"], node["right"], node["relation"]
            left_field, right_field = self._field(left), self._field(right)
            if (
                left_field.data_type is not right_field.data_type
                or left_field.timezone != right_field.timezone
            ):
                raise ValueError("compare fields must have identical type and timezone")
            if (
                relation in {"lt", "le", "gt", "ge"}
                and left_field.data_type not in _ORDERED
            ):
                raise ValueError("ordered compare requires ordered fields")
            if left == right:
                return self.true if relation in {"eq", "le", "ge"} else self.false
            if relation in {"eq", "ne"} and right < left:
                left, right = right, left
            elif relation in {"lt", "le"}:
                left, right = right, left
                relation = "gt" if relation == "lt" else "ge"
            return self._intern(
                {"op": "compare", "left": left, "right": right, "relation": relation}
            )
        raise ValueError("Unknown predicate op")

    def and_(self, *predicate_ids: str) -> str:
        return (
            self._add_node({"op": "and", "args": list(predicate_ids)}, simplify=True)
            if predicate_ids
            else self.true
        )

    def or_(self, *predicate_ids: str) -> str:
        return (
            self._add_node({"op": "or", "args": list(predicate_ids)}, simplify=True)
            if predicate_ids
            else self.false
        )

    def not_(self, predicate_id: str) -> str:
        return self._add_node({"op": "not", "arg": predicate_id}, simplify=True)

    def eq(self, field: str, native_scalar: t.Any) -> str:
        declaration = self._field(field)
        encoded = encode_scalar(
            native_scalar, declaration.data_type, timezone=declaration.timezone
        )
        return self._add_node(
            {"op": "eq", "field": field, "value": encoded}, simplify=True
        )

    def _in_encoded(
        self, field: str, encoded: list[dict[str, t.Any]], *, simplify: bool
    ) -> str:
        declaration = self._field(field)
        unique: dict[bytes, dict[str, t.Any]] = {}
        for value in encoded:
            scalar = decode_scalar(value)
            normal = encode_scalar(
                scalar, declaration.data_type, timezone=declaration.timezone
            )
            if normal != value:
                raise ValueError("in value is not canonical")
            unique[canonical_bytes({"value": value})] = value
        values = [unique[key] for key in sorted(unique)]
        if not values:
            return self.false
        if len(values) == 1:
            return self._add_node(
                {"op": "eq", "field": field, "value": values[0]}, simplify=simplify
            )
        return self._intern({"op": "in", "field": field, "values": values})

    def in_(self, field: str, native_scalars: Iterable[t.Any]) -> str:
        if isinstance(native_scalars, (str, bytes)):
            raise TypeError("in values must be a scalar collection")
        return self._in_encoded(
            field,
            [self._scalar(field, value) for value in native_scalars],
            simplify=True,
        )

    def _interval_ranks(self, field: str, lower: int | None, upper: int | None) -> str:
        declaration = self._field(field)
        minimum, maximum = rank_bounds(
            declaration.data_type, timezone=declaration.timezone
        )
        lo, hi = (
            minimum if lower is None else lower,
            maximum if upper is None else upper,
        )
        if lo > hi or hi < minimum or lo > maximum:
            return self.false
        lo, hi = max(lo, minimum), min(hi, maximum)
        if lo == minimum and hi == maximum:
            return self.true
        return self._intern(
            {
                "op": "interval",
                "field": field,
                "lower": None
                if lo == minimum
                else encode_scalar(
                    unrank(lo, declaration.data_type, timezone=declaration.timezone),
                    declaration.data_type,
                    timezone=declaration.timezone,
                ),
                "upper": None
                if hi == maximum
                else encode_scalar(
                    unrank(hi, declaration.data_type, timezone=declaration.timezone),
                    declaration.data_type,
                    timezone=declaration.timezone,
                ),
                "lower_closed": lo != minimum,
                "upper_closed": hi != maximum,
            }
        )

    def _interval_native(
        self,
        field: str,
        lower: t.Any,
        upper: t.Any,
        lower_closed: bool,
        upper_closed: bool,
        *,
        authored: bool,
    ) -> str:
        declaration = self._field(field)

        def endpoint(value: t.Any) -> int | None:
            if value is None:
                return None
            decoded = (
                decode_scalar(dict(value)) if isinstance(value, Mapping) else value
            )
            return rank(decoded, declaration.data_type, timezone=declaration.timezone)

        lo, hi = endpoint(lower), endpoint(upper)
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(
                "reversed interval bounds"
                if authored
                else "interval bounds are reversed"
            )
        if lo is not None and not lower_closed:
            lo += 1
        if hi is not None and not upper_closed:
            hi -= 1
        return self._interval_ranks(field, lo, hi)

    def interval(
        self,
        field: str,
        lower: t.Any,
        upper: t.Any,
        *,
        lower_closed: bool,
        upper_closed: bool,
    ) -> str:
        self._field(field)
        return self._interval_native(
            field, lower, upper, lower_closed, upper_closed, authored=True
        )

    def compare(self, left: str, right: str, relation: str) -> str:
        return self._add_node(
            {"op": "compare", "left": left, "right": right, "relation": relation},
            simplify=True,
        )

    @contextmanager
    def native_scope(self) -> Generator[None, None, None]:
        """Own native intermediates for one lexical reasoner/graph operation.

        A registered graph language is promoted to graph ownership. Temporary
        DFA wrappers release actual reservations when overwritten; bytes and
        strings remain scope-owned until exit. Callers must not retain an
        unregistered language beyond its scope.
        """
        scope = _NativeScope(self.budget)
        self._native_scopes.append(scope)
        try:
            yield
        finally:
            if self._native_scopes.pop() is not scope:
                raise RuntimeError("native scopes must exit in stack order")
            scope.release()

    def _active_native_scope(self) -> _NativeScope:
        if not self._native_scopes:
            raise RuntimeError("native operation requires an explicit native_scope")
        return self._native_scopes[-1]

    def _native_returns_dfa(self, phase: str) -> bool:
        return phase not in _NON_DFA_NATIVE_PHASES | _OUTPUT_NATIVE_PHASES

    def _native_resource_error(
        self, phase: str, exc: _native.LanguageResourceError
    ) -> ExactResourceError:
        resource = exc.resource
        counter = {
            "input_bytes": "max_input_bytes",
            "states": "max_dfa_states",
            "transitions": "max_dfa_transitions",
            "work": "max_work",
        }.get(resource, f"native_{resource}")
        return ExactResourceError(
            operation=self.budget.operation,
            phase=phase,
            counter=counter,
            limit=exc.limit,
            observed=0,
            requested=exc.limit + 1,
            units=f"native {resource} units (minimum required)",
        )

    def _native_call(self, phase: str, callback: Callable[[], t.Any]) -> t.Any:
        scope = self._active_native_scope()
        limits = self.language_limits
        dfa_result = self._native_returns_dfa(phase)
        workspace = _native_workspace_bytes(limits, phase)
        if dfa_result:
            self.budget.check(
                "max_dfa_states",
                limits.max_states,
                phase=phase,
                units="native DFA state capacity",
            )
            self.budget.check(
                "max_dfa_transitions",
                limits.max_transitions,
                phase=phase,
                units="native DFA transition capacity",
            )
        self.budget.check(
            "max_live_bytes",
            workspace,
            phase=phase,
            units="native workspace capacity bytes",
        )
        self.budget.check(
            "max_work", limits.max_work, phase=phase, units="native maximum work units"
        )
        if phase in _OUTPUT_NATIVE_PHASES:
            self.budget.check(
                "max_output_bytes",
                limits.max_input_bytes,
                phase=phase,
                units="native output capacity bytes",
            )
        workspace_holds: list[list[t.Any]] = []
        try:
            if dfa_result:
                workspace_holds.extend(
                    (
                        scope.reserve(
                            "max_dfa_states",
                            limits.max_states,
                            phase,
                            "native DFA state capacity",
                        ),
                        scope.reserve(
                            "max_dfa_transitions",
                            limits.max_transitions,
                            phase,
                            "native DFA transition capacity",
                        ),
                    )
                )
            if workspace:
                workspace_holds.append(
                    scope.reserve(
                        "max_live_bytes",
                        workspace,
                        phase,
                        "native workspace capacity bytes",
                    )
                )
            self._reserve(
                "max_work", limits.max_work, phase, "native maximum work units"
            )
            try:
                result = callback()
            except _native.LanguageResourceError as exc:
                raise self._native_resource_error(phase, exc) from exc
            if isinstance(result, StringLanguage):
                if not dfa_result:
                    raise RuntimeError(
                        f"native phase {phase!r} returned an unreserved DFA"
                    )
                language = _ScopedLanguage._from_native(result._handle)
                raw = self._native_call(
                    "language_encode",
                    lambda: language.to_json(limits=self.language_limits),
                )
                raw_hold = scope.take_output(raw)
                try:
                    payload = decode_json(raw)
                    if not isinstance(payload, dict):
                        raise ValueError("language payload must be an object")
                    identifier = content_id("language", payload)
                    states, transitions, live = self._language_footprint(payload)
                    scope.release_holds(workspace_holds)
                    workspace_holds.clear()
                    holds = [
                        scope.reserve("max_dfa_states", states, phase, "DFA states"),
                        scope.reserve(
                            "max_dfa_transitions", transitions, phase, "DFA transitions"
                        ),
                        scope.reserve(
                            "max_live_bytes", live, phase, "native DFA handle bytes"
                        ),
                        raw_hold,
                    ]
                    scope.own_language(language, holds, raw, raw_hold, identifier)
                    return language
                except Exception:
                    scope.release_holds((raw_hold,))
                    raise
            if isinstance(result, bytes | str):
                if phase not in _OUTPUT_NATIVE_PHASES:
                    raise RuntimeError(
                        f"native phase {phase!r} returned an unreserved output buffer"
                    )
                encoded_size = (
                    len(result)
                    if isinstance(result, bytes)
                    else len(result.encode("utf-8"))
                )
                self._reserve(
                    "max_output_bytes", encoded_size, phase, "native output bytes"
                )
                scope.release_holds(workspace_holds)
                workspace_holds.clear()
                hold = scope.reserve(
                    "max_live_bytes", encoded_size, phase, "native output buffer bytes"
                )
                scope.own_output(result, hold)
            return result
        finally:
            scope.release_holds(workspace_holds)

    def _encoded_language_payload(
        self, payload: Mapping[str, t.Any], *, charge_input: bool = True
    ) -> bytes:
        from mountainash_rules.core.codec import _bounded_json_size

        maximum = _bounded_json_size(
            payload,
            self.budget,
            phase="language_decode",
            counter="max_input_bytes" if charge_input else "max_live_bytes",
        )
        scope = self._active_native_scope()
        hold = scope.reserve(
            "max_live_bytes",
            maximum,
            "language_decode",
            "canonical language buffer capacity bytes",
        )
        try:
            raw = canonical_bytes(payload)
        finally:
            scope.release_holds((hold,))
        if charge_input:
            self._reserve(
                "max_input_bytes",
                len(raw),
                "language_decode",
                "canonical language bytes",
            )
        raw_hold = scope.reserve(
            "max_live_bytes",
            len(raw),
            "language_decode",
            "canonical language buffer bytes",
        )
        scope.own_output(raw, raw_hold)
        return raw

    @staticmethod
    def _language_footprint(payload: Mapping[str, t.Any]) -> tuple[int, int, int]:
        transitions = payload.get("transitions")
        accepting = payload.get("accepting")
        start = payload.get("start")
        if (
            not isinstance(transitions, list)
            or not isinstance(accepting, list)
            or type(start) is not int
        ):
            raise ValueError("language payload has invalid graph fields")
        states = {start, *accepting}
        for transition in transitions:
            if (
                not isinstance(transition, list)
                or len(transition) != 4
                or type(transition[0]) is not int
                or type(transition[3]) is not int
            ):
                raise ValueError("language payload has invalid transition")
            states.add(transition[0])
            states.add(transition[3])
        state_count, transition_count = len(states), len(transitions)
        return (
            state_count,
            transition_count,
            state_count * _NATIVE_STATE_BYTES
            + transition_count * _NATIVE_TRANSITION_BYTES
            + _NATIVE_HANDLE_BYTES,
        )

    def _register_language(
        self, identifier: str, language: StringLanguage, payload: Mapping[str, t.Any]
    ) -> None:
        if identifier in self._languages:
            return
        for scope in reversed(self._native_scopes):
            if scope.promote_language(language, identifier):
                self._languages[identifier] = language
                return
        states, transitions, live = self._language_footprint(payload)
        self._reserve("max_dfa_states", states, "language_graph", "DFA states")
        self._reserve(
            "max_dfa_transitions", transitions, "language_graph", "DFA transitions"
        )
        self._reserve(
            "max_live_bytes", live, "language_graph", "native DFA handle bytes"
        )
        self._languages[identifier] = language

    def add_language(self, bare_payload: Mapping[str, t.Any] | StringLanguage) -> str:
        with self.native_scope():
            scope = self._active_native_scope()
            if isinstance(bare_payload, StringLanguage):
                language = bare_payload
                raw_scope: _NativeScope | None = None
                for candidate in reversed(self._native_scopes):
                    try:
                        raw = candidate.language_raw(language)
                    except ValueError:
                        continue
                    raw_scope = candidate
                    break
                if raw_scope is None:
                    raw = self._native_call(
                        "language_encode",
                        lambda: language.to_json(limits=self.language_limits),
                    )
                    try:
                        payload = decode_json(raw)
                        if not isinstance(payload, dict):
                            raise ValueError("language payload must be an object")
                    finally:
                        scope.release_output(raw)
                else:
                    try:
                        payload = decode_json(raw)
                        if not isinstance(payload, dict):
                            raise ValueError("language payload must be an object")
                    finally:
                        raw_scope.release_language_raw(language)
            elif isinstance(bare_payload, Mapping):
                raw = self._encoded_language_payload(bare_payload)
                try:
                    language = self._native_call(
                        "language_decode",
                        lambda: StringLanguage.from_json(
                            raw, limits=self.language_limits
                        ),
                    )
                finally:
                    scope.release_output(raw)
                canonical_raw = scope.language_raw(language)
                try:
                    payload = decode_json(canonical_raw)
                    if payload != dict(bare_payload):
                        raise ValueError("language payload is not canonical")
                    if not isinstance(payload, dict):
                        raise ValueError("language payload must be an object")
                finally:
                    scope.release_language_raw(language)
            else:
                raise TypeError(
                    "language must be StringLanguage or bare language payload"
                )
            identifier = content_id("language", payload)
            self._register_language(identifier, language, payload)
            return identifier

    def language(self, field: str, language_id: str) -> str:
        return self._add_node(
            {"op": "language", "field": field, "language_id": language_id},
            simplify=True,
        )

    def lower_dimension(
        self,
        dimension: Dimension,
        row: Mapping[str, t.Any],
        *,
        regex_options: RegexOptions | None,
    ) -> str:
        """Lower exactly one authoring dimension after validating every source input."""
        if not isinstance(dimension, Dimension) or not isinstance(row, Mapping):
            raise TypeError("dimension and row must be Dimension and mapping")
        field = dimension.resolved_context_field
        declaration = self._field(field)
        if declaration.data_type is not dimension.data_type:
            raise ValueError("dimension and physical field data types disagree")
        strategy = dimension.match_strategy
        if strategy is MatchStrategy.RANGE:
            required = (
                t.cast(str, dimension.range_min_field),
                t.cast(str, dimension.range_max_field),
            )
        elif strategy is MatchStrategy.CONTEXT_REGEX:
            required = ()
        else:
            required = (dimension.resolved_rule_field,)
        for column in required:
            if column not in row:
                raise ValueError(f"Missing source column {column!r}")
        if (
            strategy in {MatchStrategy.REGEX, MatchStrategy.CONTEXT_REGEX}
            and regex_options is None
        ):
            raise ValueError("regex_options is required for regex lowering")
        if regex_options is not None and not isinstance(regex_options, RegexOptions):
            raise TypeError("regex_options must be RegexOptions")
        dtype = declaration.data_type

        def scalar(value: t.Any) -> tuple[bool, t.Any]:
            if value is None:
                if dtype is DataType.BOOL:
                    return True, None
                raise ValueError("null scalar source value is invalid")
            value = normalize_scalar(
                value, dtype, timezone=declaration.timezone, allow_reserved=True
            )
            marker = value.replace(tzinfo=None) if dtype is DataType.DATETIME else value
            if _is_not_set(marker, dtype):
                raise ValueError("NOT_SET is invalid in source conditions")
            if dtype is not DataType.BOOL and _is_unknown(marker, dtype):
                return True, None
            return False, value

        if strategy is MatchStrategy.CONTEXT_REGEX:
            with self.native_scope():
                language = self._native_call(
                    "regex_compile",
                    lambda: StringLanguage.regex(
                        t.cast(str, dimension.regex_pattern),
                        limits=self.language_limits,
                        options=t.cast(RegexOptions, regex_options),
                    ),
                )
                return self.language(field, self.add_language(language))
        if strategy is MatchStrategy.RANGE:
            low_top, low = scalar(row[required[0]])
            high_top, high = scalar(row[required[1]])
            return self.interval(
                field,
                None if low_top else low,
                None if high_top else high,
                lower_closed=dimension.range_min_inclusive,
                upper_closed=dimension.range_max_inclusive,
            )
        value = row[required[0]]
        if strategy in {MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION}:
            if value is None:
                return self.true
            if not isinstance(value, (list, tuple)):
                raise ValueError("set source value must be a list or null")
            wildcard = False
            concrete: list[t.Any] = []
            for item in value:
                if item is None:
                    raise ValueError("set source values cannot contain null elements")
                top, item = scalar(item)
                if top:
                    wildcard = True
                else:
                    concrete.append(item)
            if wildcard and concrete:
                raise ValueError(
                    "reserved wildcard marker cannot mix with business set elements"
                )
            if wildcard:
                return self.true
            result = self.in_(field, concrete)
            return (
                self.not_(result) if strategy is MatchStrategy.SET_EXCLUSION else result
            )
        top, value = scalar(value)
        if top:
            return self.true
        if strategy in {MatchStrategy.EXACT, MatchStrategy.EXACT_KEY}:
            return self.eq(field, value)
        if strategy is MatchStrategy.NOT_EQUAL:
            return self.not_(self.eq(field, value))
        if strategy is MatchStrategy.GREATER_THAN:
            return self.interval(
                field, value, None, lower_closed=False, upper_closed=False
            )
        if strategy is MatchStrategy.LESS_THAN:
            return self.interval(
                field, None, value, lower_closed=False, upper_closed=False
            )
        constructors = {
            MatchStrategy.PREFIX: StringLanguage.prefix,
            MatchStrategy.SUFFIX: StringLanguage.suffix,
            MatchStrategy.CONTAINS: StringLanguage.contains,
            MatchStrategy.REGEX: lambda text, *, limits: StringLanguage.regex(
                text, limits=limits, options=t.cast(RegexOptions, regex_options)
            ),
        }
        try:
            constructor = constructors[strategy]
        except KeyError as exc:
            raise ValueError(f"Unsupported strategy {strategy.value}") from exc
        with self.native_scope():
            language = self._native_call(
                "language_compile",
                lambda: constructor(value, limits=self.language_limits),
            )
            return self.language(field, self.add_language(language))

    def decode(
        self,
        predicate_envelopes: Iterable[Mapping[str, t.Any]],
        language_envelopes: Iterable[Mapping[str, t.Any]],
    ) -> None:
        """Strictly decode a complete received graph before accepting any node."""
        live = 0

        def reserve_live(amount: int) -> None:
            nonlocal live
            self.budget.reserve(
                "max_live_bytes", amount, phase="predicate_decode", units="bytes"
            )
            live += amount

        def admitted(
            envelopes: Iterable[Mapping[str, t.Any]],
        ) -> Iterable[Mapping[str, t.Any]]:
            for envelope in envelopes:
                reserve_live(256)
                immutable_containers = False
                stack = [(iter((envelope,)), None)]
                active: set[int] = set()
                while stack:
                    iterator, parent = stack[-1]
                    try:
                        value = next(iterator)
                    except StopIteration:
                        stack.pop()
                        if parent is not None:
                            active.remove(parent)
                        continue
                    self.budget.reserve(
                        "max_work", 1, phase="predicate_decode", units="input values"
                    )
                    if isinstance(value, (Mapping, list, tuple)):
                        immutable_containers |= isinstance(value, tuple) or (
                            isinstance(value, Mapping) and not isinstance(value, dict)
                        )
                        identity = id(value)
                        if identity in active:
                            raise ValueError("cyclic JSON input")
                        self.budget.reserve(
                            "max_input_bytes",
                            2 + 2 * len(value),
                            phase="predicate_decode",
                            units="maximum encoded bytes",
                        )
                        reserve_live(256 + 128 * len(value))
                        active.add(identity)
                        children = (
                            chain.from_iterable(value.items())
                            if isinstance(value, Mapping)
                            else iter(value)
                        )
                        stack.append((children, identity))
                    elif isinstance(value, str):
                        size = 2 + 6 * len(value)
                        self.budget.reserve(
                            "max_input_bytes",
                            size,
                            phase="predicate_decode",
                            units="maximum encoded bytes",
                        )
                        reserve_live(128 + 4 * size)
                    elif value is None or type(value) in {int, bool}:
                        size = 5 if type(value) is not int else 2 + value.bit_length()
                        self.budget.reserve(
                            "max_input_bytes",
                            size,
                            phase="predicate_decode",
                            units="maximum encoded bytes",
                        )
                        reserve_live(64 + size)
                    else:
                        raise ValueError(
                            "predicate graph input must contain JSON values"
                        )
                # The preflight above reserves the received tree before any copy.
                # Mutable JSON already has the decoder's native container shape.
                yield _materialize_json(envelope) if immutable_containers else envelope

        try:
            reserve_live(4096 + 128 * (len(self._nodes) + len(self._languages)))
            with self.native_scope():
                self._decode(
                    admitted(predicate_envelopes), admitted(language_envelopes)
                )
        finally:
            self.budget.release("max_live_bytes", live)

    def _decode(
        self,
        predicates: Iterable[Mapping[str, t.Any]],
        languages: Iterable[Mapping[str, t.Any]],
    ) -> None:
        incoming_languages: dict[str, tuple[StringLanguage, dict[str, t.Any]]] = {}
        for envelope in languages:
            if not isinstance(envelope, Mapping) or set(envelope) != {"id", "payload"}:
                raise ValueError("language envelope must contain id and payload")
            identifier, payload = envelope["id"], envelope["payload"]
            validate_id(identifier, "language")
            if (
                not isinstance(payload, Mapping)
                or content_id("language", dict(payload)) != identifier
            ):
                raise ValueError("language envelope ID/payload mismatch")
            scope = self._active_native_scope()
            raw = self._encoded_language_payload(payload, charge_input=False)
            try:
                language = self._native_call(
                    "language_decode",
                    lambda raw=raw: StringLanguage.from_json(
                        raw, limits=self.language_limits
                    ),
                )
            finally:
                scope.release_output(raw)
            canonical_raw = scope.language_raw(language)
            try:
                canonical = decode_json(canonical_raw)
                if canonical != dict(payload):
                    raise ValueError("language payload is not canonical")
                if not isinstance(canonical, dict):
                    raise ValueError("language payload must be an object")
            finally:
                scope.release_language_raw(language)
            if identifier in incoming_languages:
                raise ValueError("duplicate language envelope")
            incoming_languages[identifier] = language, canonical
        raw_nodes: dict[str, dict[str, t.Any]] = {}
        for envelope in predicates:
            if not isinstance(envelope, Mapping) or set(envelope) != {"id", "payload"}:
                raise ValueError("predicate envelope must contain id and payload")
            identifier, payload = envelope["id"], envelope["payload"]
            validate_id(identifier, "predicate")
            if (
                not isinstance(payload, Mapping)
                or set(payload) != {"schema_version", "node"}
                or payload["schema_version"] != 1
                or not isinstance(payload["node"], Mapping)
            ):
                raise ValueError("predicate envelope has invalid payload")
            if content_id("predicate", dict(payload)) != identifier:
                raise ValueError("predicate envelope ID/payload mismatch")
            if identifier in raw_nodes:
                raise ValueError("duplicate predicate envelope")
            raw_nodes[identifier] = dict(payload["node"])
        all_ids = set(self._nodes) | set(raw_nodes)
        all_languages = set(self._languages) | set(incoming_languages)
        refs: dict[str, tuple[str, ...]] = {}
        for identifier, node in raw_nodes.items():
            op = node.get("op")
            if op in {"and", "or"}:
                args = node.get("args")
                if not isinstance(args, list) or not args:
                    raise ValueError("predicate args must be a non-empty array")
                refs[identifier] = tuple(args)
            elif op == "not":
                refs[identifier] = (node.get("arg"),)
            else:
                refs[identifier] = ()
            for reference in refs[identifier]:
                if not isinstance(reference, str) or reference not in all_ids:
                    raise ValueError("unresolved predicate reference")
            if op == "language" and node.get("language_id") not in all_languages:
                raise ValueError("unresolved language reference")
        # Build into temporary dictionaries using normal construction, then
        # require each received node already is canonical syntax (no repair).
        prior_languages, prior_nodes = self._languages, self._nodes
        self._languages = {
            **self._languages,
            **{
                identifier: language
                for identifier, (language, _) in incoming_languages.items()
            },
        }
        self._nodes = dict(self._nodes)
        self._nodes_view = MappingProxyType(self._nodes)
        try:
            canonical: dict[str, str] = {}
            pending = set(raw_nodes)
            while pending:
                self.budget.reserve(
                    "max_work",
                    len(pending) + sum(len(refs[item]) for item in pending),
                    phase="predicate_decode",
                    units="dependency checks",
                )
                ready = [
                    item
                    for item in pending
                    if all(child not in pending for child in refs[item])
                ]
                if not ready:
                    raise ValueError("predicate graph contains a cycle")
                for identifier in ready:
                    actual = self._add_node(raw_nodes[identifier], simplify=True)
                    if actual != identifier:
                        raise ValueError("predicate payload is not canonical syntax")
                    canonical[identifier] = actual
                    pending.remove(identifier)
        except Exception:
            self._languages, self._nodes = prior_languages, prior_nodes
            self._nodes_view = MappingProxyType(self._nodes)
            raise
        self._languages = prior_languages
        for identifier, (language, payload) in incoming_languages.items():
            self._register_language(identifier, language, payload)
