"""Strict, immutable declarations for exact-accumulator evidence.

This module deliberately contains data contracts and resource accounting only.  It
neither produces validation reports nor serves accumulator outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
import typing as t

from pydantic import (
    BaseModel,
    ConfigDict,
    field_serializer,
    field_validator,
    model_validator,
)

from mountainash_rules.core.constants import DataType
from mountainash_rules.core.language import LanguageLimits

_COUNTER_MAX = (1 << 63) - 1


def _label(value: t.Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError(f"{name} must not contain a surrogate")
    return value


def _counter(value: t.Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= _COUNTER_MAX:
        raise ValueError(f"{name} must be a non-Boolean integer in [0, {_COUNTER_MAX}]")
    return value


def _typed_id(value: t.Any, kind: str, name: str) -> str:
    from mountainash_rules.core.codec import validate_id

    if not isinstance(value, str):
        raise ValueError(f"{name} must be a {kind} ID")
    return validate_id(value, kind)


def _unique_sorted(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {name}")
    if values != tuple(sorted(values)):
        raise ValueError(f"{name} must be sorted")
    return values


def _canonical_unique(values: t.Sequence[t.Any], name: str) -> tuple[t.Any, ...]:
    """Require a semantic set's canonical-byte order and uniqueness."""
    from mountainash_rules.core.codec import canonical_bytes

    encoded = tuple(
        canonical_bytes(
            value.model_dump(mode="json")
            if isinstance(value, BaseModel)
            else _thaw(value)
        )
        for value in values
    )
    if len(encoded) != len(set(encoded)):
        raise ValueError(f"Duplicate {name}")
    if encoded != tuple(sorted(encoded)):
        raise ValueError(f"{name} must be canonical-byte sorted")
    return tuple(values)


def _self_id(record: _ExactModel, kind: str) -> None:
    """Bind an evidence record's typed ID to its own semantic payload."""
    from mountainash_rules.core.codec import content_id

    payload = record.model_dump(mode="python", exclude={"id", "annotations"})
    if content_id(kind, payload) != record.id:
        raise ValueError(f"{type(record).__name__} ID does not match its payload")


def _freeze(value: t.Any) -> t.Any:
    if isinstance(value, BaseModel):
        return value
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: t.Any) -> t.Any:
    """Copy frozen containers only at the serialization boundary."""
    if isinstance(value, t.Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_thaw(item) for item in value)
    return value


def _frozen_annotations(value: t.Any) -> t.Mapping[str, t.Any]:
    """Validate and iteratively freeze optional nonsemantic evidence diagnostics."""
    if value is None:
        raise ValueError("annotations must be an object when supplied")
    from mountainash_rules.core.codec import _normalize_annotations

    normalized = _normalize_annotations(value)
    holder: dict[str, t.Any] = {}
    stack: list[tuple[str, t.Any, t.Any, t.Any]] = [
        ("visit", normalized, holder, "value")
    ]

    def assign(parent: t.Any, key: t.Any, item: t.Any) -> None:
        parent[key] = item

    while stack:
        action, source, parent, key = stack.pop()
        if action == "close_mapping":
            assign(parent, key, MappingProxyType(source))
            continue
        if action == "close_sequence":
            assign(parent, key, tuple(source))
            continue
        if isinstance(source, dict):
            copied: dict[str, t.Any] = {}
            stack.append(("close_mapping", copied, parent, key))
            for child_key, child in source.items():
                stack.append(("visit", child, copied, child_key))
        elif isinstance(source, list):
            copied = [None] * len(source)
            stack.append(("close_sequence", copied, parent, key))
            for index in range(len(source)):
                stack.append(("visit", source[index], copied, index))
        else:
            assign(parent, key, source)
    return holder["value"]


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    @field_serializer("*", mode="wrap", check_fields=False)
    def _serialize_frozen(self, value: t.Any, handler: t.Any) -> t.Any:
        return handler(_thaw(value))

    @field_validator("schema_version", mode="before", check_fields=False)
    @classmethod
    def _schema_version_type(cls, value: t.Any) -> t.Any:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value


