export type AlertSeverity = "info" | "warning" | "critical";
export type RiskSeverity = "low" | "medium" | "high";
export type DashboardPeriod = "today" | "yesterday" | "7d" | "30d" | "quarter" | "year";
export type ReconciliationStatus = "pending" | "validated" | "warning" | "invalid" | "unavailable";
export type MetricCategory = "source_fact" | "derived_metric" | "simulated_projection";

export interface SupervisionKpis {
  refusal_rate: number;
  slow_transactions: number | null;
  slow_transactions_available: boolean;
  fraud_timeouts: number | null;
  fraud_timeout_available: boolean;
  non_completed_transactions: number;
  global_risk_score: number;
  total_amount: number;
  amount_unit: string;
  total_transactions: number;
  success_rate: number;
  active_terminals: number;
  total_transfers_amount: number;
  transfers_count: number;
  transfer_period_available: boolean;
  affiliations_remaining: number | null;
  affiliations_total: number;
  affiliations_reservees: number;
  affiliations_affectees: number;
  affiliation_demo_data: boolean;
  average_processing_time_ms: number | null;
  avg_processing_time_available: boolean;
}

export interface IncidentHour {
  hour: string;
  transactions: number;
  refused: number;
}

export interface FraudTrendPoint {
  day: string;
  refused: number;
  slow: number | null;
  fraud_timeouts: number | null;
}

export interface DistributionItem {
  label: string;
  value: number;
  color?: string | null;
}

export interface SupervisionAlert {
  id: string;
  timestamp: string;
  type: string;
  severity: AlertSeverity;
  title: string;
  message: string;
  merchant_name?: string | null;
  merchant_id?: string | null;
  terminal_id?: string | null;
}

export interface LiveEvent {
  id: string;
  timestamp: string;
  severity: AlertSeverity;
  title: string;
  message: string;
  merchant_name?: string | null;
  merchant?: string | null;
  terminal_id?: string | null;
}

export interface AnomalyMetric {
  name: string;
  merchant_name?: string | null;
  refused: number;
  timeouts: number | null;
  slow: number | null;
  risk_score: number;
  severity: RiskSeverity;
}

export interface TerminalMetric {
  terminal_id: string;
  merchant_name?: string | null;
  merchant: string;
  transactions: number;
  refused: number;
  timeouts: number | null;
  risk_score: number;
  severity: RiskSeverity;
}

export interface MerchantMetric {
  name: string;
  merchant_name?: string | null;
  transactions: number;
  refused: number;
  non_completed: number;
  risk_score: number;
  severity: RiskSeverity;
  success_rate: number;
}

export interface ReplayStatus {
  mode: "sqlserver_replay" | "mock";
  label: string;
  running: boolean;
  paused: boolean;
  processed_transactions: number;
  batches_processed: number;
  batch_size: number;
  interval_seconds: number;
  current_speed_tx_per_sec: number;
  current_batch_size: number;
  current_interval_ms: number;
  fast_forward_enabled: boolean;
  cursor?: string | null;
  last_batch_at?: string | null;
  source_available: boolean;
  detail?: string | null;
  period?: DashboardPeriod | null;
  reference_date?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  transfer_period_available: boolean;
  replay_run_id?: string | null;
  cache_backend?: string | null;
  redis_configured?: boolean;
  redis_available?: boolean;
  redis_reason?: "available" | "not_configured" | "connection_failed";
  redis_latency_ms?: number | null;
  expected_transactions?: number | null;
  completion_rate?: number;
  reconciliation_status?: ReconciliationStatus;
  last_reconciled_at?: string | null;
  differences?: Record<string, unknown>;
}

export interface DataQualityReport {
  status: ReconciliationStatus;
  reconciliation_status: ReconciliationStatus;
  expected_transactions: number | null;
  processed_transactions: number;
  completion_rate: number;
  last_reconciled_at: string;
  differences: Record<string, unknown>;
  amount_tolerance_tnd: number;
  expected: Record<string, unknown> | null;
  actual: Record<string, unknown>;
  invariants: Array<{ name: string; valid: boolean }>;
  event_bus: Record<string, unknown>;
  unknown_status_values: Record<string, number>;
  metric_metadata: Record<string, MetricCategory>;
  unavailable_reason?: string | null;
}

export interface DashboardSnapshot {
  state_version: number;
  kpis: SupervisionKpis;
  incidents_by_hour: IncidentHour[];
  fraud_trend_7_days: FraudTrendPoint[];
  response_time_distribution: DistributionItem[];
  status_distribution: DistributionItem[];
  active_alerts: SupervisionAlert[];
  live_events: LiveEvent[];
  top_anomalies: AnomalyMetric[];
  top_tpe: TerminalMetric[];
  top_merchants: MerchantMetric[];
  replay_status: ReplayStatus;
  data_quality?: DataQualityReport | null;
  metric_metadata?: Record<string, MetricCategory>;
}

export interface SnapshotSocketMessage {
  version: "1.0";
  type: "snapshot";
  state_version: number;
  emitted_at: string;
  snapshot: DashboardSnapshot;
}

export interface EventsSocketMessage {
  version: "1.0";
  type: "events";
  state_version: number;
  emitted_at: string;
  events: Array<{ type: "supervision_update"; snapshot: DashboardSnapshot }>;
}

export interface PongSocketMessage {
  version: "1.0";
  type: "pong";
  emitted_at: string;
}

export interface ErrorSocketMessage {
  version: "1.0";
  type: "error";
  emitted_at: string;
  detail: string;
}

export type PaymentSocketMessage = SnapshotSocketMessage | EventsSocketMessage | PongSocketMessage | ErrorSocketMessage;
export type SocketStatus = "connecting" | "connected" | "reconnecting" | "closed";

// Legacy component contracts are retained while the old business widgets are
// no longer mounted. This keeps optional, reusable visual components buildable.
export type PaymentStatus = "approved" | "declined" | "pending" | "reversed";
export interface Transaction {
  transaction_id: string;
  terminal_id: string;
  merchant: string;
  region: string;
  amount: number;
  timestamp: string;
  payment_status: PaymentStatus;
  card_scheme: string;
}
export interface RankedMetric { name: string; transactions: number; approved_volume: number; approval_rate: number; }
export interface MinuteMetric { minute: string; transactions: number; approved_volume: number; }
export interface AlertMetric { id: string; type: string; severity: AlertSeverity; scope: string; message: string; emitted_at: string; }
