from datetime import datetime, timezone

from app.models.transaction import PaymentStatus, PaymentTransaction


def make_transaction(
    transaction_id: str = "TX-TEST-1",
    *,
    amount: float = 42.5,
    merchant: str = "Cedar Shop",
    region: str = "Tunis",
    status: PaymentStatus = "approved",
    timestamp: datetime | None = None,
    processing_time_ms: float | None = 250,
    fraud_time_ms: float | None = 50,
    fraud_decision: str | None = None,
    response_code: str | None = "000",
    source_status: str | None = None,
    merchant_id: str | None = None,
    terminal_id: str = "TPE-TUN-1000",
) -> PaymentTransaction:
    return PaymentTransaction(
        transaction_id=transaction_id,
        terminal_id=terminal_id,
        merchant=merchant,
        region=region,
        amount=amount,
        timestamp=timestamp or datetime.now(timezone.utc),
        payment_status=status,
        card_scheme="VISA",
        merchant_id=merchant_id,
        processing_time_ms=processing_time_ms,
        fraud_time_ms=fraud_time_ms,
        fraud_decision=fraud_decision,
        response_code=response_code,
        source_status=source_status,
    )


class RecordingWebSocketManager:
    count = 0

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def broadcast(self, payload: dict) -> None:
        self.payloads.append(payload)
