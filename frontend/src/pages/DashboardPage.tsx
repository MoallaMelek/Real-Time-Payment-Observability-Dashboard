import { useMemo, useState, type ReactNode } from "react";

import { FraudTrendChart, IncidentsChart, ResponseDistributionChart, StatusDonutChart } from "../charts/SupervisionCharts";
import { AnimatedNumberText } from "../components/AnimatedValue";
import { ChatAssistant } from "../components/ChatAssistant";
import { DashboardHeader } from "../components/DashboardHeader";
import { KpiCard } from "../components/KpiCard";
import { useDashboardData } from "../hooks/useDashboardData";
import { formatAmount, formatNumber, formatTime } from "../lib/format";
import { visualInterpolationEngine, type VisualMode } from "../lib/visualInterpolation";
import { useDashboardTheme } from "../theme/DashboardTheme";
import type { AlertSeverity, AnomalyMetric, MerchantMetric, RiskSeverity, TerminalMetric } from "../types/dashboard";

const alertTone: Record<AlertSeverity, string> = {
  info: "event-info",
  warning: "event-warning",
  critical: "event-critical",
};

const riskTone: Record<RiskSeverity, string> = {
  low: "risk-low",
  medium: "risk-medium",
  high: "risk-high",
};

function PanelTitle({ icon, title, action }: { icon: ReactNode; title: string; action?: ReactNode }) {
  return (
    <div className="panel-title">
      <span className="panel-title-icon" aria-hidden="true">{icon}</span>
      <h2>{title}</h2>
      {action ? <div className="panel-action">{action}</div> : null}
    </div>
  );
}

function SeverityChip({ severity, score }: { severity: RiskSeverity; score: number }) {
  const { t } = useDashboardTheme();
  const label = severity === "high" ? t("high") : severity === "medium" ? t("medium") : t("low");
  return <span className={`risk-pill ${riskTone[severity]}`}><b>{score}</b><em>{label}</em></span>;
}

function TableToolbar({
  query,
  onQueryChange,
  severity,
  onSeverityChange,
}: {
  query: string;
  onQueryChange: (value: string) => void;
  severity: RiskSeverity | "all";
  onSeverityChange: (value: RiskSeverity | "all") => void;
}) {
  const { t } = useDashboardTheme();
  return (
    <div className="table-toolbar">
      <label>
        <span>{t("search")}</span>
        <input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder={t("merchantName")} />
      </label>
      <label>
        <span>{t("filter")}</span>
        <select value={severity} onChange={(event) => onSeverityChange(event.target.value as RiskSeverity | "all")}>
          <option value="all">{t("allLevels")}</option>
          <option value="high">{t("high")}</option>
          <option value="medium">{t("medium")}</option>
          <option value="low">{t("low")}</option>
        </select>
      </label>
    </div>
  );
}

