"""Safe, normalized transaction models used by the supervision pipeline.

Only the fields needed for operational monitoring are represented here.  Card,
account, contact and authentication data must never enter this layer.
"""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal, cast

from app.schemas.data import TransactionData

PaymentStatus = Literal["approved", "declined", "pending", "reversed"]


@dataclass(slots=True)
class NormalizedTransaction:
    """Internal SQL Server representation, deliberately free of sensitive data."""

    id: str
    timestamp: datetime
    merchant_id: str | None
    magasin_id: str | None
    terminal_id: str | None
    terminal_merchant_id: str | None
    status: str
    response_code: str | None
    amount: float
    processing_time_ms: float | None
    fraud_time_ms: float | None
    fraud_decision: str | None
    merchant_name: str | None = None
    terminal_status: str | None = None
    softpos: bool | None = None
    connectivity: str | None = None
    virement_emis: bool = False

    def to_payment_transaction(self) -> "PaymentTransaction":
        status = self.status.strip().lower()
        if "non autor" in status or "refus" in status:
            payment_status: PaymentStatus = "declined"
        elif "non about" in status or "attente" in status or "instance" in status:
            payment_status = "pending"
        elif "annul" in status or "reverse" in status:
            payment_status = "reversed"
        else:
            payment_status = "approved"

        return PaymentTransaction(
            transaction_id=self.id,
            terminal_id=self.terminal_id or self.terminal_merchant_id or "N/A",
            merchant=self.merchant_name or self._merchant_fallback(),
            region=self.magasin_id or "N/A",
            amount=self.amount,
            timestamp=self.timestamp,
            payment_status=payment_status,
            card_scheme="N/A",
            merchant_id=self.merchant_id,
            magasin_id=self.magasin_id,
            terminal_merchant_id=self.terminal_merchant_id,
            source_status=self.status,
            response_code=self.response_code,
            processing_time_ms=self.processing_time_ms,
            fraud_time_ms=self.fraud_time_ms,
            fraud_decision=self.fraud_decision,
            terminal_status=self.terminal_status,
            connectivity=self.connectivity,
            virement_emis=self.virement_emis,
        )

    def _merchant_fallback(self) -> str:
        return f"Commerçant #{self.merchant_id}" if self.merchant_id else "Commerçant inconnu"


@dataclass(slots=True)
class PaymentTransaction:
    """Operational transaction consumed by the in-memory dashboard projection.

    The initial eight fields keep the original mock and SQLite test helpers
    compatible.  The optional fields carry SQL Server supervision signals.
    """

    transaction_id: str
    terminal_id: str
    merchant: str
    region: str
    amount: float
    timestamp: datetime
    payment_status: PaymentStatus
    card_scheme: str
    merchant_id: str | None = None
    magasin_id: str | None = None
    terminal_merchant_id: str | None = None
    source_status: str | None = None
    response_code: str | None = None
    processing_time_ms: float | None = None
    fraud_time_ms: float | None = None
    fraud_decision: str | None = None
    terminal_status: str | None = None
    connectivity: str | None = None
    virement_emis: bool = False

    @property
    def is_refused(self) -> bool:
        status = (self.source_status or "").lower()
        return self.payment_status == "declined" or "non autor" in status or "refus" in status

    @property
    def is_non_completed(self) -> bool:
        status = (self.source_status or "").lower()
        return "non about" in status or self.payment_status == "pending"

    @property
    def is_slow(self) -> bool:
        return (self.processing_time_ms or 0) > 1000

    @property
    def is_fraud_timeout(self) -> bool:
        return self.is_fraud_timeout_at(5000)

    def is_fraud_timeout_at(self, threshold_ms: float) -> bool:
        decision = (self.fraud_decision or "").strip().lower()
        suspicious_decision = decision not in {"", "approved", "accept", "accepted", "ok", "000"}
        return (self.fraud_time_ms or 0) > threshold_ms or suspicious_decision

    @property
    def has_abnormal_response(self) -> bool:
        return bool(self.response_code and self.response_code.strip() not in {"", "000"})

    @property
    def is_success(self) -> bool:
        return not self.is_refused and not self.is_non_completed and self.payment_status == "approved"

    def to_dict(self) -> TransactionData:
        return cast(
            TransactionData,
            {
                "id": self.transaction_id,
                "terminal_id": self.terminal_id,
                "merchant": self.merchant,
                "merchant_id": self.merchant_id,
                "amount": round(self.amount, 3),
                "timestamp": self.timestamp.isoformat(),
                "status": self.source_status or self.payment_status,
                "response_code": self.response_code,
                "processing_time_ms": self.processing_time_ms,
                "fraud_time_ms": self.fraud_time_ms,
            },
        )