class DomainField(_ExactModel):
    name: str
    data_type: DataType
    timezone: str | None = None

    _name = field_validator("name")(_label)

    @field_validator("data_type", mode="before")
    @classmethod
    def _data_type(cls, value: t.Any) -> DataType:
        try:
            return DataType(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("data_type is unsupported") from exc

    @model_validator(mode="after")
    def _timezone(self) -> DomainField:
        if self.data_type is DataType.DATETIME:
            if self.timezone not in {"naive", "utc"}:
                raise ValueError("datetime fields require timezone naive or utc")
        elif self.timezone is not None:
            raise ValueError("timezone is only permitted for datetime fields")
        return self


class ContextField(DomainField):
    required: bool

    @field_validator("required", mode="before")
    @classmethod
    def _required(cls, value: t.Any) -> bool:
        if type(value) is not bool:
            raise ValueError("required must be a Boolean")
        return value


class DomainDefinition(_ExactModel):
    schema_version: int
    domain_id: str
    fields: tuple[DomainField, ...]
    predicate_id: str

    _domain_id = field_validator("domain_id")(_label)

    @field_validator("schema_version")
    @classmethod
    def _schema(cls, value: int) -> int:
        if value != 1:
            raise ValueError("schema_version must be 1")
        return value

    @field_validator("predicate_id")
    @classmethod
    def _predicate(cls, value: str) -> str:
        return _typed_id(value, "predicate", "predicate_id")

    @field_validator("fields")
    @classmethod
    def _fields(cls, values: tuple[DomainField, ...]) -> tuple[DomainField, ...]:
        names = tuple(field.name for field in values)
        _unique_sorted(names, "domain field names")
        return values


class ResolutionProfile(_ExactModel):
    profile_id: str
    mode: str
    output_fields: tuple[str, ...]
    provenance: str
    dimensions: tuple[str, ...]
    allow_dont_care: tuple[str, ...]
    promise: str
    on_unresolved: str | None = None

    _profile_id = field_validator("profile_id")(_label)

    @field_validator("output_fields", "dimensions", "allow_dont_care", mode="before")
    @classmethod
    def _names(cls, value: t.Any) -> tuple[str, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("profile name collections must be arrays")
        return tuple(_label(item, "profile name") for item in value)

    @model_validator(mode="after")
    def _semantics(self) -> ResolutionProfile:
        if self.mode not in {"resolve", "candidates"}:
            raise ValueError("mode must be resolve or candidates")
        if self.provenance not in {"none", "contributors", "cell"}:
            raise ValueError("provenance is unsupported")
        if len(self.output_fields) != len(set(self.output_fields)):
            raise ValueError("Duplicate output_fields")
        _unique_sorted(self.dimensions, "dimensions")
        _unique_sorted(self.allow_dont_care, "allow_dont_care")
        if not set(self.allow_dont_care).issubset(self.dimensions):
            raise ValueError("allow_dont_care must be contained in dimensions")
        if self.mode == "resolve":
            if not self.output_fields:
                raise ValueError("resolve profiles require output_fields")
            if self.promise not in {"definite_outcome", "allow_unresolved"}:
                raise ValueError("resolve profiles require a resolve promise")
            if self.on_unresolved not in {"return", "withhold", "reject"}:
                raise ValueError("resolve profiles require on_unresolved")
        else:
            if self.promise != "candidate_only":
                raise ValueError("candidate profiles require candidate_only promise")
            if self.on_unresolved is not None:
                raise ValueError("candidate profiles forbid on_unresolved")
        return self


class ContextContract(_ExactModel):
    schema_version: int
    contract_id: str
    fields: tuple[ContextField, ...]
    domain_ref: str
    profiles: tuple[ResolutionProfile, ...]

    _contract_id = field_validator("contract_id")(_label)
    _domain_ref = field_validator("domain_ref")(_label)

    @field_validator("schema_version")
    @classmethod
    def _schema(cls, value: int) -> int:
        if value != 1:
            raise ValueError("schema_version must be 1")
        return value

    @field_validator("fields")
    @classmethod
    def _fields(cls, values: tuple[ContextField, ...]) -> tuple[ContextField, ...]:
        _unique_sorted(tuple(field.name for field in values), "ContextField names")
        return values

    @field_validator("profiles")
    @classmethod
    def _profiles(
        cls, values: tuple[ResolutionProfile, ...]
    ) -> tuple[ResolutionProfile, ...]:
        if not values:
            raise ValueError("ContextContract requires profiles")
        _unique_sorted(tuple(profile.profile_id for profile in values), "profile IDs")
        return values


class SemanticVersions(_ExactModel):
    scalar: str
    predicate: str
    language: str
    numeric: str
    canonical: str
    normalization: str

    @model_validator(mode="after")
    def _versions(self) -> SemanticVersions:
        expected = {
            "scalar": "scalar-1",
            "predicate": "predicate-1",
            "language": "language-1",
            "numeric": "numeric-1",
            "canonical": "canonical-json-1",
            "normalization": "normalization-2",
        }
        if self.model_dump() != expected:
            raise ValueError("Unsupported semantic versions")
        return self


class BindingVersions(_ExactModel):
    content: SemanticVersions
    binding: str
    checker: str

    @model_validator(mode="after")
    def _versions(self) -> BindingVersions:
        if self.binding != "binding-1" or self.checker != "binding-checker-1":
            raise ValueError("Unsupported binding versions")
        return self


class Issue(_ExactModel):
    field: str | None
    dimension: str | None
    code: str
    message: str

    _code = field_validator("code")(_label)
    _message = field_validator("message")(_label)

    @field_validator("field", "dimension")
    @classmethod
    def _optional_label(cls, value: str | None) -> str | None:
        return _label(value, "issue location") if value is not None else None


class OutcomeRecord(_ExactModel):
    status: str
    reason: str
    binding_id: str | None
    contract_id: str | None
    profile_id: str | None
    values: t.Mapping[str, t.Any] | None
    cell_id: str | None
    contributor_ids: tuple[str, ...] | None
    may_have_no_match: bool | None
    observations: t.Mapping[str, t.Any] | None
    issues: tuple[Issue, ...]

    @field_validator("may_have_no_match", mode="before")
    @classmethod
    def _no_match_type(cls, value: t.Any) -> bool | None:
        if value is not None and type(value) is not bool:
            raise ValueError("may_have_no_match must be Boolean or null")
        return value

    @field_validator("values", "observations", mode="after")
    @classmethod
    def _frozen_mapping(
        cls, value: t.Mapping[str, t.Any] | None, info: t.Any
    ) -> t.Mapping[str, t.Any] | None:
        from mountainash_rules.core.codec import canonical_bytes
        from mountainash_rules.core.scalar import decode_scalar

        if value is not None and not isinstance(value, dict):
            raise ValueError("outcome mappings must be JSON objects")
        if value is None:
            return None
        copied = dict(value)
        if info.field_name == "values":
            for output_name, scalar in copied.items():
                _label(output_name, "outcome output name")
                decode_scalar(scalar, allow_reserved=True)
        else:
            canonical_bytes(copied)
        return _freeze(copied)

    @model_validator(mode="after")
    def _outcome(self) -> OutcomeRecord:
        allowed_reasons = {
            "decision": {"established"},
            "no_match": {"no_rule_matches", "insufficient_context"},
            "unresolved": {"insufficient_context"},
            "candidates": {"candidate_query"},
            "invalid_context": {
                "invalid_type",
                "missing_required",
                "forbidden_projection",
                "domain_contradiction",
                "guard_failed",
            },
            "rejected": {"insufficient_context"},
        }
        if self.status not in allowed_reasons:
            raise ValueError("Unsupported outcome status")
        if self.reason not in allowed_reasons[self.status]:
            raise ValueError("Outcome reason is incompatible with status")
        if self.contract_id is None or self.profile_id is None:
            raise ValueError("outcomes require contract_id and profile_id")
        if self.binding_id is None:
            if self.status != "invalid_context":
                raise ValueError("analyzed outcomes require binding_id")
        else:
            _typed_id(self.binding_id, "binding", "binding_id")
        _label(self.contract_id, "contract_id")
        _label(self.profile_id, "profile_id")
        if self.cell_id is not None:
            _typed_id(self.cell_id, "cell", "cell_id")
        if self.contributor_ids is not None:
            from mountainash_rules.core.codec import _source_id

            if not self.contributor_ids:
                raise ValueError("contributor_ids must be non-empty when present")
            for source_id in self.contributor_ids:
                _source_id(source_id)
            _unique_sorted(self.contributor_ids, "contributor IDs")
        analyzed = self.status != "invalid_context"
        if analyzed != (self.may_have_no_match is not None):
            raise ValueError(
                "may_have_no_match must be present exactly after outcome analysis"
            )
        if self.status == "decision":
            if not self.values:
                raise ValueError("decisions require non-empty values")
        elif self.values is not None:
            raise ValueError("only decisions carry values")
        if self.status != "decision" and (
            self.cell_id is not None or self.contributor_ids is not None
        ):
            raise ValueError("only decisions carry definite cell or contributor IDs")
        if self.status == "invalid_context":
            if not self.issues:
                raise ValueError("invalid_context outcomes require issues")
        elif self.issues:
            raise ValueError("only invalid_context outcomes carry issues")
        return self


class CarriedResult(t.Protocol):
    """Structural inspection surface retained by request-scoped exceptions."""

    @property
    def outcome(self) -> OutcomeRecord:
        """Return the normalized outcome record."""

    @property
    def values(self) -> t.Mapping[str, t.Any] | None:
        """Return decoded decision values when established."""

    @property
    def output_fields(self) -> tuple[str, ...]:
        """Return requested logical output names in profile order."""

    @property
    def candidate_cells(self) -> t.Any | None:
        """Return possible exact cells when analysis ran."""

    @property
    def candidate_contributors(self) -> t.Any | None:
        """Return possible contributor edges when analysis ran."""


class InvalidContextError(ValueError):
    """A single request failed declared-type/admission validation."""

    result: CarriedResult | None

    def __init__(self, outcome: OutcomeRecord) -> None:
        if (
            not isinstance(outcome, OutcomeRecord)
            or outcome.status != "invalid_context"
        ):
            raise ValueError("InvalidContextError requires an invalid_context outcome")
        self.outcome = outcome
        self.result = None
        super().__init__(f"Invalid context: {outcome.reason}")


class UnresolvedContextError(ValueError):
    """A resolve profile rejected insufficient but otherwise valid context."""

    result: CarriedResult | None

    def __init__(self, outcome: OutcomeRecord) -> None:
        if not isinstance(outcome, OutcomeRecord) or outcome.status != "rejected":
            raise ValueError("UnresolvedContextError requires a rejected outcome")
        self.outcome = outcome
        self.result = None
        super().__init__(f"Unresolved context: {outcome.reason}")


class ExactLimits(_ExactModel):
    language: LanguageLimits
    max_input_bytes: int
    max_output_bytes: int
    max_work: int
    max_live_bytes: int
    max_predicate_nodes: int
    max_dfa_states: int
    max_dfa_transitions: int
    max_theory_states: int
    max_regions: int
    max_scopes: int
    max_source_scope_edges: int
    max_contributor_edges: int
    max_word_rows: int
    max_numeric_bits: int
    max_witnesses: int

    @field_validator("language", mode="before")
    @classmethod
    def _language(cls, value: t.Any) -> LanguageLimits:
        if isinstance(value, LanguageLimits):
            return value
        if not isinstance(value, dict):
            raise ValueError("language must be LanguageLimits")
        try:
            return LanguageLimits(**value)
        except (TypeError, ValueError) as exc:
            raise ValueError("language must be valid LanguageLimits") from exc

    @field_validator(
        "max_input_bytes",
        "max_output_bytes",
        "max_work",
        "max_live_bytes",
        "max_predicate_nodes",
        "max_dfa_states",
        "max_dfa_transitions",
        "max_theory_states",
        "max_regions",
        "max_scopes",
        "max_source_scope_edges",
        "max_contributor_edges",
        "max_word_rows",
        "max_numeric_bits",
        "max_witnesses",
        mode="before",
    )
    @classmethod
    def _limit(cls, value: int, info: t.Any) -> int:
        return _counter(value, info.field_name)


class ExactResourceError(ValueError):
    """A whole-operation resource reservation could not be honoured."""

    def __init__(
        self,
        *,
        operation: str,
        phase: str,
        counter: str,
        limit: int,
        observed: int,
        requested: int,
        units: str,
        partition_identity: t.Mapping[str, t.Any] | None = None,
        source_id: str | None = None,
        domain_ref: str | None = None,
        profile_id: str | None = None,
        scope_id: str | None = None,
    ) -> None:
        for name, value in (
            ("operation", operation),
            ("phase", phase),
            ("counter", counter),
            ("units", units),
        ):
            _label(value, name)
        for name, value in (("limit", limit), ("observed", observed)):
            _counter(value, name)
        if type(requested) is not int or requested < 0:
            raise ValueError("requested must be a non-Boolean non-negative integer")
        self.operation = operation
        self.phase = phase
        self.counter = counter
        self.limit = limit
        self.observed = observed
        self.requested = requested
        self.units = units
        self.partition_identity = (
            _freeze(partition_identity) if partition_identity is not None else None
        )
        self.source_id = source_id
        self.domain_ref = domain_ref
        self.profile_id = profile_id
        self.scope_id = scope_id
        super().__init__(
            f"{operation}.{phase}: {counter} requested {requested} {units}; limit is {limit}"
        )


class ExactCapabilityError(ValueError):
    """A required exact operation is unsupported by the selected backend."""

    def __init__(
        self,
        *,
        operation: str,
        backend: str,
        feature: str,
        scalar_type: str | None = None,
        predicate_op: str | None = None,
    ) -> None:
        self.operation = _label(operation, "operation")
        self.backend = _label(backend, "backend")
        self.feature = _label(feature, "feature")
        self.scalar_type = scalar_type
        self.predicate_op = predicate_op
        super().__init__(f"{operation}: {backend} does not support {feature}")


@dataclass(frozen=True, slots=True)
class Reservation:
    counter: str
    amount: int


class OperationBudget:
    """One mutable operation-scoped ledger over immutable explicit limits."""

    _CUMULATIVE = frozenset(
        {
            "max_input_bytes",
            "max_output_bytes",
            "max_work",
            "max_scopes",
            "max_source_scope_edges",
            "max_contributor_edges",
            "max_word_rows",
            "max_witnesses",
        }
    )

    def __init__(self, limits: ExactLimits, operation: str) -> None:
        if not isinstance(limits, ExactLimits):
            raise ValueError("limits must be ExactLimits")
        self.limits = limits
        self.operation = _label(operation, "operation")
        self._usage = {
            name: 0 for name in type(limits).model_fields if name != "language"
        }

    def check(
        self, counter: str, amount: int, *, phase: str, units: str, **scope: t.Any
    ) -> int:
        """Validate prospective usage without consuming cumulative capacity."""
        if counter not in self._usage:
            raise ValueError(f"Unknown ExactLimits counter: {counter}")
        if type(amount) is not int or amount < 0:
            raise ValueError("amount must be a non-Boolean non-negative integer")
        current = self._usage[counter]
        requested = current + amount
        limit = getattr(self.limits, counter)
        if requested > limit:
            raise ExactResourceError(
                operation=self.operation,
                phase=phase,
                counter=counter,
                limit=limit,
                observed=current,
                requested=requested,
                units=units,
                partition_identity=scope.get("partition_identity"),
                source_id=scope.get("source_id"),
                domain_ref=scope.get("domain_ref"),
                profile_id=scope.get("profile_id"),
                scope_id=scope.get("scope_id"),
            )
        return requested

    def reserve(
        self, counter: str, amount: int, *, phase: str, units: str, **scope: t.Any
    ) -> Reservation:
        self._usage[counter] = self.check(
            counter, amount, phase=phase, units=units, **scope
        )
        return Reservation(counter, amount)

    def release(self, counter: str, amount: int) -> None:
        if counter not in self._usage:
            raise ValueError(f"Unknown ExactLimits counter: {counter}")
        amount = _counter(amount, "amount")
        if counter in self._CUMULATIVE:
            return
        if amount > self._usage[counter]:
            raise ValueError("cannot release more than reserved")
        self._usage[counter] -= amount


class Scope(_ExactModel):
    partition_refs: tuple[t.Mapping[str, t.Any], ...]
    domain_refs: tuple[str, ...]
    profile_refs: tuple[t.Mapping[str, str], ...]

    @field_validator("partition_refs", mode="before")
    @classmethod
    def _partition_refs(cls, value: t.Any) -> t.Any:
        from mountainash_rules.core.codec import _partition

        if not isinstance(value, list | tuple):
            raise ValueError("partition_refs must be an array")
        for reference in value:
            if not isinstance(reference, t.Mapping):
                raise ValueError("partition references must be objects")
            materialized = _thaw(reference)
            if not isinstance(materialized, dict):
                raise ValueError("partition references must be objects")
            materialized["key_values"] = list(materialized["key_values"])
            _partition(materialized)
        return value

    @field_validator("profile_refs", mode="before")
    @classmethod
    def _profile_refs(cls, value: t.Any) -> t.Any:
        if not isinstance(value, list | tuple):
            raise ValueError("profile_refs must be an array")
        for reference in value:
            if not isinstance(reference, t.Mapping) or set(reference) != {
                "contract_id",
                "profile_id",
            }:
                raise ValueError("Invalid profile reference")
            _label(reference["contract_id"], "contract_id")
            _label(reference["profile_id"], "profile_id")
        return value

    @field_validator("partition_refs", "profile_refs", mode="after")
    @classmethod
    def _frozen_refs(
        cls, value: tuple[t.Mapping[str, t.Any], ...]
    ) -> tuple[t.Mapping[str, t.Any], ...]:
        return tuple(_freeze(dict(item)) for item in value)

    @model_validator(mode="after")
    def _scope(self) -> Scope:
        if not self.partition_refs or not self.domain_refs:
            raise ValueError("Scope requires partition_refs and domain_refs")
        _unique_sorted(
            tuple(_label(item, "domain_ref") for item in self.domain_refs),
            "scope domain_refs",
        )
        _canonical_unique(self.partition_refs, "partition_refs")
        _canonical_unique(self.profile_refs, "profile_refs")
        return self


def _scope_refs(values: t.Iterable[t.Mapping[str, t.Any]]) -> set[bytes]:
    """Return canonical identities for one scope reference category."""
    from mountainash_rules.core.codec import canonical_bytes

    return {canonical_bytes(dict(value)) for value in values}


def _scope_covers_nonprofiles(outer: Scope, inner: Scope) -> bool:
    """Return exact partition/domain containment without profile semantics."""
    return _scope_refs(outer.partition_refs) >= _scope_refs(
        inner.partition_refs
    ) and set(outer.domain_refs) >= set(inner.domain_refs)


def _scope_covers(outer: Scope, inner: Scope) -> bool:
    """Return exact canonical containment for all scope reference categories."""
    return _scope_covers_nonprofiles(outer, inner) and _scope_refs(
        outer.profile_refs
    ) >= _scope_refs(inner.profile_refs)


class RequiredCheck(_ExactModel):
    stage: str
    check_id: str
    scope: Scope

    @model_validator(mode="after")
    def _required_check(self) -> RequiredCheck:
        if self.stage not in {"source", "compiled"}:
            raise ValueError("RequiredCheck stage is unsupported")
        _label(self.check_id, "check_id")
        return self


class CoverageRequirement(_ExactModel):
    requirement_id: str
    domain_ref: str
    region_predicate_id: str
    scope: Scope
    severity: str

    @model_validator(mode="after")
    def _coverage(self) -> CoverageRequirement:
        _label(self.requirement_id, "requirement_id")
        _label(self.domain_ref, "domain_ref")
        _typed_id(self.region_predicate_id, "predicate", "region_predicate_id")
        if self.severity not in {"error", "warning"}:
            raise ValueError("CoverageRequirement severity is unsupported")
        if self.domain_ref not in self.scope.domain_refs:
            raise ValueError("CoverageRequirement domain_ref must be in scope")
        return self


class DiagnosticRule(_ExactModel):
    stage: str
    check_id: str
    code: str
    scope: Scope
    severity: str
    witness_kind: str
    max_witnesses: int

    @field_validator("max_witnesses", mode="before")
    @classmethod
    def _witness_limit(cls, value: t.Any) -> int:
        return _counter(value, "max_witnesses")

    @model_validator(mode="after")
    def _diagnostic(self) -> DiagnosticRule:
        if self.stage not in {"source", "compiled"}:
            raise ValueError("DiagnosticRule stage is unsupported")
        _label(self.check_id, "check_id")
        _label(self.code, "code")
        if self.severity not in {"error", "warning"} or (
            self.stage == "compiled" and self.severity != "error"
        ):
            raise ValueError("DiagnosticRule severity is invalid for stage")
        if self.witness_kind not in {"none", "point", "pair"}:
            raise ValueError("DiagnosticRule witness_kind is unsupported")
        if self.witness_kind == "none" and self.max_witnesses != 0:
            raise ValueError("none witness_kind requires max_witnesses=0")
        return self


class ValidationPolicy(_ExactModel):
    schema_version: int
    policy_id: str
    required_checks: tuple[RequiredCheck, ...]
    coverage_requirements: tuple[CoverageRequirement, ...]
    diagnostic_rules: tuple[DiagnosticRule, ...]

    @model_validator(mode="after")
    def _policy(self) -> ValidationPolicy:
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        _label(self.policy_id, "policy_id")
        identifiers = tuple(item.requirement_id for item in self.coverage_requirements)
        _unique_sorted(identifiers, "coverage requirement IDs")
        _canonical_unique(self.required_checks, "required_checks")
        _canonical_unique(self.diagnostic_rules, "diagnostic_rules")
        return self


class AnalysisInput(_ExactModel):
    schema_version: int
    id: str
    ruleset_id: str
    source_id_field: str
    annotations: t.Mapping[str, t.Any] | None = None

    @field_validator("annotations", mode="after")
    @classmethod
    def _annotations(
        cls, value: t.Mapping[str, t.Any] | None
    ) -> t.Mapping[str, t.Any] | None:
        return _frozen_annotations(value)

    source_bundle_digest: str
    compilation_domain_ref: str
    domain_digests: t.Mapping[str, str]
    metadata_digest: str
    aggregate_digest: str
    routing_digest: str
    contracts: tuple[t.Mapping[str, str], ...]
    validation_policy: ValidationPolicy
    semantic_versions: SemanticVersions

    @field_validator("domain_digests", mode="after")
    @classmethod
    def _frozen_mapping(cls, value: t.Mapping[str, str]) -> t.Mapping[str, str]:
        return _freeze(dict(value))

    @field_validator("contracts", mode="after")
    @classmethod
    def _frozen_contracts(
        cls, value: tuple[t.Mapping[str, str], ...]
    ) -> tuple[t.Mapping[str, str], ...]:
        return tuple(_freeze(dict(item)) for item in value)

    @model_validator(mode="after")
    def _analysis(self) -> AnalysisInput:
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        for value, kind, name in (
            (self.id, "analysis-input", "id"),
            (self.source_bundle_digest, "source-bundle", "source_bundle_digest"),
            (self.metadata_digest, "metadata", "metadata_digest"),
            (self.aggregate_digest, "aggregates", "aggregate_digest"),
            (self.routing_digest, "routing", "routing_digest"),
        ):
            _typed_id(value, kind, name)
        _label(self.ruleset_id, "ruleset_id")
        _label(self.source_id_field, "source_id_field")
        _label(self.compilation_domain_ref, "compilation_domain_ref")
        if self.compilation_domain_ref not in self.domain_digests:
            raise ValueError("compilation_domain_ref is absent from domain_digests")
        for label, identifier in self.domain_digests.items():
            _label(label, "domain digest label")
            _typed_id(identifier, "domain", "domain digest")
        labels: list[str] = []
        for pair in self.contracts:
            if set(pair) != {"contract_id", "contract_digest"}:
                raise ValueError("Invalid ContractDigestPair")
            labels.append(_label(pair["contract_id"], "contract_id"))
            _typed_id(pair["contract_digest"], "contract", "contract_digest")
        _unique_sorted(tuple(labels), "contract IDs")
        _self_id(self, "analysis-input")
        return self


class ReportCheck(_ExactModel):
    check_id: str
    scope: Scope
    status: str
    complete: bool
    finding_ids: tuple[str, ...]

    @field_validator("complete", mode="before")
    @classmethod
    def _complete_type(cls, value: t.Any) -> bool:
        if type(value) is not bool:
            raise ValueError("complete must be Boolean")
        return value

    @model_validator(mode="after")
    def _check(self) -> ReportCheck:
        _label(self.check_id, "check_id")
        if self.status not in {
            "passed",
            "findings",
            "not_required",
            "unsupported",
            "resource_exhausted",
        }:
            raise ValueError("Unsupported report check status")
        if type(self.complete) is not bool:
            raise ValueError("complete must be Boolean")
        if self.status in {"passed", "not_required"} and (
            not self.complete or self.finding_ids
        ):
            raise ValueError(
                "passed/not_required checks must be complete and finding-free"
            )
        if self.status == "findings" and (not self.complete or not self.finding_ids):
            raise ValueError("findings checks must be complete with findings")
        if self.status in {"unsupported", "resource_exhausted"} and self.complete:
            raise ValueError(
                "incomplete check required for capability/resource failures"
            )
        for identifier in self.finding_ids:
            _typed_id(identifier, "finding", "finding_id")
        _unique_sorted(self.finding_ids, "check finding IDs")
        return self


class ValidationReport(_ExactModel):
    schema_version: int
    id: str
    analysis_input_id: str
    annotations: t.Mapping[str, t.Any] | None = None

    @field_validator("annotations", mode="after")
    @classmethod
    def _annotations(
        cls, value: t.Mapping[str, t.Any] | None
    ) -> t.Mapping[str, t.Any] | None:
        return _frozen_annotations(value)

    stage: str
    artifact_id: str | None
    validator: t.Mapping[str, str]
    scope: Scope
    checks: tuple[ReportCheck, ...]
    finding_ids: tuple[str, ...]

    @field_validator("validator", mode="after")
    @classmethod
    def _frozen_validator(cls, value: t.Mapping[str, str]) -> t.Mapping[str, str]:
        if not isinstance(value, dict):
            raise ValueError("validator must be an object")
        return _freeze(dict(value))

    @model_validator(mode="after")
    def _report(self) -> ValidationReport:
        from mountainash_rules.core.codec import canonical_bytes

        check_keys = tuple(
            (check.check_id, canonical_bytes(check.scope.model_dump(mode="json")))
            for check in self.checks
        )
        if check_keys != tuple(sorted(check_keys)) or len(check_keys) != len(
            set(check_keys)
        ):
            raise ValueError("report checks must be sorted and unique")
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        _typed_id(self.id, "report", "id")
        _typed_id(self.analysis_input_id, "analysis-input", "analysis_input_id")
        if self.stage not in {"source", "compiled"}:
            raise ValueError("Unsupported report stage")
        if self.stage == "source" and self.artifact_id is not None:
            raise ValueError("source reports forbid artifact_id")
        if self.stage == "compiled":
            if self.artifact_id is None:
                raise ValueError("compiled reports require artifact_id")
            _typed_id(self.artifact_id, "artifact", "artifact_id")
        if set(self.validator) != {"validator_id", "semantic_version"}:
            raise ValueError("Invalid validator")
        _label(self.validator["validator_id"], "validator_id")
        _label(self.validator["semantic_version"], "validator semantic_version")
        check_ids = tuple(
            identifier for check in self.checks for identifier in check.finding_ids
        )
        if set(check_ids) != set(self.finding_ids) or len(self.finding_ids) != len(
            set(self.finding_ids)
        ):
            raise ValueError("report finding_ids must equal its checks' unique union")
        _unique_sorted(self.finding_ids, "report finding IDs")
        _self_id(self, "report")
        return self


class WarningApproval(_ExactModel):
    schema_version: int
    id: str
    analysis_input_id: str
    annotations: t.Mapping[str, t.Any] | None = None

    @field_validator("annotations", mode="after")
    @classmethod
    def _annotations(
        cls, value: t.Mapping[str, t.Any] | None
    ) -> t.Mapping[str, t.Any] | None:
        return _frozen_annotations(value)

    report_id: str
    authority_ref: str
    actor_ref: str
    decision: str
    scope: Scope
    warning_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _approval(self) -> WarningApproval:
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        for value, kind, name in (
            (self.id, "approval", "id"),
            (self.analysis_input_id, "analysis-input", "analysis_input_id"),
            (self.report_id, "report", "report_id"),
        ):
            _typed_id(value, kind, name)
        _label(self.authority_ref, "authority_ref")
        _label(self.actor_ref, "actor_ref")
        if self.decision != "approve_warnings" or not self.warning_ids:
            raise ValueError("WarningApproval requires non-empty approve_warnings IDs")
        for identifier in self.warning_ids:
            _typed_id(identifier, "finding", "warning_id")
        _unique_sorted(self.warning_ids, "warning IDs")
        _self_id(self, "approval")
        return self


def _validate_approval_links(
    approvals: t.Iterable[WarningApproval],
    report_by_id: t.Mapping[str, ValidationReport],
    finding_by_id: t.Mapping[str, Finding],
) -> None:
    """Validate reusable approval/report/finding links and scope coverage."""
    for approval in approvals:
        report = report_by_id.get(approval.report_id)
        if report is None or report.stage != "source":
            raise ValueError("WarningApproval must target a source report")
        if report.analysis_input_id != approval.analysis_input_id:
            raise ValueError("WarningApproval report/input mismatch")
        for finding_id in approval.warning_ids:
            finding = finding_by_id.get(finding_id)
            if (
                finding is None
                or finding_id not in report.finding_ids
                or finding.severity != "warning"
            ):
                raise ValueError(
                    "WarningApproval must reference report warning findings"
                )
            if not _scope_covers(approval.scope, finding.scope):
                raise ValueError("WarningApproval scope does not cover finding")


def _validate_scope_material(
    scope: Scope,
    analysis: AnalysisInput,
    routing_partition_keys: t.AbstractSet[bytes],
    profiles_by_contract: t.Mapping[str, t.AbstractSet[str]],
    name: str,
) -> None:
    """Resolve one evidence scope against its owning analysis material."""
    from mountainash_rules.core.codec import canonical_bytes

    if any(
        partition["routing_id"] != analysis.routing_digest
        or canonical_bytes({"key_values": _thaw(partition["key_values"])})
        not in routing_partition_keys
        for partition in scope.partition_refs
    ):
        raise ValueError(f"{name} refers to an unresolved routing partition")
    if not set(scope.domain_refs) <= set(analysis.domain_digests):
        raise ValueError(f"{name} refers to an unresolved analysis domain")
    analysis_contracts = {pair["contract_id"] for pair in analysis.contracts}
    if any(
        profile["contract_id"] not in analysis_contracts
        or profile["profile_id"]
        not in profiles_by_contract.get(profile["contract_id"], frozenset())
        for profile in scope.profile_refs
    ):
        raise ValueError(f"{name} refers to an unresolved analysis profile")


class WitnessRequest(_ExactModel):
    provided_values: t.Mapping[str, t.Mapping[str, t.Any]]
    unavailable_fields: tuple[str, ...]
    dont_care: tuple[str, ...]

    @field_validator("provided_values", mode="after")
    @classmethod
    def _provided_values(
        cls, value: t.Mapping[str, t.Mapping[str, t.Any]]
    ) -> t.Mapping[str, t.Mapping[str, t.Any]]:
        from mountainash_rules.core.scalar import decode_scalar

        frozen: dict[str, t.Mapping[str, t.Any]] = {}
        for field, scalar in value.items():
            _label(field, "provided field")
            decode_scalar(scalar)
            frozen[field] = _freeze(dict(scalar))
        return _freeze(frozen)

    @field_validator("unavailable_fields", "dont_care")
    @classmethod
    def _names(cls, value: tuple[str, ...], info: t.Any) -> tuple[str, ...]:
        return _unique_sorted(
            tuple(_label(item, info.field_name) for item in value), info.field_name
        )

    @model_validator(mode="after")
    def _request(self) -> WitnessRequest:
        if set(self.provided_values) & set(self.unavailable_fields):
            raise ValueError("provided_values and unavailable_fields must be disjoint")
        return self


class Witness(_ExactModel):
    kind: str
    contexts: tuple[t.Mapping[str, t.Mapping[str, t.Any]], ...]
    profile_ref: t.Mapping[str, str] | None
    request: WitnessRequest | None

    @field_validator("contexts", mode="after")
    @classmethod
    def _contexts(
        cls, value: tuple[t.Mapping[str, t.Mapping[str, t.Any]], ...]
    ) -> tuple[t.Mapping[str, t.Mapping[str, t.Any]], ...]:
        from mountainash_rules.core.scalar import decode_scalar

        output: list[t.Mapping[str, t.Mapping[str, t.Any]]] = []
        for context in value:
            frozen: dict[str, t.Mapping[str, t.Any]] = {}
            for field, scalar in context.items():
                _label(field, "context field")
                decode_scalar(scalar)
                frozen[field] = _freeze(dict(scalar))
            output.append(_freeze(frozen))
        return tuple(output)

    @model_validator(mode="after")
    def _witness(self) -> Witness:
        if self.kind not in {"point", "pair"} or len(self.contexts) != (
            1 if self.kind == "point" else 2
        ):
            raise ValueError("Witness kind and context count disagree")
        if (self.profile_ref is None) != (self.request is None):
            raise ValueError(
                "Witness profile_ref and request must be jointly null or present"
            )
        if self.profile_ref is not None:
            if set(self.profile_ref) != {"contract_id", "profile_id"}:
                raise ValueError("Invalid witness profile_ref")
            _label(self.profile_ref["contract_id"], "contract_id")
            _label(self.profile_ref["profile_id"], "profile_id")
            object.__setattr__(self, "profile_ref", _freeze(dict(self.profile_ref)))
        return self


class Finding(_ExactModel):
    schema_version: int
    id: str
    analysis_input_id: str
    stage: str
    annotations: t.Mapping[str, t.Any] | None = None

    @field_validator("annotations", mode="after")
    @classmethod
    def _annotations(
        cls, value: t.Mapping[str, t.Any] | None
    ) -> t.Mapping[str, t.Any] | None:
        return _frozen_annotations(value)

    check_id: str
    code: str
    severity: str
    scope: Scope
    source_ids: tuple[str, ...]
    cell_ids: tuple[str, ...]
    region_predicate_id: str | None
    witnesses: tuple[Witness, ...]
    witnesses_complete: bool

    @field_validator("witnesses_complete", mode="before")
    @classmethod
    def _witnesses_complete_type(cls, value: t.Any) -> bool:
        if type(value) is not bool:
            raise ValueError("witnesses_complete must be Boolean")
        return value

    @model_validator(mode="after")
    def _finding(self) -> Finding:
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        _typed_id(self.id, "finding", "id")
        _typed_id(self.analysis_input_id, "analysis-input", "analysis_input_id")
        if self.stage not in {"source", "compiled"} or self.severity not in {
            "error",
            "warning",
        }:
            raise ValueError("Unsupported finding stage or severity")
        if self.stage == "source" and self.cell_ids:
            raise ValueError("source findings have empty cell_ids")
        if self.stage == "compiled" and self.severity != "error":
            raise ValueError("compiled findings must be errors")
        _label(self.check_id, "check_id")
        _label(self.code, "code")
        from mountainash_rules.core.codec import _source_id

        for source_id in self.source_ids:
            _source_id(source_id)
        _unique_sorted(self.source_ids, "finding source IDs")
        for cell_id in self.cell_ids:
            _typed_id(cell_id, "cell", "cell_id")
        _unique_sorted(self.cell_ids, "finding cell IDs")
        if self.region_predicate_id is not None:
            _typed_id(self.region_predicate_id, "predicate", "region_predicate_id")
        _canonical_unique(self.witnesses, "finding witnesses")
        _self_id(self, "finding")
        return self


class ValidationBundle(_ExactModel):
    schema_version: int
    metadata: t.Mapping[str, t.Any]
    aggregates: t.Mapping[str, t.Any]
    routing: t.Mapping[str, t.Any]
    context_contracts: tuple[t.Mapping[str, t.Any], ...]
    predicates: t.Mapping[str, t.Any]
    validation: t.Mapping[str, t.Any]

    @model_validator(mode="after")
    def _bundle(self, info: t.Any) -> ValidationBundle:
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        from mountainash_rules.core.codec import canonical_bytes, validate_envelope

        context = info.context if isinstance(info.context, dict) else {}
        budget = context.get("budget")
        validate_envelope(self.metadata, "metadata", budget=budget)
        validate_envelope(self.aggregates, "aggregates", budget=budget)
        validate_envelope(self.routing, "routing", budget=budget)
        required_predicates = {
            "schema_version",
            "predicates",
            "languages",
            "domains",
            "source_bundles",
            "source_origins",
            "source_labels",
        }
        required_validation = {
            "schema_version",
            "analysis_inputs",
            "findings",
            "reports",
            "approvals",
            "bindings",
        }
        if (
            set(self.predicates) != required_predicates
            or self.predicates.get("schema_version") != 1
        ):
            raise ValueError("Invalid predicates container")
        if (
            set(self.validation) != required_validation
            or self.validation.get("schema_version") != 1
        ):
            raise ValueError("Invalid validation container")
        envelope_kinds = {
            "predicates": "predicate",
            "languages": "language",
            "domains": "domain",
            "source_bundles": "source-bundle",
        }
        for name, kind in envelope_kinds.items():
            entries = self.predicates[name]
            if not isinstance(entries, list | tuple):
                raise ValueError(f"{name} must be an envelope array")
            identifiers = tuple(
                validate_envelope(entry, kind, budget=budget)["id"] for entry in entries
            )
            _unique_sorted(identifiers, f"{name} envelope IDs")

        source_origins = self.predicates["source_origins"]
        source_labels = self.predicates["source_labels"]
        if not isinstance(source_origins, list | tuple) or not isinstance(
            source_labels, t.Mapping
        ):
            raise ValueError("Invalid source origins or labels container")
        origin_keys: list[tuple[str, str]] = []
        normalized_origins: list[dict[str, t.Any]] = []
        from mountainash_rules.core.codec import _materialize_json, _source_id
        from mountainash_rules.core.scalar import decode_scalar

        for source_id, label in source_labels.items():
            _source_id(source_id)
            _label(label, "source label")
        for raw_origin in source_origins:
            origin = _materialize_json(raw_origin)
            fields = {"source_id", "dimension_name", "predicate_id", "authored_values"}
            if (
                not isinstance(origin, dict)
                or not fields <= set(origin)
                or set(origin) - (fields | {"annotations"})
            ):
                raise ValueError("Invalid source origin fields")
            source_id = _source_id(origin["source_id"])
            dimension_name = _label(origin["dimension_name"], "origin dimension_name")
            origin_keys.append((source_id, dimension_name))
            _typed_id(origin["predicate_id"], "predicate", "origin predicate_id")
            if not isinstance(origin["authored_values"], dict):
                raise ValueError("origin authored_values must be an object")
            normalized = dict(origin)
            if "annotations" in origin:
                normalized["annotations"] = _frozen_annotations(origin["annotations"])
            normalized_origins.append(normalized)
            for column, encoded in origin["authored_values"].items():
                _label(column, "origin source column")
                values = encoded if isinstance(encoded, list) else [encoded]
                for value in values:
                    decode_scalar(value, allow_null=True, allow_reserved=True)
        if origin_keys != sorted(origin_keys) or len(origin_keys) != len(
            set(origin_keys)
        ):
            raise ValueError(
                "source origins must be sorted and unique by source_id/dimension_name"
            )
        contracts = tuple(
            validate_envelope(entry, "contract", budget=budget)
            for entry in self.context_contracts
        )
        contract_models = tuple(
            ContextContract.model_validate(entry["payload"]["contract"])
            for entry in contracts
        )
        contract_keys = tuple(contract.contract_id for contract in contract_models)
        _unique_sorted(contract_keys, "context contract IDs")
        analysis_inputs = tuple(
            AnalysisInput.model_validate(item)
            for item in self.validation["analysis_inputs"]
        )
        findings = tuple(
            Finding.model_validate(item) for item in self.validation["findings"]
        )
        reports = tuple(
            ValidationReport.model_validate(item) for item in self.validation["reports"]
        )
        approvals = tuple(
            WarningApproval.model_validate(item)
            for item in self.validation["approvals"]
        )
        bindings = tuple(
            ContractBinding.model_validate(item) for item in self.validation["bindings"]
        )
        for name, entries in (
            ("analysis input", analysis_inputs),
            ("finding", findings),
            ("report", reports),
            ("approval", approvals),
            ("binding", bindings),
        ):
            _unique_sorted(tuple(entry.id for entry in entries), f"{name} IDs")
        finding_by_id = {entry.id: entry for entry in findings}
        report_by_id = {entry.id: entry for entry in reports}

        domain_labels = tuple(
            entry["payload"]["domain_id"] for entry in self.predicates["domains"]
        )
        if len(domain_labels) != len(set(domain_labels)):
            raise ValueError("Duplicate logical domain IDs")
        domain_ids = {
            entry["payload"]["domain_id"]: entry["id"]
            for entry in self.predicates["domains"]
        }
        contract_ids = {
            model.contract_id: (envelope["id"], model)
            for envelope, model in zip(contracts, contract_models)
        }
        predicate_ids = {entry["id"] for entry in self.predicates["predicates"]}
        language_ids = {entry["id"] for entry in self.predicates["languages"]}
        for envelope in self.predicates["predicates"]:
            node = envelope["payload"]["node"]
            references = (
                node["args"]
                if node["op"] in {"and", "or"}
                else [node["arg"]]
                if node["op"] == "not"
                else []
            )
            if any(identifier not in predicate_ids for identifier in references):
                raise ValueError("Predicate refers to unresolved internal predicate")
            if node["op"] == "language" and node["language_id"] not in language_ids:
                raise ValueError("Predicate refers to unresolved internal language")
        for envelope in self.predicates["domains"]:
            if envelope["payload"]["predicate_id"] not in predicate_ids:
                raise ValueError("Domain refers to unresolved internal predicate")
        for origin in normalized_origins:
            if origin["predicate_id"] not in predicate_ids:
                raise ValueError(
                    "Source origin refers to unresolved internal predicate"
                )
        for contract in contract_models:
            if contract.domain_ref not in domain_ids:
                raise ValueError("ContextContract refers to unresolved internal domain")
        for analysis in analysis_inputs:
            if (
                analysis.metadata_digest != self.metadata["id"]
                or analysis.aggregate_digest != self.aggregates["id"]
                or analysis.routing_digest != self.routing["id"]
            ):
                raise ValueError("AnalysisInput semantic envelope digest mismatch")
            if any(
                domain_ids.get(label) != identifier
                for label, identifier in analysis.domain_digests.items()
            ):
                raise ValueError("AnalysisInput domain digest mismatch")
            for pair in analysis.contracts:
                contract = contract_ids.get(pair["contract_id"])
                if contract is None or contract[0] != pair["contract_digest"]:
                    raise ValueError("AnalysisInput contract digest mismatch")
        source_bundle_sources = {
            envelope["id"]: frozenset(
                source["source_id"] for source in envelope["payload"]["sources"]
            )
            for envelope in self.predicates["source_bundles"]
        }
        available_source_ids = frozenset().union(*source_bundle_sources.values())
        if any(source_id not in available_source_ids for source_id in source_labels):
            raise ValueError("Source label refers to unavailable source")
        if any(
            origin["source_id"] not in available_source_ids
            for origin in normalized_origins
        ):
            raise ValueError("Source origin refers to unavailable source")
        routing_partition_keys = {
            canonical_bytes({"key_values": key_values})
            for key_values in self.routing["payload"]["partition_keys"]
        }
        inputs_by_id = {entry.id: entry for entry in analysis_inputs}
        profiles_by_contract = {
            contract_id: frozenset(profile.profile_id for profile in contract.profiles)
            for contract_id, (_, contract) in contract_ids.items()
        }

        def validate_scope(scope: Scope, analysis: AnalysisInput, name: str) -> None:
            analysis_contract_ids = {pair["contract_id"] for pair in analysis.contracts}
            _validate_scope_material(
                scope,
                analysis,
                routing_partition_keys,
                {
                    contract_id: profiles_by_contract[contract_id]
                    for contract_id in analysis_contract_ids
                },
                name,
            )

        for analysis in analysis_inputs:
            sources = source_bundle_sources.get(analysis.source_bundle_digest)
            if sources is None:
                raise ValueError("AnalysisInput refers to an unresolved source bundle")
            if (
                self.predicates["source_bundles"][
                    next(
                        index
                        for index, envelope in enumerate(
                            self.predicates["source_bundles"]
                        )
                        if envelope["id"] == analysis.source_bundle_digest
                    )
                ]["payload"]["ruleset_id"]
                != analysis.ruleset_id
            ):
                raise ValueError("AnalysisInput source bundle ruleset mismatch")
            for check in analysis.validation_policy.required_checks:
                validate_scope(check.scope, analysis, "RequiredCheck scope")
            for requirement in analysis.validation_policy.coverage_requirements:
                validate_scope(requirement.scope, analysis, "CoverageRequirement scope")
                if requirement.region_predicate_id not in predicate_ids:
                    raise ValueError(
                        "CoverageRequirement refers to unresolved internal predicate"
                    )
            for rule in analysis.validation_policy.diagnostic_rules:
                validate_scope(rule.scope, analysis, "DiagnosticRule scope")
        for finding in findings:
            analysis = inputs_by_id.get(finding.analysis_input_id)
            if analysis is None:
                raise ValueError("Finding refers to unresolved AnalysisInput")
            validate_scope(finding.scope, analysis, "Finding scope")
            if (
                not set(finding.source_ids)
                <= source_bundle_sources[analysis.source_bundle_digest]
            ):
                raise ValueError("Finding refers to unavailable analysis source")
            if (
                finding.region_predicate_id is not None
                and finding.region_predicate_id not in predicate_ids
            ):
                raise ValueError("Finding refers to unresolved internal predicate")
            for witness in finding.witnesses:
                if witness.profile_ref is not None and not _scope_refs(
                    (witness.profile_ref,)
                ) <= _scope_refs(finding.scope.profile_refs):
                    raise ValueError("Witness profile is outside finding scope")
        mandatory_checks = {
            "source": {
                "source_schema",
                "source_identity",
                "source_predicates",
                "source_overlaps",
                "routing",
                "coverage",
                "profiles",
            },
            "compiled": {
                "cell_nonempty",
                "cell_disjointness",
                "source_union",
                "source_membership",
                "output_folds",
                "profile_consistency",
            },
        }
        for report in reports:
            analysis = inputs_by_id.get(report.analysis_input_id)
            if analysis is None:
                raise ValueError("Report refers to unresolved AnalysisInput")
            validate_scope(report.scope, analysis, "ValidationReport scope")
            for check in report.checks:
                validate_scope(check.scope, analysis, "ReportCheck scope")
                if not _scope_covers(report.scope, check.scope):
                    raise ValueError(
                        "ValidationReport scope does not cover ReportCheck"
                    )
            check_ids = {check.check_id for check in report.checks}
            required_ids = mandatory_checks[report.stage] | {
                check.check_id
                for check in analysis.validation_policy.required_checks
                if check.stage == report.stage
            }
            if not required_ids.issubset(check_ids):
                raise ValueError("Report omits mandatory or policy required checks")
            for finding_id in report.finding_ids:
                finding = finding_by_id.get(finding_id)
                if (
                    finding is None
                    or finding.analysis_input_id != report.analysis_input_id
                ):
                    raise ValueError("Report has unresolved or foreign finding")
                if finding.stage != report.stage:
                    raise ValueError("Report/finding stage mismatch")
                linked_checks = tuple(
                    check for check in report.checks if finding.id in check.finding_ids
                )
                if not linked_checks or any(
                    check.check_id != finding.check_id for check in linked_checks
                ):
                    raise ValueError("Report check/finding IDs do not match")
                if any(
                    not _scope_covers(check.scope, finding.scope)
                    for check in linked_checks
                ):
                    raise ValueError("Report check scope does not cover finding")
        for approval in approvals:
            analysis = inputs_by_id.get(approval.analysis_input_id)
            if analysis is None:
                raise ValueError("WarningApproval refers to unresolved AnalysisInput")
            validate_scope(approval.scope, analysis, "WarningApproval scope")
        _validate_approval_links(approvals, report_by_id, finding_by_id)
        binding_pairs: set[tuple[str, str]] = set()
        approval_by_id = {entry.id: entry for entry in approvals}
        for binding in bindings:
            source_report = report_by_id.get(binding.source_report_id)
            compiled_report = report_by_id.get(binding.compiled_report_id)
            if source_report is None or source_report.stage != "source":
                raise ValueError("ContractBinding requires a source report")
            if compiled_report is None or compiled_report.stage != "compiled":
                raise ValueError("ContractBinding requires a compiled report")
            if (
                source_report.analysis_input_id != binding.analysis_input_id
                or compiled_report.analysis_input_id != binding.analysis_input_id
            ):
                raise ValueError("ContractBinding report/input mismatch")
            if compiled_report.artifact_id != binding.artifact_id:
                raise ValueError("ContractBinding artifact/report mismatch")
            pair = (binding.artifact_id, binding.contract_id)
            if pair in binding_pairs:
                raise ValueError("Duplicate active artifact/contract binding")

            analysis = inputs_by_id[binding.analysis_input_id]
            if binding.semantic_versions.content != analysis.semantic_versions:
                raise ValueError("ContractBinding semantic versions mismatch")
            contract = contract_ids.get(binding.contract_id)
            analysis_contracts = {
                pair["contract_id"]: pair["contract_digest"]
                for pair in analysis.contracts
            }
            if (
                contract is None
                or analysis_contracts.get(binding.contract_id)
                != binding.contract_digest
            ):
                raise ValueError(
                    "ContractBinding contract is not owned by AnalysisInput"
                )
            if analysis.domain_digests.get(binding.domain_ref) != binding.domain_digest:
                raise ValueError("ContractBinding domain is not owned by AnalysisInput")
            if contract[1].domain_ref != binding.domain_ref:
                raise ValueError("ContractBinding contract/domain mismatch")
            if domain_ids.get(binding.domain_ref) != binding.domain_digest:
                raise ValueError("ContractBinding domain digest mismatch")
            profile_payloads = {
                profile.profile_id: {
                    "schema_version": 1,
                    "contract_id": binding.contract_id,
                    "profile": profile.model_dump(mode="json", exclude_none=True),
                }
                for profile in contract[1].profiles
            }
            from mountainash_rules.core.codec import content_id

            for profile in binding.authorized_profiles:
                expected = profile_payloads.get(profile["profile_id"])
                if (
                    expected is None
                    or content_id("profile", expected) != profile["profile_digest"]
                ):
                    raise ValueError("ContractBinding profile digest mismatch")
            binding_pairs.add(pair)
            for approval_id in binding.approval_ids:
                approval = approval_by_id.get(approval_id)
                if approval is None or approval.report_id != binding.source_report_id:
                    raise ValueError("ContractBinding approval/report mismatch")
        object.__setattr__(self, "metadata", _freeze(dict(self.metadata)))
        object.__setattr__(self, "aggregates", _freeze(dict(self.aggregates)))
        object.__setattr__(self, "routing", _freeze(dict(self.routing)))
        object.__setattr__(
            self, "context_contracts", tuple(_freeze(dict(item)) for item in contracts)
        )
        object.__setattr__(
            self,
            "predicates",
            _freeze({**dict(self.predicates), "source_origins": normalized_origins}),
        )
        object.__setattr__(
            self,
            "validation",
            _freeze(
                {
                    **dict(self.validation),
                    "analysis_inputs": analysis_inputs,
                    "findings": findings,
                    "reports": reports,
                    "approvals": approvals,
                    "bindings": bindings,
                }
            ),
        )
        return self


class ValidatedBuildInput(_ExactModel):
    schema_version: int
    analysis_input_id: str
    source_report_id: str
    approval_ids: tuple[str, ...]
    bundle: ValidationBundle

    @model_validator(mode="after")
    def _input(self) -> ValidatedBuildInput:
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        _typed_id(self.analysis_input_id, "analysis-input", "analysis_input_id")
        _typed_id(self.source_report_id, "report", "source_report_id")
        _unique_sorted(self.approval_ids, "approval IDs")
        for identifier in self.approval_ids:
            _typed_id(identifier, "approval", "approval_id")
        analysis_inputs = {
            entry.id: entry for entry in self.bundle.validation["analysis_inputs"]
        }
        reports = {entry.id: entry for entry in self.bundle.validation["reports"]}
        approvals = {entry.id: entry for entry in self.bundle.validation["approvals"]}
        if self.analysis_input_id not in analysis_inputs:
            raise ValueError("ValidatedBuildInput refers to unresolved AnalysisInput")
        source_report = reports.get(self.source_report_id)
        if source_report is None or source_report.stage != "source":
            raise ValueError("ValidatedBuildInput requires a source report")
        if source_report.analysis_input_id != self.analysis_input_id:
            raise ValueError("ValidatedBuildInput report/input mismatch")
        for identifier in self.approval_ids:
            approval = approvals.get(identifier)
            if approval is None or approval.report_id != self.source_report_id:
                raise ValueError("ValidatedBuildInput approval/report mismatch")
        return self


class ContractBinding(_ExactModel):
    schema_version: int
    id: str
    artifact_id: str
    analysis_input_id: str
    annotations: t.Mapping[str, t.Any] | None = None

    @field_validator("annotations", mode="after")
    @classmethod
    def _annotations(
        cls, value: t.Mapping[str, t.Any] | None
    ) -> t.Mapping[str, t.Any] | None:
        return _frozen_annotations(value)

    contract_id: str
    contract_digest: str
    domain_ref: str
    domain_digest: str
    authorized_profiles: tuple[t.Mapping[str, str], ...]
    source_report_id: str
    compiled_report_id: str
    approval_ids: tuple[str, ...]
    semantic_versions: BindingVersions

    @field_validator("authorized_profiles", mode="after")
    @classmethod
    def _frozen_profiles(
        cls, value: tuple[t.Mapping[str, str], ...]
    ) -> tuple[t.Mapping[str, str], ...]:
        return tuple(_freeze(dict(item)) for item in value)

    @model_validator(mode="after")
    def _binding(self) -> ContractBinding:
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        for value, kind, name in (
            (self.id, "binding", "id"),
            (self.artifact_id, "artifact", "artifact_id"),
            (self.analysis_input_id, "analysis-input", "analysis_input_id"),
            (self.contract_digest, "contract", "contract_digest"),
            (self.domain_digest, "domain", "domain_digest"),
            (self.source_report_id, "report", "source_report_id"),
            (self.compiled_report_id, "report", "compiled_report_id"),
        ):
            _typed_id(value, kind, name)
        _label(self.contract_id, "contract_id")
        _label(self.domain_ref, "domain_ref")
        if not self.authorized_profiles:
            raise ValueError("authorized_profiles must be non-empty")
        profile_ids: list[str] = []
        for profile in self.authorized_profiles:
            if set(profile) != {"profile_id", "profile_digest"}:
                raise ValueError("Invalid ProfileDigestPair")
            profile_ids.append(_label(profile["profile_id"], "profile_id"))
            _typed_id(profile["profile_digest"], "profile", "profile_digest")
        _unique_sorted(tuple(profile_ids), "authorized profile IDs")
        _unique_sorted(self.approval_ids, "binding approval IDs")
        for approval_id in self.approval_ids:
            _typed_id(approval_id, "approval", "approval_id")
        _self_id(self, "binding")
        return self
