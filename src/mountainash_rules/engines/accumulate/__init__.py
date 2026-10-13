"""Internal backend-native accumulator replacement; not a public entrypoint."""

from mountainash_rules.engines.accumulate.build import build
from mountainash_rules.engines.accumulate.compare import (
    TableComparison,
    TableSelection,
    compare,
)
from mountainash_rules.engines.accumulate.errors import (
    DomainError,
    RequestValidationError,
    UnsupportedDomainError,
)
from mountainash_rules.engines.accumulate.lattice import Lattice
from mountainash_rules.engines.accumulate.resolve import resolve
from mountainash_rules.engines.accumulate.result import ResolutionResult
from mountainash_rules.engines.accumulate.sources import SourceReport, check_sources
from mountainash_rules.engines.accumulate.updates import UpdateResult
from mountainash_rules.engines.accumulate.validate import (
    Approval,
    ArtifactReport,
    approve,
    validate,
)

__all__ = [
    "Approval",
    "ArtifactReport",
    "DomainError",
    "Lattice",
    "RequestValidationError",
    "ResolutionResult",
    "SourceReport",
    "TableComparison",
    "TableSelection",
    "UnsupportedDomainError",
    "UpdateResult",
    "approve",
    "build",
    "check_sources",
    "compare",
    "resolve",
    "validate",
]
