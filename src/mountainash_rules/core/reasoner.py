"""Exact Boolean decision search for predicate-1 graphs.

The solver deliberately works over discrete scalar ranks and symbolic DFA
languages.  It never samples numeric values or words to establish feasibility.
"""

from __future__ import annotations

from dataclasses import dataclass
import itertools
import typing as t

from mountainash_rules.core.constants import NOT_SET, UNKNOWN, DataType
from mountainash_rules.core.language import StringLanguage
from mountainash_rules.core.predicates import PredicateGraph
from mountainash_rules.core.scalar import decode_scalar, rank, rank_bounds, unrank


@dataclass(frozen=True)
class _Ranks:
    """A finite union of closed rank bands."""

    bands: tuple[tuple[int, int], ...]

    @classmethod
    def universe(cls, lower: int, upper: int) -> _Ranks:
        return cls(((lower, upper),))

    @classmethod
    def points(cls, points: t.Iterable[int]) -> _Ranks:
        ordered = sorted(set(points))
        bands: list[tuple[int, int]] = []
        for point in ordered:
            if bands and point == bands[-1][1] + 1:
                bands[-1] = (bands[-1][0], point)
            else:
                bands.append((point, point))
        return cls(tuple(bands))

    def intersect(self, other: _Ranks) -> _Ranks:
        result: list[tuple[int, int]] = []
        left = right = 0
        while left < len(self.bands) and right < len(other.bands):
            lo = max(self.bands[left][0], other.bands[right][0])
            hi = min(self.bands[left][1], other.bands[right][1])
            if lo <= hi:
                result.append((lo, hi))
            if self.bands[left][1] < other.bands[right][1]:
                left += 1
            else:
                right += 1
        return _Ranks(tuple(result))

    def complement(self, lower: int, upper: int) -> _Ranks:
        start = lower
        result: list[tuple[int, int]] = []
        for lo, hi in self.bands:
            if start < lo:
                result.append((start, lo - 1))
            start = max(start, hi + 1)
        if start <= upper:
            result.append((start, upper))
        return _Ranks(tuple(result))


class _UnionFind:
    def __init__(self, items: t.Iterable[str]) -> None:
        self.parent = {item: item for item in items}

    def find(self, item: str) -> str:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: str, right: str) -> None:
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[max(left, right)] = min(left, right)


