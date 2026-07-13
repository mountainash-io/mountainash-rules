"""Deprecated shim — moved to mountainash_rules.core.dimension.

Emits DeprecationWarning; remove in the production release after next
(shipped 2026-07; remove no earlier than the 2026-08 production release).
"""

import warnings

from mountainash_rules.core.dimension import *  # noqa: F401,F403
from mountainash_rules.core.dimension import DimensionsMetadata  # noqa: F401

warnings.warn(
    "mountainash_rules.dimension is deprecated; import from "
    "mountainash_rules.core.dimension (or the package root)",
    DeprecationWarning,
    stacklevel=2,
)
