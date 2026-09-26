from typing import Any, Literal, TypedDict


class TransactionData(TypedDict):
    id: str
    terminal_id: str
    merchant: str
    merchant_id: str | None
    amount: float
    timestamp: str
    status: str
    response_code: str | None
    processing_time_ms: float | None
    fraud_time_ms: float | None


class AlertMetricData(TypedDict):
    id: str
    timestamp: str
    type: str
    severity: Literal["info", "warning", "critical"]
    title: str
    message: str
    merchant_name: str | None
    merchant_id: str | None
    terminal_id: str | None


# The Pydantic schema is the public contract.  This broad alias keeps the
# aggregation code concise while preserving type hints around cache/WS calls.
DashboardSnapshotData = dict[str, Any]
