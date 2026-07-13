"""Deprecated shim — moved to mountainash_rules.core.context.

Emits DeprecationWarning; remove in the production release after next
(shipped 2026-07; remove no earlier than the 2026-08 production release).
"""

import warnings

from mountainash_rules.core.context import *  # noqa: F401,F403
from mountainash_rules.core.context import extract_context_values  # noqa: F401

warnings.warn(
    "mountainash_rules.context is deprecated; import from "
    "mountainash_rules.core.context (or the package root)",
    DeprecationWarning,
    stacklevel=2,
)
