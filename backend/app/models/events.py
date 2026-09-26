"""Domain events emitted by historical or future live transaction sources."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from app.models.transaction import PaymentTransaction


@dataclass(frozen=True, slots=True)
class TransactionEvent:
    event_id: str
    transaction_id: str
    event_time: datetime
    source_time: datetime
    merchant_id: str | None
    terminal_id: str
    amount_tnd: float
    status: str
    response_code: str | None
    processing_time_ms: float | None
    fraud_time_ms: float | None
    source: Literal["sqlserver_replay"] = "sqlserver_replay"
    replay: bool = True
    source_status: str | None = None
    fraud_decision: str | None = None
    merchant_label: str | None = None
    region: str = "N/A"

    @classmethod
    def from_transaction(cls, transaction: PaymentTransaction) -> "TransactionEvent":
        return cls(
            event_id=f"replay:{transaction.transaction_id}:{uuid4().hex}",
            transaction_id=transaction.transaction_id,
            event_time=datetime.now(timezone.utc),
            source_time=transaction.timestamp,
            merchant_id=transaction.merchant_id,
            terminal_id=transaction.terminal_id,
            amount_tnd=transaction.amount,
            status=transaction.payment_status,
            response_code=transaction.response_code,
            processing_time_ms=transaction.processing_time_ms,
            fraud_time_ms=transaction.fraud_time_ms,
            source_status=transaction.source_status,
            fraud_decision=transaction.fraud_decision,
            merchant_label=transaction.merchant,
            region=transaction.region,
        )

    def to_payment_transaction(self) -> PaymentTransaction:
        return PaymentTransaction(
            transaction_id=self.transaction_id,
            terminal_id=self.terminal_id,
            merchant=self.merchant_label or (f"Commerçant #{self.merchant_id}" if self.merchant_id else "Commerçant inconnu"),
            region=self.region,
            amount=self.amount_tnd,
            timestamp=self.source_time,
            payment_status=self.status,  # type: ignore[arg-type]
            card_scheme="N/A",
            merchant_id=self.merchant_id,
            source_status=self.source_status,
            response_code=self.response_code,
            processing_time_ms=self.processing_time_ms,
            fraud_time_ms=self.fraud_time_ms,
            fraud_decision=self.fraud_decision,
        )
