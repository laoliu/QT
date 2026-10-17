"""Compatibility entry point for the canonical builtin factor registration.

Register only implemented factors through the existing source of truth.
"""

from aipha.factors.registry import _register_builtin_factors

_register_builtin_factors()
