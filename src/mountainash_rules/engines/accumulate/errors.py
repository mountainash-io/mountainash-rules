"""Intrinsic declared-domain failures for the replacement accumulator."""


class DomainError(ValueError):
    """A declared domain cannot be resolved or interpreted as authored."""


class UnsupportedDomainError(DomainError):
    """A domain uses a predicate capability this runtime does not support."""


class RequestValidationError(ValueError):
    """The request collection or selector configuration is malformed."""
