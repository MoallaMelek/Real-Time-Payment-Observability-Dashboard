"""Centralized monetary normalization for SQL Server monetary values."""

from decimal import Decimal, DecimalException
from typing import Any
from math import isfinite


def millimes_to_tnd(raw_amount: Any, scale: float) -> float:
    """Convert a SQL Server amount expressed in millimes into TND.

    The conversion belongs to the backend boundary: all callers downstream
    receive already-normalized TND amounts and must never divide again.
    """
    try:
        amount = Decimal(str(raw_amount))
        divisor = Decimal(str(scale))
        if not amount.is_finite() or not divisor.is_finite() or divisor <= 0:
            raise ValueError("Monetary amount must be finite and scale must be positive and finite")
        result = float(amount / divisor)
        if not isfinite(result):
            raise ValueError("Monetary amount exceeds the supported range")
        return result
    except (DecimalException, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid monetary amount: {raw_amount!r}") from exc
