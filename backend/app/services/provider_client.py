from dataclasses import dataclass
from typing import Protocol

from app.models.transaction import PaymentTransaction


@dataclass(slots=True)
class ProviderCursor:
    value: str | None = None


@dataclass(slots=True)
class ProviderBatch:
    transactions: list[PaymentTransaction]
    next_cursor: ProviderCursor


class PaymentProviderClient(Protocol):
    """Future contract for bank/acquirer/TPE processor integrations."""

    async def poll_transactions(self, cursor: ProviderCursor) -> ProviderBatch:
        ...

    async def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        ...


class UnconfiguredPaymentProviderClient:
    """Explicit placeholder used until a real provider adapter is configured."""

    async def poll_transactions(self, cursor: ProviderCursor) -> ProviderBatch:
        raise NotImplementedError("Configure a real payment provider adapter before polling transactions.")

    async def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        raise NotImplementedError("Configure a real payment provider adapter before accepting webhooks.")
