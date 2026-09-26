import type { DashboardSnapshot, EventsSocketMessage } from "../types/dashboard";

export function mergePendingEvents(
  current: EventsSocketMessage | null,
  incoming: EventsSocketMessage,
): EventsSocketMessage {
  return !current || incoming.state_version > current.state_version ? incoming : current;
}

export function applyEvents(
  current: DashboardSnapshot | null,
  message: EventsSocketMessage,
): DashboardSnapshot | null {
  if (current && message.state_version <= current.state_version) return current;
  const update = [...message.events].reverse().find((event) => event.type === "supervision_update");
  return update?.snapshot ?? current;
}

export function normalizeSnapshot(snapshot: DashboardSnapshot): DashboardSnapshot {
  return {
    ...snapshot,
    active_alerts: snapshot.active_alerts ?? [],
    live_events: snapshot.live_events ?? [],
    top_anomalies: snapshot.top_anomalies ?? [],
    top_tpe: snapshot.top_tpe ?? [],
    top_merchants: snapshot.top_merchants ?? [],
  };
}

export function selectRestSnapshot(
  current: DashboardSnapshot | null,
  incoming: DashboardSnapshot,
): DashboardSnapshot {
  return current && current.state_version >= incoming.state_version ? current : incoming;
}
