"""Source-authoritative normalization-2, with one numeric/symbolic finalizer."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field as dataclass_field
from types import MappingProxyType
import typing as t
import uuid
import weakref

from mountainash_rules.core.codec import canonical_bytes
from mountainash_rules.core.scalar import decode_scalar, rank, rank_bounds, unrank

if t.TYPE_CHECKING:
    from mountainash_rules.core.predicates import PredicateGraph
    from mountainash_rules.core.reasoner import Reasoner
    from mountainash_rules.core.contracts import OperationBudget

Box = tuple[tuple[int, int], ...]


class _Lease:
    __slots__ = ("parent", "__weakref__")

    def __init__(self, parent: _Lease | None) -> None:
        self.parent = parent


def _release(budget: OperationBudget, regions: int, live: int) -> None:
    budget.release("max_regions", regions)
    budget.release("max_live_bytes", live)


class _Allocation:
    """Temporary normalization arena, transferred only to immutable results."""

    def __init__(self, budget: OperationBudget) -> None:
        self.budget = budget
        self.regions = 0
        self.live = 0

    def reserve(
        self, regions: int, live: int, phase: str, source_id: str | None = None
    ) -> None:
        self.budget.reserve(
            "max_regions",
            regions,
            phase=phase,
            units="region states",
            source_id=source_id,
        )
        self.regions += regions
        self.budget.reserve(
            "max_live_bytes", live, phase=phase, units="bytes", source_id=source_id
        )
        self.live += live

    def retain(self, regions: int, live: int, parent: _Lease | None = None) -> _Lease:
        self.reserve(
            max(0, regions - self.regions),
            max(0, live - self.live),
            "normalization.retain",
        )
        _release(self.budget, self.regions - regions, self.live - live)
        self.regions = self.live = 0
        lease = _Lease(parent)
        weakref.finalize(lease, _release, self.budget, regions, live)
        return lease


_CURRENT: ContextVar[_Allocation | None] = ContextVar(
    "normalization_allocation", default=None
)


@contextmanager
def _allocation_scope(graph: PredicateGraph) -> t.Iterator[_Allocation]:
    allocation = _Allocation(graph.budget)
    token = _CURRENT.set(allocation)
    try:
        yield allocation
    finally:
        _CURRENT.reset(token)
        _release(allocation.budget, allocation.regions, allocation.live)


@dataclass(frozen=True, slots=True)
class Fragment:
    predicate_id: str
    contributors: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "contributors", frozenset(self.contributors))


@dataclass(frozen=True, slots=True)
class Overlay:
    domain_id: str
    sources: t.Mapping[str, str]
    groups: tuple[tuple[str, frozenset[str]], ...]
    fragments: tuple[Fragment, ...]
    _lease: _Lease | None = dataclass_field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", MappingProxyType(dict(self.sources)))
        object.__setattr__(
            self, "groups", tuple((p, frozenset(s)) for p, s in self.groups)
        )
        object.__setattr__(self, "fragments", tuple(self.fragments))


@dataclass(frozen=True, slots=True)
class CellRegion:
    predicate_id: str
    contributors: frozenset[str]
    _lease: _Lease | None = dataclass_field(default=None, repr=False, compare=False)


def _work(graph: PredicateGraph, amount: int, phase: str) -> None:
    graph.budget.reserve("max_work", amount, phase=phase, units="normalization steps")


def _state(
    graph: PredicateGraph, amount: int, phase: str, *, source_id: str | None = None
) -> None:
    """Reserve temporary state in the active normalization operation."""
    allocation = _CURRENT.get()
    if allocation is None or allocation.budget is not graph.budget:
        raise RuntimeError("normalization state requires an allocation scope")
    allocation.reserve(
        amount, amount * (512 + 128 * len(graph.fields)), phase, source_id
    )


def _union_members(
    graph: PredicateGraph, left: frozenset[str], right: frozenset[str]
) -> frozenset[str]:
    width = len(left) + len(right)
    _work(graph, width, "overlay.membership")
    allocation = _CURRENT.get()
    if allocation is None or allocation.budget is not graph.budget:
        raise RuntimeError("normalization membership requires an allocation scope")
    allocation.reserve(0, 256 + 64 * width, "overlay.membership")
    return left | right


def _ordered(graph: PredicateGraph) -> tuple[str, ...]:
    return tuple(
        sorted(
            name
            for name, field in graph.fields.items()
            if field.data_type.is_numeric or field.data_type.is_temporal
        )
    )


def _bounds(graph: PredicateGraph, field: str) -> tuple[int, int]:
    declaration = graph.fields[field]
    return rank_bounds(declaration.data_type, timezone=declaration.timezone)


def _rank(graph: PredicateGraph, field: str, scalar: t.Mapping[str, t.Any]) -> int:
    declaration = graph.fields[field]
    return rank(
        decode_scalar(dict(scalar)),
        declaration.data_type,
        timezone=declaration.timezone,
    )


def _interval_bounds(
    graph: PredicateGraph, node: t.Mapping[str, t.Any]
) -> tuple[int, int]:
    field = node["field"]
    minimum, maximum = _bounds(graph, field)
    return (
        minimum if node["lower"] is None else _rank(graph, field, node["lower"]),
        maximum if node["upper"] is None else _rank(graph, field, node["upper"]),
    )


def intersect_box(left: Box, right: Box) -> Box | None:
    """Intersect closed admissible-rank boxes; no dense-real approximations."""
    if len(left) != len(right):
        raise ValueError("Box dimensions disagree")
    result = tuple((max(a, c), min(b, d)) for (a, b), (c, d) in zip(left, right))
    return None if any(lo > hi for lo, hi in result) else result


def subtract_box(left: Box, right: Box) -> tuple[Box, ...]:
    """Disjoint at-most-2d slabs covering precisely left minus right."""
    overlap = intersect_box(left, right)
    if overlap is None:
        return (left,)
    core = list(left)
    output = []
    for axis, (lo, hi) in enumerate(overlap):
        lower, upper = core[axis]
        if lower < lo:
            slab = list(core)
            slab[axis] = (lower, lo - 1)
            output.append(tuple(slab))
        if hi < upper:
            slab = list(core)
            slab[axis] = (hi + 1, upper)
            output.append(tuple(slab))
        core[axis] = (lo, hi)
    return tuple(output)


def _as_box(
    graph: PredicateGraph, predicate: str, fields: tuple[str, ...]
) -> Box | None:
    # Canonical AND nodes are flattened, so their immediate arity bounds this
    # recognizer's frontier.  Other Boolean operators terminate recognition.
    root = graph.nodes[predicate]
    width = len(root["args"]) if root["op"] == "and" else 1
    size = 256 + 128 * len(fields) + 64 * width
    graph.budget.reserve(
        "max_live_bytes", size, phase="overlay.box_recognition", units="bytes"
    )
    try:
        box = tuple(_bounds(graph, field) for field in fields)
        positions = {field: index for index, field in enumerate(fields)}
        pending = [predicate]
        while pending:
            _work(graph, 1, "overlay.box_recognition")
            node = graph.nodes[pending.pop()]
            if node["op"] == "true":
                continue
            if node["op"] == "and":
                pending.extend(node["args"])
            elif node["op"] == "interval" and node["field"] in positions:
                axis = positions[node["field"]]
                bounds = list(box)
                bounds[axis] = _interval_bounds(graph, node)
                clipped = intersect_box(box, tuple(bounds))
                if clipped is None:
                    return None
                box = clipped
            else:
                return None
        return box
    finally:
        graph.budget.release("max_live_bytes", size)


def _box_predicate(graph: PredicateGraph, fields: tuple[str, ...], box: Box) -> str:
    return graph.and_(
        *(
            graph.interval(
                field,
                unrank(
                    lo,
                    graph.fields[field].data_type,
                    timezone=graph.fields[field].timezone,
                ),
                unrank(
                    hi,
                    graph.fields[field].data_type,
                    timezone=graph.fields[field].timezone,
                ),
                lower_closed=True,
                upper_closed=True,
            )
            for field, (lo, hi) in zip(fields, box)
        )
    )


def _numeric_overlay(
    graph: PredicateGraph,
    fields: tuple[str, ...],
    groups: tuple[tuple[str, frozenset[str]], ...],
    boxes: t.Mapping[str, Box],
) -> tuple[Fragment, ...]:
    work: list[tuple[Box, frozenset[str]]] = []
    for predicate, members in groups:
        source_box = boxes[predicate]
        old = work
        next_stage = []
        pending = [source_box]
        for box, old_members in old:
            _work(graph, max(1, len(fields)), "overlay.subtract")
            _state(graph, 2 * len(fields) + 1, "overlay.expand")
            next_stage.extend(
                (piece, old_members) for piece in subtract_box(box, source_box)
            )
            overlap = intersect_box(box, source_box)
            if overlap is not None:
                next_stage.append(
                    (overlap, _union_members(graph, old_members, members))
                )
            remainder = []
            for piece in pending:
                _state(graph, max(1, 2 * len(fields)), "overlay.pending")
                _work(graph, max(1, len(fields)), "overlay.pending")
                remainder.extend(subtract_box(piece, box))
            pending = remainder
        _state(graph, len(pending), "overlay.complete")
        next_stage.extend((piece, members) for piece in pending)
        work = next_stage
    return tuple(
        Fragment(_box_predicate(graph, fields, box), members) for box, members in work
    )


def covered_overlay(
    reasoner: Reasoner,
    domain_id: str,
    sources: t.Mapping[str, str],
    *,
    order: t.Sequence[str] | None = None,
) -> Overlay:
    """Discover complete covered memberships under a bounded result lifetime."""
    with _allocation_scope(reasoner.graph) as allocation:
        overlay = _covered_overlay(reasoner, domain_id, sources, order=order)
        memberships = {id(members): members for _, members in overlay.groups}
        memberships.update(
            (id(fragment.contributors), fragment.contributors)
            for fragment in overlay.fragments
        )
        live = (
            512
            + 128 * len(overlay.sources)
            + 128 * len(overlay.groups)
            + 128 * len(overlay.fragments)
            + sum(128 + 64 * len(members) for members in memberships.values())
        )
        lease = allocation.retain(
            1 + len(overlay.groups) + len(overlay.fragments), live
        )
        object.__setattr__(overlay, "_lease", lease)
        return overlay


def _covered_overlay(
    reasoner: Reasoner,
    domain_id: str,
    sources: t.Mapping[str, str],
    *,
    order: t.Sequence[str] | None = None,
) -> Overlay:
    """Complete every source obligation; discover only occurring memberships."""
    graph = reasoner.graph
    if domain_id not in graph.nodes:
        raise ValueError("Unknown compilation domain predicate")
    if order is not None and (
        isinstance(order, (str, bytes)) or not isinstance(order, t.Sequence)
    ):
        raise ValueError("Source traversal must be a sequence of UUIDs")
    _state(graph, len(sources), "source.registry")
    source_order = tuple(sorted(sources)) if order is None else tuple(order)
    if len(source_order) != len(sources) or set(source_order) != set(sources):
        raise ValueError("Source traversal must contain every source exactly once")
    clipped: dict[str, str] = {}
    grouped: dict[str, set[str]] = {}
    for source in source_order:
        try:
            valid_id = str(uuid.UUID(source)) == source
        except (ValueError, TypeError, AttributeError):
            valid_id = False
        if not valid_id:
            raise ValueError("Sources require canonical UUID strings")
        if sources[source] not in graph.nodes:
            raise ValueError("Unknown source predicate")
        predicate = graph.and_(domain_id, sources[source])
        clipped[source] = predicate
        if not reasoner.is_empty(predicate):
            grouped.setdefault(predicate, set()).add(source)
    groups = tuple(
        (predicate, frozenset(members)) for predicate, members in grouped.items()
    )
    fields = _ordered(graph)
    boxes = {predicate: _as_box(graph, predicate, fields) for predicate, _ in groups}
    if all(box is not None for box in boxes.values()):
        fragments = _numeric_overlay(
            graph, fields, groups, t.cast(dict[str, Box], boxes)
        )
    else:
        work: tuple[Fragment, ...] = ()
        for predicate, members in groups:
            old = work
            covered = graph.or_(*(fragment.predicate_id for fragment in old))
            _state(graph, 2 * len(old) + 1, "overlay.expand")
            next_stage = []
            for fragment in old:
                for region, contributors in (
                    (
                        reasoner.difference(fragment.predicate_id, predicate),
                        fragment.contributors,
                    ),
                    (
                        reasoner.intersect(fragment.predicate_id, predicate),
                        _union_members(graph, fragment.contributors, members),
                    ),
                ):
                    if not reasoner.is_empty(region):
                        next_stage.append(Fragment(region, contributors))
            pending = reasoner.difference(predicate, covered)
            if not reasoner.is_empty(pending):
                next_stage.append(Fragment(pending, members))
            work = tuple(next_stage)
        fragments = work
    return Overlay(domain_id, clipped, groups, fragments)


def _authority(
    reasoner: Reasoner, overlay: Overlay, fragments: tuple[Fragment, ...]
) -> dict[frozenset[str], str]:
    graph = reasoner.graph
    by_members: dict[frozenset[str], list[str]] = {}
    for fragment in fragments:
        if fragment.predicate_id not in graph.nodes:
            raise ValueError("Unknown completed fragment predicate")
        members = fragment.contributors
        if not members or not members <= overlay.sources.keys():
            raise ValueError("Unknown or empty source membership")
        if not reasoner.is_empty(
            reasoner.difference(fragment.predicate_id, overlay.domain_id)
        ):
            raise ValueError("Completed fragment lies outside compilation domain")
        for source, predicate in overlay.sources.items():
            invalid = (
                reasoner.difference(fragment.predicate_id, predicate)
                if source in members
                else reasoner.intersect(fragment.predicate_id, predicate)
            )
            if not reasoner.is_empty(invalid):
                raise ValueError("Completed fragment has incorrect source membership")
        if reasoner.is_empty(fragment.predicate_id):
            continue
        by_members.setdefault(members, []).append(fragment.predicate_id)

    for source, predicate in overlay.sources.items():
        covered = graph.or_(
            *(
                fragment.predicate_id
                for fragment in fragments
                if source in fragment.contributors
            )
        )
        if not reasoner.is_empty(reasoner.difference(predicate, covered)):
            raise ValueError("Completed fragments omit source applicability")
    output = {}
    reachable = frozenset(source for _, group in overlay.groups for source in group)
    for members, predicates in by_members.items():
        if not members <= reachable:
            raise ValueError("Unreachable source claims a contributor edge")
        terms = [overlay.domain_id]
        for predicate, group in overlay.groups:
            included = members & group
            if included and included != group:
                raise ValueError(
                    "Equivalent clipped source group must be included all-or-none"
                )
            terms.append(predicate if included else graph.not_(predicate))
        authoritative = graph.and_(*terms)
        if not reasoner.equivalent(graph.or_(*predicates), authoritative):
            raise ValueError(
                "Completed membership region disagrees with source authority"
            )
        output[members] = authoritative
    return output


def _scaffold(reasoner: Reasoner, predicate: str) -> tuple[tuple[Box, str], ...]:
    graph = reasoner.graph
    fields = _ordered(graph)
    _state(graph, 2 * len(fields), "scaffold.cuts")
    cuts = {
        field: set((_bounds(graph, field)[0], _bounds(graph, field)[1] + 1))
        for field in fields
    }
    pending = [predicate]
    seen: set[str] = set()
    while pending:
        identifier = pending.pop()
        if identifier in seen:
            continue
        _state(graph, 1, "scaffold.atoms")
        seen.add(identifier)
        node = graph.nodes[identifier]
        op = node["op"]
        if op in ("and", "or"):
            _state(graph, len(node["args"]), "scaffold.frontier")
            pending.extend(node["args"])
        elif op == "not":
            _state(graph, 1, "scaffold.frontier")
            pending.append(node["arg"])
        elif op == "interval":
            lower, upper = _interval_bounds(graph, node)
            _state(graph, 2, "scaffold.cuts")
            cuts[node["field"]].update((lower, upper + 1))
        elif op == "in" and node["field"] in cuts:
            for value in node["values"]:
                point = _rank(graph, node["field"], value)
                _state(graph, 2, "scaffold.cuts")
                cuts[node["field"]].update((point, point + 1))
    _state(graph, sum(len(values) for values in cuts.values()), "scaffold.bands")
    bands = {}
    for field, points in cuts.items():
        ordered = sorted(points)
        bands[field] = tuple(
            (lower, upper - 1) for lower, upper in zip(ordered, ordered[1:])
        )

    restricted: dict[tuple[str, str, int, int], str] = {}

    def restrict(identifier: str, field: str, lower: int, upper: int) -> str:
        _state(graph, 1, "scaffold.frontier")
        work = [(identifier, False)]
        while work:
            current, ready = work.pop()
            key = current, field, lower, upper
            if key in restricted:
                continue
            _work(graph, 1, "scaffold.substitute")
            node = graph.nodes[current]
            op = node["op"]
            children = (
                node["args"]
                if op in ("and", "or")
                else (node["arg"],)
                if op == "not"
                else ()
            )
            if children and not ready:
                _state(graph, 1 + len(children), "scaffold.frontier")
                _work(graph, len(children), "scaffold.substitute")
                work.append((current, True))
                work.extend((child, False) for child in children)
                continue
            _state(graph, 1, "scaffold.substitute")
            if op in ("and", "or"):
                result = getattr(graph, op + "_")(
                    *(restricted[child, field, lower, upper] for child in children)
                )
            elif op == "not":
                result = graph.not_(restricted[node["arg"], field, lower, upper])
            elif op == "interval" and node["field"] == field:
                lo, hi = _interval_bounds(graph, node)
                result = graph.true if lo <= lower and upper <= hi else graph.false
            elif op == "in" and node["field"] == field:
                _work(graph, len(node["values"]), "scaffold.set_substitute")
                result = (
                    graph.true
                    if lower == upper
                    and any(
                        _rank(graph, field, value) == lower for value in node["values"]
                    )
                    else graph.false
                )
            else:
                result = current
            restricted[key] = result
        return restricted[identifier, field, lower, upper]

    # Intern suffixes by depth and immediate bands. Child tokens avoid both
    # recursive traversal and recursive equality of deeply nested structures.
    suffixes: list[t.Any] = []
    suffix_ids: dict[t.Any, int] = {}

    def frame(depth: int, residual: str, prefix: tuple[str, ...]) -> t.Any:
        _work(graph, 1, "scaffold.prefix")
        _state(graph, 1, "scaffold.prefix")
        if reasoner.is_empty(graph.and_(residual, *prefix)):
            return None
        if depth == len(fields):
            return residual
        return [depth, residual, prefix, 0, []]

    def finish(parent: t.Any, suffix: int | str | None) -> None:
        if suffix is None:
            return
        lower, upper = bands[fields[parent[0]]][parent[3] - 1]
        output = parent[4]
        if output and output[-1][1] + 1 == lower and output[-1][2] == suffix:
            output[-1] = (output[-1][0], upper, suffix)
        else:
            _state(graph, 1, "scaffold.suffix")
            output.append((lower, upper, suffix))

    root = frame(0, predicate, ())
    stack = [root] if isinstance(root, list) else []
    while stack:
        current = stack[-1]
        depth, residual, prefix, index, output = current
        field = fields[depth]
        if index == len(bands[field]):
            if output:
                _state(graph, 1 + len(output), "scaffold.suffix_cache")
                rows = tuple(output)
                key = depth, rows
                token = suffix_ids.get(key)
                if token is None:
                    token = len(suffixes)
                    suffix_ids[key] = token
                    suffixes.append(rows)
            else:
                token = None
            stack.pop()
            if stack:
                finish(stack[-1], token)
            else:
                root = token
            continue
        lower, upper = bands[field][index]
        current[3] += 1
        suffix_id = restrict(residual, field, lower, upper)
        band = _box_predicate(graph, (field,), ((lower, upper),))
        child = frame(depth + 1, suffix_id, prefix + (band,))
        if isinstance(child, list):
            stack.append(child)
        else:
            finish(current, child)

    boxes = []
    pending_boxes = [(root, 0, ())] if root is not None else []
    while pending_boxes:
        token, depth, prefix = pending_boxes.pop()
        if depth == len(fields):
            _state(graph, 1, "scaffold.boxes")
            boxes.append((prefix, token))
            continue
        rows = suffixes[token]
        _state(graph, len(rows), "scaffold.frontier")
        pending_boxes.extend(
            (suffix, depth + 1, prefix + ((lower, upper),))
            for lower, upper, suffix in reversed(rows)
        )
    return tuple(boxes)


def _face_neighbours(left: Box, right: Box) -> bool:
    separated = 0
    for (lo, hi), (other_lo, other_hi) in zip(left, right):
        if max(lo, other_lo) <= min(hi, other_hi):
            continue
        if hi + 1 != other_lo and other_hi + 1 != lo:
            return False
        separated += 1
        if separated > 1:
            return False
    return True


def _components(graph: PredicateGraph, boxes: list[Box]) -> tuple[tuple[Box, ...], ...]:
    _state(graph, len(boxes), "components.union_find")
    parents = list(range(len(boxes)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for left in range(len(boxes)):
        for right in range(left + 1, len(boxes)):
            _work(graph, max(1, len(boxes[left])), "components.pairs")
            if _face_neighbours(boxes[left], boxes[right]):
                left_root, right_root = root(left), root(right)
                if left_root != right_root:
                    parents[right_root] = left_root
    grouped: dict[int, list[Box]] = {}
    for index, box in enumerate(boxes):
        grouped.setdefault(root(index), []).append(box)
    return tuple(sorted(tuple(sorted(component)) for component in grouped.values()))


def finalize_regions(
    reasoner: Reasoner,
    overlay: Overlay,
    *,
    fragments: t.Iterable[Fragment] | None = None,
) -> tuple[CellRegion, ...]:
    """Return canonical cells while releasing temporary scaffold storage."""
    with _allocation_scope(reasoner.graph) as allocation:
        cells = _finalize_regions(reasoner, overlay, fragments=fragments)
        if cells:
            borrowed = {id(members) for _, members in overlay.groups}
            borrowed.update(id(fragment.contributors) for fragment in overlay.fragments)
            memberships = {
                id(cell.contributors): cell.contributors
                for cell in cells
                if id(cell.contributors) not in borrowed
            }
            live = (
                256
                + 128 * len(cells)
                + sum(128 + 64 * len(members) for members in memberships.values())
            )
            lease = allocation.retain(len(cells), live, overlay._lease)
            for cell in cells:
                object.__setattr__(cell, "_lease", lease)
        return cells


def _finalize_regions(
    reasoner: Reasoner,
    overlay: Overlay,
    *,
    fragments: t.Iterable[Fragment] | None = None,
) -> tuple[CellRegion, ...]:
    """Reconcile full source authority, then emit canonical compound predicates."""
    graph = reasoner.graph
    completed_rows = []
    for fragment in overlay.fragments if fragments is None else fragments:
        _state(graph, 1, "authority.fragments")
        completed_rows.append(fragment)
    completed = tuple(completed_rows)
    authoritative = _authority(reasoner, overlay, completed)
    fields = _ordered(graph)
    output = []
    for members, predicate in sorted(
        authoritative.items(), key=lambda item: tuple(sorted(item[0]))
    ):
        graph.budget.reserve(
            "max_contributor_edges",
            len(members),
            phase="final.edges",
            units="source edges",
        )
        by_residual: dict[str, list[Box]] = {}
        for box, residual in _scaffold(reasoner, predicate):
            by_residual.setdefault(residual, []).append(box)
        for residual in sorted(by_residual):
            for component in _components(graph, by_residual[residual]):
                box_union = graph.or_(
                    *(_box_predicate(graph, fields, box) for box in component)
                )
                canonical = graph.and_(box_union, residual)
                if reasoner.is_empty(canonical):
                    raise ValueError("Normalizer emitted an empty component")
                _state(graph, 1, "final.cells")
                output.append(CellRegion(canonical, members))
    for index, cell in enumerate(output):
        for other_index in range(index + 1, len(output)):
            other = output[other_index]
            if not reasoner.is_empty(graph.and_(cell.predicate_id, other.predicate_id)):
                raise ValueError("Normalizer emitted overlapping cells")
    for source, predicate in overlay.sources.items():
        region = graph.or_(
            *(cell.predicate_id for cell in output if source in cell.contributors)
        )
        if not reasoner.equivalent(region, predicate):
            raise ValueError("Normalizer changed source applicability")
    return tuple(
        sorted(
            output,
            key=lambda cell: (
                tuple(sorted(cell.contributors)),
                canonical_bytes(dict(graph.nodes[cell.predicate_id])),
            ),
        )
    )
