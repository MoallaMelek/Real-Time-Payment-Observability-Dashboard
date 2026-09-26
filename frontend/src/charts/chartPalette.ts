import type { DashboardTheme } from "../theme/DashboardTheme";

export function chartPalette(theme: DashboardTheme) {
  const isLight = theme === "light";

  return {
    axis: isLight ? "#53657d" : "#7f91aa",
    label: isLight ? "#142033" : "#dce7f7",
    secondary: isLight ? "#708096" : "#9fb0c7",
    line: isLight ? "#c9d6e2" : "#26364d",
    split: isLight ? "rgba(83,101,125,0.18)" : "rgba(127,145,170,0.14)",
    tooltipBg: isLight ? "rgba(255,255,255,0.96)" : "rgba(13,20,29,0.96)",
    tooltipText: isLight ? "#142033" : "#e7edf7",
  };
}