function useTableControls<T extends { severity: RiskSeverity }>(
  rows: T[],
  searchable: (row: T) => string,
  defaultSort: keyof T,
) {
  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState<RiskSeverity | "all">("all");
  const [sortKey, setSortKey] = useState<keyof T>(defaultSort);
  const [direction, setDirection] = useState<"asc" | "desc">("desc");

  const filteredRows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return rows
      .filter((row) => severity === "all" || row.severity === severity)
      .filter((row) => !needle || searchable(row).toLowerCase().includes(needle))
      .sort((left, right) => {
        const a = left[sortKey];
        const b = right[sortKey];
        const value = typeof a === "number" && typeof b === "number"
          ? a - b
          : String(a ?? "").localeCompare(String(b ?? ""));
        return direction === "asc" ? value : -value;
      });
  }, [direction, query, rows, searchable, severity, sortKey]);

  const requestSort = (key: keyof T) => {
    if (sortKey === key) {
      setDirection((current) => (current === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setDirection("desc");
    }
  };

  return { filteredRows, query, setQuery, severity, setSeverity, requestSort, sortKey, direction };
}

function SortButton<T>({ label, name, sortKey, direction, onSort }: { label: string; name: keyof T; sortKey: keyof T; direction: "asc" | "desc"; onSort: (name: keyof T) => void }) {
  const active = sortKey === name;
  return (
    <button type="button" className={active ? "sort-button is-active" : "sort-button"} onClick={() => onSort(name)}>
      {label}{active ? <span>{direction === "asc" ? "↑" : "↓"}</span> : null}
    </button>
  );
}

function TopAnomaliesTable({ rows }: { rows: AnomalyMetric[] }) {
  const { t } = useDashboardTheme();
  const controls = useTableControls(rows, (row) => `${row.merchant_name ?? row.name} ${row.severity}`, "risk_score");
  return (
    <>
      <TableToolbar query={controls.query} onQueryChange={controls.setQuery} severity={controls.severity} onSeverityChange={controls.setSeverity} />
      <div className="table-wrap">
        <table>
          <thead><tr>
            <th><SortButton<AnomalyMetric> label={t("merchantName")} name="name" {...controls} onSort={controls.requestSort} /></th>
            <th><SortButton<AnomalyMetric> label={t("refused")} name="refused" {...controls} onSort={controls.requestSort} /></th>
            <th><SortButton<AnomalyMetric> label={t("timeouts")} name="timeouts" {...controls} onSort={controls.requestSort} /></th>
            <th><SortButton<AnomalyMetric> label={t("slow")} name="slow" {...controls} onSort={controls.requestSort} /></th>
            <th><SortButton<AnomalyMetric> label={t("score")} name="risk_score" {...controls} onSort={controls.requestSort} /></th>
          </tr></thead>
          <tbody>
            {controls.filteredRows.map((row) => (
              <tr key={row.name}>
                <td><span className="table-avatar">{(row.merchant_name ?? row.name).slice(0, 1).toUpperCase()}</span>{row.merchant_name ?? row.name}</td>
                <td>{row.refused}</td>
                <td>{row.timeouts ?? "N/A"}</td>
                <td>{row.slow ?? "N/A"}</td>
                <td><SeverityChip severity={row.severity} score={row.risk_score} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function TopTpeTable({ rows }: { rows: TerminalMetric[] }) {
  const { t } = useDashboardTheme();
  const controls = useTableControls(rows, (row) => `${row.terminal_id} ${row.merchant_name ?? row.merchant} ${row.severity}`, "risk_score");
  return (
    <>
      <TableToolbar query={controls.query} onQueryChange={controls.setQuery} severity={controls.severity} onSeverityChange={controls.setSeverity} />
      <div className="table-wrap">
        <table>
          <thead><tr>
            <th><SortButton<TerminalMetric> label={t("serialNumber")} name="terminal_id" {...controls} onSort={controls.requestSort} /></th>
            <th><SortButton<TerminalMetric> label={t("merchantName")} name="merchant" {...controls} onSort={controls.requestSort} /></th>
            <th><SortButton<TerminalMetric> label="Trx" name="transactions" {...controls} onSort={controls.requestSort} /></th>
            <th><SortButton<TerminalMetric> label={t("refused")} name="refused" {...controls} onSort={controls.requestSort} /></th>
            <th><SortButton<TerminalMetric> label={t("risk")} name="risk_score" {...controls} onSort={controls.requestSort} /></th>
          </tr></thead>
          <tbody>
            {controls.filteredRows.map((row) => (
              <tr key={row.terminal_id}>
                <td className="mono">{row.terminal_id}</td>
                <td><span className="table-avatar">{(row.merchant_name ?? row.merchant).slice(0, 1).toUpperCase()}</span>{row.merchant_name ?? row.merchant}</td>
                <td>{row.transactions}</td>
                <td>{row.refused}</td>
                <td><SeverityChip severity={row.severity} score={row.risk_score} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function TopMerchantsTable({ rows }: { rows: MerchantMetric[] }) {
  const { t } = useDashboardTheme();
  const controls = useTableControls(rows, (row) => `${row.merchant_name ?? row.name} ${row.severity}`, "transactions");
  return (
    <>
      <TableToolbar query={controls.query} onQueryChange={controls.setQuery} severity={controls.severity} onSeverityChange={controls.setSeverity} />
      <div className="table-wrap">
        <table>
          <thead><tr>
            <th>#</th>
            <th><SortButton<MerchantMetric> label={t("merchantName")} name="name" {...controls} onSort={controls.requestSort} /></th>
            <th><SortButton<MerchantMetric> label="Trx" name="transactions" {...controls} onSort={controls.requestSort} /></th>
            <th><SortButton<MerchantMetric> label={t("refused")} name="refused" {...controls} onSort={controls.requestSort} /></th>
            <th><SortButton<MerchantMetric> label={t("nonCompletedShort")} name="non_completed" {...controls} onSort={controls.requestSort} /></th>
            <th><SortButton<MerchantMetric> label={t("risk")} name="risk_score" {...controls} onSort={controls.requestSort} /></th>
          </tr></thead>
          <tbody>
            {controls.filteredRows.map((row, index) => (
              <tr key={row.name}>
                <td><span className="rank">{index + 1}</span></td>
                <td><span className="table-avatar">{(row.merchant_name ?? row.name).slice(0, 1).toUpperCase()}</span>{row.merchant_name ?? row.name}</td>
                <td>{row.transactions}</td>
                <td>{row.refused}</td>
                <td>{row.non_completed}</td>
                <td><SeverityChip severity={row.severity} score={row.risk_score} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function DashboardPage() {
  const { snapshot, socketStatus, error, selectedPeriod, selectPeriod, periodLoading, fastForwardLoading, toggleFastForward } = useDashboardData();
  const { language, t } = useDashboardTheme();
  const locale = language === "fr" ? "fr-TN" : "en-GB";

  if (!snapshot) {
    return (
      <main className="supervision-loading">
        <div><span className="loading-orb" />{t("loading")}</div>
        {error ? <p>{error}</p> : null}
      </main>
    );
  }

  const { kpis } = snapshot;
  const visualResetKey = snapshot.replay_status.replay_run_id ?? selectedPeriod;
  const visualMode: VisualMode = snapshot.replay_status.fast_forward_enabled ? "fast_forward" : "normal";
  const expectedSnapshotIntervalMs = snapshot.replay_status.current_interval_ms || (snapshot.replay_status.fast_forward_enabled ? 100 : 500);
  visualInterpolationEngine.configureDefaults({
    visualMode,
    period: selectedPeriod,
    expectedSnapshotIntervalMs,
    maxVisualLagMs: snapshot.replay_status.fast_forward_enabled ? 450 : 1800,
  });
  const visualProps = { resetKey: visualResetKey, visualMode, period: selectedPeriod, expectedSnapshotIntervalMs };
  const refusedCount = Math.round(kpis.refusal_rate / 100 * kpis.total_transactions);
  const nonCompletedRate = kpis.total_transactions ? kpis.non_completed_transactions / kpis.total_transactions * 100 : 0;
  const affiliationDetail = kpis.affiliations_total > 0
    ? `${formatNumber(kpis.affiliations_reservees, locale)} ${t("reserved")} · ${formatNumber(kpis.affiliations_affectees, locale)} ${t("affected")}`
    : t("affiliationsUnavailable");
  const incidentSparkline = snapshot.incidents_by_hour.map((item) => item.transactions);
  const refusalSparkline = snapshot.incidents_by_hour.map((item) => item.refused);
  const fraudSparkline = snapshot.fraud_trend_7_days.map((item) => item.fraud_timeouts ?? item.refused);

  return (
    <main className="supervision-dashboard">
      <DashboardHeader socketStatus={socketStatus} replayStatus={snapshot.replay_status} selectedPeriod={selectedPeriod} onPeriodChange={selectPeriod} periodLoading={periodLoading} onFastForwardToggle={toggleFastForward} fastForwardLoading={fastForwardLoading} />

      {error ? <div className="dashboard-error">{error}</div> : null}
      {!snapshot.replay_status.source_available ? <div className="dashboard-error">{t("sourceUnavailable")} : {snapshot.replay_status.detail}</div> : null}
      {snapshot.replay_status.start_date && snapshot.replay_status.end_date ? (
        <div className="period-applied">
          {t("appliedPeriod")} : <strong>{snapshot.replay_status.start_date}</strong> → <strong>{snapshot.replay_status.end_date}</strong> · {t("historicalReference")} : {snapshot.replay_status.reference_date}
        </div>
      ) : null}
      {kpis.total_transactions === 0 ? <div className="empty-state dashboard-empty">{t("emptyPeriod")}</div> : null}

      <section className="kpi-grid kpi-grid-primary">
        <KpiCard {...visualProps} sparklineKey="refusal" label={t("refusalRate")} value={<><AnimatedNumberText id="refusal-rate" resetKey={visualResetKey} value={kpis.refusal_rate} format={(value) => `${value.toFixed(1)}%`} options={{ epsilon: 0.03, timeConstantMs: 120 }} /></>} detail={<><AnimatedNumberText id="refused-count" resetKey={visualResetKey} value={refusedCount} format={(value) => formatNumber(Math.round(value), locale)} options={{ epsilon: 0.2, timeConstantMs: 115 }} /> {t("refused")}</>} trend={<AnimatedNumberText id="refused-trend" resetKey={visualResetKey} value={refusedCount} format={(value) => formatNumber(Math.round(value), locale)} options={{ epsilon: 0.2, timeConstantMs: 115 }} />} sparkline={refusalSparkline} tone="danger" icon="×" />
        <KpiCard {...visualProps} sparklineKey="transactions" label={t("totalTransactions")} value={<AnimatedNumberText id="total-transactions" resetKey={visualResetKey} value={kpis.total_transactions} format={(value) => formatNumber(Math.round(value), locale)} options={{ epsilon: 0.2, timeConstantMs: 130 }} />} detail={t("processedHistory")} trend={<><AnimatedNumberText id="processed-transactions" resetKey={visualResetKey} value={snapshot.replay_status.processed_transactions} format={(value) => formatNumber(Math.round(value), locale)} options={{ epsilon: 0.2, timeConstantMs: 130 }} /> replay</>} sparkline={incidentSparkline} tone="blue" icon="▤" />
        <KpiCard {...visualProps} sparklineKey="success" label={t("successRate")} value={<><AnimatedNumberText id="success-rate" resetKey={visualResetKey} value={kpis.success_rate} format={(value) => `${value.toFixed(1)}%`} options={{ epsilon: 0.03, timeConstantMs: 120 }} /></>} detail={t("approvedTransactions")} trend="+live" sparkline={incidentSparkline.map((value) => Math.max(0, value - refusedCount / Math.max(1, incidentSparkline.length)))} tone="mint" icon="●" />
        <KpiCard {...visualProps} sparklineKey="affiliations" label={t("affiliationsRemaining")} value={kpis.affiliations_remaining === null ? "N/A" : <AnimatedNumberText id="affiliations-remaining" resetKey={visualResetKey} value={kpis.affiliations_remaining} format={(value) => formatNumber(Math.round(value), locale)} options={{ epsilon: 0.2, timeConstantMs: 150 }} />} detail={affiliationDetail} trend="stock" sparkline={[kpis.affiliations_affectees, kpis.affiliations_reservees, kpis.affiliations_remaining ?? 0]} tone="cyan" icon="♟" />
      </section>

      <section className="kpi-grid kpi-grid-secondary">
        <KpiCard {...visualProps} sparklineKey="slow" label={t("slowTransactions")} value={kpis.slow_transactions_available ? <AnimatedNumberText id="slow-transactions" resetKey={visualResetKey} value={kpis.slow_transactions ?? 0} format={(value) => formatNumber(Math.round(value), locale)} options={{ epsilon: 0.2, timeConstantMs: 130 }} /> : "N/A"} detail={kpis.slow_transactions_available ? `${t("threshold")} : 1 000 ms` : t("unavailable")} sparkline={fraudSparkline} tone="amber" icon="◷" />
        <KpiCard {...visualProps} sparklineKey="fraud-timeouts" label={kpis.fraud_timeout_available ? t("fraudTimeouts") : `${t("fraudTimeouts")} - N/A`} value={kpis.fraud_timeout_available ? <AnimatedNumberText id="fraud-timeouts" resetKey={visualResetKey} value={kpis.fraud_timeouts ?? 0} format={(value) => formatNumber(Math.round(value), locale)} options={{ epsilon: 0.2, timeConstantMs: 130 }} /> : "N/A"} detail={kpis.fraud_timeout_available ? t("fraudObserved") : t("fraudUnavailable")} sparkline={fraudSparkline} tone="danger" icon="♜" />
        <KpiCard {...visualProps} sparklineKey="non-completed" label={t("nonCompleted")} value={<><AnimatedNumberText id="non-completed-rate" resetKey={visualResetKey} value={nonCompletedRate} format={(value) => `${value.toFixed(1)}%`} options={{ epsilon: 0.03, timeConstantMs: 120 }} /></>} detail={<><AnimatedNumberText id="non-completed-count" resetKey={visualResetKey} value={kpis.non_completed_transactions} format={(value) => formatNumber(Math.round(value), locale)} options={{ epsilon: 0.2, timeConstantMs: 120 }} /> {t("transactions")}</>} sparkline={refusalSparkline} tone="slate" icon="◆" />
        <KpiCard {...visualProps} label={`${t("totalAmount")} (TND)`} value={<AnimatedNumberText id="total-amount" resetKey={visualResetKey} value={kpis.total_amount} format={(value) => formatAmount(value, locale)} options={{ epsilon: 0.001, timeConstantMs: 160 }} />} detail={t("replayedTransactions")} tone="slate" icon="▣" />
        <KpiCard {...visualProps} label={t("observedTpe")} value={<AnimatedNumberText id="active-terminals" resetKey={visualResetKey} value={kpis.active_terminals} format={(value) => formatNumber(Math.round(value), locale)} options={{ epsilon: 0.2, timeConstantMs: 130 }} />} detail={t("replayBatch")} tone="blue" icon="⚙" />
        <KpiCard {...visualProps} label={`${t("transfersAmount")} (TND)`} value={<AnimatedNumberText id="transfers-amount" resetKey={visualResetKey} value={kpis.total_transfers_amount} format={(value) => formatAmount(value, locale)} options={{ epsilon: 0.001, timeConstantMs: 160 }} />} detail={<><AnimatedNumberText id="transfers-count" resetKey={visualResetKey} value={kpis.transfers_count} format={(value) => formatNumber(Math.round(value), locale)} options={{ epsilon: 0.2, timeConstantMs: 130 }} /> {t("transfers")} · {kpis.transfer_period_available ? t("periodApplied") : t("allPeriods")}</>} tone="slate" icon="➤" />
        <KpiCard {...visualProps} label={t("averageProcessing")} value={kpis.avg_processing_time_available ? <><AnimatedNumberText id="average-processing" resetKey={visualResetKey} value={Math.round(kpis.average_processing_time_ms ?? 0)} format={(value) => `${Math.round(value)} ms`} options={{ epsilon: 0.2, timeConstantMs: 130 }} /></> : "N/A"} detail={kpis.avg_processing_time_available ? "processing_time_ms" : t("unavailable")} tone="slate" icon="◴" />
      </section>

      <section className="chart-grid">
        <article className="supervision-panel">
          <PanelTitle icon="▥" title={t("incidentsByHour")} />
          <IncidentsChart data={snapshot.incidents_by_hour} {...visualProps} />
        </article>
        <article className="supervision-panel">
          <PanelTitle icon="⌁" title={t("fraudTrend")} />
          <FraudTrendChart data={snapshot.fraud_trend_7_days} slowAvailable={kpis.slow_transactions_available} fraudTimeoutAvailable={kpis.fraud_timeout_available} {...visualProps} />
          {!kpis.slow_transactions_available && !kpis.fraud_timeout_available ? <p className="chart-unavailable">{t("slowFraudUnavailable")}</p> : null}
        </article>
        <article className="supervision-panel">
          <PanelTitle icon="⌛" title={t("responseDistribution")} />
          {kpis.avg_processing_time_available ? <ResponseDistributionChart data={snapshot.response_time_distribution} {...visualProps} /> : <div className="chart-unavailable chart-unavailable-large">{t("responseUnavailable")}</div>}
        </article>
        <article className="supervision-panel">
          <PanelTitle icon="◔" title={t("statusDistribution")} />
          <StatusDonutChart data={snapshot.status_distribution} {...visualProps} />
        </article>
      </section>

      <section className="monitoring-grid">
        <article className="alerts-stack">
          <PanelTitle icon="⚠" title={t("detectedAlerts")} action={<span className="alert-count">{snapshot.active_alerts.length}</span>} />
          <div className="alert-list">
            {snapshot.active_alerts.length ? snapshot.active_alerts.slice(0, 6).map((alert) => (
              <div className={`active-alert ${alertTone[alert.severity]}`} key={alert.id}>
                <span className="alert-symbol">{alert.severity === "critical" ? "!" : alert.severity === "warning" ? "△" : "i"}</span>
                <div><small>{alert.title}</small><strong>{alert.merchant_name ? `${alert.merchant_name} - ${alert.message}` : alert.message}</strong></div>
                <time>{formatTime(alert.timestamp, locale)}</time>
              </div>
            )) : <div className="empty-state">{t("noAlerts")}</div>}
          </div>
        </article>
        <article className="supervision-panel live-feed">
          <PanelTitle icon="◉" title={t("eventTimeline")} action={<span className="feed-status">REPLAY</span>} />
          <div className="event-scroll">
            {snapshot.live_events.length ? snapshot.live_events.map((event) => (
              <div className={`live-event ${alertTone[event.severity]}`} key={event.id}>
                <span className="timeline-dot" />
                <time>{formatTime(event.timestamp, locale)}</time>
                <div>
                  <b>{event.title}</b>
                  <span>{event.merchant_name ?? event.merchant ?? "-"} {event.terminal_id ? `- ${event.terminal_id}` : ""} {event.message ? `- ${event.message}` : ""}</span>
                </div>
              </div>
            )) : <div className="empty-state">{t("noEvents")}</div>}
          </div>
        </article>
      </section>

      <section className="tables-grid">
        <article className="supervision-panel data-table-panel">
          <PanelTitle icon="⚠" title={t("topAnomalies")} />
          <TopAnomaliesTable rows={snapshot.top_anomalies} />
        </article>
        <article className="supervision-panel data-table-panel">
          <PanelTitle icon="⚙" title={t("topTpe")} />
          <TopTpeTable rows={snapshot.top_tpe} />
        </article>
        <article className="supervision-panel data-table-panel">
          <PanelTitle icon="♛" title={t("topMerchants")} />
          <TopMerchantsTable rows={snapshot.top_merchants} />
        </article>
      </section>

      <footer className="dashboard-footer">
        <span>{t("replayVia")}</span>
        <span>{t("redisCache")}</span>
        <span>{t("sqlReplay")}</span>
        <span>{t("version")} 0.1.0</span>
        <span>{t("lastUpdate")} {snapshot.replay_status.last_batch_at ? formatTime(snapshot.replay_status.last_batch_at, locale) : "-"}</span>
      </footer>
      <ChatAssistant selectedPeriod={selectedPeriod} />
    </main>
  );
}
