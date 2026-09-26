import { useEffect, useMemo, useState } from "react";

import { ThemeToggle } from "./ThemeToggle";
import { redisStatusClass, redisStatusKey } from "../lib/redisStatus";
import { useDashboardTheme } from "../theme/DashboardTheme";
import type { DashboardPeriod, ReconciliationStatus, ReplayStatus, SocketStatus } from "../types/dashboard";

interface Props {
  socketStatus: SocketStatus;
  replayStatus: ReplayStatus;
  selectedPeriod: DashboardPeriod;
  onPeriodChange: (period: DashboardPeriod) => void;
  periodLoading: boolean;
  onFastForwardToggle: () => void;
  fastForwardLoading: boolean;
}

export function DashboardHeader({ socketStatus, replayStatus, selectedPeriod, onPeriodChange, periodLoading, onFastForwardToggle, fastForwardLoading }: Props) {
  const [now, setNow] = useState(() => new Date());
  const { language, t } = useDashboardTheme();
  const txRate = useMemo(() => replayStatus.current_speed_tx_per_sec, [replayStatus.current_speed_tx_per_sec]);
  const periods = useMemo<Array<{ value: DashboardPeriod; label: string }>>(
    () => [
      { value: "today", label: t("today") },
      { value: "yesterday", label: t("yesterday") },
      { value: "7d", label: t("sevenDays") },
      { value: "30d", label: t("thirtyDays") },
      { value: "quarter", label: t("quarter") },
      { value: "year", label: t("year") },
    ],
    [t],
  );

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const toggleFullscreen = async () => {
    if (document.fullscreenElement) {
      await document.exitFullscreen();
    } else {
      await document.documentElement.requestFullscreen();
    }
  };
  const reconciliationStatus = replayStatus.reconciliation_status ?? "unavailable";
  const qualityClass: Record<ReconciliationStatus, string> = {
    pending: "is-warn",
    validated: "is-online",
    warning: "is-warn",
    invalid: "is-offline",
    unavailable: "is-offline",
  };
  const qualityLabel: Record<ReconciliationStatus, string> = {
    pending: t("qualityPending"),
    validated: t("qualityValidated"),
    warning: t("qualityWarning"),
    invalid: t("qualityInvalid"),
    unavailable: t("qualityUnavailable"),
  };
  const processed = replayStatus.processed_transactions;
  const expected = replayStatus.expected_transactions;
  const progress = `${processed}${expected !== null && expected !== undefined ? ` / ${expected}` : ""} · ${(replayStatus.completion_rate ?? 0).toFixed(1)}%`;
  const redisDetail = t(redisStatusKey(replayStatus));
  const redisClass = redisStatusClass(replayStatus);

  return (
    <header className="supervision-header">
      <div className="supervision-brand">
        <span className="brand-mark" aria-hidden="true">T</span>
        <div className="supervision-title">
          <h1>{t("title")}</h1>
          <span className="replay-source">{t("subtitle")}</span>
        </div>
        <span className="live-badge"><i /> {t("liveReplay")}</span>
        <button type="button" className={`fast-forward-toggle ${replayStatus.fast_forward_enabled ? "is-active" : ""}`} onClick={onFastForwardToggle} disabled={fastForwardLoading} aria-pressed={replayStatus.fast_forward_enabled} title={t("fastForward")}>
          <span aria-hidden="true">⏩</span> {fastForwardLoading ? t("tuning") : t("fastForward")}
        </button>
        <span className="tx-rate" title={`${replayStatus.current_batch_size} transactions / ${replayStatus.current_interval_ms} ms`}>
          {replayStatus.fast_forward_enabled ? t("acceleratedReplay") : t("normalReplay")} · {t("target")} {txRate.toFixed(0)} trx/s
        </span>
      </div>

      <div className="period-switcher" aria-label={t("period")}>
        {periods.map((item) => (
          <button key={item.value} className={selectedPeriod === item.value ? "is-selected" : ""} onClick={() => onPeriodChange(item.value)} disabled={periodLoading}>
            {item.label}
          </button>
        ))}
      </div>

      <div className="header-clock">
        <div className="system-badges" aria-label="System status">
          <span className={`status-chip ${redisClass}`} title={`${t("redis")} : ${redisDetail}`}>
            {t("redis")} <b>{redisDetail}</b>
          </span>
          <span className={`status-chip ${socketStatus === "connected" ? "is-online" : "is-warn"}`}>{t("websocket")} <b>{socketStatus}</b></span>
          <span className={`status-chip ${replayStatus.source_available ? "is-online" : "is-offline"}`}>{t("sql")} <b>{replayStatus.source_available ? t("online") : t("offline")}</b></span>
          <span className={`status-chip quality-chip ${qualityClass[reconciliationStatus]}`}>
            {qualityLabel[reconciliationStatus]} <b>{progress}</b>
          </span>
        </div>
        <ThemeToggle />
        <span>{new Intl.DateTimeFormat(language === "fr" ? "fr-TN" : "en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric" }).format(now)}</span>
        <strong>{now.toLocaleTimeString(language === "fr" ? "fr-TN" : "en-GB", { hour12: false })}</strong>
        <button className="fullscreen-button" onClick={toggleFullscreen} title={t("fullscreen")} aria-label={t("fullscreen")}>⛶</button>
      </div>
    </header>
  );
}
