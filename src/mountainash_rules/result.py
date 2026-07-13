"""Deprecated shim — moved to mountainash_rules.core.result.

Emits DeprecationWarning; remove in the production release after next
(shipped 2026-07; remove no earlier than the 2026-08 production release).
"""

import warnings

from mountainash_rules.core.result import *  # noqa: F401,F403
from mountainash_rules.core.result import RuleResult  # noqa: F401

warnings.warn(
    "mountainash_rules.result is deprecated; import from "
    "mountainash_rules.core.result (or the package root)",
    DeprecationWarning,
    stacklevel=2,
)
