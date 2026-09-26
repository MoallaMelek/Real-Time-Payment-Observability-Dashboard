import { memo, type ReactNode } from "react";

import { useAnimatedNumberSeries } from "../hooks/useAnimatedSeries";
import type { VisualMode, VisualPeriod } from "../lib/visualInterpolation";

interface Props {
  label: string;
  value: ReactNode;
  tone: "mint" | "cyan" | "amber" | "danger" | "blue" | "slate";
  detail?: ReactNode;
  trend?: ReactNode;
  sparkline?: number[];
  sparklineKey?: string;
  resetKey?: string | null;
  visualMode?: VisualMode;
  period?: VisualPeriod;
  expectedSnapshotIntervalMs?: number;
  icon: ReactNode;
}

function sparklinePoints(values: number[] = []) {
  if (values.length < 2) return "";
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const spread = max - min || 1;
  return values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * 100;
      const y = 32 - ((value - min) / spread) * 28;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function KpiCardBase({
  label,
  value,
  tone,
  detail,
  trend,
  sparkline = [],
  sparklineKey,
  resetKey,
  visualMode,
  period,
  expectedSnapshotIntervalMs,
  icon,
}: Props) {
  const animatedSparkline = useAnimatedNumberSeries(
    `kpi-sparkline:${sparklineKey ?? label}`,
    sparkline,
    resetKey,
    { epsilon: 0.15, timeConstantMs: 155, visualMode, period, expectedSnapshotIntervalMs },
  );
  const points = sparklinePoints(animatedSparkline);

  return (
    <article className={`supervision-kpi kpi-${tone}`}>
      <span className="kpi-icon">{icon}</span>
      <div className="kpi-copy">
        <p>{label}</p>
        <strong>{value}</strong>
        {detail ? <small>{detail}</small> : null}
      </div>
      <div className="kpi-side">
        {trend ? <span className="kpi-trend">{trend}</span> : null}
        {points ? (
          <svg className="kpi-sparkline" viewBox="0 0 100 36" role="img" aria-label={`${label} trend`}>
            <polyline points={points} fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        ) : null}
      </div>
    </article>
  );
}

export const KpiCard = memo(KpiCardBase);
