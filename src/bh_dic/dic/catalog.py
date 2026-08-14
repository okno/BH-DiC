"""Adapter-facing projections of the authoritative policy function catalog.

The policy package is the sole source of approval, feature-flag, sensitivity,
and exposure decisions.  This module only converts stable string IDs to the
typed adapter enum for compatibility.
"""

from __future__ import annotations

from bh_dic.dic.models import FunctionId
from bh_dic.policies.catalog import (
    FUNCTION_CATALOG as POLICY_FUNCTION_CATALOG,
)
from bh_dic.policies.catalog import (
    READ_FUNCTION_IDS,
    WRITE_FUNCTION_IDS,
)

FUNCTION_CATALOG = POLICY_FUNCTION_CATALOG

READ_FUNCTIONS = frozenset(FunctionId(value) for value in READ_FUNCTION_IDS)
MUTATING_FUNCTIONS = frozenset(FunctionId(value) for value in WRITE_FUNCTION_IDS)

# Compatibility name for adapter code.  Membership is derived from the
# normative ``expose_to_model``/``destructive`` properties and is not policy.
FORBIDDEN_FUNCTIONS = frozenset(
    FunctionId(value)
    for value, spec in POLICY_FUNCTION_CATALOG.items()
    if spec.destructive and not spec.expose_to_model
)

if frozenset(item.value for item in FunctionId) != frozenset(POLICY_FUNCTION_CATALOG):
    raise RuntimeError("DIC FunctionId enum diverges from the authoritative policy catalog")

__all__ = [
    "FORBIDDEN_FUNCTIONS",
    "FUNCTION_CATALOG",
    "MUTATING_FUNCTIONS",
    "READ_FUNCTIONS",
]
