import type { DashboardSnapshot } from "../types/dashboard";

export interface DashboardViewState {
  snapshot: DashboardSnapshot | null;
  lastBatchSize: number;
}

export const initialDashboardState: DashboardViewState = {
  snapshot: null,
  lastBatchSize: 0,
};
