"""Canonical JSON and strict hash-envelope validation for exact records."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import typing as t
import uuid
from decimal import Decimal, InvalidOperation
from types import MappingProxyType

if t.TYPE_CHECKING:
    from mountainash_rules.core.contracts import (
        ExactLimits,
        OperationBudget,
        ValidationBundle,
    )

from pydantic import BaseModel

_COUNTER_MAX = (1 << 63) - 1
_ID_RE = re.compile(r"^(?P<kind>[a-z][a-z-]*):1:(?P<digest>[0-9a-f]{64})$")
_KINDS = frozenset(
    {
        "predicate",
        "language",
        "domain",
        "metadata",
        "aggregates",
        "routing",
        "contract",
        "profile",
        "source-content",
        "source-bundle",
        "contributor-set",
        "cell",
        "artifact",
        "analysis-input",
        "finding",
        "report",
        "approval",
        "binding",
        "scope",
        "source-map",
        "vector",
    }
)

_EVIDENCE_RECORD_KINDS = frozenset(
    {"analysis-input", "finding", "report", "approval", "binding"}
)


def _identity_payload(
    kind: str, payload: t.Mapping[str, t.Any]
) -> t.Mapping[str, t.Any]:
    """Exclude permitted nonsemantic evidence annotations from record identities."""
    if kind in _EVIDENCE_RECORD_KINDS:
        return {key: value for key, value in payload.items() if key != "annotations"}
    return payload


def _fail(message: str) -> t.NoReturn:
    raise ValueError(message)


def _object(value: t.Any, keys: set[str], name: str) -> dict[str, t.Any]:
    if not isinstance(value, dict):
        _fail(f"{name} must be an object")
    actual = set(value)
    if actual != keys:
        unknown = actual - keys
        missing = keys - actual
        if unknown:
            _fail(f"Unknown {name} fields: {sorted(unknown)!r}")
        _fail(f"Missing {name} fields: {sorted(missing)!r}")
    return value


def _source_id(value: t.Any, name: str = "source_id") -> str:
    value = _string(value, name)
    try:
        if str(uuid.UUID(value)) != value:
            _fail(f"{name} must be lowercase hyphenated UUID")
    except ValueError as exc:
        raise ValueError(f"{name} must be lowercase hyphenated UUID") from exc
    return value


def _counter(value: t.Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= _COUNTER_MAX:
        _fail(f"{name} must be a counter")
    return value


def _string(value: t.Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{name} must be a non-empty string")
    _unicode(value)
    return value


def _unicode(value: str) -> None:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        _fail("Invalid Unicode surrogate")


def _scalar(
    value: t.Any, *, allow_null: bool = False, allow_reserved: bool = False
) -> None:
    """Validate scalar-1 without treating a native value as JSON shorthand."""
    from mountainash_rules.core.scalar import decode_scalar

    try:
        decode_scalar(value, allow_null=allow_null, allow_reserved=allow_reserved)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid scalar-1 payload") from exc


def _canonical_annotation_decimal(value: Decimal) -> str:
    """Return the prescribed finite annotation-number spelling."""
    if not value.is_finite():
        _fail("Annotation numbers must be finite")
    sign, digits, exponent = value.as_tuple()
    start = 0
    end = len(digits)
    while start < end and digits[start] == 0:
        start += 1
    if start == end:
        return "0"
    while end > start and digits[end - 1] == 0:
        end -= 1
        exponent += 1
    coefficient = "".join(str(digits[index]) for index in range(start, end))
    prefix = "-" if sign else ""
    if exponent >= 0:
        return prefix + coefficient + ("0" * exponent)
    return f"{prefix}{coefficient}e{exponent}"


def _annotation_decimal_length(value: Decimal) -> int:
    """Measure canonical annotation output without expanding an integer."""
    if not value.is_finite():
        _fail("Annotation numbers must be finite")
    sign, digits, exponent = value.as_tuple()
    start = 0
    end = len(digits)
    while start < end and digits[start] == 0:
        start += 1
    if start == end:
        return 1
    while end > start and digits[end - 1] == 0:
        end -= 1
        exponent += 1
    prefix = 1 if sign else 0
    coefficient_length = end - start
    if exponent >= 0:
        return prefix + coefficient_length + exponent
    return prefix + coefficient_length + 2 + len(str(-exponent))


def _normalize_annotations(value: t.Any) -> dict[str, t.Any]:
    """Admit finite JSON annotations without recursive descent."""
    if not isinstance(value, t.Mapping):
        _fail("annotations must be an object")

    def scalar(item: t.Any) -> t.Any:
        if item is None or type(item) is bool or type(item) is int:
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                _fail("Annotation numbers must be finite")
            try:
                return Decimal(repr(item))
            except InvalidOperation as exc:
                raise ValueError("Invalid annotation number") from exc
        if isinstance(item, Decimal):
            if not item.is_finite():
                _fail("Annotation numbers must be finite")
            return item
        if isinstance(item, str):
            _unicode(item)
            return item
        _fail(f"Unsupported annotation JSON type: {type(item).__name__}")

    result: dict[str, t.Any] = {}
    active = {id(value)}
    stack: list[tuple[t.Iterator[t.Any], t.Any, int, bool]] = [
        (iter(value.items()), result, id(value), True)
    ]
    while stack:
        iterator, destination, identity, is_object = stack[-1]
        try:
            entry = next(iterator)
        except StopIteration:
            stack.pop()
            active.remove(identity)
            continue
        if is_object:
            key, child = entry
            if not isinstance(key, str):
                _fail("annotation object keys must be strings")
            _unicode(key)
        else:
            key, child = None, entry
        if isinstance(child, t.Mapping | list | tuple):
            child_identity = id(child)
            if child_identity in active:
                _fail("cyclic annotation")
            copied: dict[str, t.Any] | list[t.Any]
            copied = {} if isinstance(child, t.Mapping) else []
            if is_object:
                destination[key] = copied
            else:
                destination.append(copied)
            active.add(child_identity)
            stack.append(
                (
                    iter(child.items())
                    if isinstance(child, t.Mapping)
                    else iter(child),
                    copied,
                    child_identity,
                    isinstance(child, t.Mapping),
                )
            )
        elif is_object:
            destination[key] = scalar(child)
        else:
            destination.append(scalar(child))
    return result


def _canonical(value: t.Any, *, annotations: bool = False) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is int:
        return str(value)
    if isinstance(value, Decimal):
        if annotations:
            return _canonical_annotation_decimal(value)
        _fail("JSON numeric decimals are permitted only in annotations")
    if isinstance(value, float):
        _fail("JSON numeric floats are not canonical typed scalars")
    if isinstance(value, str):
        _unicode(value)
        escaped: list[str] = ['"']
        for char in value:
            codepoint = ord(char)
            if char == '"':
                escaped.append('\\"')
            elif char == "\\":
                escaped.append("\\\\")
            elif codepoint < 0x20:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                escaped.append(char)
        escaped.append('"')
        return "".join(escaped)
    if not isinstance(value, BaseModel | list | tuple | t.Mapping):
        _fail(f"Unsupported canonical JSON type: {type(value).__name__}")

    def mapping_items(mapping: t.Mapping[str, t.Any]) -> t.Iterator[tuple[str, t.Any]]:
        if any(not isinstance(key, str) for key in mapping):
            _fail("JSON object keys must be strings")
        for key in sorted(mapping):
            yield key, mapping[key]

    def model_items(model: BaseModel) -> t.Iterator[tuple[str, t.Any]]:
        values = model.__dict__
        for key in sorted(type(model).model_fields):
            if key in values and not (key == "annotations" and values[key] is None):
                yield key, values[key]

    parts: list[str] = []
    active: set[int] = set()
    stack = [(iter(((None, value),)), None, None, True)]
    while stack:
        iterator, closing, identity, first = stack[-1]
        try:
            key, item = next(iterator)
        except StopIteration:
            stack.pop()
            if closing is not None:
                parts.append(closing)
                active.remove(identity)
            continue
        if not first:
            parts.append(",")
        stack[-1] = iterator, closing, identity, False
        if key is not None:
            parts.extend((_canonical(key, annotations=annotations), ":"))
        if isinstance(item, BaseModel | t.Mapping | list | tuple):
            identity = id(item)
            if identity in active:
                _fail("cyclic JSON payload")
            active.add(identity)
            if isinstance(item, BaseModel):
                parts.append("{")
                stack.append((model_items(item), "}", identity, True))
            elif isinstance(item, t.Mapping):
                parts.append("{")
                stack.append((mapping_items(item), "}", identity, True))
            else:
                parts.append("[")
                stack.append((((None, child) for child in item), "]", identity, True))
        else:
            parts.append(_canonical(item, annotations=annotations))
    return "".join(parts)


def _normalize_materialized_annotations(value: t.Any) -> None:
    """Normalize a preflighted mutable annotation object in place."""
    if not isinstance(value, dict):
        _fail("annotations must be an object")

    def scalar(item: t.Any) -> t.Any:
        if item is None or type(item) is bool or type(item) is int:
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                _fail("Annotation numbers must be finite")
            try:
                return Decimal(repr(item))
            except InvalidOperation as exc:
                raise ValueError("Invalid annotation number") from exc
        if isinstance(item, Decimal):
            if not item.is_finite():
                _fail("Annotation numbers must be finite")
            return item
        if isinstance(item, str):
            _unicode(item)
            return item
        _fail(f"Unsupported annotation JSON type: {type(item).__name__}")

    active = {id(value)}
    stack: list[tuple[t.Iterator[t.Any], dict[str, t.Any] | list[t.Any], int, bool]] = [
        (iter(value.items()), value, id(value), True)
    ]
    while stack:
        iterator, destination, identity, is_object = stack[-1]
        try:
            entry = next(iterator)
        except StopIteration:
            stack.pop()
            active.remove(identity)
            continue
        if is_object:
            key, child = entry
            if not isinstance(key, str):
                _fail("annotation object keys must be strings")
            _unicode(key)
        else:
            key, child = entry
        if isinstance(child, dict | list):
            child_identity = id(child)
            if child_identity in active:
                _fail("cyclic annotation")
            active.add(child_identity)
            stack.append(
                (
                    iter(child.items())
                    if isinstance(child, dict)
                    else iter(enumerate(child)),
                    child,
                    child_identity,
                    isinstance(child, dict),
                )
            )
        else:
            destination[key] = scalar(child)


def _freeze_materialized_json(value: t.Any) -> t.Any:
    """Convert a private JSON copy to immutable mappings and tuples iteratively."""
    if not isinstance(value, dict | list):
        return value
    holder: dict[str, t.Any] = {"value": value}
    stack: list[tuple[dict[str, t.Any] | list[t.Any], t.Any, t.Any, bool]] = [
        (holder, "value", value, False)
    ]
    while stack:
        parent, key, source, complete = stack.pop()
        if complete:
            parent[key] = (
                MappingProxyType(source) if isinstance(source, dict) else tuple(source)
            )
            continue
        stack.append((parent, key, source, True))
        entries = source.items() if isinstance(source, dict) else enumerate(source)
        for child_key, child in entries:
            if isinstance(child, dict | list):
                stack.append((source, child_key, child, False))
    return holder["value"]


_JSON_TRAVERSAL_FRAME_BYTES = 512
_JSON_PARSE_VALUE_BYTES = max(
    sys.getsizeof(Decimal(0)),
    sys.getsizeof(""),
    sys.getsizeof([]),
    sys.getsizeof({}),
)


def _json_parse_live_bytes(data_size: int) -> int:
    """Bound parser output by raw bytes plus every possible JSON value slot.

    Apart from one root value, a distinct JSON value needs at least two source
    bytes.  The per-slot capacity is measured from the active Python runtime,
    rather than relying on a guessed object-footprint constant.
    """
    return data_size + (1 + data_size // 2) * _JSON_PARSE_VALUE_BYTES


def _bounded_json_size(
    value: t.Any,
    budget: t.Any,
    *,
    phase: str,
    counter: str = "max_input_bytes",
) -> int:
    """Check a byte-size upper bound; charge only work and temporary traversal."""
    from mountainash_rules.core.contracts import OperationBudget, Reservation

    if not isinstance(budget, OperationBudget):
        _fail("canonical JSON sizing requires an explicit OperationBudget")

    def string_size(item: str) -> int:
        # Canonical encoding validates Unicode after this admission pass.
        return 2 + 6 * len(item)

    total = 0
    active: set[int] = set()
    stack: list[tuple[t.Any, ...]] = []
    holds: list[Reservation] = []
    reserved_depth = 0

    def push(frame: tuple[t.Any, ...]) -> None:
        nonlocal reserved_depth
        if len(stack) == reserved_depth:
            holds.append(
                budget.reserve(
                    "max_live_bytes",
                    _JSON_TRAVERSAL_FRAME_BYTES,
                    phase=phase,
                    units="canonical traversal frame and cycle guard bytes",
                )
            )
            reserved_depth += 1
        stack.append(frame)

    try:
        push(("value", value))
        while stack:
            budget.reserve(
                "max_work",
                1,
                phase=phase,
                units="canonical traversal steps",
            )
            frame = stack.pop()
            tag = frame[0]
            if tag == "value":
                item = frame[1]
                if item is None:
                    total += 4
                elif type(item) is bool:
                    total += 4 if item else 5
                elif type(item) is int:
                    total += 1 if item == 0 else 2 + item.bit_length()
                elif isinstance(item, Decimal):
                    budget.reserve(
                        "max_work",
                        3 * len(item.as_tuple().digits),
                        phase=phase,
                        units="annotation decimal coefficient traversal steps",
                    )
                    total += _annotation_decimal_length(item)
                elif isinstance(item, float):
                    if not math.isfinite(item):
                        _fail("Annotation numbers must be finite")
                    normalized = Decimal(repr(item))
                    budget.reserve(
                        "max_work",
                        3 * len(normalized.as_tuple().digits),
                        phase=phase,
                        units="native float shortest-roundtrip traversal steps",
                    )
                    total += _annotation_decimal_length(normalized)
                elif isinstance(item, str):
                    total += string_size(item)
                elif isinstance(item, BaseModel):
                    identity = id(item)
                    if identity in active:
                        _fail("cyclic JSON payload")
                    active.add(identity)
                    total += 2
                    values = item.__dict__
                    push(
                        (
                            "mapping",
                            (
                                (key, values[key])
                                for key in type(item).model_fields
                                if key in values
                                and not (key == "annotations" and values[key] is None)
                            ),
                            identity,
                            True,
                        )
                    )
                elif isinstance(item, t.Mapping):
                    identity = id(item)
                    if identity in active:
                        _fail("cyclic JSON payload")
                    active.add(identity)
                    total += 2
                    push(("mapping", iter(item.items()), identity, True))
                elif isinstance(item, list | tuple):
                    identity = id(item)
                    if identity in active:
                        _fail("cyclic JSON payload")
                    active.add(identity)
                    total += 2
                    push(("sequence", iter(item), identity, True))
                else:
                    _fail(f"Unsupported canonical JSON type: {type(item).__name__}")
            elif tag == "mapping":
                iterator, identity, first = frame[1:]
                try:
                    key, item = next(iterator)
                except StopIteration:
                    active.remove(identity)
                    continue
                if not isinstance(key, str):
                    _fail("JSON object keys must be strings")
                total += (0 if first else 1) + string_size(key) + 1
                push(("mapping", iterator, identity, False))
                push(("value", item))
            else:
                iterator, identity, first = frame[1:]
                try:
                    item = next(iterator)
                except StopIteration:
                    active.remove(identity)
                    continue
                total += 0 if first else 1
                push(("sequence", iterator, identity, False))
                push(("value", item))
            budget.check(
                counter, total, phase=phase, units="maximum canonical JSON bytes"
            )
        return total
    finally:
        for hold in reversed(holds):
            budget.release(hold.counter, hold.amount)


def _materialize_json(value: t.Any) -> t.Any:
    """Copy preflighted JSON containers into strict wire list/dict shapes."""
    if not isinstance(value, t.Mapping | list | tuple):
        return value

    def target(source: t.Any) -> dict[str, t.Any] | list[t.Any]:
        return {} if isinstance(source, t.Mapping) else []

    result = target(value)
    stack: list[tuple[t.Any, dict[str, t.Any] | list[t.Any]]] = [(value, result)]
    while stack:
        source, destination = stack.pop()
        entries = source.items() if isinstance(source, t.Mapping) else enumerate(source)
        for key, child in entries:
            if isinstance(child, t.Mapping | list | tuple):
                copied = target(child)
                if isinstance(destination, list):
                    destination.append(copied)
                else:
                    destination[key] = copied
                stack.append((child, copied))
            elif isinstance(destination, list):
                destination.append(child)
            else:
                destination[key] = child
    return result


def canonical_bytes(payload: t.Mapping[str, t.Any]) -> bytes:
    """Return canonical-json-1 UTF-8 bytes for a finite JSON payload."""
    if not isinstance(payload, t.Mapping):
        _fail("canonical payload must be an object")
    return _canonical(payload).encode("utf-8")


def _transport_bytes(payload: t.Mapping[str, t.Any]) -> bytes:
    """Encode validated evidence, including annotation-only exact decimals."""
    if not isinstance(payload, t.Mapping):
        _fail("transport payload must be an object")
    return _canonical(payload, annotations=True).encode("utf-8")


def _content_id_bytes(kind: str, data: bytes) -> str:
    """Return the domain-separated versioned SHA-256 identity for canonical bytes."""
    if kind not in _KINDS:
        _fail(f"Unsupported ID kind: {kind}")
    digest = hashlib.sha256(f"{kind}:1".encode("ascii"))
    digest.update(b"\0")
    digest.update(data)
    return f"{kind}:1:{digest.hexdigest()}"


def content_id(kind: str, payload: t.Mapping[str, t.Any]) -> str:
    """Return the domain-separated, versioned SHA-256 identity for payload."""
    if kind not in _KINDS:
        _fail(f"Unsupported ID kind: {kind}")
    return _content_id_bytes(kind, canonical_bytes(payload))


def validate_id(identifier: str, kind: str) -> str:
    """Validate the complete typed-ID grammar and expected domain kind."""
    if kind not in _KINDS:
        _fail(f"Unsupported ID kind: {kind}")
    if not isinstance(identifier, str):
        _fail("ID must be a string")
    match = _ID_RE.fullmatch(identifier)
    if match is None or match.group("kind") != kind:
        _fail(f"ID must be a {kind}:1 SHA-256 ID")
    return identifier


def decode_json(data: bytes) -> t.Any:
    """Decode untrusted JSON while rejecting duplicate keys and nonfinite values."""
    if not isinstance(data, bytes):
        _fail("JSON input must be bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Malformed JSON") from exc

    def constant(value: str) -> t.NoReturn:
        _fail(f"Nonfinite JSON number: {value}")

    decoder = json.JSONDecoder(parse_constant=constant, parse_float=Decimal)
    missing = object()
    root: t.Any = missing
    stack: list[dict[str, t.Any]] = []

    def whitespace(index: int) -> int:
        while index < len(text) and text[index] in " \t\r\n":
            index += 1
        return index

    def scalar(index: int) -> tuple[t.Any, int]:
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError as exc:
            raise ValueError("Malformed JSON") from exc
        if isinstance(value, list | dict):
            _fail("Malformed JSON")
        return value, end

    def attach(value: t.Any) -> None:
        nonlocal root
        if not stack:
            if root is not missing:
                _fail("Malformed JSON")
            root = value
            return
        frame = stack[-1]
        if frame["kind"] == "array":
            if frame["state"] not in {"value_or_end", "value_required"}:
                _fail("Malformed JSON")
            frame["value"].append(value)
        else:
            if frame["state"] != "value":
                _fail("Malformed JSON")
            frame["value"][frame["key"]] = value
            frame["key"] = None
        frame["state"] = "comma_or_end"

    def value(index: int) -> int:
        if index == len(text):
            _fail("Malformed JSON")
        if text[index] == "[":
            result: list[t.Any] = []
            attach(result)
            stack.append({"kind": "array", "value": result, "state": "value_or_end"})
            return index + 1
        if text[index] == "{":
            object_value: dict[str, t.Any] = {}
            attach(object_value)
            stack.append(
                {
                    "kind": "object",
                    "value": object_value,
                    "key": None,
                    "state": "key_or_end",
                }
            )
            return index + 1
        item, end = scalar(index)
        attach(item)
        return end

    index = 0
    while True:
        index = whitespace(index)
        if not stack:
            if root is missing:
                index = value(index)
                continue
            if index != len(text):
                _fail("Malformed JSON")
            _walk_json(root)
            return root

        frame = stack[-1]
        state = frame["state"]
        if frame["kind"] == "array":
            if state == "value_or_end" and index < len(text) and text[index] == "]":
                stack.pop()
                index += 1
            elif state == "comma_or_end":
                if index < len(text) and text[index] == ",":
                    frame["state"] = "value_required"
                    index += 1
                elif index < len(text) and text[index] == "]":
                    stack.pop()
                    index += 1
                else:
                    _fail("Malformed JSON")
            else:
                index = value(index)
            continue

        if state in {"key_or_end", "key_required"}:
            if state == "key_or_end" and index < len(text) and text[index] == "}":
                stack.pop()
                index += 1
                continue
            if index == len(text) or text[index] != '"':
                _fail("Malformed JSON")
            key, index = scalar(index)
            if not isinstance(key, str):
                _fail("Malformed JSON")
            if key in frame["value"]:
                _fail(f"Duplicate JSON object key: {key}")
            frame["key"] = key
            frame["state"] = "colon"
        elif state == "colon":
            if index == len(text) or text[index] != ":":
                _fail("Malformed JSON")
            frame["state"] = "value"
            index += 1
        elif state == "comma_or_end":
            if index < len(text) and text[index] == ",":
                frame["state"] = "key_required"
                index += 1
            elif index < len(text) and text[index] == "}":
                stack.pop()
                index += 1
            else:
                _fail("Malformed JSON")
        else:
            index = value(index)


def _walk_json(value: t.Any) -> None:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            _unicode(item)
        elif type(item) is float and not math.isfinite(item):
            _fail("Nonfinite JSON number")
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    _fail("JSON object keys must be strings")
                _unicode(key)
                stack.append(child)


def _typed_id(value: t.Any, kind: str, name: str) -> None:
    validate_id(_string(value, name), kind)


def _versioned(payload: dict[str, t.Any]) -> None:
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        _fail("schema_version must be integer 1")


def _predicate_node(node: t.Any) -> None:
    if not isinstance(node, dict) or not isinstance(node.get("op"), str):
        _fail("predicate node must have op")
    op = node["op"]
    fields: dict[str, set[str]] = {
        "true": {"op"},
        "false": {"op"},
        "and": {"op", "args"},
        "or": {"op", "args"},
        "not": {"op", "arg"},
        "eq": {"op", "field", "value"},
        "in": {"op", "field", "values"},
        "interval": {"op", "field", "lower", "upper", "lower_closed", "upper_closed"},
        "language": {"op", "field", "language_id"},
        "compare": {"op", "left", "right", "relation"},
    }
    if op not in fields:
        _fail("Unknown predicate op")
    _object(node, fields[op], "predicate node")
    if op in {"and", "or"}:
        if not isinstance(node["args"], list) or len(node["args"]) < 2:
            _fail("predicate args must contain at least two children")
        for item in node["args"]:
            _typed_id(item, "predicate", "predicate arg")
        if len(node["args"]) != len(set(node["args"])):
            _fail("predicate args must be unique")
    elif op == "not":
        _typed_id(node["arg"], "predicate", "predicate arg")
    elif op == "eq":
        _string(node["field"], "field")
        _scalar(node["value"])
    elif op == "in":
        _string(node["field"], "field")
        if not isinstance(node["values"], list) or len(node["values"]) < 2:
            _fail("predicate in values must contain at least two values")
        for item in node["values"]:
            _scalar(item)
        encoded = tuple(canonical_bytes({"value": item}) for item in node["values"])
        if encoded != tuple(sorted(encoded)):
            _fail("predicate in values must be canonical-sorted")
        if len(encoded) != len(set(encoded)):
            _fail("predicate in values must be unique")
    elif op == "interval":
        if (
            type(node["lower_closed"]) is not bool
            or type(node["upper_closed"]) is not bool
        ):
            _fail("interval closure flags must be Booleans")
        if node["lower"] is None and node["lower_closed"]:
            _fail("unbounded interval lower must be open")
        if node["upper"] is None and node["upper_closed"]:
            _fail("unbounded interval upper must be open")
        if node["lower"] is not None:
            _scalar(node["lower"])
        if node["upper"] is not None:
            _scalar(node["upper"])
    elif op == "language":
        _string(node["field"], "field")
        _typed_id(node["language_id"], "language", "language_id")
    elif op == "compare":
        _string(node["left"], "left")
        _string(node["right"], "right")
        if node["relation"] not in {"eq", "ne", "lt", "le", "gt", "ge"}:
            _fail("Unsupported comparison relation")


def _dimension(value: t.Any) -> None:
    fields = {
        "dimension_name",
        "context_field",
        "rule_field",
        "match_strategy",
        "data_type",
        "role",
        "range_min_field",
        "range_max_field",
        "range_min_inclusive",
        "range_max_inclusive",
        "regex_pattern",
    }
    record = _object(value, fields, "dimension")
    for name in ("dimension_name", "context_field", "rule_field"):
        _string(record[name], name)
    if record["match_strategy"] not in {
        "exact",
        "exact_key",
        "not_equal",
        "range",
        "greater_than",
        "less_than",
        "prefix",
        "suffix",
        "contains",
        "regex",
        "context_regex",
        "set_membership",
        "set_exclusion",
    }:
        _fail("Unsupported dimension match_strategy")
    if record["data_type"] not in {"str", "int", "float", "bool", "date", "datetime"}:
        _fail("Unsupported dimension data_type")
    if record["role"] not in {"constraint", "context_key"}:
        _fail("Unsupported dimension role")
    from mountainash_rules.core.dimension import Dimension

    compatible = {
        "dimension_name": record["dimension_name"],
        "match_strategy": record["match_strategy"],
        "data_type": record["data_type"],
    }
    if record["match_strategy"] == "range":
        compatible.update(
            {
                "range_min_field": record["range_min_field"],
                "range_max_field": record["range_max_field"],
                "range_min_inclusive": record["range_min_inclusive"],
                "range_max_inclusive": record["range_max_inclusive"],
            }
        )
    if record["match_strategy"] == "context_regex":
        compatible["regex_pattern"] = record["regex_pattern"]
    try:
        Dimension.model_validate(compatible)
    except ValueError as exc:
        raise ValueError("Invalid dimension strategy/data_type") from exc
    if record["match_strategy"] == "range":
        _string(record["range_min_field"], "range_min_field")
        _string(record["range_max_field"], "range_max_field")
        if (
            type(record["range_min_inclusive"]) is not bool
            or type(record["range_max_inclusive"]) is not bool
        ):
            _fail("range inclusivity must be Boolean")
    elif any(
        record[name] is not None
        for name in (
            "range_min_field",
            "range_max_field",
            "range_min_inclusive",
            "range_max_inclusive",
        )
    ):
        _fail("non-range dimension fields must be null")
    if record["match_strategy"] == "context_regex":
        _string(record["regex_pattern"], "regex_pattern")
    elif record["regex_pattern"] is not None:
        _fail("non-context_regex regex_pattern must be null")


def _regex_semantics(value: t.Any) -> None:
    if value is None:
        return
    record = _object(
        value,
        {"dialect", "dialect_version", "mode", "unicode_version", "flags"},
        "regex semantics",
    )
    if (
        record["dialect"] != "rust-regex"
        or record["dialect_version"] != "regex-syntax-0.8.10"
        or record["mode"] != "search"
        or record["unicode_version"] != "16.0.0"
    ):
        _fail("Unsupported regex semantics")
    flags = _object(
        record["flags"],
        {
            "case_insensitive",
            "multi_line",
            "dot_matches_new_line",
            "crlf",
            "swap_greed",
            "ignore_whitespace",
            "unicode",
        },
        "regex flags",
    )
    if any(type(flag) is not bool for flag in flags.values()):
        _fail("regex flags must be Booleans")


def _semantic_versions(value: t.Any) -> None:
    from mountainash_rules.core.contracts import SemanticVersions

    SemanticVersions.model_validate(value)


def _aggregate(value: t.Any) -> None:
    if not isinstance(value, dict):
        _fail("aggregate declaration must be an object")
    required = {
        "column_name",
        "output_name",
        "operation",
        "data_type",
        "numeric_semantics",
    }
    permitted = required | {"timezone"}
    if set(value) != required and set(value) != permitted:
        _fail("Unknown or missing aggregate declaration fields")
    for name in (
        "column_name",
        "output_name",
        "operation",
        "data_type",
        "numeric_semantics",
    ):
        _string(value[name], name)
    if "." not in value["output_name"] or any(
        not segment for segment in value["output_name"].split(".")
    ):
        _fail("aggregate output_name must be qualified")
    if value["operation"] not in {"sum", "min", "max", "product"}:
        _fail("Unsupported aggregate operation")
    if value["data_type"] not in {"str", "int", "float", "bool", "date", "datetime"}:
        _fail("Unsupported aggregate data_type")
    if value["numeric_semantics"] != "numeric-1":
        _fail("Unsupported aggregate numeric semantics")
    if value["data_type"] == "datetime":
        if value.get("timezone") not in {"naive", "utc"}:
            _fail("datetime aggregate requires timezone")
    elif "timezone" in value:
        _fail("only datetime aggregates may include timezone")


def _key_values(value: t.Any) -> None:
    if not isinstance(value, list):
        _fail("key_values must be an array")
    names: list[str] = []
    for entry in value:
        record = _object(entry, {"dimension_name", "match"}, "routing key")
        names.append(_string(record["dimension_name"], "dimension_name"))
        match = record["match"]
        if not isinstance(match, dict) or match.get("kind") not in {
            "wildcard",
            "value",
        }:
            _fail("Invalid routing key match")
        if match["kind"] == "wildcard":
            _object(match, {"kind"}, "wildcard match")
        else:
            _object(match, {"kind", "value"}, "value match")
            _scalar(match["value"])
    if names != sorted(names) or len(names) != len(set(names)):
        _fail("routing key dimensions must be sorted and unique")


def _partition(value: t.Any) -> None:
    record = _object(value, {"routing_id", "key_values"}, "partition identity")
    _typed_id(record["routing_id"], "routing", "routing_id")
    _key_values(record["key_values"])


def _validate_language_payload(
    payload: dict[str, t.Any], budget: t.Any, identifier: str
) -> None:
    from mountainash_rules import _native
    from mountainash_rules.core.contracts import ExactResourceError, OperationBudget
    from mountainash_rules.core.language import StringLanguage
    from mountainash_rules.core.predicates import _native_workspace_bytes

    if not isinstance(budget, OperationBudget):
        _fail("language envelopes require an explicit OperationBudget")
    phase = "decode_language"
    data_size = _bounded_json_size(payload, budget, phase=phase)
    budget.reserve(
        "max_input_bytes", data_size, phase=phase, units="maximum canonical JSON bytes"
    )
    workspace = _native_workspace_bytes(budget.limits.language, "language_decode")
    reservations = []
    try:
        reservations.append(
            budget.reserve(
                "max_live_bytes",
                data_size,
                phase=phase,
                units="canonical language buffer capacity bytes",
            )
        )
        reservations.append(
            budget.reserve(
                "max_dfa_states",
                budget.limits.language.max_states,
                phase=phase,
                units="native DFA state capacity",
            )
        )
        reservations.append(
            budget.reserve(
                "max_dfa_transitions",
                budget.limits.language.max_transitions,
                phase=phase,
                units="native DFA transition capacity",
            )
        )
        if workspace:
            reservations.append(
                budget.reserve(
                    "max_live_bytes",
                    workspace,
                    phase=phase,
                    units="native workspace capacity bytes",
                )
            )
        budget.reserve(
            "max_work",
            budget.limits.language.max_work,
            phase=phase,
            units="native maximum work units",
        )
        data = canonical_bytes(payload)
        StringLanguage.from_json(data, limits=budget.limits.language)
    except ExactResourceError:
        raise
    except _native.LanguageResourceError as exc:
        counter = {
            "input_bytes": "max_input_bytes",
            "states": "max_dfa_states",
            "transitions": "max_dfa_transitions",
            "work": "max_work",
        }.get(exc.resource, f"native_{exc.resource}")
        raise ExactResourceError(
            operation=budget.operation,
            phase=phase,
            counter=counter,
            limit=exc.limit,
            observed=0,
            requested=exc.limit + 1,
            units=f"native {exc.resource} units (minimum required)",
        ) from exc
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid language-1 payload") from exc
    else:
        if _content_id_bytes("language", data) != identifier:
            _fail("envelope digest does not match payload")
    finally:
        for reservation in reversed(reservations):
            budget.release(reservation.counter, reservation.amount)


def _validate_record_payload(kind: str, payload: dict[str, t.Any]) -> None:
    """Dispatch id-less evidence payloads through their closed Pydantic models."""
    from mountainash_rules.core.contracts import (
        AnalysisInput,
        ContractBinding,
        ContextContract,
        Finding,
        ResolutionProfile,
        ValidationReport,
        WarningApproval,
    )

    models: dict[str, type[t.Any]] = {
        "contract": ContextContract,
        "profile": ResolutionProfile,
        "analysis-input": AnalysisInput,
        "finding": Finding,
        "report": ValidationReport,
        "approval": WarningApproval,
        "binding": ContractBinding,
    }
    model = models[kind]
    if kind == "contract":
        _object(payload, {"schema_version", "contract"}, "contract payload")
        _versioned(payload)
        ContextContract.model_validate(payload["contract"])
        return
    if kind == "profile":
        _object(
            payload, {"schema_version", "contract_id", "profile"}, "profile payload"
        )
        _versioned(payload)
        _string(payload["contract_id"], "contract_id")
        ResolutionProfile.model_validate(payload["profile"])
        return
    fields = {
        "analysis-input": {
            "schema_version",
            "ruleset_id",
            "source_id_field",
            "source_bundle_digest",
            "compilation_domain_ref",
            "domain_digests",
            "metadata_digest",
            "aggregate_digest",
            "routing_digest",
            "contracts",
            "validation_policy",
            "semantic_versions",
        },
        "finding": {
            "schema_version",
            "analysis_input_id",
            "stage",
            "check_id",
            "code",
            "severity",
            "scope",
            "source_ids",
            "cell_ids",
            "region_predicate_id",
            "witnesses",
            "witnesses_complete",
        },
        "report": {
            "schema_version",
            "analysis_input_id",
            "stage",
            "artifact_id",
            "validator",
            "scope",
            "checks",
            "finding_ids",
        },
        "approval": {
            "schema_version",
            "analysis_input_id",
            "report_id",
            "authority_ref",
            "actor_ref",
            "decision",
            "scope",
            "warning_ids",
        },
        "binding": {
            "schema_version",
            "artifact_id",
            "analysis_input_id",
            "contract_id",
            "contract_digest",
            "domain_ref",
            "domain_digest",
            "authorized_profiles",
            "source_report_id",
            "compiled_report_id",
            "approval_ids",
            "semantic_versions",
        },
    }[kind]
    actual = set(payload)
    if actual != fields and actual != fields | {"annotations"}:
        _fail(f"Invalid {kind} payload fields")
    semantic_payload = {
        key: value for key, value in payload.items() if key != "annotations"
    }
    model.model_validate({"id": content_id(kind, semantic_payload), **payload})


def _validate_physical_payload(kind: str, payload: dict[str, t.Any]) -> None:
    if kind == "scope":
        _object(
            payload,
            {"schema_version", "partition_identity", "predicate_id", "keys"},
            "scope payload",
        )
        _versioned(payload)
        _partition(payload["partition_identity"])
        _typed_id(payload["predicate_id"], "predicate", "predicate_id")
        if not isinstance(payload["keys"], list):
            _fail("scope keys must be an array")
        names: list[str] = []
        for entry in payload["keys"]:
            record = _object(entry, {"field", "predicate_id"}, "scope key")
            names.append(_string(record["field"], "scope key field"))
            _typed_id(record["predicate_id"], "predicate", "scope key predicate_id")
        if names != sorted(names) or len(names) != len(set(names)):
            _fail("scope keys must be sorted and unique")
        return
    if kind == "source-map":
        _object(
            payload, {"schema_version", "scope_id", "allocations"}, "source-map payload"
        )
        _versioned(payload)
        _typed_id(payload["scope_id"], "scope", "scope_id")
        if not isinstance(payload["allocations"], list):
            _fail("source-map allocations must be an array")
        source_ids: list[str] = []
        positions: list[int] = []
        for allocation in payload["allocations"]:
            record = _object(
                allocation, {"source_id", "position"}, "source-map allocation"
            )
            source_ids.append(_source_id(record["source_id"]))
            positions.append(_counter(record["position"], "source-map position"))
        if source_ids != sorted(source_ids) or len(source_ids) != len(set(source_ids)):
            _fail("source-map allocations must be sorted and unique by source_id")
        if positions != list(range(len(positions))):
            _fail("source-map positions must be contiguous from zero")
        return
    _object(
        payload,
        {
            "schema_version",
            "scope_id",
            "map_id",
            "cell_id",
            "predicate_id",
            "contributor_set_id",
        },
        "vector payload",
    )
    _versioned(payload)
    for name, expected in (
        ("scope_id", "scope"),
        ("map_id", "source-map"),
        ("cell_id", "cell"),
        ("predicate_id", "predicate"),
        ("contributor_set_id", "contributor-set"),
    ):
        _typed_id(payload[name], expected, name)


def _validate_payload(
    kind: str, payload: t.Any, *, budget: t.Any = None, identifier: str | None = None
) -> dict[str, t.Any]:
    if not isinstance(payload, dict):
        _fail("envelope payload must be an object")
    schemas: dict[str, set[str]] = {
        "predicate": {"schema_version", "node"},
        "domain": {"schema_version", "domain_id", "fields", "predicate_id"},
        "metadata": {
            "schema_version",
            "dimensions",
            "regex_semantics",
            "normalization_semantics",
        },
        "aggregates": {"schema_version", "declarations"},
        "routing": {"schema_version", "semantics", "key_dimensions", "partition_keys"},
        "source-content": {
            "schema_version",
            "ruleset_id",
            "source_id",
            "predicate_id",
            "routing_values",
            "contributions",
        },
        "source-bundle": {"schema_version", "ruleset_id", "sources"},
        "contributor-set": {"schema_version", "source_ids"},
        "cell": {
            "cell_schema",
            "ruleset_id",
            "partition_identity",
            "predicate_id",
            "contributor_set_id",
        },
        "artifact": {
            "schema_version",
            "ruleset_id",
            "partition_identity",
            "metadata_id",
            "aggregates_id",
            "compilation_domain_id",
            "source_bundle_id",
            "cells",
            "semantic_versions",
        },
    }
    if kind == "language":
        _object(
            payload,
            {"schema_version", "alphabet", "start", "accepting", "transitions"},
            "language payload",
        )
        _versioned(payload)
        if identifier is None:
            _fail("language envelopes require an identifier")
        _validate_language_payload(payload, budget, identifier)
        return payload
    if kind in {
        "contract",
        "profile",
        "analysis-input",
        "finding",
        "report",
        "approval",
        "binding",
    }:
        _validate_record_payload(kind, payload)
        return payload
    if kind in {"scope", "source-map", "vector"}:
        _validate_physical_payload(kind, payload)
        return payload
    _object(payload, schemas[kind], f"{kind} payload")
    if kind == "cell":
        if type(payload["cell_schema"]) is not int or payload["cell_schema"] != 1:
            _fail("cell_schema must be integer 1")
    else:
        _versioned(payload)
    if kind == "predicate":
        _predicate_node(payload["node"])
    elif kind == "domain":
        _string(payload["domain_id"], "domain_id")
        _typed_id(payload["predicate_id"], "predicate", "predicate_id")
        if not isinstance(payload["fields"], list):
            _fail("domain fields must be an array")
        names: list[str] = []
        for field in payload["fields"]:
            permitted = {"name", "data_type", "timezone"}
            if not isinstance(field, dict) or set(field) not in (
                {"name", "data_type"},
                permitted,
            ):
                _fail("Invalid domain field")
            names.append(_string(field["name"], "domain field name"))
            if field["data_type"] not in {
                "str",
                "int",
                "float",
                "bool",
                "date",
                "datetime",
            }:
                _fail("Unsupported domain field type")
            if field["data_type"] == "datetime":
                if field.get("timezone") not in {"naive", "utc"}:
                    _fail("datetime domain field requires timezone")
            elif "timezone" in field:
                _fail("only datetime domain fields may include timezone")
        if names != sorted(names) or len(names) != len(set(names)):
            _fail("domain fields must be sorted and unique")
    elif kind == "metadata":
        _regex_semantics(payload["regex_semantics"])
        if payload["normalization_semantics"] != "normalization-2":
            _fail("Unsupported normalization semantics")
        if not isinstance(payload["dimensions"], list):
            _fail("metadata dimensions must be an array")
        for dimension in payload["dimensions"]:
            _dimension(dimension)
        names = [dimension["dimension_name"] for dimension in payload["dimensions"]]
        if names != sorted(names) or len(names) != len(set(names)):
            _fail("metadata dimensions must be sorted and unique")
        uses_regex = any(
            dimension["match_strategy"] in {"regex", "context_regex"}
            for dimension in payload["dimensions"]
        )
        if uses_regex != (payload["regex_semantics"] is not None):
            _fail(
                "regex semantics must be present exactly when regex strategies are used"
            )
    elif kind == "aggregates":
        declarations = payload["declarations"]
        if not isinstance(declarations, list):
            _fail("aggregate declarations must be an array")
        names: list[str] = []
        for declaration in declarations:
            _aggregate(declaration)
            names.append(declaration["output_name"])
        if names != sorted(names) or len(names) != len(set(names)):
            _fail("aggregate output names must be sorted and unique")
    elif kind == "routing":
        if payload["semantics"] != "exact-key-1":
            _fail("Unsupported routing semantics")
        if not isinstance(payload["key_dimensions"], list) or not isinstance(
            payload["partition_keys"], list
        ):
            _fail("routing collections must be arrays")
        for dimension in payload["key_dimensions"]:
            _dimension(dimension)
            if dimension["match_strategy"] != "exact_key":
                _fail("routing key_dimensions require exact_key strategy")
        for key_values in payload["partition_keys"]:
            _key_values(key_values)
        key_bytes = [
            canonical_bytes({"key_values": key_values})
            for key_values in payload["partition_keys"]
        ]
        if key_bytes != sorted(key_bytes) or len(key_bytes) != len(set(key_bytes)):
            _fail("routing partition_keys must be sorted and unique")
    elif kind == "source-content":
        _string(payload["ruleset_id"], "ruleset_id")
        _source_id(payload["source_id"])
        _typed_id(payload["predicate_id"], "predicate", "predicate_id")
        _key_values(payload["routing_values"])
        if not isinstance(payload["contributions"], dict):
            _fail("source contributions must be an object")
        for column_name, contribution in payload["contributions"].items():
            _string(column_name, "contribution column_name")
            _scalar(contribution, allow_reserved=True)
    elif kind == "source-bundle":
        _string(payload["ruleset_id"], "ruleset_id")
        sources = payload["sources"]
        if not isinstance(sources, list):
            _fail("source bundle sources must be an array")
        source_ids: list[str] = []
        for source in sources:
            _object(source, {"source_id", "content_id"}, "source bundle source")
            source_ids.append(_source_id(source["source_id"]))
            _typed_id(source["content_id"], "source-content", "content_id")
        if source_ids != sorted(source_ids) or len(source_ids) != len(set(source_ids)):
            _fail("source bundle sources must be sorted and unique")
    elif kind == "cell":
        _string(payload["ruleset_id"], "ruleset_id")
        _partition(payload["partition_identity"])
        _typed_id(payload["predicate_id"], "predicate", "predicate_id")
        _typed_id(
            payload["contributor_set_id"], "contributor-set", "contributor_set_id"
        )
    elif kind == "artifact":
        _string(payload["ruleset_id"], "ruleset_id")
        _partition(payload["partition_identity"])
        _semantic_versions(payload["semantic_versions"])
        for name, expected in (
            ("metadata_id", "metadata"),
            ("aggregates_id", "aggregates"),
            ("compilation_domain_id", "domain"),
            ("source_bundle_id", "source-bundle"),
        ):
            _typed_id(payload[name], expected, name)
        if not isinstance(payload["cells"], list):
            _fail("artifact cells must be an array")
        cell_ids: list[str] = []
        for cell in payload["cells"]:
            _object(cell, {"cell_id", "outputs"}, "artifact cell")
            cell_ids.append(validate_id(cell["cell_id"], "cell"))
            if not isinstance(cell["outputs"], dict):
                _fail("artifact cell outputs must be an object")
            for output_name, scalar in cell["outputs"].items():
                _string(output_name, "output_name")
                _scalar(scalar, allow_reserved=True)
        if cell_ids != sorted(cell_ids) or len(cell_ids) != len(set(cell_ids)):
            _fail("artifact cells must be sorted and unique")
    elif kind == "contributor-set":
        source_ids = payload["source_ids"]
        if not isinstance(source_ids, list) or not source_ids:
            _fail("contributor-set source_ids must be non-empty")
        for source_id in source_ids:
            _source_id(source_id)
        if source_ids != sorted(source_ids) or len(source_ids) != len(set(source_ids)):
            _fail("contributor-set source_ids must be sorted and unique")
    return payload


def validate_envelope(
    envelope: t.Mapping[str, t.Any],
    kind: str,
    *,
    budget: t.Any = None,
) -> dict[str, t.Any]:
    """Validate one exact semantic envelope and its domain-separated digest."""
    if kind not in _KINDS:
        _fail(f"Unsupported ID kind: {kind}")
    if not isinstance(envelope, t.Mapping):
        _fail("envelope must be an object")
    reservation = None
    if budget is not None:
        size = _bounded_json_size(
            envelope,
            budget,
            phase="envelope_validation",
            counter="max_live_bytes",
        )
        reservation = budget.reserve(
            "max_live_bytes",
            size,
            phase="envelope_validation",
            units="mutable envelope validation copy capacity",
        )
    try:
        candidate = _materialize_json(envelope)
        if not isinstance(candidate, dict):
            _fail("envelope must be an object")
        _object(candidate, {"id", "payload"}, "envelope")
        identifier = validate_id(candidate["id"], kind)
        payload = _validate_payload(
            kind, candidate["payload"], budget=budget, identifier=identifier
        )
        if kind in _EVIDENCE_RECORD_KINDS and "annotations" in candidate["payload"]:
            _normalize_materialized_annotations(candidate["payload"]["annotations"])
        if kind != "language" and (
            content_id(kind, _identity_payload(kind, payload)) != identifier
        ):
            _fail("envelope digest does not match payload")
        return candidate
    finally:
        if reservation is not None:
            budget.release(reservation.counter, reservation.amount)


def _make_exact_envelope(
    kind: str, payload: t.Mapping[str, t.Any], *, budget: OperationBudget
) -> dict[str, t.Any]:
    """Construct one bounded, validated semantic envelope on a shared ledger."""
    if not isinstance(payload, t.Mapping):
        raise TypeError("payload must be a mapping")
    if kind not in _KINDS:
        _fail(f"Unsupported ID kind: {kind}")
    size = _bounded_json_size(payload, budget, phase="make_exact_envelope")
    budget.reserve(
        "max_input_bytes",
        size,
        phase="make_exact_envelope",
        units="semantic payload canonical bytes",
    )
    budget.reserve(
        "max_live_bytes",
        size,
        phase="make_exact_envelope",
        units="retained mutable payload copy capacity",
    )
    candidate = _materialize_json(payload)
    if not isinstance(candidate, dict):
        _fail("envelope payload must be an object")
    if kind in _EVIDENCE_RECORD_KINDS and "annotations" in candidate:
        _normalize_materialized_annotations(candidate["annotations"])
    identifier = _content_id_bytes(
        kind, canonical_bytes(_identity_payload(kind, candidate))
    )
    _validate_payload(kind, candidate, budget=budget, identifier=identifier)
    identifier_size = _bounded_json_size(
        identifier,
        budget,
        phase="make_exact_envelope",
        counter="max_output_bytes",
    )
    payload_size = _bounded_json_size(
        candidate,
        budget,
        phase="make_exact_envelope",
        counter="max_output_bytes",
    )
    envelope_size = (
        2
        + (2 + 6 * len("id"))
        + 1
        + identifier_size
        + 1
        + (2 + 6 * len("payload"))
        + 1
        + payload_size
    )
    budget.reserve(
        "max_output_bytes",
        envelope_size,
        phase="make_exact_envelope",
        units="canonical envelope bytes",
    )
    budget.reserve(
        "max_live_bytes",
        sys.getsizeof({}) + sys.getsizeof(identifier),
        phase="make_exact_envelope",
        units="envelope mapping and typed ID bytes",
    )
    return {"id": identifier, "payload": candidate}


def make_exact_envelope(
    kind: str, payload: t.Mapping[str, t.Any], *, limits: ExactLimits
) -> t.Mapping[str, t.Any]:
    from mountainash_rules.core.contracts import ExactLimits, OperationBudget

    if not isinstance(limits, ExactLimits):
        raise TypeError("limits must be ExactLimits")
    budget = OperationBudget(limits, "make_exact_envelope")
    envelope = _make_exact_envelope(kind, payload, budget=budget)
    payload_size = _bounded_json_size(
        envelope["payload"],
        budget,
        phase="make_exact_envelope",
        counter="max_live_bytes",
    )
    budget.reserve(
        "max_live_bytes",
        payload_size,
        phase="make_exact_envelope",
        units="immutable envelope sequence capacity",
    )
    return MappingProxyType(
        {
            "id": envelope["id"],
            "payload": _freeze_materialized_json(envelope["payload"]),
        }
    )


def _bundle_payload(
    bundle: ValidationBundle | t.Mapping[str, t.Any],
) -> t.Mapping[str, t.Any]:
    """Expose a shallow wire view without copying immutable evidence records."""
    from mountainash_rules.core.contracts import ValidationBundle

    if isinstance(bundle, ValidationBundle):
        return {
            "schema_version": bundle.schema_version,
            "metadata": bundle.metadata,
            "aggregates": bundle.aggregates,
            "routing": bundle.routing,
            "context_contracts": bundle.context_contracts,
            "predicates": bundle.predicates,
            "validation": bundle.validation,
        }
    if isinstance(bundle, t.Mapping):
        return bundle
    raise TypeError("bundle must be a ValidationBundle or mapping")


def _admit_validation_bundle(
    bundle: ValidationBundle | t.Mapping[str, t.Any], *, budget: OperationBudget
) -> ValidationBundle:
    """Preflight and admit a complete bundle without a JSON round trip."""
    from mountainash_rules.core.contracts import OperationBudget, ValidationBundle

    if not isinstance(budget, OperationBudget):
        raise TypeError("budget must be an OperationBudget")
    if isinstance(bundle, ValidationBundle):
        payload = _bundle_payload(bundle)
        size = _bounded_json_size(payload, budget, phase="validation_bundle_admission")
        budget.reserve(
            "max_input_bytes",
            size,
            phase="validation_bundle_admission",
            units="validation bundle canonical bytes",
        )
        return bundle
    if not isinstance(bundle, t.Mapping):
        raise TypeError("bundle must be a ValidationBundle or mapping")
    size = _bounded_json_size(bundle, budget, phase="validation_bundle_admission")
    budget.reserve(
        "max_input_bytes",
        size,
        phase="validation_bundle_admission",
        units="validation bundle canonical bytes",
    )
    hydration = budget.reserve(
        "max_live_bytes",
        size,
        phase="validation_bundle_admission",
        units="validation bundle immutable hydration capacity",
    )
    try:
        return ValidationBundle.model_validate(bundle, context={"budget": budget})
    finally:
        budget.release(hydration.counter, hydration.amount)


def encode_validation_bundle(bundle: ValidationBundle, *, limits: ExactLimits) -> bytes:
    """Encode one fully admitted validation bundle as canonical UTF-8 JSON."""
    from mountainash_rules.core.contracts import ExactLimits, OperationBudget

    if not isinstance(limits, ExactLimits):
        raise TypeError("limits must be ExactLimits")
    budget = OperationBudget(limits, "encode_validation_bundle")
    admitted = _admit_validation_bundle(bundle, budget=budget)
    payload = _bundle_payload(admitted)
    size = _bounded_json_size(
        payload,
        budget,
        phase="validation_bundle_encoding",
        counter="max_output_bytes",
    )
    budget.reserve(
        "max_output_bytes",
        size,
        phase="validation_bundle_encoding",
        units="canonical validation bundle bytes",
    )
    output = budget.reserve(
        "max_live_bytes",
        size,
        phase="validation_bundle_encoding",
        units="canonical validation bundle output buffer capacity",
    )
    try:
        return _transport_bytes(payload)
    finally:
        budget.release(output.counter, output.amount)


def decode_validation_bundle(data: bytes, *, limits: ExactLimits) -> ValidationBundle:
    """Strictly decode, hydrate and admit one portable validation bundle."""
    from mountainash_rules.core.contracts import ExactLimits, OperationBudget

    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if not isinstance(limits, ExactLimits):
        raise TypeError("limits must be ExactLimits")
    budget = OperationBudget(limits, "decode_validation_bundle")
    budget.reserve(
        "max_input_bytes",
        len(data),
        phase="validation_bundle_decoding",
        units="input JSON bytes",
    )
    parse = budget.reserve(
        "max_live_bytes",
        _json_parse_live_bytes(len(data)),
        phase="validation_bundle_decoding",
        units="JSON parser tree capacity",
    )
    try:
        return _admit_validation_bundle(decode_json(data), budget=budget)
    finally:
        budget.release(parse.counter, parse.amount)
