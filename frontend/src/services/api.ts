import type { DashboardPeriod, DashboardSnapshot, ReplayStatus } from "../types/dashboard";

export const API_BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export async function fetchDashboardSnapshot(period?: DashboardPeriod): Promise<DashboardSnapshot> {
  const query = period ? `?period=${encodeURIComponent(period)}` : "";
  const response = await fetch(`${API_BASE_URL}/api/supervision/snapshot${query}`);
  if (!response.ok) {
    throw new Error(`dashboard_snapshot_load_failed:${response.status}`);
  }
  return response.json();
}

export async function setFastForward(enabled: boolean): Promise<ReplayStatus> {
  const response = await fetch(`${API_BASE_URL}/api/replay/fast-forward?enabled=${enabled}`, { method: "POST" });
  if (!response.ok) {
    throw new Error(`fast_forward_update_failed:${response.status}`);
  }
  return response.json();
}
