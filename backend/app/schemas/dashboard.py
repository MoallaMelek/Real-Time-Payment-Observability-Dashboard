from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.transaction import TransactionRead


class SupervisionKpis(BaseModel):
    refusal_rate: float
    slow_transactions: int | None = None
    slow_transactions_available: bool
    fraud_timeouts: int | None = None
    fraud_timeout_available: bool
    non_completed_transactions: int
    global_risk_score: int
    total_amount: float
    amount_unit: str = "TND"
    total_transactions: int
    success_rate: float
    active_terminals: int
    total_transfers_amount: float
    transfers_count: int
    transfer_period_available: bool = False
    affiliations_remaining: int | None = None
    affiliations_total: int = 0
    affiliations_reservees: int = 0
    affiliations_affectees: int = 0
    affiliation_demo_data: bool = False
    average_processing_time_ms: float | None = None
    avg_processing_time_available: bool


class IncidentHour(BaseModel):
    hour: str
    transactions: int
    refused: int


class FraudTrendPoint(BaseModel):
    day: str
    refused: int
    slow: int | None = None
    fraud_timeouts: int | None = None


class DistributionItem(BaseModel):
    label: str
    value: int
    color: str | None = None


class AlertMetric(BaseModel):
    id: str
    timestamp: datetime
    type: str
    severity: Literal["info", "warning", "critical"]
    title: str
    message: str
    merchant_name: str | None = None
    merchant_id: str | None = None
    terminal_id: str | None = None


class LiveEvent(BaseModel):
    id: str
    timestamp: datetime
    severity: Literal["info", "warning", "critical"]
    title: str
    message: str
    merchant_name: str | None = None
    merchant: str | None = None
    terminal_id: str | None = None


class AnomalyMetric(BaseModel):
    name: str
    merchant_name: str | None = None
    refused: int
    timeouts: int | None = None
    slow: int | None = None
    risk_score: int
    severity: Literal["low", "medium", "high"]


class TerminalMetric(BaseModel):
    terminal_id: str
    merchant_name: str | None = None
    merchant: str
    transactions: int
    refused: int
    timeouts: int | None = None
    risk_score: int
    severity: Literal["low", "medium", "high"]


class MerchantMetric(BaseModel):
    name: str
    merchant_name: str | None = None
    transactions: int
    refused: int
    non_completed: int
    risk_score: int
    severity: Literal["low", "medium", "high"]
    success_rate: float


class ReplayStatus(BaseModel):
    mode: Literal["sqlserver_replay", "mock"]
    replay_mode: str = "historical_replay"
    label: str
    running: bool
    paused: bool
    processed_transactions: int
    batches_processed: int
    batch_size: int
    interval_seconds: float
    current_speed_tx_per_sec: float
    current_batch_size: int
    current_interval_ms: int
    fast_forward_enabled: bool
    cursor: str | None = None
    last_batch_at: datetime | None = None
    source_available: bool = True
    detail: str | None = None
    period: str | None = None
    reference_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    transfer_period_available: bool = False
    replay_run_id: str | None = None
    cache_backend: str | None = None
    redis_configured: bool = False
    redis_available: bool = True
    redis_reason: Literal["available", "not_configured", "connection_failed"] = "available"
    redis_latency_ms: float | None = None
    expected_transactions: int | None = None
    completion_rate: float = 0.0
    reconciliation_status: Literal["pending", "validated", "warning", "invalid", "unavailable"] = "unavailable"
    last_reconciled_at: datetime | None = None
    differences: dict[str, Any] = Field(default_factory=dict)


class DataQualityReport(BaseModel):
    status: Literal["pending", "validated", "warning", "invalid", "unavailable"]
    reconciliation_status: Literal["pending", "validated", "warning", "invalid", "unavailable"]
    expected_transactions: int | None = None
    processed_transactions: int
    completion_rate: float
    last_reconciled_at: datetime
    differences: dict[str, Any] = Field(default_factory=dict)
    amount_tolerance_tnd: float
    expected: dict[str, Any] | None = None
    actual: dict[str, Any]
    invariants: list[dict[str, Any]] = Field(default_factory=list)
    event_bus: dict[str, Any] = Field(default_factory=dict)
    unknown_status_values: dict[str, int] = Field(default_factory=dict)
    metric_metadata: dict[str, Literal["source_fact", "derived_metric", "simulated_projection"]] = Field(default_factory=dict)
    unavailable_reason: str | None = None


class DashboardSnapshot(BaseModel):
    state_version: int
    snapshot_version: int | None = None
    snapshot_generated_at: datetime | None = None
    kpis: SupervisionKpis
    incidents_by_hour: list[IncidentHour] = Field(default_factory=list)
    fraud_trend_7_days: list[FraudTrendPoint] = Field(default_factory=list)
    response_time_distribution: list[DistributionItem] = Field(default_factory=list)
    status_distribution: list[DistributionItem] = Field(default_factory=list)
    active_alerts: list[AlertMetric] = Field(default_factory=list)
    live_events: list[LiveEvent] = Field(default_factory=list)
    top_anomalies: list[AnomalyMetric] = Field(default_factory=list)
    top_tpe: list[TerminalMetric] = Field(default_factory=list)
    top_merchants: list[MerchantMetric] = Field(default_factory=list)
    replay_status: ReplayStatus
    data_quality: DataQualityReport | None = None
    metric_metadata: dict[str, Literal["source_fact", "derived_metric", "simulated_projection"]] = Field(default_factory=dict)
