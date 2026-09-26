import { memo, useEffect, useRef } from "react";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import type { EChartsOption } from "echarts";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, MarkLineComponent, CanvasRenderer]);

interface EChartProps {
  option: EChartsOption;
  height?: number;
}

function EChartBase({ option, height = 280 }: EChartProps) {
  const elementRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!elementRef.current) return;
    chartRef.current = echarts.init(elementRef.current, undefined, { renderer: "canvas" });

    const observer = new ResizeObserver(() => chartRef.current?.resize());
    observer.observe(elementRef.current);

    return () => {
      observer.disconnect();
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: false, lazyUpdate: true });
  }, [option]);

  return <div ref={elementRef} style={{ height }} className="w-full" />;
}

export const EChart = memo(EChartBase);
