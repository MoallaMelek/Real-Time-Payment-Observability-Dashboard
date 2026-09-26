import { useMemo } from "react";
import type { EChartsOption } from "echarts";

import { EChart } from "./EChart";
import { useAnimatedNumberSeries, useStableAxisMax } from "../hooks/useAnimatedSeries";
import type { VisualMode, VisualPeriod } from "../lib/visualInterpolation";
import { useDashboardTheme } from "../theme/DashboardTheme";
import type { DistributionItem, FraudTrendPoint, IncidentHour } from "../types/dashboard";

type Palette = ReturnType<typeof useChartPalette>;
interface VisualChartProps {
  resetKey?: string | null;
  visualMode?: VisualMode;
  period?: VisualPeriod;
  expectedSnapshotIntervalMs?: number;
}

function cssVar(name: string, fallback: string) {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function useChartPalette() {
  const { theme } = useDashboardTheme();
  return useMemo(
    () => ({
      theme,
      text: cssVar("--text-muted", "#7c8da8"),
      strong: cssVar("--text", "#e8eef9"),
      grid: cssVar("--chart-grid", "rgba(119,131,154,.14)"),
      panel: cssVar("--panel-solid", "#111a2b"),
      border: cssVar("--border", "#2a3952"),
      blue: cssVar("--accent-blue", "#2f80ed"),
      cyan: cssVar("--accent-cyan", "#16b8f3"),
      mint: cssVar("--accent-mint", "#00c896"),
      amber: cssVar("--accent-amber", "#f59e0b"),
      danger: cssVar("--accent-danger", "#ef4565"),
      purple: cssVar("--accent-purple", "#8b5cf6"),
    }),
    [theme],
  );
}

function area(color: string, opacity = 0.22) {
  return {
    type: "linear" as const,
    x: 0,
    y: 0,
    x2: 0,
    y2: 1,
    colorStops: [
      { offset: 0, color: colorWithAlpha(color, opacity) },
      { offset: 1, color: colorWithAlpha(color, 0.02) },
    ],
  };
}

function colorWithAlpha(color: string, opacity: number) {
  if (color.startsWith("#") && color.length === 7) {
    const r = Number.parseInt(color.slice(1, 3), 16);
    const g = Number.parseInt(color.slice(3, 5), 16);
    const b = Number.parseInt(color.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${opacity})`;
  }
  return color;
}

function baseOption(palette: Palette): Pick<EChartsOption, "tooltip" | "grid" | "dataZoom"> {
  return {
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", lineStyle: { color: palette.cyan, opacity: 0.35 } },
      backgroundColor: palette.panel,
      borderColor: palette.border,
      borderWidth: 1,
      padding: 12,
      textStyle: { color: palette.strong, fontSize: 12 },
      confine: true,
    },
    grid: { left: 42, right: 20, top: 44, bottom: 38 },
    dataZoom: [
      { type: "inside", throttle: 80 },
      { type: "slider", height: 15, bottom: 6, borderColor: "transparent", fillerColor: colorWithAlpha(palette.cyan, 0.18), handleSize: 0, moveHandleSize: 0, textStyle: { color: palette.text } },
    ],
  };
}

function axis(palette: Palette) {
  return {
    axisLine: { lineStyle: { color: palette.border } },
    axisLabel: { color: palette.text, fontSize: 10 },
    axisTick: { show: false },
    splitLine: { lineStyle: { color: palette.grid } },
  };
}

const liveAnimation = {
  animation: true,
  animationDuration: 90,
  animationDurationUpdate: 90,
  animationEasing: "cubicOut" as const,
  animationEasingUpdate: "cubicOut" as const,
};

export function IncidentsChart({
  data,
  resetKey,
  visualMode,
  period,
  expectedSnapshotIntervalMs,
}: { data: IncidentHour[] } & VisualChartProps) {
  const palette = useChartPalette();
  const visualOptions = { epsilon: 0.1, timeConstantMs: 155, visualMode, period, expectedSnapshotIntervalMs };
  const transactions = useAnimatedNumberSeries("chart:incidents:transactions", data.map((item) => item.transactions), resetKey, visualOptions);
  const refused = useAnimatedNumberSeries("chart:incidents:refused", data.map((item) => item.refused), resetKey, visualOptions);
  const anomalies = useAnimatedNumberSeries("chart:incidents:anomalies", data.map((item) => Math.max(0, item.refused - Math.round(item.transactions * 0.08))), resetKey, visualOptions);
  const trendTarget = data.map((_item, index) => {
    const windowRows = data.slice(Math.max(0, index - 2), index + 1);
    return Math.round(windowRows.reduce((sum, row) => sum + row.refused, 0) / Math.max(1, windowRows.length));
  });
  const trend = useAnimatedNumberSeries("chart:incidents:trend", trendTarget, resetKey, visualOptions);
  const yMax = useStableAxisMax([...transactions, ...refused, ...anomalies, ...trend], resetKey);
  const option = useMemo<EChartsOption>(() => {
    return {
      ...baseOption(palette),
      legend: { top: 5, textStyle: { color: palette.text, fontSize: 11 }, itemWidth: 18, itemHeight: 9 },
      xAxis: { type: "category", data: data.map((item) => item.hour), ...axis(palette) },
      yAxis: { type: "value", max: yMax, ...axis(palette) },
      series: [
        { name: "Transactions", type: "bar", stack: "traffic", data: transactions, barMaxWidth: 26, itemStyle: { color: area(palette.blue, 0.55), borderRadius: [6, 6, 0, 0] as [number, number, number, number] } },
        { name: "Refus", type: "bar", stack: "traffic", data: refused, barMaxWidth: 26, itemStyle: { color: palette.danger, borderRadius: [6, 6, 0, 0] as [number, number, number, number] } },
        { name: "Anomalies", type: "bar", stack: "traffic", data: anomalies, barMaxWidth: 26, itemStyle: { color: palette.amber, borderRadius: [6, 6, 0, 0] as [number, number, number, number] } },
        { name: "Trend", type: "line", smooth: true, data: trend, symbol: "circle", symbolSize: 7, lineStyle: { color: palette.mint, width: 3 }, itemStyle: { color: palette.mint }, areaStyle: { color: area(palette.mint, 0.16) } },
      ],
      ...liveAnimation,
    };
  }, [anomalies, data, palette, refused, transactions, trend, yMax]);
  return <EChart option={option} height={260} />;
}

export function FraudTrendChart({
  data,
  slowAvailable,
  fraudTimeoutAvailable,
  resetKey,
  visualMode,
  period,
  expectedSnapshotIntervalMs,
}: {
  data: FraudTrendPoint[];
  slowAvailable: boolean;
  fraudTimeoutAvailable: boolean;
} & VisualChartProps) {
  const palette = useChartPalette();
  const visualOptions = { epsilon: 0.1, timeConstantMs: 150, visualMode, period, expectedSnapshotIntervalMs };
  const refused = useAnimatedNumberSeries("chart:fraud:refused", data.map((item) => item.refused), resetKey, visualOptions);
  const slow = useAnimatedNumberSeries("chart:fraud:slow", data.map((item) => item.slow ?? 0), resetKey, visualOptions);
  const fraudTimeouts = useAnimatedNumberSeries("chart:fraud:timeouts", data.map((item) => item.fraud_timeouts ?? 0), resetKey, visualOptions);
  const yMax = useStableAxisMax([
    ...refused,
    ...(slowAvailable ? slow : []),
    ...(fraudTimeoutAvailable ? fraudTimeouts : []),
  ], resetKey);
  const option = useMemo<EChartsOption>(() => {
    const series: EChartsOption["series"] = [
      { name: "Refus", type: "line", smooth: 0.45, data: refused, symbol: "circle", symbolSize: 7, lineStyle: { color: palette.danger, width: 3 }, itemStyle: { color: palette.danger }, areaStyle: { color: area(palette.danger, 0.24) } },
      ...(slowAvailable ? [{ name: "Lentes", type: "line" as const, smooth: 0.45, data: slow, symbol: "circle", symbolSize: 6, lineStyle: { color: palette.amber, width: 2 }, itemStyle: { color: palette.amber }, areaStyle: { color: area(palette.amber, 0.12) } }] : []),
      ...(fraudTimeoutAvailable ? [{ name: "Anti-fraude", type: "line" as const, smooth: 0.45, data: fraudTimeouts, symbol: "circle", symbolSize: 6, lineStyle: { color: palette.purple, width: 2 }, itemStyle: { color: palette.purple } }] : []),
    ];
    return {
      ...baseOption(palette),
      legend: { top: 5, textStyle: { color: palette.text, fontSize: 11 }, itemWidth: 18, itemHeight: 9 },
      xAxis: { type: "category", data: data.map((item) => item.day), boundaryGap: false, ...axis(palette) },
      yAxis: { type: "value", max: yMax, ...axis(palette) },
      series,
      ...liveAnimation,
    };
  }, [data, fraudTimeoutAvailable, fraudTimeouts, palette, refused, slow, slowAvailable, yMax]);
  return <EChart option={option} height={260} />;
}

export function ResponseDistributionChart({
  data,
  resetKey,
  visualMode,
  period,
  expectedSnapshotIntervalMs,
}: { data: DistributionItem[] } & VisualChartProps) {
  const palette = useChartPalette();
  const values = useAnimatedNumberSeries("chart:response-distribution", data.map((item) => item.value), resetKey, { epsilon: 0.1, timeConstantMs: 140, visualMode, period, expectedSnapshotIntervalMs });
  const xMax = useStableAxisMax(values, resetKey);
  const option = useMemo<EChartsOption>(() => ({
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, backgroundColor: palette.panel, borderColor: palette.border, textStyle: { color: palette.strong }, confine: true },
    grid: { left: 86, right: 24, top: 18, bottom: 24 },
    xAxis: { type: "value", max: xMax, ...axis(palette) },
    yAxis: { type: "category", data: data.map((item) => item.label), ...axis(palette) },
    series: [
      {
        name: "Distribution",
        type: "bar",
        data: data.map((item, index) => ({
          value: values[index] ?? 0,
          itemStyle: { color: [palette.mint, palette.amber, palette.danger][index] ?? item.color, borderRadius: [0, 8, 8, 0] },
        })),
        barMaxWidth: 30,
        markLine: {
          symbol: "none",
          label: { color: palette.text, formatter: "SLA" },
          lineStyle: { color: palette.cyan, type: "dashed", width: 2 },
          data: [{ xAxis: Math.max(...data.map((item) => item.value), 1) * 0.72 }],
        },
      },
    ],
    ...liveAnimation,
  }), [data, palette, values, xMax]);
  return <EChart option={option} height={226} />;
}

export function StatusDonutChart({
  data,
  resetKey,
  visualMode,
  period,
  expectedSnapshotIntervalMs,
}: { data: DistributionItem[] } & VisualChartProps) {
  const palette = useChartPalette();
  const values = useAnimatedNumberSeries("chart:status-donut", data.map((item) => item.value), resetKey, { epsilon: 0.01, timeConstantMs: 145, visualMode, period, expectedSnapshotIntervalMs });
  const option = useMemo<EChartsOption>(() => ({
    tooltip: { trigger: "item", backgroundColor: palette.panel, borderColor: palette.border, textStyle: { color: palette.strong }, confine: true },
    legend: { orient: "vertical", right: 4, top: "middle", textStyle: { color: palette.text, fontSize: 11 }, itemWidth: 10, itemHeight: 10 },
    series: [
      {
        type: "pie",
        radius: ["58%", "82%"],
        center: ["37%", "66%"],
        startAngle: 180,
        endAngle: 360,
        avoidLabelOverlap: true,
        padAngle: 2,
        label: { show: true, formatter: "{d}%", color: palette.strong, fontSize: 11, fontWeight: 700 },
        labelLine: { length: 8, length2: 6, lineStyle: { color: palette.border } },
        itemStyle: { borderWidth: 3, borderColor: palette.panel, borderRadius: 8 },
        data: data.map((item, index) => ({ name: item.label, value: values[index] ?? 0, itemStyle: { color: item.color ?? palette.blue } })),
      },
    ],
    ...liveAnimation,
  }), [data, palette, values]);
  return <EChart option={option} height={226} />;
}
