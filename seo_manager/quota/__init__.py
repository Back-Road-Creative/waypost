"""
API Quota Management Module.

Tracks and enforces YouTube (and sibling API) quota limits by
(project, API, bucket), with atomic reservations. See
``docs/quota-accounting.md``.
"""

from .manager import (
    OperationCost,
    OperationSpec,
    Outcome,
    QuotaExceededError,
    QuotaManager,
    Reservation,
)

__all__ = [
    "OperationCost",
    "OperationSpec",
    "Outcome",
    "QuotaExceededError",
    "QuotaManager",
    "Reservation",
]
