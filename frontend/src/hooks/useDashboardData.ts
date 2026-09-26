import { useEffect, useRef, useState } from "react";

import { fetchDashboardSnapshot, setFastForward } from "../services/api";
import { applyEvents, mergePendingEvents, normalizeSnapshot, selectRestSnapshot } from "../lib/dashboardSync";
import { initialDashboardState } from "../store/dashboardStore";
import { useDashboardTheme } from "../theme/DashboardTheme";
import type { DashboardPeriod, DashboardSnapshot, EventsSocketMessage, SocketStatus } from "../types/dashboard";
import { connectPaymentsSocket } from "../websocket/paymentsSocket";

export function useDashboardData() {
  const { t } = useDashboardTheme();
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(initialDashboardState.snapshot);
  const [socketStatus, setSocketStatus] = useState<SocketStatus>("connecting");
  const [lastBatchSize, setLastBatchSize] = useState(initialDashboardState.lastBatchSize);
  const [selectedPeriod, setSelectedPeriod] = useState<DashboardPeriod>("today");
  const [periodLoading, setPeriodLoading] = useState(false);
  const [fastForwardLoading, setFastForwardLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pendingMessage = useRef<EventsSocketMessage | null>(null);
  const flushFrame = useRef<number | null>(null);

  const messageForError = (err: unknown, fallback: string) => {
    if (!(err instanceof Error)) return fallback;
    if (err.message.startsWith("dashboard_snapshot_load_failed")) return t("loadSnapshotError");
    if (err.message.startsWith("fast_forward_update_failed")) return t("fastForwardError");
    return err.message;
  };

  useEffect(() => {
    let mounted = true;

    fetchDashboardSnapshot("today")
      .then((data) => {
        if (mounted) {
          const normalized = normalizeSnapshot(data);
          setSnapshot((current) => selectRestSnapshot(current, normalized));
          setError(null);
        }
      })
      .catch((err: Error) => {
        if (mounted) {
          setError(messageForError(err, t("loadSnapshotError")));
        }
      });

    const flush = () => {
      const message = pendingMessage.current;
      pendingMessage.current = null;
      flushFrame.current = null;
      if (!message) return;
      setSnapshot((current) => applyEvents(current, message));
    };

    const scheduleFlush = () => {
      if (flushFrame.current !== null) return;
      flushFrame.current = window.requestAnimationFrame(flush);
    };

    const disconnect = connectPaymentsSocket({
      onStatus: setSocketStatus,
      onMessage: (message) => {
        if (message.type === "snapshot") {
          pendingMessage.current = null;
          if (flushFrame.current !== null) {
            window.cancelAnimationFrame(flushFrame.current);
            flushFrame.current = null;
          }
          const normalized = normalizeSnapshot(message.snapshot);
          setSnapshot((current) => {
            if (current && current.state_version > normalized.state_version) return current;
            return normalized;
          });
          return;
        }
        if (message.type === "events") {
          const update = message.events.find((event) => event.type === "supervision_update");
          if (update) {
            setLastBatchSize(update.snapshot.replay_status.batch_size);
          }
          pendingMessage.current = mergePendingEvents(pendingMessage.current, message);
          scheduleFlush();
        }
      },
    });

    return () => {
      mounted = false;
      disconnect();
      if (flushFrame.current !== null) {
        window.cancelAnimationFrame(flushFrame.current);
      }
    };
  }, []);

  const selectPeriod = async (period: DashboardPeriod) => {
    if (period === selectedPeriod && snapshot?.replay_status.period === period) return;
    setPeriodLoading(true);
    setError(null);
    try {
      const data = normalizeSnapshot(await fetchDashboardSnapshot(period));
      pendingMessage.current = null;
      setSelectedPeriod(period);
      setSnapshot(data);
    } catch (err) {
      setError(messageForError(err, t("changePeriodError")));
    } finally {
      setPeriodLoading(false);
    }
  };

  const toggleFastForward = async () => {
    if (!snapshot || fastForwardLoading) return;
    setFastForwardLoading(true);
    setError(null);
    try {
      const replayStatus = await setFastForward(!snapshot.replay_status.fast_forward_enabled);
      setSnapshot((current) => current ? { ...current, replay_status: replayStatus } : current);
    } catch (err) {
      setError(messageForError(err, t("fastForwardError")));
    } finally {
      setFastForwardLoading(false);
    }
  };

  return { snapshot, socketStatus, lastBatchSize, error, selectedPeriod, selectPeriod, periodLoading, fastForwardLoading, toggleFastForward };
}
