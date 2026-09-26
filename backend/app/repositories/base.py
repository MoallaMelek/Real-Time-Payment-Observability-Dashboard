from typing import Protocol

from app.models.transaction import PaymentTransaction


class TransactionStore(Protocol):
    async def persist_batch(self, transactions: list[PaymentTransaction]) -> list[PaymentTransaction]: ...

    async def load_transactions(self) -> list[PaymentTransaction]: ...

    async def diagnostics(self) -> dict[str, str | int]: ...
