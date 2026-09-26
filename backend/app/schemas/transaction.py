from datetime import datetime

from pydantic import BaseModel, Field


class TransactionRead(BaseModel):
    id: str
    terminal_id: str
    merchant: str
    merchant_id: str | None = None
    amount: float = Field(ge=0)
    timestamp: datetime
    status: str
    response_code: str | None = None
    processing_time_ms: float | None = None
    fraud_time_ms: float | None = None
