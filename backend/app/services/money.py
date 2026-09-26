"""Centralized monetary normalization for SQL Server monetary values."""

from decimal import Decimal, InvalidOperation
from typing import Any


def millimes_to_tnd(raw_amount: Any, scale: float) -> float:
    """Convert a SQL Server amount expressed in millimes into TND.

    The conversion belongs to the backend boundary: all callers downstream
    receive already-normalized TND amounts and must never divide again.
    """
    try:
        return float(Decimal(str(raw_amount)) / Decimal(str(scale)))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid monetary amount: {raw_amount!r}") from exc