class Reasoner:
    """Complete decision procedures for the supported predicate-1 fragment."""

    def __init__(self, graph: PredicateGraph) -> None:
        if not isinstance(graph, PredicateGraph):
            raise TypeError("graph must be PredicateGraph")
        self.graph = graph

    def intersect(self, left_id: str, right_id: str) -> str:
        return self.graph.and_(left_id, right_id)

    def difference(self, left_id: str, right_id: str) -> str:
        return self.graph.and_(left_id, self.graph.not_(right_id))

    def equivalent(self, left_id: str, right_id: str) -> bool:
        return self.is_empty(
            self.graph.or_(
                self.difference(left_id, right_id), self.difference(right_id, left_id)
            )
        )

    def is_empty(self, predicate_id: str) -> bool:
        return self._search(((predicate_id, False),), (), recover=False) is None

    def _reserve(self, amount: int = 1, phase: str = "theory") -> None:
        budget = self.graph.budget
        budget.reserve("max_theory_states", amount, phase=phase, units="theory states")
        try:
            budget.reserve("max_work", amount, phase=phase, units="theory work units")
        finally:
            budget.release("max_theory_states", amount)

    def witness(self, predicate_id: str) -> t.Mapping[str, t.Any] | None:
        result = self._search(((predicate_id, False),), ())
        if result is not None:
            size = 2 + sum(
                128
                + 6 * len(name)
                + (6 * len(value) if isinstance(value, str) else 128)
                for name, value in result.items()
            )
            self.graph.budget.reserve(
                "max_output_bytes",
                size,
                phase="theory_witness",
                units="maximum encoded bytes",
            )
        return result

    def _search(
        self,
        pending: tuple[tuple[str, bool], ...],
        atoms: tuple[tuple[str, bool], ...],
        *,
        recover: bool = True,
    ) -> dict[str, t.Any] | None:
        budget = self.graph.budget
        stack: list[t.Any] = []

        def push(
            rest: t.Sequence[tuple[str, bool]],
            known: t.Mapping[str, bool],
            extra: tuple[str, bool] | None = None,
        ) -> None:
            size = 512 + 128 * (len(rest) + len(known) + (extra is not None))
            budget.reserve(
                "max_theory_states", 1, phase="boolean_search", units="live branches"
            )
            try:
                budget.reserve(
                    "max_live_bytes", size, phase="boolean_search", units="bytes"
                )
            except Exception:
                budget.release("max_theory_states", 1)
                raise
            work = list(rest)
            if extra is not None:
                work.append(extra)
            stack.append([work, dict(known), size])

        try:
            push(pending, dict(atoms))
            while stack:
                frame = stack.pop()
                work, known, _ = frame
                try:
                    while work:
                        self._reserve(1, "boolean_search")
                        identifier, negated = work.pop()
                        node = self.graph.nodes[identifier]
                        op = node["op"]
                        if op in {"true", "false"}:
                            if (op == "true") == negated:
                                break
                            continue
                        if op == "not":
                            budget.reserve(
                                "max_live_bytes",
                                128,
                                phase="boolean_search",
                                units="bytes",
                            )
                            frame[2] += 128
                            work.append((node["arg"], not negated))
                            continue
                        if op in {"and", "or"}:
                            if (op == "and") != negated:
                                size = 128 * len(node["args"])
                                budget.reserve(
                                    "max_live_bytes",
                                    size,
                                    phase="boolean_search",
                                    units="bytes",
                                )
                                frame[2] += size
                                work.extend((child, negated) for child in node["args"])
                                continue
                            for child in reversed(node["args"]):
                                push(work, known, (child, negated))
                            break
                        if identifier in known:
                            if known[identifier] != negated:
                                break
                            continue
                        budget.reserve(
                            "max_live_bytes", 128, phase="boolean_search", units="bytes"
                        )
                        frame[2] += 128
                        known[identifier] = negated
                        if (
                            self._branch_witness(
                                tuple(known.items()), numeric_only=True
                            )
                            is None
                        ):
                            break
                    else:
                        result = self._branch_witness(
                            tuple(known.items()), recover=recover
                        )
                        if result is not None:
                            return result
                finally:
                    budget.release("max_live_bytes", frame[2])
                    budget.release("max_theory_states", 1)
            return None
        finally:
            for frame in stack:
                budget.release("max_live_bytes", frame[2])
                budget.release("max_theory_states", 1)

    def _branch_witness(
        self,
        atoms: tuple[tuple[str, bool], ...],
        *,
        numeric_only: bool = False,
        recover: bool = True,
    ) -> dict[str, t.Any] | None:
        budget = self.graph.budget
        count = len(self.graph.fields) + len(atoms)
        budget.reserve(
            "max_work", len(atoms), phase="theory_workspace", units="constraint scans"
        )
        elements = sum(
            len(self.graph.nodes[identifier].get("values", ()))
            for identifier, _ in atoms
        )
        size = 1024 + 512 * (count + elements)
        budget.reserve(
            "max_theory_states",
            count,
            phase="theory_workspace",
            units="live constraints",
        )
        try:
            budget.reserve(
                "max_live_bytes", size, phase="theory_workspace", units="bytes"
            )
            try:
                with self.graph.native_scope():
                    return self._branch_witness_impl(
                        atoms, numeric_only=numeric_only, recover=recover
                    )
            finally:
                budget.release("max_live_bytes", size)
        finally:
            budget.release("max_theory_states", count)

    def _branch_witness_impl(
        self, atoms: tuple[tuple[str, bool], ...], *, numeric_only: bool, recover: bool
    ) -> dict[str, t.Any] | None:
        fields = self.graph.fields
        equality = _UnionFind(fields)
        comparisons: list[tuple[dict[str, t.Any], bool]] = []
        for identifier, negated in atoms:
            node = self.graph.nodes[identifier]
            if node["op"] == "compare" and (
                (node["relation"] == "eq" and not negated)
                or (node["relation"] == "ne" and negated)
            ):
                equality.union(node["left"], node["right"])
            else:
                comparisons.append((dict(node), negated))
        roots = {field: equality.find(field) for field in fields}
        root_type: dict[str, DataType] = {}
        for field, root in roots.items():
            datatype = fields[field].data_type
            prior = root_type.setdefault(root, datatype)
            if prior is not datatype:
                return None
        ordered_domains: dict[str, _Ranks] = {}
        string_constraints: dict[str, list[tuple[StringLanguage, bool]]] = {}
        for root, datatype in root_type.items():
            representative = next(field for field in fields if roots[field] == root)
            if datatype is DataType.STR:
                string_constraints[root] = []
            else:
                lo, hi = rank_bounds(datatype, timezone=fields[representative].timezone)
                ordered_domains[root] = _Ranks.universe(lo, hi)
        inequalities: list[tuple[str, str, str]] = []
        for node, negated in comparisons:
            op = node["op"]
            if (
                numeric_only
                and fields[node["left"] if op == "compare" else node["field"]].data_type
                is DataType.STR
            ):
                continue
            if op == "compare":
                relation = (
                    self._negated_relation(node["relation"])
                    if negated
                    else node["relation"]
                )
                left, right = roots[node["left"]], roots[node["right"]]
                if relation == "eq":
                    # Equalities are unioned before unary domain collection.  A
                    # negated equality after a separate merge is impossible.
                    if left != right:
                        return None
                elif relation == "ne":
                    if left == right:
                        return None
                    inequalities.append((left, right, "ne"))
                else:
                    if left == right:
                        if relation in {"lt", "gt"}:
                            return None
                    else:
                        inequalities.append((left, right, relation))
                continue
            field = node["field"]
            root = roots[field]
            datatype = root_type[root]
            if op == "language":
                language = self.graph.languages[node["language_id"]]
                string_constraints[root].append((language, negated))
                continue
            if op == "eq":
                value = decode_scalar(dict(node["value"]))
                if datatype is DataType.STR:
                    string_constraints[root].append((self._literal(value), negated))
                else:
                    self._constrain_ranks(
                        ordered_domains,
                        root,
                        _Ranks.points(
                            (rank(value, datatype, timezone=fields[field].timezone),)
                        ),
                        negated,
                        fields[field].timezone,
                    )
                continue
            if op == "in":
                values = [decode_scalar(dict(value)) for value in node["values"]]
                if datatype is DataType.STR:
                    language = self._empty_language()
                    for value in values:
                        language = self._native(
                            "language_union",
                            lambda language=language, value=value: language.union(
                                self._literal(value), limits=self.graph.language_limits
                            ),
                        )
                    string_constraints[root].append((language, negated))
                else:
                    ranks = (
                        rank(value, datatype, timezone=fields[field].timezone)
                        for value in values
                    )
                    self._constrain_ranks(
                        ordered_domains,
                        root,
                        _Ranks.points(ranks),
                        negated,
                        fields[field].timezone,
                    )
                continue
            if op == "interval":
                minimum, maximum = rank_bounds(
                    datatype, timezone=fields[field].timezone
                )
                lower = (
                    minimum
                    if node["lower"] is None
                    else rank(
                        decode_scalar(dict(node["lower"])),
                        datatype,
                        timezone=fields[field].timezone,
                    )
                )
                upper = (
                    maximum
                    if node["upper"] is None
                    else rank(
                        decode_scalar(dict(node["upper"])),
                        datatype,
                        timezone=fields[field].timezone,
                    )
                )
                self._constrain_ranks(
                    ordered_domains,
                    root,
                    _Ranks(((lower, upper),)),
                    negated,
                    fields[field].timezone,
                )
                continue
            raise ValueError(f"Unsupported predicate op {op}")
        if any(not domain.bands for domain in ordered_domains.values()):
            return None
        numeric = self._solve_ordered(ordered_domains, inequalities, roots, fields)
        if numeric is None:
            return None
        if numeric_only:
            return {}
        strings = self._solve_strings(string_constraints, inequalities, root_type)
        if strings is None:
            return None
        if not recover:
            return {}
        result: dict[str, t.Any] = {}
        for field, declaration in fields.items():
            root = roots[field]
            if declaration.data_type is DataType.STR:
                result[field] = strings[root]
            else:
                result[field] = unrank(
                    numeric[root], declaration.data_type, timezone=declaration.timezone
                )
        return result

    @staticmethod
    def _negated_relation(relation: str) -> str:
        return {"eq": "ne", "ne": "eq", "lt": "ge", "le": "gt", "gt": "le", "ge": "lt"}[
            relation
        ]

    def _constrain_ranks(
        self,
        domains: dict[str, _Ranks],
        root: str,
        constraint: _Ranks,
        negated: bool,
        timezone: str | None,
    ) -> None:
        datatype = (
            self.graph.fields[root].data_type if root in self.graph.fields else None
        )
        # Root names are physical fields by canonical union-find choice.
        if datatype is None:
            datatype = next(
                field.data_type
                for name, field in self.graph.fields.items()
                if name == root
            )
        lo, hi = rank_bounds(datatype, timezone=timezone)
        accepted = constraint.complement(lo, hi) if negated else constraint
        domains[root] = domains[root].intersect(accepted)

    def _solve_ordered(
        self,
        domains: dict[str, _Ranks],
        inequalities: list[tuple[str, str, str]],
        roots: dict[str, str],
        fields: t.Mapping[str, t.Any],
    ) -> dict[str, int] | None:
        numeric_roots = tuple(sorted(domains))
        relations = [
            item for item in inequalities if item[0] in domains and item[1] in domains
        ]
        fixed_edges = []
        distinct = []
        for left, right, relation in relations:
            if relation == "ne":
                distinct.append((left, right))
            elif relation in {"lt", "le"}:
                fixed_edges.append((left, right, int(relation == "lt")))
            elif relation in {"gt", "ge"}:
                fixed_edges.append((right, left, int(relation == "gt")))
        # Disequality is the exact disjunction x<y OR y<x.  Orient every
        # disequality under the same difference constraints; never repair a
        # witness after solving and silently violate an earlier inequality.
        for selected in itertools.product(
            *(domains[root].bands for root in numeric_roots)
        ):
            bounds = dict(zip(numeric_roots, selected))
            for orientations in itertools.product((False, True), repeat=len(distinct)):
                self._reserve(1, "rank_interval_choice")
                values = {root: band[0] for root, band in bounds.items()}
                edges = fixed_edges + [
                    (right, left, 1) if reverse else (left, right, 1)
                    for (left, right), reverse in zip(distinct, orientations)
                ]
                possible = True
                changed = False
                for _ in range(len(values) + 1):
                    changed = False
                    for left, right, distance in edges:
                        self._reserve(1, "rank_difference")
                        required = values[left] + distance
                        if values[right] < required:
                            if required > bounds[right][1]:
                                possible = False
                                break
                            values[right] = required
                            changed = True
                    if not possible or not changed:
                        break
                if possible and not changed:
                    return values
        return None

    def _native(self, phase: str, callback: t.Callable[[], t.Any]) -> t.Any:
        return self.graph._native_call(phase, callback)  # graph owns the shared ledger

    def _literal(self, value: str) -> StringLanguage:
        return self._native(
            "language_literal",
            lambda: StringLanguage.literal(value, limits=self.graph.language_limits),
        )

    def _empty_language(self) -> StringLanguage:
        return self._native(
            "language_empty",
            lambda: StringLanguage.empty(limits=self.graph.language_limits),
        )

    def _field_universe(self) -> StringLanguage:
        language = self._native(
            "language_universal",
            lambda: StringLanguage.universal(limits=self.graph.language_limits),
        )
        for reserved in (UNKNOWN, NOT_SET):
            literal = self._literal(reserved)
            language = self._native(
                "language_reserved_exclusion",
                lambda language=language, literal=literal: language.difference(
                    literal, limits=self.graph.language_limits
                ),
            )
        return language

    def _solve_strings(
        self,
        constraints: dict[str, list[tuple[StringLanguage, bool]]],
        inequalities: list[tuple[str, str, str]],
        root_types: dict[str, DataType],
    ) -> dict[str, str] | None:
        roots = tuple(sorted(constraints))
        if not roots:
            return {}
        budget = self.graph.budget
        live = 0
        states = 0

        def retain(size: int, count: int = 0) -> None:
            nonlocal live, states
            budget.reserve(
                "max_theory_states",
                count,
                phase="language_candidates",
                units="representatives",
            )
            states += count
            budget.reserve(
                "max_live_bytes", size, phase="language_candidates", units="bytes"
            )
            live += size

        try:
            retain(512 + 512 * len(roots) + 128 * len(inequalities))
            languages: dict[str, StringLanguage] = {}
            for root in roots:
                language = self._field_universe()
                for constraint, negated in constraints[root]:
                    if negated:
                        constraint = self._native(
                            "language_complement",
                            lambda constraint=constraint: constraint.complement(
                                limits=self.graph.language_limits
                            ),
                        )
                    language = self._native(
                        "language_constraint",
                        lambda language=language,
                        constraint=constraint: language.intersection(
                            constraint, limits=self.graph.language_limits
                        ),
                    )
                if self._native(
                    "language_empty_check",
                    lambda language=language: language.is_empty(
                        limits=self.graph.language_limits
                    ),
                ):
                    return None
                languages[root] = language
            disequalities = [
                (left, right)
                for left, right, relation in inequalities
                if relation == "ne" and left in languages and right in languages
            ]
            # Exact, disjoint membership classes need at most N distinct words
            # each for N variables.  No sampled words or cardinality inference.
            candidate_sets: dict[str, list[str]] = {root: [] for root in roots}
            for membership in itertools.product((False, True), repeat=len(roots)):
                self._reserve(1, "language_membership_class")
                if not any(membership):
                    continue
                language = self._field_universe()
                members = tuple(
                    root for root, selected in zip(roots, membership) if selected
                )
                for root, selected in zip(roots, membership):
                    constraint = languages[root]
                    if not selected:
                        constraint = self._native(
                            "language_complement",
                            lambda constraint=constraint: constraint.complement(
                                limits=self.graph.language_limits
                            ),
                        )
                    language = self._native(
                        "language_membership_intersection",
                        lambda language=language,
                        constraint=constraint: language.intersection(
                            constraint, limits=self.graph.language_limits
                        ),
                    )
                for _ in range(len(roots)):
                    if self._native(
                        "language_class_empty",
                        lambda language=language: language.is_empty(
                            limits=self.graph.language_limits
                        ),
                    ):
                        break
                    witness = self._native(
                        "language_class_witness",
                        lambda language=language: language.witness(
                            limits=self.graph.language_limits
                        ),
                    )
                    if witness is None:
                        break
                    retain(128 + 32 * len(members), 1)
                    for root in members:
                        candidate_sets[root].append(witness)
                    literal = self._literal(witness)
                    language = self._native(
                        "language_remove_witness",
                        lambda language=language, literal=literal: language.difference(
                            literal, limits=self.graph.language_limits
                        ),
                    )
            if any(not values for values in candidate_sets.values()):
                return None
            # Classes are disjoint and representatives are removed exactly:
            # candidate lists are already duplicate-free.
            for values in candidate_sets.values():
                budget.reserve(
                    "max_work",
                    len(values) * max(1, len(values).bit_length()),
                    phase="language_candidates",
                    units="sorting comparisons",
                )
                values.sort()
            order = sorted(roots, key=lambda root: (len(candidate_sets[root]), root))
            chosen: dict[str, str] = {}
            positions = [0] * len(order)
            index = 0
            while index >= 0:
                if index == len(order):
                    return chosen
                root = order[index]
                values = candidate_sets[root]
                if positions[index] == len(values):
                    positions[index] = 0
                    chosen.pop(root, None)
                    index -= 1
                    continue
                value = values[positions[index]]
                positions[index] += 1
                self._reserve(1, "language_disequality")
                if any(
                    (root == left and chosen.get(right) == value)
                    or (root == right and chosen.get(left) == value)
                    for left, right in disequalities
                ):
                    continue
                chosen[root] = value
                index += 1
            return None
        finally:
            budget.release("max_live_bytes", live)
            budget.release("max_theory_states", states)
