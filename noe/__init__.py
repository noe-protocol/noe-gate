"""
Noe Runtime - Safety-critical epistemic logic for agent and robot systems.

v1.0 Public API:
- NoeRuntime: Canonical runtime entrypoint (recommended)
- ContextManager: Context snapshots with provenance hashing
- pi_safe: Safety projection (C_rich -> C_safe)
- run_noe_logic: Legacy parser entrypoint (backward compatibility)
"""

from .noe_runtime import NoeRuntime
from .context_manager import ContextManager
from .context_projection import pi_safe
from .noe_parser import run_noe_logic

# Derive version from package metadata
try:
    from importlib.metadata import version
    __version__ = version("noe-gate")
except Exception:
    __version__ = "1.0.0"

__all__ = [
    "NoeRuntime",
    "ContextManager",
    "pi_safe",
    "run_noe_logic",
]
