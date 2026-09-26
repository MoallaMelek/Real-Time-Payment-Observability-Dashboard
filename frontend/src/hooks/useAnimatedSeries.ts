import { useEffect, useMemo, useRef, useState } from "react";

import {
  StableAxisMaxTracker,
  visualInterpolationEngine,
  type InterpolationOptions,
} from "../lib/visualInterpolation";

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function optionSignature(options: InterpolationOptions | undefined): string {
  return JSON.stringify({
    epsilon: options?.epsilon,
    timeConstantMs: options?.timeConstantMs,
    maxStepPerSecond: options?.maxStepPerSecond,
    visualMode: options?.visualMode,
    period: options?.period,
    expectedSnapshotIntervalMs: options?.expectedSnapshotIntervalMs,
    visualLagMs: options?.visualLagMs,
    maxVisualLagMs: options?.maxVisualLagMs,
  });
}

function valuesSignature(values: number[]): string {
  return values.map((value) => (Number.isFinite(value) ? value : 0)).join("|");
}

export function useAnimatedNumberSeries(
  id: string,
  values: number[],
  resetKey?: string | null,
  options?: InterpolationOptions,
): number[] {
  const channelKey = `${resetKey ?? "default"}:${id}`;
  const reducedMotion = prefersReducedMotion();
  const [displayValues, setDisplayValues] = useState(values);
  const signature = optionSignature(options);
  const targetSignature = valuesSignature(values);
  const stableOptions = useMemo(
    () => ({ ...options, reducedMotion }),
    [reducedMotion, signature],
  );

  useEffect(() => {
    const unsubscribe = visualInterpolationEngine.subscribeSeries(channelKey, setDisplayValues);
    visualInterpolationEngine.setSeriesTarget(channelKey, values, { ...stableOptions, reset: true });
    return unsubscribe;
  }, [channelKey]);

  useEffect(() => {
    visualInterpolationEngine.setSeriesTarget(channelKey, values, stableOptions);
  }, [channelKey, stableOptions, targetSignature]);

  return displayValues;
}

export function useStableAxisMax(
  values: Array<number | null | undefined>,
  resetKey?: string | null,
): number {
  const trackerRef = useRef(new StableAxisMaxTracker());
  useEffect(() => {
    trackerRef.current.reset();
  }, [resetKey]);
  return trackerRef.current.update(values);
}
