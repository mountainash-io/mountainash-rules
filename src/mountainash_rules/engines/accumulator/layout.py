"""Private scoped dense-word layout for exact accumulator cells.

The layout is deliberately physical: scoped masks accelerate candidate work but
never define a cell's identity or contributor membership.  Semantic authority
remains the immutable :class:`ExactAnalysis` cell records.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
import typing as t

from mountainash_rules.core.codec import validate_id
from mountainash_rules.core.constants import DataType
from mountainash_rules.core.contracts import OperationBudget
from mountainash_rules.core.normalization import Fragment, Overlay, covered_overlay
from mountainash_rules.core.reasoner import Reasoner
from mountainash_rules.core.scalar import decode_scalar, rank, rank_bounds, unrank
from mountainash_rules.engines.accumulator.compiler import (
    ExactAnalysis,
    PreparedSources,
    SourceRecord,
    _canonical_id,
)

_WORD_BITS = 63
_WORD_LIMIT = 1 << _WORD_BITS


@dataclass(frozen=True, slots=True)
class ScopeKey:
    field: str
    predicate_id: str


@dataclass(frozen=True, slots=True)
class DiscoveredScope:
    scope_id: str
    predicate_id: str
    keys: tuple[ScopeKey, ...]
    applicable_source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "keys", tuple(self.keys))
        object.__setattr__(
            self, "applicable_source_ids", tuple(self.applicable_source_ids)
        )


@dataclass(frozen=True, slots=True)
class ScopedDiscovery:
    """Physical scopes plus a globally authoritative completed overlay."""

    partition_identity: t.Mapping[str, t.Any]
    domain_id: str
    overlay: Overlay
    scopes: tuple[DiscoveredScope, ...]
    segmentation_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "partition_identity", _freeze(self.partition_identity))
        object.__setattr__(self, "scopes", tuple(self.scopes))
        object.__setattr__(self, "segmentation_fields", tuple(self.segmentation_fields))


class _NativeRow(t.Mapping[str, t.Any]):
    """Immutable mapping view with the exact persisted physical column names."""

    _NATIVE: t.ClassVar[tuple[tuple[str, str], ...]]

    def __getitem__(self, key: str) -> t.Any:
        for native, attribute in self._NATIVE:
            if key == native:
                return getattr(self, attribute)
        raise KeyError(key)

    def __iter__(self) -> t.Iterator[str]:
        return (native for native, _ in self._NATIVE)

    def __len__(self) -> int:
        return len(self._NATIVE)


@dataclass(frozen=True, slots=True)
class ScopeRow(_NativeRow):
    _NATIVE: t.ClassVar[tuple[tuple[str, str], ...]] = (
        ("__scope_id", "scope_id"),
        ("__predicate_id", "predicate_id"),
        ("__map_id", "map_id"),
        ("__source_count", "source_count"),
        ("__word_count", "word_count"),
    )
    scope_id: str
    predicate_id: str
    map_id: str
    source_count: int
    word_count: int


@dataclass(frozen=True, slots=True)
class ScopeKeyRow(_NativeRow):
    _NATIVE: t.ClassVar[tuple[tuple[str, str], ...]] = (
        ("__scope_id", "scope_id"),
        ("__field", "field"),
        ("__predicate_id", "predicate_id"),
    )
    scope_id: str
    field: str
    predicate_id: str


@dataclass(frozen=True, slots=True)
class SourceMapRow(_NativeRow):
    _NATIVE: t.ClassVar[tuple[tuple[str, str], ...]] = (
        ("__map_id", "map_id"),
        ("__source_id", "source_id"),
        ("__position", "position"),
    )
    map_id: str
    source_id: str
    position: int


@dataclass(frozen=True, slots=True)
class VectorRow(_NativeRow):
    _NATIVE: t.ClassVar[tuple[tuple[str, str], ...]] = (
        ("__vector_id", "vector_id"),
        ("__scope_id", "scope_id"),
        ("__map_id", "map_id"),
        ("__cell_id", "cell_id"),
        ("__predicate_id", "predicate_id"),
        ("__contributor_set_id", "contributor_set_id"),
    )
    vector_id: str
    scope_id: str
    map_id: str
    cell_id: str
    predicate_id: str
    contributor_set_id: str


@dataclass(frozen=True, slots=True)
class WordRow(_NativeRow):
    _NATIVE: t.ClassVar[tuple[tuple[str, str], ...]] = (
        ("__vector_id", "vector_id"),
        ("__word_index", "word_index"),
        ("__word_value", "word_value"),
    )
    vector_id: str
    word_index: int
    word_value: int


@dataclass(frozen=True, slots=True)
class ExactLayout:
    """The five immutable narrow compiled-layout relations."""

    scopes: tuple[ScopeRow, ...]
    scope_keys: tuple[ScopeKeyRow, ...]
    source_maps: tuple[SourceMapRow, ...]
    vectors: tuple[VectorRow, ...]
    words: tuple[WordRow, ...]
    segmentation_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        for field in ("scopes", "scope_keys", "source_maps", "vectors", "words"):
            object.__setattr__(self, field, tuple(getattr(self, field)))
        object.__setattr__(self, "segmentation_fields", tuple(self.segmentation_fields))

    @property
    def columns(self) -> t.Mapping[str, tuple[tuple[str, str, bool], ...]]:
        """Declared physical schemas for the five narrow relations."""
        return MappingProxyType(
            {
                "scopes": (
                    ("__scope_id", "utf8", False),
                    ("__predicate_id", "utf8", False),
                    ("__map_id", "utf8", False),
                    ("__source_count", "int64", False),
                    ("__word_count", "int64", False),
                ),
                "scope_keys": (
                    ("__scope_id", "utf8", False),
                    ("__field", "utf8", False),
                    ("__predicate_id", "utf8", False),
                ),
                "source_maps": (
                    ("__map_id", "utf8", False),
                    ("__source_id", "utf8", False),
                    ("__position", "int64", False),
                ),
                "vectors": (
                    ("__vector_id", "utf8", False),
                    ("__scope_id", "utf8", False),
                    ("__map_id", "utf8", False),
                    ("__cell_id", "utf8", False),
                    ("__predicate_id", "utf8", False),
                    ("__contributor_set_id", "utf8", False),
                ),
                "words": (
                    ("__vector_id", "utf8", False),
                    ("__word_index", "int64", False),
                    ("__word_value", "int64", False),
                ),
            }
        )


def _freeze(value: t.Any) -> t.Any:
    if isinstance(value, t.Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _word_count(count: int) -> int:
    if type(count) is not int or count < 0 or count > (1 << 63) - 1:
        raise ValueError("source count must be a non-negative signed-int64 value")
    return (count + _WORD_BITS - 1) // _WORD_BITS


def _reserve(
    budget: OperationBudget,
    counter: str,
    amount: int,
    phase: str,
    units: str,
    *,
    scope_id: str | None = None,
) -> None:
    budget.reserve(counter, amount, phase=phase, units=units, scope_id=scope_id)


def _retain(
    budget: OperationBudget, count: int, *, phase: str, scope_id: str | None = None
) -> None:
    """Reserve immutable Python record storage before creating the records."""
    _reserve(
        budget,
        "max_live_bytes",
        128 + count * 192,
        phase,
        "layout record bytes",
        scope_id=scope_id,
    )


def _scope_payload(
    partition_identity: t.Mapping[str, t.Any],
    predicate_id: str,
    keys: tuple[ScopeKey, ...],
) -> dict[str, t.Any]:
    return {
        "schema_version": 1,
        "partition_identity": dict(partition_identity),
        "predicate_id": predicate_id,
        "keys": [
            {"field": key.field, "predicate_id": key.predicate_id} for key in keys
        ],
    }


def _map_payload(scope_id: str, source_ids: tuple[str, ...]) -> dict[str, t.Any]:
    return {
        "schema_version": 1,
        "scope_id": scope_id,
        "allocations": [
            {"source_id": source_id, "position": position}
            for position, source_id in enumerate(source_ids)
        ],
    }


def _vector_payload(
    scope_id: str,
    map_id: str,
    cell_id: str,
    predicate_id: str,
    contributor_set_id: str,
) -> dict[str, t.Any]:
    return {
        "schema_version": 1,
        "scope_id": scope_id,
        "map_id": map_id,
        "cell_id": cell_id,
        "predicate_id": predicate_id,
        "contributor_set_id": contributor_set_id,
    }


def _partition(
    prepared: PreparedSources, key_values: t.Sequence[t.Mapping[str, t.Any]]
) -> tuple[tuple[SourceRecord, ...], t.Mapping[str, t.Any], str]:
    """Recreate the fixed routing restriction without invoking full analysis."""
    if isinstance(key_values, (str, bytes)) or not isinstance(key_values, t.Sequence):
        raise ValueError("key_values must be a sequence")
    keys = _freeze(key_values)
    if keys not in prepared.routing["payload"]["partition_keys"]:
        raise ValueError("Unknown semantic partition key")
    graph = prepared.graph
    _reserve(
        graph.budget,
        "max_work",
        len(prepared.sources) + len(keys),
        "layout.partition",
        "routing checks",
    )
    partition = _freeze({"routing_id": prepared.routing["id"], "key_values": keys})
    declarations = {
        item["dimension_name"]: item
        for item in prepared.routing["payload"]["key_dimensions"]
    }
    terms = []
    for key in keys:
        if not isinstance(key, t.Mapping) or set(key) != {"dimension_name", "match"}:
            raise ValueError("Partition key is malformed")
        declaration = declarations.get(key["dimension_name"])
        if declaration is None:
            raise ValueError("Partition key dimension is unknown")
        match = key["match"]
        if match["kind"] == "value":
            terms.append(
                graph.eq(
                    declaration["context_field"], decode_scalar(dict(match["value"]))
                )
            )
        elif match["kind"] != "wildcard":
            raise ValueError("Partition key match is malformed")
    domain_id = graph.and_(prepared.domain.predicate_id, *terms)
    sources = tuple(
        source for source in prepared.sources if source.routing_values == keys
    )
    return sources, partition, domain_id


def _segmentation_fields(
    prepared: PreparedSources, configured: tuple[str, ...]
) -> tuple[str, ...]:
    """Validate the engine-resolved physical segmentation field sequence."""
    if not isinstance(configured, tuple):
        raise ValueError("segmentation_fields must be a tuple of physical field names")
    if any(type(name) is not str or not name for name in configured):
        raise ValueError("segmentation fields must be nonempty strings")
    if len(configured) != len(set(configured)):
        raise ValueError("Duplicate segmentation fields")
    if configured != tuple(sorted(configured)):
        raise ValueError("segmentation fields must be sorted physical names")
    if not set(configured) <= set(prepared.graph.fields):
        raise ValueError("Unknown segmentation field")
    return configured


def _scope_prefixes(reasoner, domain_id, partitions):
    """Depth-first feasible prefixes; retain no Cartesian generation."""
    if not partitions:
        yield ()
        return
    budget = reasoner.graph.budget
    stack = []

    def push(prefix, depth):
        size = 256 + 128 * len(prefix)
        budget.reserve("max_regions", 1, phase="layout.prefix", units="live prefixes")
        try:
            budget.reserve(
                "max_live_bytes", size, phase="layout.prefix", units="prefix frames"
            )
        except BaseException:
            budget.release("max_regions", 1)
            raise
        stack.append((prefix, depth, iter(partitions[depth][1]), size))

    push((), 0)
    try:
        while stack:
            prefix, depth, pieces, size = stack[-1]
            try:
                piece = next(pieces)
            except StopIteration:
                stack.pop()
                budget.release("max_regions", 1)
                budget.release("max_live_bytes", size)
                continue
            budget.reserve(
                "max_work", 1, phase="layout.prefix", units="feasibility checks"
            )
            temporary = 256 + 128 * (len(prefix) + 1)
            budget.reserve(
                "max_live_bytes",
                temporary,
                phase="layout.prefix",
                units="candidate prefix",
            )
            try:
                keys = (*prefix, ScopeKey(partitions[depth][0], piece))
                predicate = reasoner.graph.and_(
                    domain_id, *(key.predicate_id for key in keys)
                )
                if reasoner.is_empty(predicate):
                    continue
                if depth + 1 == len(partitions):
                    yield keys
                else:
                    push(keys, depth + 1)
            finally:
                budget.release("max_live_bytes", temporary)
    finally:
        for _, _, _, size in stack:
            budget.release("max_regions", 1)
            budget.release("max_live_bytes", size)


def _unary_atoms(graph: t.Any, field: str, roots: t.Sequence[str]) -> tuple[str, ...]:
    """Collect unary atoms with bounded frontier, visited and sorting storage."""
    from mountainash_rules.core.codec import _bounded_json_size, canonical_bytes

    edges = sum(
        len(node["args"]) if node["op"] in {"and", "or"} else int(node["op"] == "not")
        for node in graph.nodes.values()
    )
    size = 512 + 256 * len(graph.nodes) + 16 * (edges + len(roots))
    _reserve(
        graph.budget,
        "max_live_bytes",
        size,
        "layout.atoms",
        "traversal workspace bytes",
    )
    sort_bytes = 0
    try:
        seen: set[str] = set()
        atoms: set[str] = set()
        stack = list(roots)
        while stack:
            _reserve(graph.budget, "max_work", 1, "layout.atoms", "predicate visits")
            identifier = stack.pop()
            if identifier in seen:
                continue
            seen.add(identifier)
            node = graph.nodes[identifier]
            if (
                node["op"] in {"eq", "in", "interval", "language"}
                and node.get("field") == field
            ):
                atoms.add(identifier)
            if node["op"] in {"and", "or"}:
                stack.extend(node["args"])
            elif node["op"] == "not":
                stack.append(node["arg"])
        required_sort_bytes = sum(
            _bounded_json_size(
                graph.nodes[item],
                graph.budget,
                phase="layout.atom-order",
                counter="max_live_bytes",
            )
            + 64
            for item in atoms
        )
        _reserve(
            graph.budget,
            "max_live_bytes",
            required_sort_bytes,
            "layout.atom-order",
            "canonical sort keys",
        )
        sort_bytes = required_sort_bytes
        _retain(graph.budget, len(atoms), phase="layout.atoms")
        return tuple(
            sorted(atoms, key=lambda item: canonical_bytes(dict(graph.nodes[item])))
        )
    finally:
        graph.budget.release("max_live_bytes", size)
        if sort_bytes:
            graph.budget.release("max_live_bytes", sort_bytes)


def _ordered_keys(
    reasoner: Reasoner, field: str, atoms: tuple[str, ...]
) -> tuple[str, ...] | None:
    """Produce closed admissible-rank bands rather than a disconnected residual."""
    graph = reasoner.graph
    declaration = graph.fields[field]
    if declaration.data_type not in {
        DataType.INT,
        DataType.FLOAT,
        DataType.DATE,
        DataType.DATETIME,
    }:
        return None
    lower, upper = rank_bounds(declaration.data_type, timezone=declaration.timezone)
    cuts = {lower, upper + 1}
    for identifier in atoms:
        node = graph.nodes[identifier]
        values: tuple[t.Mapping[str, t.Any] | None, ...]
        if node["op"] == "eq":
            values = (node["value"],)
        elif node["op"] == "in":
            values = tuple(node["values"])
        elif node["op"] == "interval":
            lo = (
                lower
                if node["lower"] is None
                else rank(
                    decode_scalar(dict(node["lower"])),
                    declaration.data_type,
                    timezone=declaration.timezone,
                )
            )
            hi = (
                upper
                if node["upper"] is None
                else rank(
                    decode_scalar(dict(node["upper"])),
                    declaration.data_type,
                    timezone=declaration.timezone,
                )
            )
            cuts.add(lo)
            if hi < upper:
                cuts.add(hi + 1)
            continue
        else:
            continue
        for value in values:
            scalar_rank = rank(
                decode_scalar(dict(t.cast(t.Mapping[str, t.Any], value))),
                declaration.data_type,
                timezone=declaration.timezone,
            )
            cuts.add(scalar_rank)
            if scalar_rank < upper:
                cuts.add(scalar_rank + 1)
    if len(cuts) == 2:
        return (graph.true,)
    ordered = tuple(sorted(cuts))
    output = []
    for lo, next_lo in zip(ordered[:-1], ordered[1:], strict=True):
        hi = next_lo - 1
        _reserve(
            graph.budget, "max_work", 1, "layout.bands", "ordered band construction"
        )
        _reserve(graph.budget, "max_regions", 1, "layout.bands", "ordered field bands")
        _retain(graph.budget, 1, phase="layout.bands")
        output.append(
            graph.interval(
                field,
                unrank(lo, declaration.data_type, timezone=declaration.timezone),
                unrank(hi, declaration.data_type, timezone=declaration.timezone),
                lower_closed=True,
                upper_closed=True,
            )
        )
    return tuple(output)


def _field_keys(
    reasoner: Reasoner, field: str, roots: t.Iterable[str]
) -> tuple[str, ...]:
    """Refine an exact unary field universe by atom/complement, symbolically."""
    graph = reasoner.graph
    atoms = _unary_atoms(graph, field, roots)
    ordered = _ordered_keys(reasoner, field, atoms)
    if ordered is not None:
        return ordered
    pieces = (graph.true,)
    for atom in atoms:
        next_pieces: list[str] = []
        for piece in pieces:
            _reserve(graph.budget, "max_work", 2, "layout.refine", "unary refinements")
            for candidate in (
                reasoner.intersect(piece, atom),
                reasoner.difference(piece, atom),
            ):
                if not reasoner.is_empty(candidate):
                    _reserve(
                        graph.budget, "max_regions", 1, "layout.refine", "field regions"
                    )
                    _retain(graph.budget, 1, phase="layout.refine")
                    next_pieces.append(candidate)
        pieces = tuple(sorted(set(next_pieces)))
    return pieces


def _global_overlay(
    reasoner: Reasoner,
    domain_id: str,
    sources: tuple[SourceRecord, ...],
    fragments: tuple[Fragment, ...],
) -> Overlay:
    graph = reasoner.graph
    clipped: dict[str, str] = {}
    grouped: dict[str, set[str]] = {}
    for source in sources:
        predicate = graph.and_(domain_id, source.predicate_id)
        clipped[source.source_id] = predicate
        if not reasoner.is_empty(predicate):
            grouped.setdefault(predicate, set()).add(source.source_id)
    from mountainash_rules.core.codec import canonical_bytes

    groups = tuple(
        (predicate, frozenset(source_ids))
        for predicate, source_ids in sorted(
            grouped.items(),
            key=lambda item: canonical_bytes(dict(graph.nodes[item[0]])),
        )
    )
    ordered_fragments = tuple(
        sorted(
            fragments,
            key=lambda fragment: (
                tuple(sorted(fragment.contributors)),
                fragment.predicate_id,
            ),
        )
    )
    _retain(
        graph.budget, 1 + len(groups) + len(ordered_fragments), phase="layout.overlay"
    )
    return Overlay(domain_id, clipped, groups, ordered_fragments)


def discover_scoped(
    prepared: PreparedSources,
    *,
    key_values: t.Sequence[t.Mapping[str, t.Any]],
    segmentation_fields: tuple[str, ...],
    order: t.Sequence[str] | None = None,
) -> ScopedDiscovery:
    """Discover exact configured scopes before any candidate overlay joins.

    The returned overlay intentionally has the original partition domain and
    source applicability.  Scope-local fragments are only a construction aid;
    callers pass this global overlay to ``finalize_regions`` for seam-free
    canonical publication.
    """
    if not isinstance(prepared, PreparedSources):
        raise TypeError("prepared must be PreparedSources")
    graph = prepared.graph
    source_rows, partition, domain_id = _partition(prepared, key_values)
    fields = _segmentation_fields(prepared, segmentation_fields)
    if order is not None:
        if isinstance(order, (str, bytes)) or not isinstance(order, t.Sequence):
            raise ValueError("Source traversal must be a sequence of UUIDs")
        ordered_sources = tuple(order)
        if len(ordered_sources) != len(source_rows) or set(ordered_sources) != {
            source.source_id for source in source_rows
        }:
            raise ValueError(
                "Source traversal must contain every partition source exactly once"
            )
    reasoner = Reasoner(graph)
    if reasoner.is_empty(domain_id):
        return ScopedDiscovery(
            partition,
            domain_id,
            _global_overlay(reasoner, domain_id, source_rows, ()),
            (),
            fields,
        )

    roots = (domain_id, *(source.predicate_id for source in source_rows))
    field_partitions = tuple(
        (field, _field_keys(reasoner, field, roots)) for field in fields
    )
    prefixes = _scope_prefixes(reasoner, domain_id, field_partitions)

    discovered: list[DiscoveredScope] = []
    fragments: list[Fragment] = []
    source_predicates = {
        source.source_id: source.predicate_id for source in source_rows
    }
    for keys in prefixes:
        scope_predicate = graph.and_(domain_id, *(key.predicate_id for key in keys))
        if reasoner.is_empty(scope_predicate):
            continue
        payload = _scope_payload(partition, scope_predicate, keys)
        scope_id = _canonical_id(
            graph.budget, "scope", payload, phase="layout.scope-id"
        )
        _reserve(
            graph.budget,
            "max_scopes",
            1,
            "layout.scope",
            "physical scopes",
            scope_id=scope_id,
        )
        applicable_rows: list[str] = []
        for source in source_rows:
            _reserve(
                graph.budget,
                "max_work",
                1,
                "layout.scope-sources",
                "source scope checks",
                scope_id=scope_id,
            )
            if not reasoner.is_empty(graph.and_(scope_predicate, source.predicate_id)):
                _reserve(
                    graph.budget,
                    "max_source_scope_edges",
                    1,
                    "layout.scope-sources",
                    "source scope edges",
                    scope_id=scope_id,
                )
                _retain(
                    graph.budget,
                    1,
                    phase="layout.scope-sources",
                    scope_id=scope_id,
                )
                applicable_rows.append(source.source_id)
        applicable = tuple(applicable_rows)
        _retain(
            graph.budget,
            1 + len(keys),
            phase="layout.scope",
            scope_id=scope_id,
        )
        discovered.append(DiscoveredScope(scope_id, scope_predicate, keys, applicable))
        if applicable:
            local_order = (
                None
                if order is None
                else tuple(
                    source_id for source_id in order if source_id in set(applicable)
                )
            )
            local = covered_overlay(
                reasoner,
                scope_predicate,
                {source_id: source_predicates[source_id] for source_id in applicable},
                order=local_order,
            )
            _retain(
                graph.budget,
                len(local.fragments),
                phase="layout.fragments",
                scope_id=scope_id,
            )
            fragments.extend(local.fragments)

    _reserve(
        graph.budget,
        "max_work",
        len(discovered) * (len(discovered) + 1) // 2,
        "layout.cover",
        "scope cover comparisons",
    )
    for index, scope in enumerate(discovered):
        for other in discovered[index + 1 :]:
            if not reasoner.is_empty(
                graph.and_(scope.predicate_id, other.predicate_id)
            ):
                raise ValueError("Configured scopes overlap")
    scope_union = graph.or_(*(scope.predicate_id for scope in discovered))
    if not reasoner.equivalent(scope_union, domain_id):
        raise ValueError("Configured scopes do not cover the partition domain")
    return ScopedDiscovery(
        partition,
        domain_id,
        _global_overlay(reasoner, domain_id, source_rows, tuple(fragments)),
        tuple(sorted(discovered, key=lambda scope: scope.scope_id)),
        fields,
    )


def _encode_membership(
    source_ids: t.Iterable[str], positions: t.Mapping[str, int]
) -> tuple[int, ...]:
    words = [0] * _word_count(len(positions))
    for source_id in source_ids:
        try:
            position = positions[source_id]
        except KeyError as exc:
            raise ValueError("Contributor is absent from scope source map") from exc
        words[position // _WORD_BITS] |= 1 << (position % _WORD_BITS)
    return tuple(words)


def _validate_positions(positions: t.Mapping[str, int]) -> dict[str, int]:
    if not isinstance(positions, t.Mapping):
        raise ValueError("source positions must be a mapping")
    copied = dict(positions)
    if any(type(source_id) is not str for source_id in copied):
        raise ValueError("source positions require UUID strings")
    if any(type(position) is not int or position < 0 for position in copied.values()):
        raise ValueError("source positions must be non-negative integers")
    if sorted(copied.values()) != list(range(len(copied))):
        raise ValueError("source positions must be contiguous and unique")
    return copied


def _words(words: t.Sequence[int]) -> tuple[int, ...]:
    if isinstance(words, (str, bytes)) or not isinstance(words, t.Sequence):
        raise ValueError("words must be a sequence")
    result = tuple(words)
    if any(type(word) is not int or not 0 <= word < _WORD_LIMIT for word in result):
        raise ValueError("word values must be non-negative signed-int64 masks")
    return result


def wordwise_union(left: t.Sequence[int], right: t.Sequence[int]) -> tuple[int, ...]:
    left_words, right_words = _words(left), _words(right)
    if len(left_words) != len(right_words):
        raise ValueError("word vectors have different widths")
    return tuple(a | b for a, b in zip(left_words, right_words, strict=True))


def wordwise_intersection(
    left: t.Sequence[int], right: t.Sequence[int]
) -> tuple[int, ...]:
    left_words, right_words = _words(left), _words(right)
    if len(left_words) != len(right_words):
        raise ValueError("word vectors have different widths")
    return tuple(a & b for a, b in zip(left_words, right_words, strict=True))


def wordwise_subset(left: t.Sequence[int], right: t.Sequence[int]) -> bool:
    return wordwise_intersection(left, right) == _words(left)


def wordwise_strict_subset(left: t.Sequence[int], right: t.Sequence[int]) -> bool:
    return wordwise_subset(left, right) and _words(left) != _words(right)


def decode_membership(
    words: t.Sequence[int], positions: t.Mapping[str, int]
) -> frozenset[str]:
    source_positions = _validate_positions(positions)
    masks = _words(words)
    if len(masks) != _word_count(len(source_positions)):
        raise ValueError("word vector does not have its dense width")
    output = set()
    for source_id, position in source_positions.items():
        if masks[position // _WORD_BITS] & (1 << (position % _WORD_BITS)):
            output.add(source_id)
    return frozenset(output)


def remap_words(
    words: t.Sequence[int],
    source_positions: t.Mapping[str, int],
    target_positions: t.Mapping[str, int],
) -> tuple[int, ...]:
    """Decode by UUID then encode under another scope-local allocation."""
    source = _validate_positions(source_positions)
    target = _validate_positions(target_positions)
    if set(source) != set(target):
        raise ValueError("word remapping requires equal UUID namespaces")
    return _encode_membership(decode_membership(words, source), target)


def materialize_layout(
    analysis: ExactAnalysis, discovery: ScopedDiscovery
) -> ExactLayout:
    """Encode final public cells as nonempty scope intersections and dense words."""
    if not isinstance(analysis, ExactAnalysis) or not isinstance(
        discovery, ScopedDiscovery
    ):
        raise TypeError("analysis and discovery must be exact immutable records")
    if (
        analysis.partition_identity != discovery.partition_identity
        or analysis.domain.predicate_id != discovery.domain_id
    ):
        raise ValueError("Discovery does not belong to this exact analysis partition")
    graph = analysis.prepared.graph
    reasoner = Reasoner(graph)
    source_ids = {source.source_id for source in analysis.sources}
    scope_rows: list[ScopeRow] = []
    key_rows: list[ScopeKeyRow] = []
    map_rows: list[SourceMapRow] = []
    vector_rows: list[VectorRow] = []
    word_rows: list[WordRow] = []

    for scope in discovery.scopes:
        if any(
            source_id not in source_ids for source_id in scope.applicable_source_ids
        ):
            raise ValueError("Scope references a source outside the partition")
        allocations = tuple(sorted(scope.applicable_source_ids))
        if len(allocations) != len(set(allocations)):
            raise ValueError("Scope source allocation contains duplicate UUIDs")
        map_id = _canonical_id(
            graph.budget,
            "source-map",
            _map_payload(scope.scope_id, allocations),
            phase="layout.map-id",
        )
        width = _word_count(len(allocations))
        _retain(
            graph.budget,
            1 + len(scope.keys) + len(allocations),
            phase="layout.materialize",
            scope_id=scope.scope_id,
        )
        scope_rows.append(
            ScopeRow(
                scope.scope_id, scope.predicate_id, map_id, len(allocations), width
            )
        )
        key_rows.extend(
            ScopeKeyRow(scope.scope_id, key.field, key.predicate_id)
            for key in scope.keys
        )
        map_rows.extend(
            SourceMapRow(map_id, source_id, position)
            for position, source_id in enumerate(allocations)
        )
        positions = {
            source_id: position for position, source_id in enumerate(allocations)
        }
        for cell in analysis.cells:
            _reserve(
                graph.budget,
                "max_work",
                1,
                "layout.vectors",
                "cell scope intersections",
                scope_id=scope.scope_id,
            )
            piece = graph.and_(cell.predicate_id, scope.predicate_id)
            if reasoner.is_empty(piece):
                continue
            vector_id = _canonical_id(
                graph.budget,
                "vector",
                _vector_payload(
                    scope.scope_id, map_id, cell.cell_id, piece, cell.contributor_set_id
                ),
                phase="layout.vector-id",
            )
            _reserve(
                graph.budget,
                "max_word_rows",
                width,
                "layout.words",
                "dense word rows",
                scope_id=scope.scope_id,
            )
            _retain(
                graph.budget,
                1 + width,
                phase="layout.vectors",
                scope_id=scope.scope_id,
            )
            masks = _encode_membership(cell.contributors, positions)
            vector_rows.append(
                VectorRow(
                    vector_id,
                    scope.scope_id,
                    map_id,
                    cell.cell_id,
                    piece,
                    cell.contributor_set_id,
                )
            )
            word_rows.extend(
                WordRow(vector_id, index, value) for index, value in enumerate(masks)
            )

    return ExactLayout(
        tuple(sorted(scope_rows, key=lambda row: row.scope_id)),
        tuple(sorted(key_rows, key=lambda row: (row.scope_id, row.field))),
        tuple(sorted(map_rows, key=lambda row: (row.map_id, row.source_id))),
        tuple(sorted(vector_rows, key=lambda row: row.vector_id)),
        tuple(sorted(word_rows, key=lambda row: (row.vector_id, row.word_index))),
        discovery.segmentation_fields,
    )


def _required(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_layout(
    analysis: ExactAnalysis, layout: ExactLayout, *, budget: OperationBudget
) -> None:
    """Validate persisted narrow state without rediscovery or reallocation."""
    if not isinstance(analysis, ExactAnalysis) or not isinstance(layout, ExactLayout):
        raise TypeError("analysis and layout must be immutable exact records")
    if budget is not analysis.prepared.graph.budget:
        raise ValueError("layout validation must use analysis.prepared.graph.budget")
    graph = analysis.prepared.graph
    reasoner = Reasoner(graph)
    _required(
        all(type(field) is str and field for field in layout.segmentation_fields),
        "segmentation fields must be strings",
    )
    _required(
        layout.segmentation_fields == tuple(sorted(set(layout.segmentation_fields))),
        "segmentation fields must be sorted and unique",
    )
    known_fields = set(graph.fields)
    _required(
        set(layout.segmentation_fields) <= known_fields, "unknown segmentation field"
    )
    sources = {source.source_id for source in analysis.sources}
    cells = {cell.cell_id: cell for cell in analysis.cells}
    _required(len(cells) == len(analysis.cells), "analysis has duplicate cells")
    scope_by_id: dict[str, ScopeRow] = {}
    map_to_scope: dict[str, ScopeRow] = {}
    for row in layout.scopes:
        _required(type(row) is ScopeRow, "invalid scope row type")
        _reserve(
            budget,
            "max_work",
            1,
            "layout.validate.scopes",
            "scope checks",
            scope_id=row.scope_id,
        )
        _required(
            row.scope_id not in scope_by_id and row.map_id not in map_to_scope,
            "duplicate scope or map ID",
        )
        validate_id(row.scope_id, "scope")
        validate_id(row.predicate_id, "predicate")
        validate_id(row.map_id, "source-map")
        _required(row.predicate_id in graph.nodes, "scope predicate is unresolved")
        _required(
            type(row.source_count) is int and row.source_count >= 0,
            "invalid scope source_count",
        )
        _required(
            row.word_count == _word_count(row.source_count),
            "scope word_count is invalid",
        )
        scope_by_id[row.scope_id] = row
        map_to_scope[row.map_id] = row
    key_rows: dict[str, list[ScopeKeyRow]] = {scope_id: [] for scope_id in scope_by_id}
    seen_key_pairs = set()
    for row in layout.scope_keys:
        _required(type(row) is ScopeKeyRow, "invalid scope key row type")
        _reserve(budget, "max_work", 1, "layout.validate.keys", "scope key checks")
        _required(
            (row.scope_id, row.field) not in seen_key_pairs, "duplicate scope key"
        )
        _required(
            row.scope_id in key_rows and row.field in known_fields,
            "scope key foreign key is invalid",
        )
        validate_id(row.predicate_id, "predicate")
        _required(row.predicate_id in graph.nodes, "scope key predicate is unresolved")
        seen_key_pairs.add((row.scope_id, row.field))
        key_rows[row.scope_id].append(row)
    expected_fields = set(layout.segmentation_fields)
    maps: dict[str, dict[str, int]] = {map_id: {} for map_id in map_to_scope}
    positions: dict[str, set[int]] = {map_id: set() for map_id in map_to_scope}
    for row in layout.source_maps:
        _required(type(row) is SourceMapRow, "invalid source map row type")
        _reserve(budget, "max_work", 1, "layout.validate.maps", "source map checks")
        _required(
            row.map_id in maps and row.source_id in sources,
            "source map foreign key is invalid",
        )
        _required(
            type(row.position) is int and row.position >= 0,
            "source map position is invalid",
        )
        _required(
            row.source_id not in maps[row.map_id]
            and row.position not in positions[row.map_id],
            "duplicate source map allocation",
        )
        maps[row.map_id][row.source_id] = row.position
        positions[row.map_id].add(row.position)
    for scope_id, scope in scope_by_id.items():
        keys = tuple(sorted(key_rows[scope_id], key=lambda row: row.field))
        _required(
            {row.field for row in keys} == expected_fields,
            "scope keys do not match segmentation fields",
        )
        expected_scope_id = _canonical_id(
            budget,
            "scope",
            _scope_payload(
                analysis.partition_identity,
                scope.predicate_id,
                tuple(ScopeKey(row.field, row.predicate_id) for row in keys),
            ),
            phase="layout.validate.scope-id",
        )
        _required(scope.scope_id == expected_scope_id, "scope hash mismatch")
        expected_predicate = graph.and_(
            analysis.domain.predicate_id, *(row.predicate_id for row in keys)
        )
        _required(
            reasoner.equivalent(scope.predicate_id, expected_predicate),
            "scope predicate disagrees with keys",
        )
        allocation = maps[scope.map_id]
        _required(len(allocation) == scope.source_count, "scope source count mismatch")
        _required(
            sorted(allocation.values()) == list(range(scope.source_count)),
            "source map positions are not dense",
        )
        expected_map_id = _canonical_id(
            budget,
            "source-map",
            {
                "schema_version": 1,
                "scope_id": scope.scope_id,
                "allocations": [
                    {"source_id": source_id, "position": allocation[source_id]}
                    for source_id in sorted(allocation)
                ],
            },
            phase="layout.validate.map-id",
        )
        _required(scope.map_id == expected_map_id, "source map hash mismatch")

    vector_by_id: dict[str, VectorRow] = {}
    scoped_cells = set()
    words: dict[str, dict[int, int]] = {}
    for row in layout.vectors:
        _required(type(row) is VectorRow, "invalid vector row type")
        _reserve(budget, "max_work", 1, "layout.validate.vectors", "vector checks")
        _required(
            row.vector_id not in vector_by_id
            and (row.scope_id, row.cell_id) not in scoped_cells,
            "duplicate vector",
        )
        scope = scope_by_id.get(row.scope_id)
        cell = cells.get(row.cell_id)
        _required(
            scope is not None and cell is not None and row.map_id == scope.map_id,
            "vector foreign key is invalid",
        )
        validate_id(row.vector_id, "vector")
        validate_id(row.predicate_id, "predicate")
        _required(
            row.predicate_id in graph.nodes
            and row.contributor_set_id == cell.contributor_set_id,
            "vector semantic reference is invalid",
        )
        expected_vector_id = _canonical_id(
            budget,
            "vector",
            _vector_payload(
                row.scope_id,
                row.map_id,
                row.cell_id,
                row.predicate_id,
                row.contributor_set_id,
            ),
            phase="layout.validate.vector-id",
        )
        _required(row.vector_id == expected_vector_id, "vector hash mismatch")
        expected_piece = graph.and_(cell.predicate_id, scope.predicate_id)
        _required(
            not reasoner.is_empty(expected_piece),
            "vector has empty cell scope intersection",
        )
        _required(
            reasoner.equivalent(row.predicate_id, expected_piece),
            "vector predicate is not cell intersect scope",
        )
        vector_by_id[row.vector_id] = row
        scoped_cells.add((row.scope_id, row.cell_id))
        words[row.vector_id] = {}
    for row in layout.words:
        _required(type(row) is WordRow, "invalid word row type")
        _reserve(budget, "max_work", 1, "layout.validate.words", "word checks")
        _required(
            row.vector_id in words and row.word_index not in words[row.vector_id],
            "word foreign key or uniqueness is invalid",
        )
        _required(
            type(row.word_index) is int and row.word_index >= 0, "word index is invalid"
        )
        _required(
            type(row.word_value) is int and 0 <= row.word_value < _WORD_LIMIT,
            "word value is invalid",
        )
        words[row.vector_id][row.word_index] = row.word_value
    for vector_id, vector in vector_by_id.items():
        scope = scope_by_id[vector.scope_id]
        actual_rows = words[vector_id]
        _required(
            set(actual_rows) == set(range(scope.word_count)), "word rows are not dense"
        )
        masks = tuple(actual_rows[index] for index in range(scope.word_count))
        if scope.word_count:
            valid_last = (1 << (scope.source_count % _WORD_BITS or _WORD_BITS)) - 1
            _required((masks[-1] & ~valid_last) == 0, "word has nonzero unused bits")
        allocation = maps[scope.map_id]
        decoded = decode_membership(masks, allocation)
        cell = cells[vector.cell_id]
        expected = _encode_membership(cell.contributors, allocation)
        _required(
            wordwise_intersection(masks, expected) == masks,
            "word membership has extra UUIDs",
        )
        _required(
            wordwise_union(masks, expected) == expected, "word membership omits UUIDs"
        )
        _required(
            not wordwise_strict_subset(masks, expected)
            and not wordwise_strict_subset(expected, masks),
            "word membership differs from contributors",
        )
        _required(
            decoded == frozenset(cell.contributors), "decoded word membership mismatch"
        )
        _required(
            remap_words(masks, allocation, allocation) == masks,
            "word UUID remapping mismatch",
        )

    scope_rows = tuple(scope_by_id.values())
    _reserve(
        budget,
        "max_work",
        len(scope_rows) * (len(scope_rows) + 1) // 2,
        "layout.validate.cover",
        "scope comparisons",
    )
    for index, left in enumerate(scope_rows):
        for right in scope_rows[index + 1 :]:
            _required(
                reasoner.is_empty(graph.and_(left.predicate_id, right.predicate_id)),
                "scopes overlap",
            )
    _required(
        reasoner.equivalent(
            graph.or_(*(row.predicate_id for row in scope_rows)),
            analysis.domain.predicate_id,
        ),
        "scopes do not cover domain",
    )
    for cell in analysis.cells:
        pieces = [
            vector.predicate_id
            for vector in vector_by_id.values()
            if vector.cell_id == cell.cell_id
        ]
        _required(
            reasoner.equivalent(graph.or_(*pieces), cell.predicate_id),
            "vectors do not cover cell",
        )
