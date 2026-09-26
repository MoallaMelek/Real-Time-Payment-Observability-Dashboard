import { useDashboardTheme } from "../theme/DashboardTheme";
import type { SocketStatus } from "../types/dashboard";

interface Props {
  status: SocketStatus;
  clients: number;
  batchSize: number;
}

const statusClass: Record<SocketStatus, string> = {
  connected: "bg-mint/15 text-mint ring-mint/30",
  connecting: "bg-amber/15 text-amber ring-amber/30",
  reconnecting: "bg-amber/15 text-amber ring-amber/30",
  closed: "bg-danger/15 text-danger ring-danger/30",
};

export function WebSocketStatus({ status, clients, batchSize }: Props) {
  const { t } = useDashboardTheme();
  const statusLabel: Record<SocketStatus, string> = {
    connected: t("replayConnected"),
    connecting: t("replayConnecting"),
    reconnecting: t("replayReconnecting"),
    closed: t("replayOffline"),
  };

  return (
    <div className="flex flex-wrap items-center justify-end gap-2 text-xs">
      <span className={`rounded-full px-3 py-1 font-semibold ring-1 ${statusClass[status]}`}>
        {statusLabel[status]}
      </span>
      <span className="rounded-full bg-slate-900/70 px-3 py-1 text-slate-300 ring-1 ring-line">
        {t("observers")} {clients}
      </span>
      <span className="rounded-full bg-slate-900/70 px-3 py-1 text-slate-300 ring-1 ring-line">
        {t("batchTransactions")} {batchSize}
      </span>
    </div>
  );
}
