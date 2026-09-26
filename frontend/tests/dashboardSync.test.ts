import assert from "node:assert/strict";

import { applyEvents, mergePendingEvents, selectRestSnapshot } from "../src/lib/dashboardSync.ts";
import type { DashboardSnapshot, EventsSocketMessage } from "../src/types/dashboard.ts";

const snapshot: DashboardSnapshot = {
  state_version: 10,
  kpis: { refusal_rate: 0, slow_transactions: 0, slow_transactions_available: true, fraud_timeouts: 0, fraud_timeout_available: true, non_completed_transactions: 0, global_risk_score: 0, total_amount: 0, amount_unit: "TND", total_transactions: 0, success_rate: 0, active_terminals: 0, total_transfers_amount: 0, transfers_count: 0, affiliations_remaining: null, average_processing_time_ms: 0, avg_processing_time_available: true },
  incidents_by_hour: [], fraud_trend_7_days: [], response_time_distribution: [], status_distribution: [], active_alerts: [], live_events: [], top_anomalies: [], top_tpe: [], top_merchants: [],
  replay_status: { mode: "sqlserver_replay", label: "SQL Server replay mode from historical data", running: true, paused: false, processed_transactions: 0, batches_processed: 0, batch_size: 500, interval_seconds: 5, current_speed_tx_per_sec: 100, current_batch_size: 500, current_interval_ms: 5000, fast_forward_enabled: false, source_available: true, transfer_period_available: true },
};

const newer: EventsSocketMessage = { version: "1.0", type: "events", state_version: 11, emitted_at: "2026-06-15T08:31:00+00:00", events: [{ type: "supervision_update", snapshot: { ...snapshot, state_version: 11, kpis: { ...snapshot.kpis, total_transactions: 11 } } }] };
const newest: EventsSocketMessage = { ...newer, state_version: 13, events: [{ type: "supervision_update", snapshot: { ...snapshot, state_version: 13, kpis: { ...snapshot.kpis, total_transactions: 13 } } }] };
const older: EventsSocketMessage = { ...newer, state_version: 12, events: [{ type: "supervision_update", snapshot: { ...snapshot, state_version: 12, kpis: { ...snapshot.kpis, total_transactions: 12 } } }] };

const updated = applyEvents(snapshot, newer);
assert.equal(updated?.state_version, 11);
assert.equal(updated?.kpis.total_transactions, 11);
assert.equal(applyEvents(updated, { ...newer, state_version: 9 }), updated);
assert.equal(mergePendingEvents(newer, { ...newer, state_version: 9 }), newer);
assert.equal(selectRestSnapshot(updated, { ...snapshot, state_version: 11 }), updated);
assert.equal(selectRestSnapshot(updated, { ...snapshot, state_version: 12 }).state_version, 12);
const burst = [newer, newest, older].reduce<EventsSocketMessage | null>((pending, message) => mergePendingEvents(pending, message), null);
assert.equal(burst?.state_version, 13);
assert.equal(applyEvents(snapshot, burst!)?.kpis.total_transactions, 13);
assert.equal(applyEvents({ ...snapshot, state_version: 14 }, burst!)?.state_version, 14);
assert.equal(selectRestSnapshot({ ...snapshot, state_version: 20 }, { ...snapshot, state_version: 19 }).state_version, 20);

console.log("supervision dashboard synchronization tests passed");
